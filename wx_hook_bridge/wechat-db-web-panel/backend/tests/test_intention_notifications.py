from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from backend.app.intention_notifications import (
    CHINA_TZ,
    IntentionNotificationError,
    IntentionNotificationService,
    IntentionNotificationStore,
    NotificationConfig,
    ON_DUTY_DIALED_NUMBER_THRESHOLD,
    RobotDispatchRemote,
    SCHEDULED_REPORT_GROUPS,
    SCHEDULED_REPORT_SLOTS,
    ScheduledReportSlot,
    TargetGroup,
    TeamMemberSnapshot,
    TeamSnapshot,
    WeChatNotificationBridge,
    parse_scheduled_snapshot_batch,
    render_announcement,
    render_scheduled_group_report,
)


def delivery(
    event_id: str,
    *,
    group_id: int = 7,
    group_name: str = "第一外呼组",
    agent_id: int = 11,
    agent_name: str = "外呼员甲",
    attempt_count: int = 1,
    occurred_at: str = "2026-09-03T10:30:00+08:00",
    order_date: str | None = None,
    team_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "event_id": event_id,
        "event_type": "intention_order.created",
        "attempt_count": attempt_count,
        "payload": {
            "occurred_at": occurred_at,
            "data": {
                "order": {
                    "id": int(event_id.rsplit(":", 1)[-1]),
                    "order_date": order_date,
                },
                "organization": {
                    "outbound_group": {"id": group_id, "name": group_name}
                },
                "people": {"agent": {"user_id": agent_id, "name": agent_name}},
            },
        },
    }
    if team_snapshot is not None:
        result["payload"]["data"]["team_snapshot"] = team_snapshot
    return result


def snapshot(
    *,
    group_id: int = 7,
    group_name: str = "第一外呼组",
    group_today: int = 7,
    group_yesterday_same_time: int = 5,
    overall_today: int = 23,
    snapshot_at: str = "2026-09-03T10:30:00+08:00",
    members: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "complete": True,
        "group_id": group_id,
        "group_name": group_name,
        "snapshot_at": snapshot_at,
        "group_today": group_today,
        "group_yesterday_same_time": group_yesterday_same_time,
        "overall_today": overall_today,
        "members": members
        if members is not None
        else [
            {
                "user_id": 11,
                "name": "外呼员甲",
                "dialed_number_count": 18,
                "intention_today": 2,
            },
            {
                "user_id": 12,
                "name": "外呼员乙",
                "dialed_number_count": 15,
                "intention_today": 1,
            },
            {
                "user_id": 13,
                "name": "外呼员丙",
                "dialed_number_count": 11,
                "intention_today": 0,
            },
            {
                "user_id": 14,
                "name": "外呼员丁",
                "dialed_number_count": 10,
                "intention_today": 0,
            },
        ],
    }


def scheduled_batch(
    snapshot_at: str,
    *,
    groups: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "complete": True,
        "snapshot_at": snapshot_at,
        "groups": groups
        if groups is not None
        else [
            snapshot(
                group_id=int(group_id),
                group_name=group_name,
                snapshot_at=snapshot_at,
            )
            for group_id, group_name in SCHEDULED_REPORT_GROUPS.items()
        ],
    }


class FakeBridge:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail_rooms: set[str] = set()

    async def groups(self) -> list[TargetGroup]:
        return [
            TargetGroup("room-1@chatroom", "通知一群"),
            TargetGroup("room-2@chatroom", "通知二群"),
        ]

    async def send_text(self, room_id: str, content: str) -> str | None:
        self.sent.append((room_id, content))
        if room_id in self.fail_rooms:
            raise RuntimeError("微信未登录")
        return f"message-{room_id}"

    async def close(self) -> None:
        return None


class FakeRemote:
    rest_base_url = "https://example.test/consumer"

    def __init__(self) -> None:
        self.acks: list[dict[str, Any]] = []
        self.snapshot_calls = 0
        self.snapshot_response: dict[str, Any] = {}

    async def claim(self, config: NotificationConfig) -> list[dict[str, Any]]:
        return []

    async def team_snapshots(self, config: NotificationConfig) -> dict[str, Any]:
        self.snapshot_calls += 1
        return self.snapshot_response

    async def ack(
        self,
        config: NotificationConfig,
        event_id: str,
        status: str,
        **fields: Any,
    ) -> dict[str, Any]:
        self.acks.append(
            {
                "consumer_id": config.consumer_id,
                "event_id": event_id,
                "status": status,
                **fields,
            }
        )
        return {"accepted": True}

    async def close(self) -> None:
        return None


def make_store(tmp_path: Path) -> IntentionNotificationStore:
    store = IntentionNotificationStore(tmp_path / "intention.sqlite")
    store.initialize()
    return store


def test_status_distinguishes_a_day_without_events(tmp_path: Path) -> None:
    status = make_store(tmp_path).status_snapshot()

    assert status["today"]["team_snapshot_ready"] is False
    assert status["today"]["team_snapshot_state"] == "no_events"


def test_standard_announcement_falls_back_without_claiming_zero_members() -> None:
    message = render_announcement(
        group_name="第一外呼组",
        agent_name="外呼员甲",
        agent_today=2,
        group_today=7,
        overall_today=23,
        recorded_at="17:28",
    )

    assert message == (
        "【新单｜第一外呼组】\n"
        "外呼员甲 +1｜个人 2 单\n"
        "17:28 录单\n\n"
        "小组 7 单｜全场 23 单\n"
        "今日已有 1 人出单\n\n"
        "本次变化：1→2"
    )
    assert "待破零" not in message
    assert "0 单" not in message


def test_team_snapshot_uses_strict_on_duty_threshold() -> None:
    team = TeamSnapshot(
        group_key="7",
        group_name="第一外呼组",
        snapshot_at="2026-09-03T17:28:00+08:00",
        group_today=7,
        group_yesterday_same_time=5,
        overall_today=23,
        members=(
            TeamMemberSnapshot("11", "外呼员甲", 18, 2),
            TeamMemberSnapshot("12", "外呼员乙", 15, 1),
            TeamMemberSnapshot("13", "外呼员丙", 11, 0),
            TeamMemberSnapshot("14", "外呼员丁", 10, 0),
        ),
    )

    message = render_announcement(
        group_name="第一外呼组",
        agent_name="外呼员甲",
        agent_key="11",
        agent_today=2,
        group_today=7,
        overall_today=23,
        recorded_at="17:28",
        team_snapshot=team,
    )

    assert message == (
        "【新单｜第一外呼组】\n"
        "外呼员甲 +1｜个人 2 单\n"
        "17:28 录单\n\n"
        "小组 7 单｜昨日同期 5 单\n"
        "已破零 2/3 人｜待破零 1 人\n\n"
        "本次变化：1→2，第 1\n"
        "全场累计 23 单"
    )
    assert ON_DUTY_DIALED_NUMBER_THRESHOLD == 10
    assert "2/4" not in message


def test_fifth_order_uses_full_stage_report() -> None:
    previous = TeamSnapshot(
        group_key="7",
        group_name="第一外呼组",
        snapshot_at="2026-09-03T16:00:00+08:00",
        group_today=10,
        group_yesterday_same_time=8,
        overall_today=30,
        members=(
            TeamMemberSnapshot("11", "外呼员甲", 15, 1),
            TeamMemberSnapshot("12", "外呼员乙", 13, 1),
            TeamMemberSnapshot("13", "外呼员丙", 11, 0),
            TeamMemberSnapshot("14", "外呼员丁", 10, 0),
        ),
    )
    current = TeamSnapshot(
        group_key="7",
        group_name="第一外呼组",
        snapshot_at="2026-09-03T17:28:00+08:00",
        group_today=15,
        group_yesterday_same_time=12,
        overall_today=48,
        members=(
            TeamMemberSnapshot("11", "外呼员甲", 18, 2),
            TeamMemberSnapshot("12", "外呼员乙", 17, 2),
            TeamMemberSnapshot("13", "外呼员丙", 12, 1),
            TeamMemberSnapshot("14", "外呼员丁", 10, 0),
            TeamMemberSnapshot("15", "外呼员戊", 16, 0),
        ),
    )

    message = render_announcement(
        group_name="第一外呼组",
        agent_name="外呼员甲",
        agent_key="11",
        agent_today=2,
        group_today=15,
        overall_today=48,
        recorded_at="17:28",
        team_snapshot=current,
        previous_stage_snapshot=previous,
    )

    assert "【阶段战报｜17:28】" in message
    assert "今日 15 单｜昨日同期 12 单｜+25%" in message
    assert "已破零 3/4 人｜破零率 75%" in message
    assert "2 单：外呼员乙、外呼员甲" in message
    assert "1 单：外呼员丙" in message
    assert "0 单：外呼员戊" in message
    assert "外呼员丙 0→1｜新破零" in message
    assert "待破零：外呼员戊" in message
    assert "外呼员丁" not in message
    assert "在岗口径：拨打号码 > 10" in message


def test_scheduled_report_renders_one_message_per_group() -> None:
    point = datetime(2026, 9, 5, 12, 0, tzinfo=CHINA_TZ)
    batch = scheduled_batch(
        point.isoformat(),
        groups=[
            snapshot(
                group_today=15,
                group_yesterday_same_time=12,
                overall_today=48,
                snapshot_at=point.isoformat(),
            ),
            snapshot(
                group_id=8,
                group_name="第二外呼组",
                group_today=10,
                group_yesterday_same_time=8,
                overall_today=48,
                snapshot_at=point.isoformat(),
                members=[
                    {
                        "user_id": 21,
                        "name": "组员甲",
                        "dialed_number_count": 20,
                        "intention_today": 1,
                    },
                    {
                        "user_id": 22,
                        "name": "组员乙",
                        "dialed_number_count": 11,
                        "intention_today": 0,
                    },
                ],
            ),
            {
                "complete": False,
                "group_id": 9,
                "group_name": "第三外呼组",
                "snapshot_at": point.isoformat(),
            },
        ],
    )
    batch["complete"] = False

    snapshot_at, snapshots, unavailable = parse_scheduled_snapshot_batch(
        batch,
        expected_date=point.date(),
    )
    messages = [
        render_scheduled_group_report(
            slot=SCHEDULED_REPORT_SLOTS[0],
            snapshot_at=snapshot_at,
            snapshot=item,
        )
        for item in snapshots
    ]

    assert len(messages) == 2
    assert unavailable == ("第三外呼组",)
    assert "【意向阶段战报｜第一外呼组】" in messages[0]
    assert "09-05 12:00 场｜数据截至 12:00" in messages[0]
    assert "第二外呼组" not in messages[0]
    assert "待破零：外呼员丙" in messages[0]
    assert "0 单：外呼员丙" not in messages[0]
    assert "外呼员丁" not in messages[0]
    assert "【意向阶段战报｜第二外呼组】" in messages[1]
    assert "第一外呼组" not in messages[1]
    assert all("全场累计" not in message for message in messages)


def test_closing_report_identifies_next_day_focus() -> None:
    point = datetime(2026, 9, 5, 21, 0, tzinfo=CHINA_TZ)
    snapshot_at, snapshots, _ = parse_scheduled_snapshot_batch(
        scheduled_batch(point.isoformat()),
        expected_date=point.date(),
    )

    message = render_scheduled_group_report(
        slot=SCHEDULED_REPORT_SLOTS[-1],
        snapshot_at=snapshot_at,
        snapshot=snapshots[0],
    )

    assert "【今日收官｜21:00】" in message
    assert "在岗人均 1.0 单｜最高 2 单" in message
    assert "明日优先关注：外呼员丙" in message


@pytest.mark.asyncio
@pytest.mark.parametrize("slot", SCHEDULED_REPORT_SLOTS, ids=lambda slot: slot.key)
async def test_scheduled_reports_only_send_four_purchase_groups(
    tmp_path: Path,
    slot: ScheduledReportSlot,
) -> None:
    store = make_store(tmp_path)
    bridge = FakeBridge()
    remote = FakeRemote()
    service = IntentionNotificationService(
        store, bridge, remote, scheduled_group_send_interval=0
    )
    await service.configure(
        enabled=True,
        consumer_id="yunsheng-wx-robot-01",
        target_groups=[
            {"room_id": "room-1@chatroom", "name": "通知一群"},
            {"room_id": "room-2@chatroom", "name": "通知二群"},
        ],
        api_token="test-token",
        startup_policy="include_pending",
    )
    point = datetime(2026, 9, 5, slot.hour, slot.minute, tzinfo=CHINA_TZ)
    batch = scheduled_batch(point.isoformat())
    excluded_groups = [
        snapshot(
            group_id=1,
            group_name="呼叫中心三组",
            overall_today=99,
            snapshot_at=point.isoformat(),
        ),
        {"complete": False, "group_id": 6, "group_name": "邀约组"},
        {"complete": True, "group_id": 7, "group_name": "恩健组"},
        snapshot(
            group_id=8,
            group_name="四会外呼一组",
            snapshot_at=(point - timedelta(days=1)).isoformat(),
        ),
    ]
    batch["groups"] = [*excluded_groups, *reversed(batch["groups"])]
    batch["complete"] = False
    remote.snapshot_response = batch

    assert await service.create_due_scheduled_reports_once(point) is True
    assert await service.create_due_scheduled_reports_once(point) is False
    assert remote.snapshot_calls == 1
    assert service._last_schedule_error is None  # noqa: SLF001
    assert store.schedule_run("2026-09-05", slot.label)["status"] == "complete"
    assert store.schedule_runs_for_day("2026-09-05")[0]["unavailable_groups"] == []
    with store._connect() as connection:  # noqa: SLF001
        rows = connection.execute(
            "SELECT outbound_group_key FROM intention_notification_scheduled_group_reports"
        ).fetchall()
    assert {row["outbound_group_key"] for row in rows} == {"2", "3", "4", "5"}

    for _ in range(4):
        assert await service.deliver_pending_once() is True
    assert await service.deliver_pending_once() is False

    assert len(bridge.sent) == 8
    if slot.kind == 'closing':
        assert all(f"【今日收官｜{slot.label}】" in message for _, message in bridge.sent)
        assert all("全场累计 23 单" in message for _, message in bridge.sent)
    else:
        assert all('【意向阶段战报｜' in message for _, message in bridge.sent)
        assert all('全场累计' not in message for _, message in bridge.sent)
    for group_name in ["呼叫中心一组", "呼叫中心二组", "呼叫中心四组", "润汇外呼小组"]:
        header = f'【{group_name}】' if slot.kind == 'closing' else f'【意向阶段战报｜{group_name}】'
        assert sum(header in message for _, message in bridge.sent) == 2
    assert all(
        group["group_name"] not in message
        for group in excluded_groups
        for _, message in bridge.sent
    )


@pytest.mark.asyncio
async def test_schedule_does_not_fall_back_to_other_groups(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    bridge = FakeBridge()
    remote = FakeRemote()
    service = IntentionNotificationService(store, bridge, remote)
    await service.configure(
        enabled=True,
        consumer_id="yunsheng-wx-robot-01",
        target_groups=[{"room_id": "room-1@chatroom", "name": "通知群"}],
        api_token="test-token",
        startup_policy="include_pending",
    )
    point = datetime(2026, 9, 5, 12, 0, tzinfo=CHINA_TZ)
    remote.snapshot_response = scheduled_batch(
        point.isoformat(),
        groups=[snapshot(snapshot_at=point.isoformat())],
    )

    with pytest.raises(IntentionNotificationError, match="没有可用的小组快照"):
        await service.create_due_scheduled_reports_once(point)

    assert store.scheduled_reports_for_day("2026-09-05") == []
    assert store.schedule_run("2026-09-05", "12:00") is None
    assert await service.deliver_pending_once() is False
    assert bridge.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("slot", SCHEDULED_REPORT_SLOTS, ids=lambda slot: slot.key)
@pytest.mark.parametrize("old_status", ["received", "waiting_retry"])
async def test_schedule_does_not_deliver_old_out_of_scope_reports(
    tmp_path: Path,
    slot: ScheduledReportSlot,
    old_status: str,
) -> None:
    store = make_store(tmp_path)
    bridge = FakeBridge()
    service = IntentionNotificationService(
        store, bridge, FakeRemote(), scheduled_group_send_interval=0
    )
    await service.configure(
        enabled=True,
        consumer_id="yunsheng-wx-robot-01",
        target_groups=[{"room_id": "room-1@chatroom", "name": "通知群"}],
        api_token="test-token",
        startup_policy="include_pending",
    )
    for group_id, group_name in [("1", "呼叫中心三组"), ("5", "润汇外呼小组")]:
        store.persist_scheduled_report(
            report_id=f"old:{group_id}",
            report_date="2026-09-05",
            slot=slot,
            outbound_group_key=group_id,
            outbound_group_name=group_name,
            snapshot_batch={},
            announcement_text=group_name,
            target_groups=store.configuration().target_groups,
        )
    with store._connect() as connection:  # noqa: SLF001
        connection.execute(
            """
            UPDATE intention_notification_scheduled_group_reports
            SET status=?, next_attempt_at_ms=0
            WHERE report_id='old:1'
            """,
            (old_status,),
        )

    # Restarting the store must not make excluded queued reports eligible again.
    restarted_store = IntentionNotificationStore(store.path)
    restarted_store.initialize()
    service.store = restarted_store
    assert await service.deliver_pending_once() is True
    assert await service.deliver_pending_once() is False
    assert bridge.sent == [("room-1@chatroom", "润汇外呼小组")]
    assert restarted_store.scheduled_report("old:1")["status"] == old_status
    assert [
        report["outbound_group_key"]
        for report in restarted_store.scheduled_reports_for_day("2026-09-05")
    ] == ["5"]


@pytest.mark.asyncio
async def test_scheduled_slots_are_durable_idempotent_and_compare_stages(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    bridge = FakeBridge()
    remote = FakeRemote()
    service = IntentionNotificationService(
        store,
        bridge,
        remote,
        scheduled_group_send_interval=0,
    )
    await service.configure(
        enabled=True,
        consumer_id="yunsheng-wx-robot-01",
        target_groups=[{"room_id": "room-1@chatroom", "name": "通知群"}],
        api_token="test-token",
        startup_policy="include_pending",
    )
    noon = datetime(2026, 9, 5, 12, 0, tzinfo=CHINA_TZ)
    second_group_noon = snapshot(
        group_id=3,
        group_name="呼叫中心二组",
        group_today=4,
        group_yesterday_same_time=3,
        overall_today=23,
        snapshot_at=noon.isoformat(),
        members=[
            {
                "user_id": 21,
                "name": "组员甲",
                "dialed_number_count": 14,
                "intention_today": 1,
            }
        ],
    )
    remote.snapshot_response = scheduled_batch(
        noon.isoformat(),
        groups=[
            scheduled_batch(noon.isoformat())["groups"][0],
            second_group_noon,
            *scheduled_batch(noon.isoformat())["groups"][2:],
        ],
    )

    assert await service.create_due_scheduled_reports_once(noon) is True
    assert await service.create_due_scheduled_reports_once(noon) is False
    assert remote.snapshot_calls == 1

    afternoon = datetime(2026, 9, 5, 16, 0, tzinfo=CHINA_TZ)
    changed_members = [
        {
            "user_id": 11,
            "name": "外呼员甲",
            "dialed_number_count": 28,
            "intention_today": 3,
        },
        {
            "user_id": 12,
            "name": "外呼员乙",
            "dialed_number_count": 22,
            "intention_today": 1,
        },
        {
            "user_id": 13,
            "name": "外呼员丙",
            "dialed_number_count": 19,
            "intention_today": 1,
        },
    ]
    remote.snapshot_response = scheduled_batch(
        afternoon.isoformat(),
        groups=[
            snapshot(
                group_id=2,
                group_name="呼叫中心一组",
                group_today=9,
                group_yesterday_same_time=7,
                overall_today=31,
                snapshot_at=afternoon.isoformat(),
                members=changed_members,
            ),
            {
                **second_group_noon,
                "snapshot_at": afternoon.isoformat(),
                "group_today": 5,
                "group_yesterday_same_time": 4,
                "overall_today": 31,
                "members": [
                    {
                        **second_group_noon["members"][0],
                        "dialed_number_count": 20,
                        "intention_today": 2,
                    }
                ],
            },
            *[
                {**group, "overall_today": 31}
                for group in scheduled_batch(afternoon.isoformat())["groups"][2:]
            ],
        ],
    )
    assert await service.create_due_scheduled_reports_once(afternoon) is True

    report = store.scheduled_report("intention_report:2026-09-05:stage_1600:2")
    assert report is not None
    assert "较上次 12:00 快照增加 2 单" in report["announcement_text"]
    assert "1 单：外呼员丙、外呼员乙" in report["announcement_text"]
    assert len(store.scheduled_reports_for_day("2026-09-05")) == 8

    for _ in range(8):
        assert await service.deliver_pending_once() is True
    assert await service.deliver_pending_once() is False
    assert len(bridge.sent) == 8
    assert sum("【意向阶段战报｜呼叫中心一组】" in message for _, message in bridge.sent) == 2
    assert sum("【意向阶段战报｜呼叫中心二组】" in message for _, message in bridge.sent) == 2
    assert all(
        not ("呼叫中心一组" in message and "呼叫中心二组" in message)
        for _, message in bridge.sent
    )
    assert {row["status"] for row in store.scheduled_reports_for_day("2026-09-05")} == {
        "sent"
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_group_present", [True, False])
async def test_partial_schedule_retries_missing_group_without_duplicates(
    tmp_path: Path,
    missing_group_present: bool,
) -> None:
    store = make_store(tmp_path)
    bridge = FakeBridge()
    remote = FakeRemote()
    service = IntentionNotificationService(
        store,
        bridge,
        remote,
        scheduled_group_send_interval=0,
    )
    await service.configure(
        enabled=True,
        consumer_id="yunsheng-wx-robot-01",
        target_groups=[{"room_id": "room-1@chatroom", "name": "通知群"}],
        api_token="test-token",
        startup_policy="include_pending",
    )
    point = datetime(2026, 9, 5, 12, 0, tzinfo=CHINA_TZ)
    available_groups = scheduled_batch(point.isoformat())["groups"]
    first_group = available_groups[0]
    missing_group = {
        "complete": False,
        "group_id": 3,
        "group_name": "呼叫中心二组",
        "snapshot_at": point.isoformat(),
    }
    remote.snapshot_response = scheduled_batch(
        point.isoformat(),
        groups=[
            first_group,
            *available_groups[2:],
            *([missing_group] if missing_group_present else []),
        ],
    )
    remote.snapshot_response["complete"] = False

    assert await service.create_due_scheduled_reports_once(point) is True
    assert await service.create_due_scheduled_reports_once(point) is False
    assert len(store.scheduled_reports_for_day("2026-09-05")) == 3
    assert store.schedule_run("2026-09-05", "12:00")["status"] == "partial"
    assert store.schedule_runs_for_day("2026-09-05")[0]["unavailable_groups"] == [
        "呼叫中心二组"
    ]

    second_group = snapshot(
        group_id=3,
        group_name="呼叫中心二组",
        group_today=4,
        group_yesterday_same_time=3,
        overall_today=23,
        snapshot_at=point.isoformat(),
        members=[
            {
                "user_id": 21,
                "name": "组员甲",
                "dialed_number_count": 15,
                "intention_today": 1,
            }
        ],
    )
    remote.snapshot_response = scheduled_batch(
        point.isoformat(),
        groups=[first_group, second_group, *available_groups[2:]],
    )
    assert await service.create_due_scheduled_reports_once(point) is True

    reports = store.scheduled_reports_for_day("2026-09-05")
    assert len(reports) == 4
    assert {report["outbound_group_key"] for report in reports} == {"2", "3", "4", "5"}
    assert store.schedule_run("2026-09-05", "12:00")["status"] == "complete"
    assert remote.snapshot_calls == 3

    for _ in range(4):
        assert await service.deliver_pending_once() is True
    assert len(bridge.sent) == 4
    assert sum("【意向阶段战报｜呼叫中心一组】" in message for _, message in bridge.sent) == 1
    assert sum("【意向阶段战报｜呼叫中心二组】" in message for _, message in bridge.sent) == 1


@pytest.mark.asyncio
async def test_group_reports_are_staggered_between_messages(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = make_store(tmp_path)
    bridge = FakeBridge()
    remote = FakeRemote()
    service = IntentionNotificationService(
        store,
        bridge,
        remote,
        scheduled_group_send_interval=2.5,
    )
    await service.configure(
        enabled=True,
        consumer_id="yunsheng-wx-robot-01",
        target_groups=[{"room_id": "room-1@chatroom", "name": "通知群"}],
        api_token="test-token",
        startup_policy="include_pending",
    )
    point = datetime(2026, 9, 5, 12, 0, tzinfo=CHINA_TZ)
    remote.snapshot_response = scheduled_batch(point.isoformat())
    assert await service.create_due_scheduled_reports_once(point) is True
    delays: list[float] = []

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)
    assert await service.deliver_pending_once() is True

    assert delays == [2.5]
    assert len(bridge.sent) == 1


@pytest.mark.asyncio
async def test_scheduled_report_retries_only_failed_target(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    bridge = FakeBridge()
    remote = FakeRemote()
    service = IntentionNotificationService(
        store,
        bridge,
        remote,
        scheduled_group_send_interval=0,
    )
    await service.configure(
        enabled=True,
        consumer_id="yunsheng-wx-robot-01",
        target_groups=[
            {"room_id": "room-1@chatroom", "name": "通知一群"},
            {"room_id": "room-2@chatroom", "name": "通知二群"},
        ],
        api_token="test-token",
        startup_policy="include_pending",
    )
    point = datetime(2026, 9, 5, 18, 30, tzinfo=CHINA_TZ)
    remote.snapshot_response = scheduled_batch(point.isoformat())
    assert await service.create_due_scheduled_reports_once(point) is True

    bridge.fail_rooms.add("room-2@chatroom")
    assert await service.deliver_pending_once() is True
    report_id = "intention_report:2026-09-05:stage_1830:2"
    assert store.scheduled_report(report_id)["status"] == "waiting_retry"

    with store._connect() as connection:  # noqa: SLF001
        connection.execute(
            """
            UPDATE intention_notification_scheduled_group_reports
            SET next_attempt_at_ms=0
            WHERE report_id=?
            """,
            (report_id,),
        )
    bridge.fail_rooms.clear()
    assert await service.deliver_pending_once() is True

    assert [room_id for room_id, _ in bridge.sent].count("room-1@chatroom") == 1
    assert [room_id for room_id, _ in bridge.sent].count("room-2@chatroom") == 2
    assert store.scheduled_report(report_id)["status"] == "sent"


@pytest.mark.asyncio
async def test_schedule_does_not_fetch_outside_grace_window(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    remote = FakeRemote()
    service = IntentionNotificationService(store, FakeBridge(), remote)
    await service.configure(
        enabled=True,
        consumer_id="yunsheng-wx-robot-01",
        target_groups=[{"room_id": "room-1@chatroom", "name": "通知群"}],
        api_token="test-token",
        startup_policy="include_pending",
    )

    assert (
        await service.create_due_scheduled_reports_once(
            datetime(2026, 9, 5, 12, 30, tzinfo=CHINA_TZ)
        )
        is False
    )
    assert remote.snapshot_calls == 0


@pytest.mark.asyncio
async def test_service_lifecycle_starts_and_stops_schedule_worker(
    tmp_path: Path,
) -> None:
    service = IntentionNotificationService(
        make_store(tmp_path),
        FakeBridge(),
        FakeRemote(),
    )

    await service.start()
    await asyncio.sleep(0)
    assert service._schedule_task is not None  # noqa: SLF001
    assert service._schedule_task.done() is False  # noqa: SLF001

    await service.stop()
    assert service._schedule_task is None  # noqa: SLF001


def test_store_counts_each_event_once(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    groups = (TargetGroup("room-1@chatroom", "通知群"),)

    first = store.persist_event(delivery("intention_order.created:1"), groups)
    second = store.persist_event(
        delivery(
            "intention_order.created:2",
            agent_id=12,
            agent_name="外呼员乙",
        ),
        groups,
    )
    duplicate = store.persist_event(
        delivery("intention_order.created:1", attempt_count=2), groups
    )
    third = store.persist_event(
        delivery(
            "intention_order.created:3",
            group_id=8,
            group_name="第二外呼组",
        ),
        groups,
    )

    assert (first["agent_today"], first["group_today"], first["overall_today"]) == (
        1,
        1,
        1,
    )
    assert (second["agent_today"], second["group_today"], second["overall_today"]) == (
        1,
        2,
        2,
    )
    assert duplicate["overall_today"] == 1
    assert (third["agent_today"], third["group_today"], third["overall_today"]) == (
        2,
        1,
        3,
    )
    assert len(store.status_snapshot()["recent"]) == 3


def test_order_date_controls_statistics_day(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    row = store.persist_event(
        delivery(
            "intention_order.created:4",
            occurred_at="2026-09-03T10:30:00+08:00",
            order_date="2026-09-02",
        ),
        (TargetGroup("room-1@chatroom", "通知群"),),
    )

    assert row["stat_date"] == "2026-09-02"
    assert row["occurred_at"] == "2026-09-03T10:30:00+08:00"
    assert "10:30" in row["announcement_text"]


def test_store_uses_complete_team_snapshot_for_stage_report(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    occurred_at = datetime.now().astimezone().replace(microsecond=0).isoformat()
    team = snapshot(
        group_today=5,
        group_yesterday_same_time=4,
        overall_today=20,
        snapshot_at=occurred_at,
    )

    row = store.persist_event(
        delivery(
            "intention_order.created:5",
            occurred_at=occurred_at,
            team_snapshot=team,
        ),
        (TargetGroup("room-1@chatroom", "通知群"),),
    )
    status = store.status_snapshot()

    assert (row["agent_today"], row["group_today"], row["overall_today"]) == (
        2,
        5,
        20,
    )
    assert "【阶段战报" in row["announcement_text"]
    assert "待破零：外呼员丙" in row["announcement_text"]
    assert "外呼员丁" not in row["announcement_text"]
    assert status["today"]["team_snapshot_ready"] is True
    assert status["today"]["team_snapshot_state"] == "ready"
    assert status["today"]["on_duty_dialed_number_threshold"] == 10


def test_store_compares_stage_report_with_previous_five_order_snapshot(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    first_members = [
        {
            "user_id": 11,
            "name": "外呼员甲",
            "dialed_number_count": 15,
            "intention_today": 1,
        },
        {
            "user_id": 12,
            "name": "外呼员乙",
            "dialed_number_count": 14,
            "intention_today": 1,
        },
        {
            "user_id": 13,
            "name": "外呼员丙",
            "dialed_number_count": 11,
            "intention_today": 0,
        },
    ]
    second_members = [
        {**first_members[0], "dialed_number_count": 22, "intention_today": 2},
        {**first_members[1], "dialed_number_count": 20, "intention_today": 2},
        {**first_members[2], "dialed_number_count": 18, "intention_today": 1},
    ]
    groups = (TargetGroup("room-1@chatroom", "通知群"),)

    store.persist_event(
        delivery(
            "intention_order.created:50",
            occurred_at="2026-09-03T10:00:00+08:00",
            team_snapshot=snapshot(
                group_today=10,
                snapshot_at="2026-09-03T10:00:00+08:00",
                members=first_members,
            ),
        ),
        groups,
    )
    row = store.persist_event(
        delivery(
            "intention_order.created:51",
            occurred_at="2026-09-03T11:00:00+08:00",
            team_snapshot=snapshot(
                group_today=15,
                group_yesterday_same_time=12,
                snapshot_at="2026-09-03T11:00:00+08:00",
                members=second_members,
            ),
        ),
        groups,
    )

    assert "【本阶段变化】" in row["announcement_text"]
    assert "外呼员甲 1→2" in row["announcement_text"]
    assert "外呼员乙 1→2" in row["announcement_text"]
    assert "外呼员丙 0→1｜新破零" in row["announcement_text"]


def test_incomplete_snapshot_degrades_without_zero_list(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    occurred_at = datetime.now().astimezone().replace(microsecond=0).isoformat()
    incomplete = snapshot(group_today=5, snapshot_at=occurred_at)
    incomplete["complete"] = False

    row = store.persist_event(
        delivery(
            "intention_order.created:6",
            occurred_at=occurred_at,
            team_snapshot=incomplete,
        ),
        (TargetGroup("room-1@chatroom", "通知群"),),
    )

    assert "【新单" in row["announcement_text"]
    assert "今日已有 1 人出单" in row["announcement_text"]
    assert "待破零" not in row["announcement_text"]
    assert "0 单" not in row["announcement_text"]
    assert store.status_snapshot()["today"]["team_snapshot_state"] == "missing"


def test_stale_snapshot_degrades_without_zero_list(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    stale = snapshot(
        group_today=5,
        snapshot_at="2026-09-03T10:29:59+08:00",
    )

    row = store.persist_event(
        delivery(
            "intention_order.created:7",
            occurred_at="2026-09-03T10:30:00+08:00",
            team_snapshot=stale,
        ),
        (TargetGroup("room-1@chatroom", "通知群"),),
    )

    assert "【新单" in row["announcement_text"]
    assert "今日已有 1 人出单" in row["announcement_text"]
    assert "待破零" not in row["announcement_text"]


@pytest.mark.asyncio
async def test_partial_group_failure_retries_only_failed_target(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    bridge = FakeBridge()
    remote = FakeRemote()
    service = IntentionNotificationService(store, bridge, remote)
    await service.configure(
        enabled=True,
        consumer_id="yunsheng-wx-robot-01",
        target_groups=[
            {"room_id": "room-1@chatroom", "name": "通知一群"},
            {"room_id": "room-2@chatroom", "name": "通知二群"},
        ],
        api_token="test-token",
        startup_policy="include_pending",
    )
    config = store.configuration()
    received_acks: list[str] = []

    async def receive_ack(event_id: str, status: str, fields: dict[str, Any]) -> None:
        received_acks.append(status)

    event = delivery("intention_order.created:10")
    await service._accept_delivery(config, event, receive_ack)
    bridge.fail_rooms.add("room-2@chatroom")

    assert await service.deliver_pending_once() is True
    assert received_acks == ["RECEIVED"]
    assert remote.acks[-1]["status"] == "FAILED"
    assert [room_id for room_id, _ in bridge.sent] == [
        "room-1@chatroom",
        "room-2@chatroom",
    ]

    bridge.fail_rooms.clear()
    await service._accept_delivery(
        config,
        delivery("intention_order.created:10", attempt_count=2),
        receive_ack,
    )
    assert await service.deliver_pending_once() is True

    assert remote.acks[-1]["status"] == "SENT"
    assert [room_id for room_id, _ in bridge.sent].count("room-1@chatroom") == 1
    assert [room_id for room_id, _ in bridge.sent].count("room-2@chatroom") == 2
    targets = store.targets("intention_order.created:10")
    assert {target["status"] for target in targets} == {"sent"}

    sent_count = len(bridge.sent)
    await service._accept_delivery(
        config,
        delivery("intention_order.created:10", attempt_count=3),
        receive_ack,
    )
    assert received_acks[-1] == "SENT"
    assert await service.deliver_pending_once() is False
    assert len(bridge.sent) == sent_count


@pytest.mark.asyncio
async def test_public_status_never_returns_token(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    service = IntentionNotificationService(store, FakeBridge(), FakeRemote())
    await service.configure(
        enabled=False,
        consumer_id="yunsheng-wx-robot-01",
        target_groups=[],
        api_token="secret-token",
    )

    status = await service.status()

    assert status["config"]["token_configured"] is True
    assert "api_token" not in status["config"]
    assert "secret-token" not in str(status)


@pytest.mark.asyncio
async def test_start_requires_explicit_backlog_policy(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    service = IntentionNotificationService(store, FakeBridge(), FakeRemote())

    with pytest.raises(ValueError, match="是否补发"):
        await service.configure(
            enabled=True,
            consumer_id="yunsheng-wx-robot-01",
            target_groups=[{"room_id": "room-1@chatroom", "name": "通知一群"}],
            api_token="test-token",
        )


@pytest.mark.asyncio
async def test_new_only_skips_backlog_but_counts_it_for_next_message(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    bridge = FakeBridge()
    remote = FakeRemote()
    service = IntentionNotificationService(store, bridge, remote)
    await service.configure(
        enabled=True,
        consumer_id="yunsheng-wx-robot-01",
        target_groups=[{"room_id": "room-1@chatroom", "name": "通知一群"}],
        api_token="test-token",
        startup_policy="new_only",
    )
    config = store.configuration()
    assert config.activation_cutoff is not None
    cutoff = datetime.fromisoformat(config.activation_cutoff)
    acknowledgements: list[tuple[str, dict[str, Any]]] = []

    async def receive_ack(event_id: str, status: str, fields: dict[str, Any]) -> None:
        acknowledgements.append((status, fields))

    await service._accept_delivery(
        config,
        delivery(
            "intention_order.created:30",
            occurred_at=(cutoff - timedelta(seconds=1)).isoformat(),
        ),
        receive_ack,
    )

    assert acknowledgements[-1][0] == "SENT"
    assert acknowledgements[-1][1]["details"]["skipped"] is True
    assert bridge.sent == []
    assert store.status_snapshot()["recent"][0]["status"] == "skipped"

    await service._accept_delivery(
        config,
        delivery(
            "intention_order.created:31",
            occurred_at=(cutoff + timedelta(seconds=1)).isoformat(),
        ),
        receive_ack,
    )
    assert acknowledgements[-1][0] == "RECEIVED"
    assert await service.deliver_pending_once() is True
    assert len(bridge.sent) == 1
    assert "个人 2 单" in bridge.sent[0][1]
    assert "小组 2 单｜全场 2 单" in bridge.sent[0][1]
    assert "今日已有 1 人出单" in bridge.sent[0][1]


@pytest.mark.asyncio
async def test_rest_fallback_uses_consumer_contract() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/events/claim"):
            return httpx.Response(
                200, json={"items": [delivery("intention_order.created:21")]}
            )
        if request.url.path.endswith("/team-snapshots"):
            return httpx.Response(
                200,
                json=scheduled_batch("2026-09-03T12:00:00+08:00"),
            )
        return httpx.Response(200, json={"accepted": True})

    remote = RobotDispatchRemote("https://example.test/api/consumer")
    remote._client = httpx.AsyncClient(  # noqa: SLF001
        base_url=remote.rest_base_url,
        transport=httpx.MockTransport(handler),
    )
    config = NotificationConfig(
        enabled=True,
        consumer_id="yunsheng-wx-robot-01",
        api_token="test-token",
        target_groups=(TargetGroup("room-1@chatroom", "通知群"),),
    )

    claimed = await remote.claim(config)
    snapshots = await remote.team_snapshots(config)
    await remote.ack(
        config,
        claimed[0]["event_id"],
        "RECEIVED",
        details={"local_queue_id": claimed[0]["event_id"]},
    )
    await remote.close()

    assert len(requests) == 3
    assert all(
        request.headers["Authorization"] == "Bearer test-token" for request in requests
    )
    assert requests[0].url.path == "/api/consumer/events/claim"
    assert requests[0].read().decode("utf-8").find('"wait_seconds":5') >= 0
    assert requests[1].url.path == "/api/consumer/team-snapshots"
    assert snapshots["groups"][0]["group_today"] == 7
    assert requests[2].url.path.endswith("/events/intention_order.created:21/ack")
    assert '"status":"RECEIVED"' in requests[2].read().decode("utf-8")


@pytest.mark.asyncio
async def test_group_discovery_tolerates_legacy_contact_bytes() -> None:
    content = (
        b'{"code":1,"msg":"success","data":['
        b'{"wxid":"room-1@chatroom","nickname":"legacy\xa7room"}'
        b"]}"
    )
    bridge = WeChatNotificationBridge("http://127.0.0.1:19088")
    bridge._client = httpx.AsyncClient(  # noqa: SLF001
        base_url=bridge.base_url,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content)),
    )

    groups = await bridge.groups()
    await bridge.close()

    assert groups == [TargetGroup("room-1@chatroom", "legacy�room")]
