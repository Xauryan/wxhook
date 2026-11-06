from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol
from urllib.parse import quote, urlencode, urlsplit
from zoneinfo import ZoneInfo

import httpx
from websockets.asyncio.client import connect as websocket_connect

logger = logging.getLogger(__name__)

DEFAULT_CONSUMER_ID = "yunsheng-wx-robot-01"
DEFAULT_REST_BASE_URL = "https://youshenghe666.com/api/v1/robot-dispatch/consumer"
DEFAULT_WSS_URL = "wss://youshenghe666.com/api/v1/robot-dispatch/consumer/ws"
CONSUMER_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
EVENT_TYPE = "intention_order.created"
CHINA_TZ = ZoneInfo("Asia/Shanghai")
STARTUP_POLICIES = {"new_only", "include_pending"}
ON_DUTY_DIALED_NUMBER_THRESHOLD = 10
STAGE_REPORT_INTERVAL = 5
SCHEDULE_GRACE = timedelta(minutes=30)
SCHEDULE_RETRY_SECONDS = 30
SCHEDULED_GROUP_SEND_INTERVAL_SECONDS = 2.5
# Upstream group IDs identify the four purchase teams, not destination chatrooms.
SCHEDULED_REPORT_GROUPS = {
    "2": "呼叫中心一组",
    "3": "呼叫中心二组",
    "4": "呼叫中心四组",
    "5": "润汇外呼小组",
}


@dataclass(frozen=True, slots=True)
class ScheduledReportSlot:
    key: str
    label: str
    hour: int
    minute: int
    kind: str


SCHEDULED_REPORT_SLOTS = (
    ScheduledReportSlot("stage_1200", "12:00", 12, 0, "stage"),
    ScheduledReportSlot("stage_1600", "16:00", 16, 0, "stage"),
    ScheduledReportSlot("stage_1830", "18:30", 18, 30, "stage"),
    ScheduledReportSlot("closing_2100", "21:00", 21, 0, "closing"),
)


class IntentionNotificationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TargetGroup:
    room_id: str
    name: str


@dataclass(frozen=True, slots=True)
class NotificationConfig:
    enabled: bool
    consumer_id: str
    api_token: str
    target_groups: tuple[TargetGroup, ...]
    startup_policy: str = "new_only"
    activation_cutoff: str | None = None

    @property
    def ready(self) -> bool:
        startup_ready = self.startup_policy == "include_pending" or bool(
            self.activation_cutoff
        )
        return bool(
            self.enabled and self.api_token and self.target_groups and startup_ready
        )

    def public_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "consumer_id": self.consumer_id,
            "token_configured": bool(self.api_token),
            "startup_policy": self.startup_policy,
            "activation_cutoff": self.activation_cutoff,
            "target_groups": [
                {"room_id": group.room_id, "name": group.name}
                for group in self.target_groups
            ],
        }


@dataclass(frozen=True, slots=True)
class TeamMemberSnapshot:
    user_key: str
    name: str
    dialed_number_count: int
    intention_today: int

    @property
    def on_duty(self) -> bool:
        return self.dialed_number_count > ON_DUTY_DIALED_NUMBER_THRESHOLD


@dataclass(frozen=True, slots=True)
class TeamSnapshot:
    group_key: str
    group_name: str
    snapshot_at: str
    group_today: int | None
    group_yesterday_same_time: int | None
    overall_today: int | None
    members: tuple[TeamMemberSnapshot, ...]

    @property
    def on_duty_members(self) -> tuple[TeamMemberSnapshot, ...]:
        return tuple(member for member in self.members if member.on_duty)


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _json_object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any, fallback: str) -> str:
    normalized = str(value or "").strip()
    return normalized or fallback


def _key(value: Any, fallback: str) -> str:
    normalized = str(value or "").strip()
    return normalized if normalized else fallback.casefold()


def _nonnegative_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    return parsed if parsed >= 0 else None


def _raw_occurred_at(delivery: dict[str, Any]) -> str:
    payload = _json_object(delivery.get("payload"))
    data = _json_object(payload.get("data"))
    order = _json_object(data.get("order"))
    return str(
        payload.get("occurred_at")
        or order.get("created_at")
        or delivery.get("occurred_at")
        or ""
    ).strip()


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CHINA_TZ)
    return parsed.astimezone(CHINA_TZ)


def event_precedes_cutoff(delivery: dict[str, Any], cutoff: str | None) -> bool:
    if not cutoff:
        return False
    cutoff_at = _parse_datetime(cutoff)
    occurred_at = _parse_datetime(_raw_occurred_at(delivery))
    if cutoff_at is None:
        return False
    # Missing timestamps are treated as backlog so "new only" never floods a group.
    return occurred_at is None or occurred_at <= cutoff_at


def _event_context(delivery: dict[str, Any]) -> dict[str, str]:
    payload = _json_object(delivery.get("payload"))
    data = _json_object(payload.get("data"))
    order = _json_object(data.get("order"))
    organization = _json_object(data.get("organization"))
    group = _json_object(organization.get("outbound_group"))
    people = _json_object(data.get("people"))
    agent = _json_object(people.get("agent"))

    group_name = _text(group.get("name") or order.get("outbound_group_name"), "未分组")
    agent_name = _text(agent.get("name") or order.get("agent_name"), "未标记组员")
    now = datetime.now(CHINA_TZ)
    raw_occurred_at = _raw_occurred_at(delivery)
    parsed = _parse_datetime(raw_occurred_at) or now
    occurred_at = raw_occurred_at or parsed.isoformat()
    raw_order_date = str(order.get("order_date") or "").strip()
    try:
        stat_date = date.fromisoformat(raw_order_date).isoformat()
    except ValueError:
        stat_date = parsed.date().isoformat()
    recorded_at = (
        parsed.strftime("%H:%M")
        if parsed.date() == now.date()
        else parsed.strftime("%m-%d %H:%M")
    )

    return {
        "group_key": _key(group.get("id"), group_name),
        "group_name": group_name,
        "agent_key": _key(
            agent.get("user_id") or order.get("agent_user_id"), agent_name
        ),
        "agent_name": agent_name,
        "occurred_at": occurred_at,
        "stat_date": stat_date,
        "recorded_at": recorded_at,
    }


def _extract_team_snapshot(
    delivery: dict[str, Any], context: dict[str, str] | None = None
) -> TeamSnapshot | None:
    payload = _json_object(delivery.get("payload"))
    data = _json_object(payload.get("data"))
    raw = _json_object(data.get("team_snapshot") or payload.get("team_snapshot"))
    if raw.get("complete") is not True:
        return None

    event_context = context or _event_context(delivery)
    raw_group_id = raw.get("group_id")
    raw_group_name = _text(raw.get("group_name"), event_context["group_name"])
    group_key = (
        _key(raw_group_id, raw_group_name)
        if raw_group_id is not None
        else event_context["group_key"]
    )
    if raw_group_id is not None and group_key != event_context["group_key"]:
        return None

    snapshot_at = str(raw.get("snapshot_at") or "").strip()
    parsed_snapshot_at = _parse_datetime(snapshot_at)
    if (
        parsed_snapshot_at is None
        or parsed_snapshot_at.date().isoformat() != event_context["stat_date"]
    ):
        return None
    event_at = _parse_datetime(event_context["occurred_at"])
    if event_at is not None and parsed_snapshot_at < event_at:
        return None

    raw_members = raw.get("members")
    if not isinstance(raw_members, list):
        return None
    members: list[TeamMemberSnapshot] = []
    seen: set[str] = set()
    for item in raw_members:
        if not isinstance(item, dict):
            return None
        name = _text(item.get("name") or item.get("user_name"), "")
        user_key = _key(item.get("user_id"), name)
        dialed_number_count = _nonnegative_int(
            item.get(
                "dialed_number_count",
                item.get(
                    "customer_reach_count",
                    item.get("unique_dialed_number_count"),
                ),
            )
        )
        intention_today = _nonnegative_int(
            item.get(
                "intention_today",
                item.get("intention_count", item.get("today_count")),
            )
        )
        if not name or dialed_number_count is None or intention_today is None:
            return None
        if user_key in seen:
            return None
        seen.add(user_key)
        members.append(
            TeamMemberSnapshot(
                user_key=user_key,
                name=name,
                dialed_number_count=dialed_number_count,
                intention_today=intention_today,
            )
        )

    event_member = next(
        (member for member in members if member.user_key == event_context["agent_key"]),
        None,
    )
    if event_member is None:
        same_name = [
            member
            for member in members
            if member.name.casefold() == event_context["agent_name"].casefold()
        ]
        event_member = same_name[0] if len(same_name) == 1 else None
    if event_member is not None and event_member.intention_today == 0:
        return None

    return TeamSnapshot(
        group_key=group_key,
        group_name=raw_group_name,
        snapshot_at=parsed_snapshot_at.isoformat(),
        group_today=_nonnegative_int(raw.get("group_today")),
        group_yesterday_same_time=_nonnegative_int(
            raw.get("group_yesterday_same_time")
        ),
        overall_today=_nonnegative_int(raw.get("overall_today")),
        members=tuple(members),
    )


def _split_label(value: str, width: int = 12) -> list[str]:
    value = value.strip()
    return [value[index : index + width] for index in range(0, len(value), width)] or [
        ""
    ]


def _snapshot_member(
    snapshot: TeamSnapshot, agent_key: str, agent_name: str
) -> TeamMemberSnapshot | None:
    for member in snapshot.members:
        if member.user_key == agent_key:
            return member
    normalized_name = agent_name.strip().casefold()
    same_name = [
        member
        for member in snapshot.members
        if member.name.casefold() == normalized_name
    ]
    return same_name[0] if len(same_name) == 1 else None


def _rank_phrase(snapshot: TeamSnapshot, member: TeamMemberSnapshot) -> str | None:
    if not member.on_duty:
        return None
    on_duty = snapshot.on_duty_members
    rank = 1 + sum(
        candidate.intention_today > member.intention_today for candidate in on_duty
    )
    tied = sum(
        candidate.intention_today == member.intention_today for candidate in on_duty
    )
    return f"并列第 {rank}" if tied > 1 else f"第 {rank}"


def _rank_number(snapshot: TeamSnapshot, member: TeamMemberSnapshot) -> int | None:
    if not member.on_duty:
        return None
    return 1 + sum(
        candidate.intention_today > member.intention_today
        for candidate in snapshot.on_duty_members
    )


def _trend_text(current: int, previous: int | None) -> str | None:
    if previous is None:
        return None
    delta = current - previous
    if previous == 0:
        if delta > 0:
            return f"新增 {delta} 单"
        return "持平"
    percentage = round(delta / previous * 100)
    if percentage > 0:
        return f"+{percentage}%"
    if percentage < 0:
        return f"{percentage}%"
    return "持平"


def _name_lines(prefix: str, names: list[str], size: int = 4) -> list[str]:
    if not names:
        return [f"{prefix}无人"]
    chunks = [names[index : index + size] for index in range(0, len(names), size)]
    return [
        f"{prefix if index == 0 else '      '}{'、'.join(chunk)}"
        for index, chunk in enumerate(chunks)
    ]


def _distribution_lines(snapshot: TeamSnapshot) -> list[str]:
    members = sorted(
        snapshot.on_duty_members,
        key=lambda member: (-member.intention_today, member.name.casefold()),
    )
    if not members:
        return ["暂无符合在岗口径的组员"]
    lines: list[str] = []
    counts = sorted({member.intention_today for member in members}, reverse=True)
    for count in counts:
        names = [member.name for member in members if member.intention_today == count]
        lines.extend(_name_lines(f"{count} 单：", names))
    return lines


def _stage_change_lines(
    current: TeamSnapshot, previous: TeamSnapshot | None
) -> list[str]:
    if previous is None:
        return []
    previous_by_key = {member.user_key: member for member in previous.members}
    changes: list[tuple[int, int, str]] = []
    for member in current.on_duty_members:
        prior = previous_by_key.get(member.user_key)
        prior_count = prior.intention_today if prior is not None else 0
        delta = member.intention_today - prior_count
        if delta <= 0:
            continue
        if prior_count == 0:
            detail = "新破零"
        else:
            current_rank = _rank_number(current, member)
            previous_rank = _rank_number(previous, prior) if prior is not None else None
            if (
                current_rank is not None
                and previous_rank is not None
                and current_rank < previous_rank
            ):
                detail = f"升至{_rank_phrase(current, member)}"
            else:
                detail = f"+{delta} 单"
        changes.append(
            (
                delta,
                member.intention_today,
                f"{member.name} {prior_count}→{member.intention_today}｜{detail}",
            )
        )
    changes.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [item[2] for item in changes[:5]]


def render_announcement(
    *,
    group_name: str,
    agent_name: str,
    agent_today: int,
    group_today: int,
    overall_today: int,
    recorded_at: str,
    agent_key: str | None = None,
    contributor_count: int | None = None,
    team_snapshot: TeamSnapshot | None = None,
    previous_stage_snapshot: TeamSnapshot | None = None,
) -> str:
    """Render a team-oriented plain-text announcement for WeChat."""

    member: TeamMemberSnapshot | None = None
    if team_snapshot is not None:
        member = _snapshot_member(
            team_snapshot, agent_key or agent_name.casefold(), agent_name
        )
        if member is not None:
            agent_today = member.intention_today
        if team_snapshot.group_today is not None:
            group_today = team_snapshot.group_today
        if team_snapshot.overall_today is not None:
            overall_today = team_snapshot.overall_today

    is_stage_report = bool(
        team_snapshot and group_today > 0 and group_today % STAGE_REPORT_INTERVAL == 0
    )
    if is_stage_report and team_snapshot is not None:
        previous_group = team_snapshot.group_yesterday_same_time
        trend = _trend_text(group_today, previous_group)
        summary = f"今日 {group_today} 单"
        if previous_group is not None:
            summary += f"｜昨日同期 {previous_group} 单"
        if trend:
            summary += f"｜{trend}"

        on_duty = team_snapshot.on_duty_members
        broken = [candidate for candidate in on_duty if candidate.intention_today > 0]
        on_duty_output = sum(candidate.intention_today for candidate in on_duty)
        zero_names = [
            candidate.name for candidate in on_duty if candidate.intention_today == 0
        ]
        change_lines = _stage_change_lines(team_snapshot, previous_stage_snapshot)
        if not change_lines:
            before = max(0, agent_today - 1)
            detail = "新破零" if before == 0 else "+1 单"
            change_lines = [f"{agent_name} {before}→{agent_today}｜{detail}"]

        lines = [
            f"【阶段战报｜{recorded_at}】",
            group_name,
            "",
            summary,
            f"已破零 {len(broken)}/{len(on_duty)} 人｜破零率 {round(len(broken) / len(on_duty) * 100) if on_duty else 0}%",
        ]
        if on_duty_output != group_today:
            lines.append(
                f"在岗产出 {on_duty_output} 单｜未达在岗口径 {max(0, group_today - on_duty_output)} 单"
            )
        lines.extend(
            [
                "",
                "【组内分布】",
                *_distribution_lines(team_snapshot),
                "",
                "【本阶段变化】",
                *change_lines,
                "",
                *_name_lines("待破零：", zero_names),
                f"全场累计 {overall_today} 单",
                f"在岗口径：拨打号码 > {ON_DUTY_DIALED_NUMBER_THRESHOLD}",
            ]
        )
        return "\n".join(lines)

    before = max(0, agent_today - 1)
    change_detail = "完成破零" if before == 0 else None
    if member is not None and team_snapshot is not None:
        change_detail = change_detail or _rank_phrase(team_snapshot, member)
    change = f"本次变化：{before}→{agent_today}"
    if change_detail:
        change += f"，{change_detail}"

    lines = [
        f"【新单｜{group_name}】",
        f"{agent_name} +1｜个人 {agent_today} 单",
        f"{recorded_at} 录单",
        "",
    ]
    if team_snapshot is not None:
        previous_group = team_snapshot.group_yesterday_same_time
        group_line = f"小组 {group_today} 单"
        if previous_group is not None:
            group_line += f"｜昨日同期 {previous_group} 单"
        on_duty = team_snapshot.on_duty_members
        broken_count = sum(candidate.intention_today > 0 for candidate in on_duty)
        lines.extend(
            [
                group_line,
                f"已破零 {broken_count}/{len(on_duty)} 人｜待破零 {len(on_duty) - broken_count} 人",
            ]
        )
    else:
        lines.extend(
            [
                f"小组 {group_today} 单｜全场 {overall_today} 单",
                f"今日已有 {max(1, contributor_count or 1)} 人出单",
            ]
        )
    lines.extend(["", change])
    if team_snapshot is not None:
        lines.append(f"全场累计 {overall_today} 单")
    return "\n".join(lines)


def parse_scheduled_snapshot_batch(
    batch: dict[str, Any],
    *,
    expected_date: date | None = None,
    group_keys: frozenset[str] | None = None,
) -> tuple[datetime, tuple[TeamSnapshot, ...], tuple[str, ...]]:
    raw_snapshot_at = str(batch.get("snapshot_at") or "").strip()
    snapshot_at = _parse_datetime(raw_snapshot_at)
    raw_groups = batch.get("groups")
    if snapshot_at is None or not isinstance(raw_groups, list):
        raise IntentionNotificationError("定时战报快照格式不正确")
    if expected_date is not None and snapshot_at.date() != expected_date:
        raise IntentionNotificationError("定时战报快照日期不正确")

    snapshots: list[TeamSnapshot] = []
    unavailable: list[str] = []
    seen_groups: set[str] = set()
    expected_overall: int | None = None
    for raw_group in raw_groups:
        if not isinstance(raw_group, dict):
            raise IntentionNotificationError("定时战报小组快照格式不正确")
        group_name = _text(
            raw_group.get("group_name"),
            f"小组 {raw_group.get('group_id') or '未知'}",
        )
        if (
            group_keys is not None
            and _key(raw_group.get("group_id"), group_name) not in group_keys
        ):
            continue
        if raw_group.get("complete") is not True:
            unavailable.append(group_name)
            continue
        group_snapshot_at = _parse_datetime(str(raw_group.get("snapshot_at") or ""))
        if group_snapshot_at != snapshot_at:
            unavailable.append(group_name)
            continue
        synthetic_delivery = {
            "payload": {
                "occurred_at": raw_snapshot_at,
                "data": {
                    "order": {"order_date": snapshot_at.date().isoformat()},
                    "organization": {
                        "outbound_group": {
                            "id": raw_group.get("group_id"),
                            "name": group_name,
                        }
                    },
                    "team_snapshot": raw_group,
                },
            }
        }
        snapshot = _extract_team_snapshot(synthetic_delivery)
        if (
            snapshot is None
            or snapshot.group_today is None
            or snapshot.group_yesterday_same_time is None
            or snapshot.overall_today is None
            or snapshot.group_key in seen_groups
        ):
            unavailable.append(group_name)
            continue
        if expected_overall is None:
            expected_overall = snapshot.overall_today
        elif snapshot.overall_today != expected_overall:
            raise IntentionNotificationError("定时战报全场统计不一致")
        seen_groups.add(snapshot.group_key)
        snapshots.append(snapshot)

    if not snapshots:
        raise IntentionNotificationError("定时战报没有可用的小组快照")
    snapshots.sort(key=lambda item: (item.group_name.casefold(), item.group_key))
    return snapshot_at, tuple(snapshots), tuple(dict.fromkeys(unavailable))


def _scheduled_group_lines(
    snapshot: TeamSnapshot,
    *,
    previous: TeamSnapshot | None,
    closing: bool,
) -> list[str]:
    group_today = snapshot.group_today or 0
    previous_group = snapshot.group_yesterday_same_time
    trend = _trend_text(group_today, previous_group)
    summary = f"今日 {group_today} 单｜昨日同期 {previous_group or 0} 单"
    if trend:
        summary += f"｜{trend}"

    on_duty = snapshot.on_duty_members
    broken = [member for member in on_duty if member.intention_today > 0]
    active_output = sum(member.intention_today for member in on_duty)
    zero_names = [member.name for member in on_duty if member.intention_today == 0]
    lines = [
        f"【{snapshot.group_name}】",
        summary,
        f"已破零 {len(broken)}/{len(on_duty)} 人｜待破零 {len(zero_names)} 人",
    ]
    if active_output != group_today:
        lines.append(
            f"在岗产出 {active_output} 单｜未达在岗口径 {max(0, group_today - active_output)} 单"
        )
    if closing:
        average = active_output / len(on_duty) if on_duty else 0
        highest = max((member.intention_today for member in on_duty), default=0)
        lines.append(f"在岗人均 {average:.1f} 单｜最高 {highest} 单")
    lines.extend(_distribution_lines(snapshot))

    changes = _stage_change_lines(snapshot, previous)
    if changes:
        lines.append("本阶段变化：")
        lines.extend(changes[:3])
    if closing:
        lines.extend(_name_lines("明日优先关注：", zero_names))
    else:
        lines.extend(_name_lines("待破零：", zero_names))
    return lines


def _scheduled_group_batch(
    snapshot_at: datetime, snapshot: TeamSnapshot
) -> dict[str, Any]:
    return {
        "complete": True,
        "snapshot_at": snapshot_at.isoformat(),
        "groups": [
            {
                "complete": True,
                "group_id": snapshot.group_key,
                "group_name": snapshot.group_name,
                "snapshot_at": snapshot.snapshot_at,
                "group_today": snapshot.group_today,
                "group_yesterday_same_time": snapshot.group_yesterday_same_time,
                "overall_today": snapshot.overall_today,
                "members": [
                    {
                        "user_id": member.user_key,
                        "name": member.name,
                        "dialed_number_count": member.dialed_number_count,
                        "intention_today": member.intention_today,
                    }
                    for member in snapshot.members
                ],
            }
        ],
    }


def _scheduled_stage_report(slot, snapshot_at, snapshot, previous):
    point = snapshot_at.astimezone(CHINA_TZ)
    total = snapshot.group_today
    lines = [f"【意向阶段战报｜{snapshot.group_name}】",
             f"{point:%m-%d} {slot.label} 场｜数据截至 {point:%H:%M}"]
    summary = f'今日 {total} 单' if total is not None else '今日单量暂缺'
    yesterday = snapshot.group_yesterday_same_time
    if yesterday is not None:
        summary += f'｜昨日同期 {yesterday} 单'
        if total is not None:
            delta = total - yesterday
            summary += '｜' + (f'增加 {delta} 单' if delta > 0 else f'减少 {-delta} 单' if delta < 0 else '持平')
    lines.append(summary)
    prior_at = _parse_datetime(previous.snapshot_at) if previous else None
    current_at = _parse_datetime(snapshot.snapshot_at)
    if (previous and prior_at and current_at and current_at == point
            and previous.group_key == snapshot.group_key and prior_at.date() == point.date()
            and prior_at < point and previous.group_today is not None and total is not None):
        delta = total - previous.group_today
        change = f'增加 {delta} 单' if delta > 0 else f'减少 {-delta} 单' if delta < 0 else '持平'
        lines.append(f'较上次 {prior_at:%H:%M} 快照{change}')
    members = sorted(snapshot.on_duty_members, key=lambda member: (-member.intention_today, member.name.casefold()))
    lines.append('')
    if not members:
        lines.append('本次通话统计人数为 0，不计算破零率和人均。')
    else:
        broken = [member for member in members if member.intention_today > 0]
        zero_names = [member.name for member in members if member.intention_today == 0]
        lines.append(f'通话统计组员 {len(members)} 人：已破零 {len(broken)} 人，待破零 {len(zero_names)} 人')
        if broken:
            lines.append('成员分布')
            for number in sorted({member.intention_today for member in broken}, reverse=True):
                lines.extend(_name_lines(f'{number} 单：', [member.name for member in broken if member.intention_today == number]))
        lines.extend(_name_lines('待破零：', zero_names))
    member_total = sum(member.intention_today for member in members)
    if total is not None and total > member_total:
        lines.append(f'另有 {total - member_total} 单不在上述成员分布内')
    elif total is not None and total < member_total:
        lines.append('成员分布与小组总量不一致，请核对统计。')
    lines.extend(['', f'统计组员口径：当日拨打不同号码 >{ON_DUTY_DIALED_NUMBER_THRESHOLD}'])
    return '\n'.join(lines)


def render_scheduled_group_report(
    *,
    slot: ScheduledReportSlot,
    snapshot_at: datetime,
    snapshot: TeamSnapshot,
    previous_snapshot: TeamSnapshot | None = None,
) -> str:
    closing = slot.kind == "closing"
    if not closing:
        return _scheduled_stage_report(slot, snapshot_at, snapshot, previous_snapshot)
    title = "今日收官" if closing else "定时阶段战报"
    lines = [
        f"【{title}｜{slot.label}】",
        f"数据截至 {snapshot_at.strftime('%H:%M')}",
        "",
        *_scheduled_group_lines(
            snapshot,
            previous=previous_snapshot,
            closing=closing,
        ),
        "",
        f"全场累计 {snapshot.overall_today or 0} 单",
        f"在岗口径：拨打不同号码 > {ON_DUTY_DIALED_NUMBER_THRESHOLD}",
    ]
    return "\n".join(lines)


class IntentionNotificationStore:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_lock = threading.Lock()
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _set_meta(connection: sqlite3.Connection, key: str, value: str) -> None:
        connection.execute(
            """
            INSERT INTO intention_notification_meta(key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, value),
        )

    def initialize(self, seed_token: str = "") -> None:
        with self._initialize_lock:
            if self._initialized:
                if seed_token.strip():
                    with self._connect() as connection:
                        connection.execute(
                            """
                            UPDATE intention_notification_meta
                            SET value=?
                            WHERE key='api_token' AND value=''
                            """,
                            (seed_token.strip(),),
                        )
                return
            now = _now_ms()
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.executescript("""
                CREATE TABLE IF NOT EXISTS intention_notification_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS intention_notification_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    source_attempt_count INTEGER NOT NULL DEFAULT 0,
                    occurred_at TEXT NOT NULL,
                    stat_date TEXT NOT NULL,
                    group_key TEXT NOT NULL,
                    group_name TEXT NOT NULL,
                    agent_key TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    agent_today INTEGER NOT NULL,
                    group_today INTEGER NOT NULL,
                    overall_today INTEGER NOT NULL,
                    announcement_text TEXT NOT NULL,
                    delivery_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_error TEXT,
                    remote_ack_status TEXT,
                    remote_ack_error TEXT,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    sent_at_ms INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_intention_events_daily
                    ON intention_notification_events(stat_date, group_key, agent_key);
                CREATE INDEX IF NOT EXISTS idx_intention_events_status
                    ON intention_notification_events(status, created_at_ms);
                CREATE TABLE IF NOT EXISTS intention_notification_targets (
                    event_id TEXT NOT NULL,
                    room_id TEXT NOT NULL,
                    room_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    message_id TEXT,
                    last_error TEXT,
                    sent_at_ms INTEGER,
                    updated_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(event_id, room_id),
                    FOREIGN KEY(event_id) REFERENCES intention_notification_events(event_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_intention_targets_status
                    ON intention_notification_targets(status, updated_at_ms);
                CREATE TABLE IF NOT EXISTS intention_notification_scheduled_group_reports (
                    report_id TEXT PRIMARY KEY,
                    report_date TEXT NOT NULL,
                    slot TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    outbound_group_key TEXT NOT NULL,
                    outbound_group_name TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    announcement_text TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at_ms INTEGER NOT NULL,
                    last_error TEXT,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    sent_at_ms INTEGER,
                    UNIQUE(report_date, slot, outbound_group_key)
                );
                CREATE INDEX IF NOT EXISTS idx_intention_scheduled_group_reports_status
                    ON intention_notification_scheduled_group_reports(status, next_attempt_at_ms);
                CREATE TABLE IF NOT EXISTS intention_notification_scheduled_group_targets (
                    report_id TEXT NOT NULL,
                    room_id TEXT NOT NULL,
                    room_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    message_id TEXT,
                    last_error TEXT,
                    sent_at_ms INTEGER,
                    updated_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(report_id, room_id),
                    FOREIGN KEY(report_id)
                        REFERENCES intention_notification_scheduled_group_reports(report_id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS intention_notification_schedule_runs (
                    report_date TEXT NOT NULL,
                    slot TEXT NOT NULL,
                    status TEXT NOT NULL,
                    unavailable_groups_json TEXT NOT NULL DEFAULT '[]',
                    updated_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(report_date, slot)
                );
                """)
                connection.execute(
                    "INSERT OR IGNORE INTO intention_notification_meta(key, value) VALUES ('enabled', '0')"
                )
                connection.execute(
                    "INSERT OR IGNORE INTO intention_notification_meta(key, value) VALUES ('consumer_id', ?)",
                    (DEFAULT_CONSUMER_ID,),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO intention_notification_meta(key, value) VALUES ('target_groups', '[]')"
                )
                connection.execute(
                    "INSERT OR IGNORE INTO intention_notification_meta(key, value) VALUES ('startup_policy', 'new_only')"
                )
                connection.execute(
                    "INSERT OR IGNORE INTO intention_notification_meta(key, value) VALUES ('activation_cutoff', '')"
                )
                connection.execute(
                    "INSERT OR IGNORE INTO intention_notification_meta(key, value) VALUES ('api_token', ?)",
                    (seed_token.strip(),),
                )
                if seed_token.strip():
                    connection.execute(
                        """
                        UPDATE intention_notification_meta
                        SET value=?
                        WHERE key='api_token' AND value=''
                        """,
                        (seed_token.strip(),),
                    )
                connection.execute(
                    """
                    UPDATE intention_notification_targets
                    SET status='pending', last_error=COALESCE(last_error, 'send outcome unknown after restart'),
                        updated_at_ms=?
                    WHERE status='sending'
                    """,
                    (now,),
                )
                connection.execute(
                    """
                    UPDATE intention_notification_events
                    SET status='received', updated_at_ms=?
                    WHERE status='sending'
                    """,
                    (now,),
                )
                connection.execute(
                    """
                    UPDATE intention_notification_scheduled_group_targets
                    SET status='pending',
                        last_error=COALESCE(last_error, 'send outcome unknown after restart'),
                        updated_at_ms=?
                    WHERE status='sending'
                    """,
                    (now,),
                )
                connection.execute(
                    """
                    UPDATE intention_notification_scheduled_group_reports
                    SET status='received', next_attempt_at_ms=?, updated_at_ms=?
                    WHERE status='sending'
                    """,
                    (now, now),
                )
            self._initialized = True

    def configuration(self) -> NotificationConfig:
        self.initialize()
        with self._connect() as connection:
            values = {
                str(row["key"]): str(row["value"])
                for row in connection.execute(
                    "SELECT key, value FROM intention_notification_meta"
                )
            }
        try:
            raw_groups = json.loads(values.get("target_groups", "[]"))
        except json.JSONDecodeError:
            raw_groups = []
        groups: list[TargetGroup] = []
        if isinstance(raw_groups, list):
            for item in raw_groups:
                if not isinstance(item, dict):
                    continue
                room_id = str(item.get("room_id") or "").strip()
                name = str(item.get("name") or room_id).strip()
                if room_id and name:
                    groups.append(TargetGroup(room_id=room_id, name=name))
        return NotificationConfig(
            enabled=values.get("enabled") == "1",
            consumer_id=values.get("consumer_id", DEFAULT_CONSUMER_ID),
            api_token=values.get("api_token", ""),
            target_groups=tuple(groups),
            startup_policy=(
                values.get("startup_policy", "new_only")
                if values.get("startup_policy", "new_only") in STARTUP_POLICIES
                else "new_only"
            ),
            activation_cutoff=values.get("activation_cutoff") or None,
        )

    def update_configuration(
        self,
        *,
        enabled: bool,
        consumer_id: str,
        target_groups: tuple[TargetGroup, ...],
        api_token: str | None,
        startup_policy: str,
        activation_cutoff: str | None,
    ) -> NotificationConfig:
        self.initialize()
        encoded_groups = json.dumps(
            [{"room_id": group.room_id, "name": group.name} for group in target_groups],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self._connect() as connection:
            self._set_meta(connection, "enabled", "1" if enabled else "0")
            self._set_meta(connection, "consumer_id", consumer_id)
            self._set_meta(connection, "target_groups", encoded_groups)
            self._set_meta(connection, "startup_policy", startup_policy)
            self._set_meta(connection, "activation_cutoff", activation_cutoff or "")
            if api_token is not None:
                self._set_meta(connection, "api_token", api_token)
        return self.configuration()

    def scheduled_report(self, report_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM intention_notification_scheduled_group_reports
                WHERE report_id=?
                """,
                (report_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def schedule_run(self, report_date: str, slot: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM intention_notification_schedule_runs
                WHERE report_date=? AND slot=?
                """,
                (report_date, slot),
            ).fetchone()
        return dict(row) if row is not None else None

    def mark_schedule_run(
        self,
        report_date: str,
        slot: str,
        *,
        complete: bool,
        unavailable_groups: tuple[str, ...],
    ) -> None:
        unavailable_json = json.dumps(
            list(unavailable_groups),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO intention_notification_schedule_runs(
                    report_date, slot, status, unavailable_groups_json,
                    updated_at_ms
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(report_date, slot) DO UPDATE SET
                    status=excluded.status,
                    unavailable_groups_json=excluded.unavailable_groups_json,
                    updated_at_ms=excluded.updated_at_ms
                """,
                (
                    report_date,
                    slot,
                    "complete" if complete else "partial",
                    unavailable_json,
                    _now_ms(),
                ),
            )

    def persist_scheduled_report(
        self,
        *,
        report_id: str,
        report_date: str,
        slot: ScheduledReportSlot,
        outbound_group_key: str,
        outbound_group_name: str,
        snapshot_batch: dict[str, Any],
        announcement_text: str,
        target_groups: tuple[TargetGroup, ...],
    ) -> dict[str, Any]:
        self.initialize()
        now = _now_ms()
        serialized = json.dumps(
            snapshot_batch,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            inserted = connection.execute(
                """
                INSERT OR IGNORE INTO intention_notification_scheduled_group_reports(
                    report_id, report_date, slot, kind,
                    outbound_group_key, outbound_group_name, snapshot_json,
                    announcement_text, status, next_attempt_at_ms,
                    created_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'received', ?, ?, ?)
                """,
                (
                    report_id,
                    report_date,
                    slot.label,
                    slot.kind,
                    outbound_group_key,
                    outbound_group_name,
                    serialized,
                    announcement_text,
                    now,
                    now,
                    now,
                ),
            ).rowcount
            if inserted:
                for group in target_groups:
                    connection.execute(
                        """
                        INSERT INTO intention_notification_scheduled_group_targets(
                            report_id, room_id, room_name, status, updated_at_ms
                        ) VALUES (?, ?, ?, 'pending', ?)
                        """,
                        (report_id, group.room_id, group.name, now),
                    )
            row = connection.execute(
                """
                SELECT * FROM intention_notification_scheduled_group_reports
                WHERE report_id=?
                """,
                (report_id,),
            ).fetchone()
        if row is None:
            raise IntentionNotificationError("定时战报未能可靠落盘")
        return dict(row)

    def latest_scheduled_snapshot(
        self, report_date: str, before_slot: str, outbound_group_key: str
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT snapshot_json
                FROM intention_notification_scheduled_group_reports
                WHERE report_date=? AND slot<? AND outbound_group_key=?
                ORDER BY slot DESC
                LIMIT 1
                """,
                (report_date, before_slot, outbound_group_key),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(str(row["snapshot_json"]))
        except (json.JSONDecodeError, TypeError):
            return None
        return payload if isinstance(payload, dict) else None

    def next_scheduled_report(self) -> dict[str, Any] | None:
        group_placeholders = ",".join("?" for _ in SCHEDULED_REPORT_GROUPS)
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT *
                FROM intention_notification_scheduled_group_reports
                WHERE status IN ('received', 'waiting_retry')
                  AND next_attempt_at_ms<=?
                  AND outbound_group_key IN ({group_placeholders})
                ORDER BY report_date, slot, outbound_group_name, outbound_group_key
                LIMIT 1
                """,
                (_now_ms(), *SCHEDULED_REPORT_GROUPS),
            ).fetchone()
        return dict(row) if row is not None else None

    def mark_scheduled_report_sending(self, report_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE intention_notification_scheduled_group_reports
                SET status='sending', attempts=attempts+1,
                    last_error=NULL, updated_at_ms=?
                WHERE report_id=? AND status IN ('received', 'waiting_retry')
                """,
                (_now_ms(), report_id),
            )
        return cursor.rowcount == 1

    def scheduled_targets(self, report_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM intention_notification_scheduled_group_targets
                WHERE report_id=?
                ORDER BY room_name, room_id
                """,
                (report_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_scheduled_target_sending(self, report_id: str, room_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE intention_notification_scheduled_group_targets
                SET status='sending', attempts=attempts+1,
                    last_error=NULL, updated_at_ms=?
                WHERE report_id=? AND room_id=? AND status!='sent'
                """,
                (_now_ms(), report_id, room_id),
            )

    def mark_scheduled_target_sent(
        self, report_id: str, room_id: str, message_id: str | None
    ) -> None:
        now = _now_ms()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE intention_notification_scheduled_group_targets
                SET status='sent', message_id=?, last_error=NULL,
                    sent_at_ms=?, updated_at_ms=?
                WHERE report_id=? AND room_id=?
                """,
                (message_id, now, now, report_id, room_id),
            )

    def mark_scheduled_target_failed(
        self, report_id: str, room_id: str, error: str
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE intention_notification_scheduled_group_targets
                SET status='failed', last_error=?, updated_at_ms=?
                WHERE report_id=? AND room_id=?
                """,
                (error[:2000], _now_ms(), report_id, room_id),
            )

    def finish_scheduled_report(self, report_id: str) -> dict[str, Any]:
        now = _now_ms()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            targets = connection.execute(
                """
                SELECT room_name, status, last_error
                FROM intention_notification_scheduled_group_targets
                WHERE report_id=?
                ORDER BY room_name, room_id
                """,
                (report_id,),
            ).fetchall()
            failed = [target for target in targets if target["status"] != "sent"]
            status = "sent" if targets and not failed else "waiting_retry"
            error = "; ".join(
                f"{target['room_name']}: {target['last_error'] or '发送失败'}"
                for target in failed
            )
            connection.execute(
                """
                UPDATE intention_notification_scheduled_group_reports
                SET status=?, last_error=?, next_attempt_at_ms=?,
                    sent_at_ms=?, updated_at_ms=?
                WHERE report_id=?
                """,
                (
                    status,
                    error[:2000] or None,
                    now + SCHEDULE_RETRY_SECONDS * 1000,
                    now if status == "sent" else None,
                    now,
                    report_id,
                ),
            )
        return {"status": status, "error": error[:2000] or None}

    def scheduled_reports_for_day(self, report_date: str) -> list[dict[str, Any]]:
        group_placeholders = ",".join("?" for _ in SCHEDULED_REPORT_GROUPS)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT report_id, slot, kind, outbound_group_key,
                       outbound_group_name, status, attempts, last_error,
                       sent_at_ms
                FROM intention_notification_scheduled_group_reports
                WHERE report_date=?
                  AND outbound_group_key IN ({group_placeholders})
                ORDER BY slot, outbound_group_name, outbound_group_key
                """,
                (report_date, *SCHEDULED_REPORT_GROUPS),
            ).fetchall()
        return [dict(row) for row in rows]

    def schedule_runs_for_day(self, report_date: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT report_date, slot, status, unavailable_groups_json,
                       updated_at_ms
                FROM intention_notification_schedule_runs
                WHERE report_date=?
                ORDER BY slot
                """,
                (report_date,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                unavailable = json.loads(str(item.pop("unavailable_groups_json")))
            except (json.JSONDecodeError, TypeError):
                unavailable = []
            item["unavailable_groups"] = (
                [str(name) for name in unavailable]
                if isinstance(unavailable, list)
                else []
            )
            result.append(item)
        return result

    def persist_event(
        self,
        delivery: dict[str, Any],
        target_groups: tuple[TargetGroup, ...],
        *,
        skip: bool = False,
    ) -> dict[str, Any]:
        event_id = str(delivery.get("event_id") or "").strip()
        event_type = str(delivery.get("event_type") or "").strip()
        if not event_id or len(event_id) > 256:
            raise ValueError("robot event_id is missing or too long")
        if event_type != EVENT_TYPE:
            raise ValueError(f"unsupported robot event type: {event_type or 'missing'}")
        context = _event_context(delivery)
        team_snapshot = _extract_team_snapshot(delivery, context)
        now = _now_ms()
        try:
            source_attempt_count = max(0, int(delivery.get("attempt_count") or 0))
        except (TypeError, ValueError):
            source_attempt_count = 0
        serialized = json.dumps(
            delivery, ensure_ascii=False, separators=(",", ":"), default=str
        )

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM intention_notification_events WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO intention_notification_events(
                        event_id, event_type, source_attempt_count, occurred_at,
                        stat_date, group_key, group_name, agent_key, agent_name,
                        agent_today, group_today, overall_today, announcement_text,
                        delivery_json, status, created_at_ms, updated_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, '', ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        event_type,
                        source_attempt_count,
                        context["occurred_at"],
                        context["stat_date"],
                        context["group_key"],
                        context["group_name"],
                        context["agent_key"],
                        context["agent_name"],
                        serialized,
                        "skipped" if skip else "received",
                        now,
                        now,
                    ),
                )
                counts = connection.execute(
                    """
                    SELECT
                        SUM(CASE WHEN agent_key=? THEN 1 ELSE 0 END) AS agent_today,
                        SUM(CASE WHEN group_key=? THEN 1 ELSE 0 END) AS group_today,
                        COUNT(DISTINCT CASE WHEN group_key=? THEN agent_key END) AS group_contributors,
                        COUNT(*) AS overall_today
                    FROM intention_notification_events
                    WHERE stat_date=?
                    """,
                    (
                        context["agent_key"],
                        context["group_key"],
                        context["group_key"],
                        context["stat_date"],
                    ),
                ).fetchone()
                agent_today = int(counts["agent_today"] or 0)
                group_today = int(counts["group_today"] or 0)
                contributor_count = int(counts["group_contributors"] or 0)
                overall_today = int(counts["overall_today"] or 0)
                previous_stage_snapshot: TeamSnapshot | None = None
                if team_snapshot is not None:
                    member = _snapshot_member(
                        team_snapshot, context["agent_key"], context["agent_name"]
                    )
                    if member is not None:
                        agent_today = member.intention_today
                    if team_snapshot.group_today is not None:
                        group_today = team_snapshot.group_today
                    if team_snapshot.overall_today is not None:
                        overall_today = team_snapshot.overall_today
                    if group_today > 0 and group_today % STAGE_REPORT_INTERVAL == 0:
                        previous_rows = connection.execute(
                            """
                            SELECT delivery_json
                            FROM intention_notification_events
                            WHERE stat_date=? AND group_key=? AND event_id!=?
                            ORDER BY created_at_ms DESC
                            LIMIT 200
                            """,
                            (
                                context["stat_date"],
                                context["group_key"],
                                event_id,
                            ),
                        ).fetchall()
                        for previous_row in previous_rows:
                            try:
                                previous_delivery = json.loads(
                                    str(previous_row["delivery_json"])
                                )
                            except (json.JSONDecodeError, TypeError):
                                continue
                            if not isinstance(previous_delivery, dict):
                                continue
                            candidate = _extract_team_snapshot(previous_delivery)
                            candidate_group_today = (
                                candidate.group_today if candidate is not None else None
                            )
                            if (
                                candidate is not None
                                and candidate_group_today is not None
                                and candidate_group_today < group_today
                                and candidate_group_today % STAGE_REPORT_INTERVAL == 0
                            ):
                                previous_stage_snapshot = candidate
                                break
                announcement = render_announcement(
                    group_name=context["group_name"],
                    agent_name=context["agent_name"],
                    agent_today=agent_today,
                    group_today=group_today,
                    overall_today=overall_today,
                    recorded_at=context["recorded_at"],
                    agent_key=context["agent_key"],
                    contributor_count=contributor_count,
                    team_snapshot=team_snapshot,
                    previous_stage_snapshot=previous_stage_snapshot,
                )
                connection.execute(
                    """
                    UPDATE intention_notification_events
                    SET agent_today=?, group_today=?, overall_today=?, announcement_text=?
                    WHERE event_id=?
                    """,
                    (
                        agent_today,
                        group_today,
                        overall_today,
                        announcement,
                        event_id,
                    ),
                )
                if not skip:
                    for group in target_groups:
                        connection.execute(
                            """
                            INSERT INTO intention_notification_targets(
                                event_id, room_id, room_name, status, updated_at_ms
                            ) VALUES (?, ?, ?, 'pending', ?)
                            """,
                            (event_id, group.room_id, group.name, now),
                        )
            else:
                current_status = str(existing["status"])
                if current_status in {"sent", "skipped"}:
                    next_status = current_status
                elif skip:
                    next_status = "skipped"
                elif current_status == "waiting_retry":
                    next_status = "received"
                else:
                    next_status = current_status
                connection.execute(
                    """
                    UPDATE intention_notification_events
                    SET source_attempt_count=MAX(source_attempt_count, ?), delivery_json=?,
                        status=?, updated_at_ms=?
                    WHERE event_id=?
                    """,
                    (source_attempt_count, serialized, next_status, now, event_id),
                )
                if skip and current_status not in {"sent", "skipped"}:
                    connection.execute(
                        """
                        UPDATE intention_notification_targets
                        SET status='skipped', last_error=NULL, updated_at_ms=?
                        WHERE event_id=? AND status!='sent'
                        """,
                        (now, event_id),
                    )
                elif current_status == "waiting_retry":
                    connection.execute(
                        """
                        UPDATE intention_notification_targets
                        SET status='pending', updated_at_ms=?
                        WHERE event_id=? AND status='failed'
                        """,
                        (now, event_id),
                    )
            row = connection.execute(
                "SELECT * FROM intention_notification_events WHERE event_id=?",
                (event_id,),
            ).fetchone()
        if row is None:
            raise IntentionNotificationError("persisted event could not be reloaded")
        return dict(row)

    def next_received_event(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("""
                SELECT * FROM intention_notification_events
                WHERE status='received'
                ORDER BY created_at_ms, event_id
                LIMIT 1
                """).fetchone()
        return dict(row) if row is not None else None

    def mark_event_sending(self, event_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE intention_notification_events
                SET status='sending', last_error=NULL, updated_at_ms=?
                WHERE event_id=? AND status='received'
                """,
                (_now_ms(), event_id),
            )
        return cursor.rowcount == 1

    def targets(self, event_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM intention_notification_targets
                WHERE event_id=?
                ORDER BY room_name, room_id
                """,
                (event_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_target_sending(self, event_id: str, room_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE intention_notification_targets
                SET status='sending', attempts=attempts+1, last_error=NULL,
                    updated_at_ms=?
                WHERE event_id=? AND room_id=? AND status!='sent'
                """,
                (_now_ms(), event_id, room_id),
            )

    def mark_target_sent(
        self, event_id: str, room_id: str, message_id: str | None
    ) -> None:
        now = _now_ms()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE intention_notification_targets
                SET status='sent', message_id=?, last_error=NULL, sent_at_ms=?,
                    updated_at_ms=?
                WHERE event_id=? AND room_id=?
                """,
                (message_id, now, now, event_id, room_id),
            )

    def mark_target_failed(self, event_id: str, room_id: str, error: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE intention_notification_targets
                SET status='failed', last_error=?, updated_at_ms=?
                WHERE event_id=? AND room_id=?
                """,
                (error[:2000], _now_ms(), event_id, room_id),
            )

    def finish_event(self, event_id: str) -> dict[str, Any]:
        now = _now_ms()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            targets = connection.execute(
                """
                SELECT room_id, room_name, status, message_id, last_error
                FROM intention_notification_targets
                WHERE event_id=?
                ORDER BY room_name, room_id
                """,
                (event_id,),
            ).fetchall()
            failed = [row for row in targets if row["status"] != "sent"]
            status = "sent" if targets and not failed else "waiting_retry"
            error = "; ".join(
                f"{row['room_name']}: {row['last_error'] or '发送失败'}"
                for row in failed
            )
            connection.execute(
                """
                UPDATE intention_notification_events
                SET status=?, last_error=?, sent_at_ms=?, updated_at_ms=?
                WHERE event_id=?
                """,
                (
                    status,
                    error[:2000] or None,
                    now if status == "sent" else None,
                    now,
                    event_id,
                ),
            )
        return {
            "status": status,
            "error": error[:2000] or None,
            "sent_groups": [
                row["room_name"] for row in targets if row["status"] == "sent"
            ],
            "failed_groups": [row["room_name"] for row in failed],
            "message_ids": [row["message_id"] for row in targets if row["message_id"]],
        }

    def mark_remote_ack(
        self, event_id: str, status: str | None, error: str | None = None
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE intention_notification_events
                SET remote_ack_status=COALESCE(?, remote_ack_status),
                    remote_ack_error=?, updated_at_ms=?
                WHERE event_id=?
                """,
                (status, error[:2000] if error else None, _now_ms(), event_id),
            )

    def event_ack_details(self, event_id: str) -> dict[str, Any]:
        targets = self.targets(event_id)
        return {
            "groups": [row["room_name"] for row in targets if row["status"] == "sent"],
            "room_ids": [row["room_id"] for row in targets if row["status"] == "sent"],
        }

    def status_snapshot(self, recent_limit: int = 20) -> dict[str, Any]:
        today = datetime.now(CHINA_TZ).date().isoformat()
        with self._connect() as connection:
            summary = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN status='sent' THEN 1 ELSE 0 END) AS sent,
                       SUM(CASE WHEN status='skipped' THEN 1 ELSE 0 END) AS skipped,
                       SUM(CASE WHEN status IN ('received','sending') THEN 1 ELSE 0 END) AS pending,
                       SUM(CASE WHEN status='waiting_retry' THEN 1 ELSE 0 END) AS retrying
                FROM intention_notification_events
                WHERE stat_date=?
                """,
                (today,),
            ).fetchone()
            rows = connection.execute(
                """
                SELECT event_id, occurred_at, group_name, agent_name, agent_today,
                       group_today, overall_today, announcement_text, status,
                       last_error, remote_ack_status, remote_ack_error, sent_at_ms
                FROM intention_notification_events
                ORDER BY created_at_ms DESC
                LIMIT ?
                """,
                (max(1, min(recent_limit, 100)),),
            ).fetchall()
            latest_delivery = connection.execute(
                """
                SELECT delivery_json
                FROM intention_notification_events
                WHERE stat_date=?
                ORDER BY created_at_ms DESC
                LIMIT 1
                """,
                (today,),
            ).fetchone()
        snapshot_state = "no_events"
        if latest_delivery is not None:
            snapshot_state = "missing"
            try:
                delivery = json.loads(str(latest_delivery["delivery_json"]))
            except (json.JSONDecodeError, TypeError):
                delivery = None
            if isinstance(delivery, dict) and _extract_team_snapshot(delivery):
                snapshot_state = "ready"
        scheduled_reports = self.scheduled_reports_for_day(today)
        schedule_runs = self.schedule_runs_for_day(today)
        return {
            "today": {
                "date": today,
                "total": int(summary["total"] or 0),
                "sent": int(summary["sent"] or 0),
                "skipped": int(summary["skipped"] or 0),
                "pending": int(summary["pending"] or 0),
                "retrying": int(summary["retrying"] or 0),
                "team_snapshot_ready": snapshot_state == "ready",
                "team_snapshot_state": snapshot_state,
                "on_duty_dialed_number_threshold": ON_DUTY_DIALED_NUMBER_THRESHOLD,
            },
            "recent": [dict(row) for row in rows],
            "schedule": {
                "timezone": "Asia/Shanghai",
                "slots": [
                    {
                        "key": slot.key,
                        "label": slot.label,
                        "kind": slot.kind,
                    }
                    for slot in SCHEDULED_REPORT_SLOTS
                ],
                "reports": scheduled_reports,
                "runs": schedule_runs,
            },
        }


class BridgeClient(Protocol):
    async def groups(self) -> list[TargetGroup]: ...

    async def send_text(self, room_id: str, content: str) -> str | None: ...

    async def close(self) -> None: ...


class WeChatNotificationBridge:
    def __init__(self, base_url: str) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("WeChat bridge URL must be loopback HTTP")
        self.base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(40.0, connect=3.0),
                trust_env=False,
            )
        return self._client

    async def _post(self, path: str, payload: dict[str, Any]) -> Any:
        try:
            response = await self._http_client().post(path, json=payload)
            response.raise_for_status()
            try:
                response_text = response.content.decode("utf-8")
            except UnicodeDecodeError:
                response_text = response.content.decode("utf-8", errors="replace")
            body = json.loads(response_text)
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise IntentionNotificationError(
                f"微信桥接请求失败：{path}：{exc}"
            ) from exc
        if not isinstance(body, dict) or int(body.get("code") or 0) != 1:
            message = body.get("msg") if isinstance(body, dict) else "响应格式错误"
            if path == '/api/sendImageQuoteMsg' and isinstance(body, dict) and body.get('code') == -1:
                rejected = body.get('data')
                if isinstance(rejected, str):
                    try:
                        rejected = json.loads(rejected)
                    except ValueError:
                        rejected = None
                if str(message).startswith('validation:') or (isinstance(rejected, dict) and rejected.get('stage') == 'send-rejected'):
                    from .delivery_image_quotes import ImageQuoteRejected
                    raise ImageQuoteRejected('引用图片未提交或被微信拒绝')
            raise IntentionNotificationError(f"微信桥接拒绝请求：{message}")
        data = body.get("data")
        if isinstance(data, str):
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                return data
        return data

    async def groups(self) -> list[TargetGroup]:
        data = await self._post("/api/getContactList", {"limit": 10_000})
        if not isinstance(data, list):
            raise IntentionNotificationError("微信联系人响应不是数组")
        groups: dict[str, TargetGroup] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            room_id = str(item.get("wxid") or "").strip()
            if not room_id.endswith("@chatroom"):
                continue
            name = ""
            for key in ("remark", "display_name", "nickname", "name"):
                name = str(item.get(key) or "").strip()
                if name:
                    break
            groups[room_id] = TargetGroup(room_id=room_id, name=name or room_id)
        return sorted(
            groups.values(), key=lambda group: (group.name.casefold(), group.room_id)
        )

    async def send_text(self, room_id: str, content: str) -> str | None:
        if not room_id.endswith("@chatroom"):
            raise IntentionNotificationError("通知目标不是微信群")
        data = await self._post("/api/sendTextMsg", {"wxid": room_id, "msg": content})
        if isinstance(data, dict):
            for key in ("message_id", "msg_id", "msgSvrID", "local_id"):
                value = str(data.get(key) or "").strip()
                if value:
                    return value
        return None

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None


class RemoteClient(Protocol):
    async def claim(self, config: NotificationConfig) -> list[dict[str, Any]]: ...

    async def team_snapshots(self, config: NotificationConfig) -> dict[str, Any]: ...

    async def ack(
        self,
        config: NotificationConfig,
        event_id: str,
        status: str,
        *,
        retryable: bool = True,
        error: str | None = None,
        message_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    async def close(self) -> None: ...


class RobotDispatchRemote:
    def __init__(self, rest_base_url: str = DEFAULT_REST_BASE_URL) -> None:
        parsed = urlsplit(rest_base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("robot dispatch REST URL must use HTTPS")
        self.rest_base_url = rest_base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.rest_base_url,
                timeout=httpx.Timeout(35.0, connect=8.0),
                follow_redirects=False,
            )
        return self._client

    @staticmethod
    def _headers(config: NotificationConfig) -> dict[str, str]:
        return {"Authorization": f"Bearer {config.api_token}"}

    async def claim(self, config: NotificationConfig) -> list[dict[str, Any]]:
        try:
            response = await self._http_client().post(
                "/events/claim",
                headers=self._headers(config),
                json={
                    "consumer_id": config.consumer_id,
                    "limit": 10,
                    "wait_seconds": 5,
                    "lease_seconds": 90,
                },
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise IntentionNotificationError(f"REST 领取意向单失败：{exc}") from exc
        items = body.get("items") if isinstance(body, dict) else None
        if not isinstance(items, list):
            raise IntentionNotificationError("REST 领取响应缺少 items")
        return [item for item in items if isinstance(item, dict)]

    async def team_snapshots(self, config: NotificationConfig) -> dict[str, Any]:
        try:
            response = await self._http_client().get(
                "/team-snapshots",
                headers=self._headers(config),
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise IntentionNotificationError(
                f"定时战报团队快照读取失败：{exc}"
            ) from exc
        if not isinstance(body, dict):
            raise IntentionNotificationError("定时战报团队快照响应格式不正确")
        return body

    async def ack(
        self,
        config: NotificationConfig,
        event_id: str,
        status: str,
        *,
        retryable: bool = True,
        error: str | None = None,
        message_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "consumer_id": config.consumer_id,
            "status": status,
            "retryable": retryable,
            "occurred_at": datetime.now(CHINA_TZ).isoformat(),
        }
        if error:
            payload["error"] = error[:2000]
        if message_id:
            payload["message_id"] = message_id[:256]
        if details:
            payload["details"] = details
        try:
            response = await self._http_client().post(
                f"/events/{quote(event_id, safe='')}/ack",
                headers=self._headers(config),
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise IntentionNotificationError(f"REST 回执 {status} 失败：{exc}") from exc
        return body if isinstance(body, dict) else {}

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None


AckSender = Callable[[str, str, dict[str, Any]], Awaitable[None]]


class IntentionNotificationService:
    def __init__(
        self,
        store: IntentionNotificationStore,
        bridge: BridgeClient,
        remote: RemoteClient,
        *,
        wss_url: str = DEFAULT_WSS_URL,
        seed_token: str = "",
        clock: Callable[[], datetime] | None = None,
        scheduled_group_send_interval: float = SCHEDULED_GROUP_SEND_INTERVAL_SECONDS,
    ) -> None:
        parsed = urlsplit(wss_url)
        if parsed.scheme != "wss" or not parsed.hostname:
            raise ValueError("robot dispatch WebSocket URL must use WSS")
        self.store = store
        self.bridge = bridge
        self.remote = remote
        self.wss_url = wss_url
        self.seed_token = seed_token.strip()
        self._clock = clock or (lambda: datetime.now(CHINA_TZ))
        self._scheduled_group_send_interval = max(
            0.0, float(scheduled_group_send_interval)
        )
        self._started = False
        self._stop = asyncio.Event()
        self._config_wake = asyncio.Event()
        self._delivery_wake = asyncio.Event()
        self._consumer_task: asyncio.Task[None] | None = None
        self._delivery_task: asyncio.Task[None] | None = None
        self._schedule_task: asyncio.Task[None] | None = None
        self._runtime_state = "stopped"
        self._transport: str | None = None
        self._last_error: str | None = None
        self._last_connected_at: str | None = None
        self._last_event_at: str | None = None
        self._last_schedule_error: str | None = None

    async def start(self) -> None:
        if self._started:
            return
        await asyncio.to_thread(self.store.initialize, self.seed_token)
        self._started = True
        self._stop.clear()
        self._consumer_task = asyncio.create_task(
            self._consume_loop(), name="intention-notification-consumer"
        )
        self._delivery_task = asyncio.create_task(
            self._delivery_loop(), name="intention-notification-delivery"
        )
        self._schedule_task = asyncio.create_task(
            self._schedule_loop(), name="intention-notification-schedule"
        )

    async def stop(self) -> None:
        if not self._started:
            await self.bridge.close()
            await self.remote.close()
            return
        self._stop.set()
        self._config_wake.set()
        self._delivery_wake.set()
        tasks = [
            task
            for task in (
                self._consumer_task,
                self._delivery_task,
                self._schedule_task,
            )
            if task is not None
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._consumer_task = None
        self._delivery_task = None
        self._schedule_task = None
        self._started = False
        self._runtime_state = "stopped"
        self._transport = None
        await self.bridge.close()
        await self.remote.close()

    async def configure(
        self,
        *,
        enabled: bool,
        consumer_id: str,
        target_groups: list[dict[str, str]],
        api_token: str | None,
        startup_policy: str | None = None,
    ) -> dict[str, Any]:
        normalized_consumer = consumer_id.strip()
        if not CONSUMER_ID_PATTERN.fullmatch(normalized_consumer):
            raise ValueError("consumer_id 格式不正确")
        groups: list[TargetGroup] = []
        seen: set[str] = set()
        for item in target_groups:
            room_id = str(item.get("room_id") or "").strip()
            name = str(item.get("name") or room_id).strip()
            if not room_id.endswith("@chatroom"):
                raise ValueError(f"目标群 ID 格式不正确：{room_id or '空值'}")
            if len(room_id) > 256 or not name or len(name) > 128:
                raise ValueError("目标群名称或 ID 过长")
            if room_id in seen:
                continue
            seen.add(room_id)
            groups.append(TargetGroup(room_id=room_id, name=name))
        if len(groups) > 50:
            raise ValueError("目标群不能超过 50 个")
        normalized_token = api_token.strip() if api_token is not None else None
        current = await asyncio.to_thread(self.store.configuration)
        normalized_policy = (
            startup_policy.strip() if startup_policy is not None else None
        )
        if normalized_policy is not None and normalized_policy not in STARTUP_POLICIES:
            raise ValueError("启动策略不正确")
        effective_token = (
            current.api_token if normalized_token is None else normalized_token
        )
        if enabled and not effective_token:
            raise ValueError("开启通知前需要配置鉴权令牌")
        if enabled and not groups:
            raise ValueError("开启通知前至少选择一个微信群")
        is_starting = enabled and (
            not current.enabled
            or (current.startup_policy == "new_only" and not current.activation_cutoff)
        )
        if is_starting and normalized_policy is None:
            raise ValueError("开启通知前必须选择是否补发历史积压单")
        if is_starting:
            effective_policy = normalized_policy or "new_only"
            activation_cutoff = (
                datetime.now(CHINA_TZ).isoformat()
                if effective_policy == "new_only"
                else None
            )
        elif enabled:
            effective_policy = current.startup_policy
            activation_cutoff = current.activation_cutoff
        else:
            effective_policy = normalized_policy or current.startup_policy
            activation_cutoff = None

        updated = await asyncio.to_thread(
            self.store.update_configuration,
            enabled=enabled,
            consumer_id=normalized_consumer,
            target_groups=tuple(groups),
            api_token=normalized_token,
            startup_policy=effective_policy,
            activation_cutoff=activation_cutoff,
        )
        self._config_wake.set()
        self._delivery_wake.set()
        if self._started and self._consumer_task is not None:
            self._consumer_task.cancel()
            await asyncio.gather(self._consumer_task, return_exceptions=True)
            self._consumer_task = asyncio.create_task(
                self._consume_loop(), name="intention-notification-consumer"
            )
        return updated.public_dict()

    async def available_groups(self) -> list[dict[str, str]]:
        groups = await self.bridge.groups()
        return [{"room_id": group.room_id, "name": group.name} for group in groups]

    async def status(self) -> dict[str, Any]:
        config = await asyncio.to_thread(self.store.configuration)
        snapshot = await asyncio.to_thread(self.store.status_snapshot)
        state = self._runtime_state
        if not config.enabled:
            state = "disabled"
        elif not config.api_token:
            state = "needs_token"
        elif not config.target_groups:
            state = "needs_groups"
        elif not config.ready:
            state = "needs_startup_policy"
        return {
            "config": config.public_dict(),
            "runtime": {
                "state": state,
                "transport": self._transport,
                "running": self._started,
                "last_error": self._last_error,
                "last_connected_at": self._last_connected_at,
                "last_event_at": self._last_event_at,
                "last_schedule_error": self._last_schedule_error,
                "rest_base_url": getattr(
                    self.remote, "rest_base_url", DEFAULT_REST_BASE_URL
                ),
                "wss_url": self.wss_url,
            },
            **snapshot,
        }

    async def _wait_for_config(self, timeout: float | None = None) -> None:
        self._config_wake.clear()
        try:
            await asyncio.wait_for(self._config_wake.wait(), timeout=timeout)
        except TimeoutError:
            pass

    async def _consume_loop(self) -> None:
        while not self._stop.is_set():
            config = await asyncio.to_thread(self.store.configuration)
            if not config.ready:
                self._runtime_state = "disabled" if not config.enabled else "not_ready"
                self._transport = None
                await self._wait_for_config()
                continue
            try:
                await self._consume_wss(config)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = str(exc)
                self._runtime_state = "fallback"
                self._transport = "rest"
                logger.warning("intention notification WSS failed: %s", exc)
                try:
                    await self._consume_rest_once(config)
                except asyncio.CancelledError:
                    raise
                except Exception as rest_exc:
                    self._last_error = str(rest_exc)
                    self._runtime_state = "error"
                    logger.warning("intention notification REST failed: %s", rest_exc)
                await self._wait_for_config(timeout=2.0)

    async def _consume_wss(self, config: NotificationConfig) -> None:
        query_string = urlencode({"consumer_id": config.consumer_id})
        separator = "&" if "?" in self.wss_url else "?"
        url = f"{self.wss_url}{separator}{query_string}"
        self._runtime_state = "connecting"
        self._transport = "wss"
        async with websocket_connect(
            url,
            additional_headers={"Authorization": f"Bearer {config.api_token}"},
            open_timeout=10,
            close_timeout=5,
            ping_interval=20,
            ping_timeout=20,
            max_size=2 * 1024 * 1024,
        ) as socket:
            self._runtime_state = "connected"
            self._last_error = None
            self._last_connected_at = datetime.now(CHINA_TZ).isoformat()

            async def send_ack(
                event_id: str, status: str, fields: dict[str, Any]
            ) -> None:
                await socket.send(
                    json.dumps(
                        {
                            "type": "ack",
                            "event_id": event_id,
                            "status": status,
                            **fields,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                )

            while not self._stop.is_set():
                try:
                    raw = await asyncio.wait_for(socket.recv(), timeout=25.0)
                except TimeoutError:
                    await socket.send('{"type":"ping"}')
                    continue
                message = json.loads(raw)
                if not isinstance(message, dict):
                    continue
                message_type = str(message.get("type") or "").casefold()
                if message_type == "event" and isinstance(
                    message.get("delivery"), dict
                ):
                    await self._accept_delivery(
                        config, dict(message["delivery"]), send_ack
                    )
                elif message_type == "error":
                    self._last_error = str(message.get("message") or "WSS 返回错误")

    async def _consume_rest_once(self, config: NotificationConfig) -> None:
        deliveries = await self.remote.claim(config)

        async def send_ack(event_id: str, status: str, fields: dict[str, Any]) -> None:
            await self.remote.ack(
                config,
                event_id,
                status,
                retryable=bool(fields.get("retryable", True)),
                error=fields.get("error"),
                message_id=fields.get("message_id"),
                details=fields.get("details"),
            )

        for delivery in deliveries:
            await self._accept_delivery(config, delivery, send_ack)

    async def _accept_delivery(
        self,
        config: NotificationConfig,
        delivery: dict[str, Any],
        send_ack: AckSender,
    ) -> None:
        event_id = str(delivery.get("event_id") or "").strip()
        skip = config.startup_policy == "new_only" and event_precedes_cutoff(
            delivery, config.activation_cutoff
        )
        try:
            event = await asyncio.to_thread(
                self.store.persist_event,
                delivery,
                config.target_groups,
                skip=skip,
            )
        except ValueError as exc:
            if event_id:
                await send_ack(
                    event_id,
                    "FAILED",
                    {"retryable": False, "error": str(exc)[:2000]},
                )
            return
        self._last_event_at = datetime.now(CHINA_TZ).isoformat()
        if event["status"] == "skipped":
            details = {
                "groups": [],
                "skipped": True,
                "reason": "before_activation_cutoff",
                "activation_cutoff": config.activation_cutoff,
            }
            await send_ack(event_id, "SENT", {"details": details})
            await asyncio.to_thread(self.store.mark_remote_ack, event_id, "SENT")
            return
        if event["status"] == "sent":
            details = await asyncio.to_thread(self.store.event_ack_details, event_id)
            await send_ack(event_id, "SENT", {"details": details})
            await asyncio.to_thread(self.store.mark_remote_ack, event_id, "SENT")
            return
        await send_ack(
            event_id,
            "RECEIVED",
            {"details": {"local_queue_id": event_id}},
        )
        await asyncio.to_thread(self.store.mark_remote_ack, event_id, "RECEIVED")
        self._delivery_wake.set()

    def _schedule_now(self) -> datetime:
        current = self._clock()
        if current.tzinfo is None:
            return current.replace(tzinfo=CHINA_TZ)
        return current.astimezone(CHINA_TZ)

    @staticmethod
    def _scheduled_report_id(
        report_date: date,
        slot: ScheduledReportSlot,
        outbound_group_key: str,
    ) -> str:
        return (
            f"intention_report:{report_date.isoformat()}:{slot.key}:"
            f"{outbound_group_key}"
        )

    async def create_due_scheduled_reports_once(
        self, now: datetime | None = None
    ) -> bool:
        config = await asyncio.to_thread(self.store.configuration)
        if not config.ready:
            return False
        current = now or self._schedule_now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=CHINA_TZ)
        else:
            current = current.astimezone(CHINA_TZ)

        created = False
        report_date = current.date().isoformat()
        for slot in SCHEDULED_REPORT_SLOTS:
            due_at = current.replace(
                hour=slot.hour,
                minute=slot.minute,
                second=0,
                microsecond=0,
            )
            if current < due_at or current >= due_at + SCHEDULE_GRACE:
                continue
            run = await asyncio.to_thread(
                self.store.schedule_run,
                report_date,
                slot.label,
            )
            if run is not None and run["status"] == "complete":
                continue

            batch = await self.remote.team_snapshots(config)
            snapshot_at, snapshots, unavailable = parse_scheduled_snapshot_batch(
                batch,
                expected_date=current.date(),
                group_keys=frozenset(SCHEDULED_REPORT_GROUPS),
            )
            if abs((snapshot_at - current).total_seconds()) > 5 * 60:
                raise IntentionNotificationError("定时战报团队快照时间偏差超过 5 分钟")
            available_keys = {snapshot.group_key for snapshot in snapshots}
            missing_groups = [
                name
                for key, name in SCHEDULED_REPORT_GROUPS.items()
                if key not in available_keys
            ]
            unavailable = tuple(dict.fromkeys((*unavailable, *missing_groups)))

            for snapshot in snapshots:
                report_id = self._scheduled_report_id(
                    current.date(),
                    slot,
                    snapshot.group_key,
                )
                existing = await asyncio.to_thread(
                    self.store.scheduled_report, report_id
                )
                if existing is not None:
                    continue

                previous_snapshot: TeamSnapshot | None = None
                previous_batch = await asyncio.to_thread(
                    self.store.latest_scheduled_snapshot,
                    report_date,
                    slot.label,
                    snapshot.group_key,
                )
                if previous_batch is not None:
                    try:
                        _, previous_snapshots, _ = parse_scheduled_snapshot_batch(
                            previous_batch,
                            expected_date=current.date(),
                        )
                    except IntentionNotificationError:
                        previous_snapshots = ()
                    previous_snapshot = next(
                        (
                            item
                            for item in previous_snapshots
                            if item.group_key == snapshot.group_key
                        ),
                        None,
                    )

                announcement = render_scheduled_group_report(
                    slot=slot,
                    snapshot_at=snapshot_at,
                    snapshot=snapshot,
                    previous_snapshot=previous_snapshot,
                )
                await asyncio.to_thread(
                    self.store.persist_scheduled_report,
                    report_id=report_id,
                    report_date=report_date,
                    slot=slot,
                    outbound_group_key=snapshot.group_key,
                    outbound_group_name=snapshot.group_name,
                    snapshot_batch=_scheduled_group_batch(snapshot_at, snapshot),
                    announcement_text=announcement,
                    target_groups=config.target_groups,
                )
                created = True

            # Completeness is scoped to purchase teams, not unrelated groups.
            complete = not unavailable
            await asyncio.to_thread(
                self.store.mark_schedule_run,
                report_date,
                slot.label,
                complete=complete,
                unavailable_groups=unavailable,
            )
            self._last_schedule_error = (
                None
                if complete
                else f"部分小组快照不可用：{'、'.join(unavailable) or '未知小组'}"
            )
            if created:
                self._delivery_wake.set()
        return created

    async def _schedule_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.create_due_scheduled_reports_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_schedule_error = str(exc) or exc.__class__.__name__
                logger.warning("intention scheduled report failed: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=10.0)
            except TimeoutError:
                pass

    async def _deliver_scheduled_report_once(self) -> bool:
        report = await asyncio.to_thread(self.store.next_scheduled_report)
        if report is None:
            return False
        report_id = str(report["report_id"])
        claimed = await asyncio.to_thread(
            self.store.mark_scheduled_report_sending, report_id
        )
        if not claimed:
            return True

        targets = await asyncio.to_thread(self.store.scheduled_targets, report_id)
        for target in targets:
            if target["status"] == "sent":
                continue
            room_id = str(target["room_id"])
            await asyncio.to_thread(
                self.store.mark_scheduled_target_sending,
                report_id,
                room_id,
            )
            try:
                message_id = await self.bridge.send_text(
                    room_id,
                    str(report["announcement_text"]),
                )
            except Exception as exc:
                error = str(exc) or exc.__class__.__name__
                await asyncio.to_thread(
                    self.store.mark_scheduled_target_failed,
                    report_id,
                    room_id,
                    error,
                )
            else:
                await asyncio.to_thread(
                    self.store.mark_scheduled_target_sent,
                    report_id,
                    room_id,
                    message_id,
                )

        result = await asyncio.to_thread(self.store.finish_scheduled_report, report_id)
        self._last_schedule_error = result["error"]
        next_report = await asyncio.to_thread(self.store.next_scheduled_report)
        if (
            next_report is not None
            and next_report["report_date"] == report["report_date"]
            and next_report["slot"] == report["slot"]
            and self._scheduled_group_send_interval > 0
        ):
            await asyncio.sleep(self._scheduled_group_send_interval)
        return True

    async def _delivery_loop(self) -> None:
        while not self._stop.is_set():
            processed = await self.deliver_pending_once()
            if processed:
                continue
            self._delivery_wake.clear()
            try:
                await asyncio.wait_for(self._delivery_wake.wait(), timeout=2.0)
            except TimeoutError:
                pass

    async def deliver_pending_once(self) -> bool:
        config = await asyncio.to_thread(self.store.configuration)
        if not config.ready:
            return False
        if await self._deliver_scheduled_report_once():
            return True
        event = await asyncio.to_thread(self.store.next_received_event)
        if event is None:
            return False
        event_id = str(event["event_id"])
        claimed = await asyncio.to_thread(self.store.mark_event_sending, event_id)
        if not claimed:
            return True

        targets = await asyncio.to_thread(self.store.targets, event_id)
        for target in targets:
            if target["status"] == "sent":
                continue
            room_id = str(target["room_id"])
            await asyncio.to_thread(self.store.mark_target_sending, event_id, room_id)
            try:
                message_id = await self.bridge.send_text(
                    room_id, str(event["announcement_text"])
                )
            except Exception as exc:
                error = str(exc) or exc.__class__.__name__
                await asyncio.to_thread(
                    self.store.mark_target_failed, event_id, room_id, error
                )
            else:
                await asyncio.to_thread(
                    self.store.mark_target_sent, event_id, room_id, message_id
                )

        result = await asyncio.to_thread(self.store.finish_event, event_id)
        details = {
            "groups": result["sent_groups"],
            "failed_groups": result["failed_groups"],
        }
        status = "SENT" if result["status"] == "sent" else "FAILED"
        try:
            await self._ack_with_retry(
                config,
                event_id,
                status,
                retryable=status == "FAILED",
                error=result["error"],
                message_id=(result["message_ids"] or [None])[0],
                details=details,
            )
        except Exception as exc:
            await asyncio.to_thread(
                self.store.mark_remote_ack, event_id, None, str(exc)
            )
            self._last_error = str(exc)
        else:
            await asyncio.to_thread(self.store.mark_remote_ack, event_id, status)
        return True

    async def _ack_with_retry(
        self,
        config: NotificationConfig,
        event_id: str,
        status: str,
        **fields: Any,
    ) -> None:
        last_error: Exception | None = None
        for delay in (0.0, 0.25, 0.75):
            if delay:
                await asyncio.sleep(delay)
            try:
                await self.remote.ack(config, event_id, status, **fields)
                return
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
