from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import struct
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

import backend.app.friend_automation as friend_automation_module
from backend.app.config import Settings
from backend.app.friend_automation import (
    FriendAutomation,
    FriendAutomationError,
    FriendAutomationStore,
    WeChatBridgeClient,
    event_keyword,
    parse_friend_request,
)
from backend.app.fmessage_source import FMessageSource
from backend.app.realtime import RealtimeStore


class Clock:
    def __init__(self, value: int = 1_000_000) -> None:
        self.value = value

    def __call__(self) -> int:
        self.value += 1
        return self.value

    def advance(self, milliseconds: int) -> None:
        self.value += milliseconds


class FakeRealtime:
    def __init__(
        self,
        events: list[dict[str, Any]] | None = None,
        latest_sequence: int | None = None,
    ) -> None:
        self.events = list(events or [])
        self.latest_sequence = latest_sequence
        self.latest_calls = 0

    def ledger_latest_sequence(self) -> int:
        self.latest_calls += 1
        if self.latest_sequence is not None:
            return self.latest_sequence
        return max((int(event["sequence"]) for event in self.events), default=0)

    def ledger_events_since(self, after: int, limit: int) -> list[dict[str, Any]]:
        return [
            event
            for event in sorted(self.events, key=lambda item: int(item["sequence"]))
            if int(event["sequence"]) > after
        ][:limit]


class FakeRepository:
    def __init__(self, realtime: FakeRealtime | None = None) -> None:
        self.realtime = realtime or FakeRealtime()


class FakeBridge:
    def __init__(
        self,
        *,
        contacts: list[dict[str, Any]] | None = None,
        rooms: dict[str, list[dict[str, Any]] | dict[str, Any]] | None = None,
        join_on_add: bool = False,
        add_hook: Callable[[str, str], None] | None = None,
        room_members_hook: Callable[[str], None] | None = None,
        invite_hook: Callable[[str, str], None] | None = None,
        remove_on_delete: bool = False,
        remove_hook: Callable[[str, str], None] | None = None,
        accept_hook: Callable[[str, str, int, int], None] | None = None,
        send_at_hook: Callable[[str, str, str, str], None] | None = None,
    ) -> None:
        self.contact_rows = list(contacts or [])
        self.rooms = dict(rooms or {})
        self.join_on_add = join_on_add
        self.add_hook = add_hook
        self.room_members_hook = room_members_hook
        self.invite_hook = invite_hook
        self.remove_on_delete = remove_on_delete
        self.remove_hook = remove_hook
        self.accept_hook = accept_hook
        self.send_at_hook = send_at_hook
        self.auto_accept_calls: list[bool] = []
        self.accept_calls: list[tuple[str, str, int, int]] = []
        self.sent_text: list[tuple[str, str]] = []
        self.sent_at: list[tuple[str, str, str, str]] = []
        self.room_member_calls: list[str] = []
        self.add_calls: list[tuple[str, str]] = []
        self.invite_calls: list[tuple[str, str]] = []
        self.remove_calls: list[tuple[str, str]] = []
        self.close_calls = 0

    async def set_auto_accept(self, enabled: bool) -> dict[str, Any]:
        self.auto_accept_calls.append(enabled)
        return {
            "enabled": enabled,
            "queued": 0,
            "succeeded": 0,
            "failed": 0,
            "parseFailed": 0,
            "deduplicated": 0,
        }

    async def accept_friend(
        self,
        encrypt_username: str,
        ticket: str,
        scene: int,
        friend_flag: int,
    ) -> dict[str, Any]:
        self.accept_calls.append((encrypt_username, ticket, scene, friend_flag))
        if self.accept_hook:
            self.accept_hook(encrypt_username, ticket, scene, friend_flag)
        return {"ok": True, "message": "friend request accepted"}

    async def contacts(self) -> list[dict[str, Any]]:
        return self.contact_rows

    async def send_text(self, wxid: str, content: str) -> None:
        self.sent_text.append((wxid, content))

    async def send_at_text(
        self, room_id: str, wxids: str, nickname: str, content: str
    ) -> None:
        self.sent_at.append((room_id, wxids, nickname, content))
        if self.send_at_hook:
            self.send_at_hook(room_id, wxids, nickname, content)

    async def room_members(self, room_id: str) -> list[dict[str, Any]] | dict[str, Any]:
        self.room_member_calls.append(room_id)
        if self.room_members_hook:
            self.room_members_hook(room_id)
        return self.rooms.get(room_id, [])

    async def add_room_member(self, room_id: str, wxid: str) -> None:
        self.add_calls.append((room_id, wxid))
        if self.join_on_add:
            self._add_member(room_id, wxid)
        if self.add_hook:
            self.add_hook(room_id, wxid)

    async def invite_room_member(self, room_id: str, wxid: str) -> None:
        self.invite_calls.append((room_id, wxid))
        if self.invite_hook:
            self.invite_hook(room_id, wxid)

    async def remove_room_member(self, room_id: str, wxid: str) -> None:
        self.remove_calls.append((room_id, wxid))
        if self.remove_hook:
            self.remove_hook(room_id, wxid)
        if self.remove_on_delete:
            self._remove_member(room_id, wxid)

    async def close(self) -> None:
        self.close_calls += 1

    def _add_member(self, room_id: str, wxid: str) -> None:
        room = self.rooms.setdefault(room_id, [])
        if isinstance(room, dict):
            members = room.setdefault("members", [])
            assert isinstance(members, list)
            members.append({"wxid": wxid})
            room["count"] = max(int(room.get("count") or 0), len(members))
        else:
            room.append({"wxid": wxid})

    def _remove_member(self, room_id: str, wxid: str) -> None:
        room = self.rooms.setdefault(room_id, [])
        if isinstance(room, dict):
            members = room.setdefault("members", [])
            assert isinstance(members, list)
            retained = [item for item in members if str(item.get("wxid") or "") != wxid]
            removed = len(members) - len(retained)
            members[:] = retained
            room["count"] = max(0, int(room.get("count") or 0) - removed)
        else:
            room[:] = [item for item in room if str(item.get("wxid") or "") != wxid]


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    value = Clock()
    monkeypatch.setattr(friend_automation_module, "_now_ms", value)
    return value


def make_settings(
    tmp_path: Path,
    *,
    enabled: bool = True,
    group_count: int = 2,
    group_capacity: int = 3,
) -> Settings:
    panel_root = tmp_path / "panel"
    return Settings(
        panel_root=panel_root,
        repository_root=tmp_path,
        database_root=tmp_path / "database",
        report_path=tmp_path / "decryption_report.json",
        cache_path=panel_root / ".cache" / "analytics.sqlite",
        fts_extension=tmp_path / "weixin_fts_tokenizer.dll",
        weixin_dll=tmp_path / "Weixin.dll",
        friend_automation_enabled=enabled,
        friend_automation_group_count=group_count,
        friend_automation_group_capacity=group_capacity,
    )


def create_fmessage_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE FMessageTable(
                user_name_ TEXT,
                type_ INTEGER,
                timestamp_ INTEGER,
                encrypt_user_name_ TEXT,
                content_ TEXT,
                is_sender_ INTEGER,
                ticket_ TEXT,
                scene_ INTEGER,
                fmessage_detail_buf_ TEXT,
                remark_ TEXT,
                label_ids_ TEXT
            )
            """
        )
        connection.commit()


def insert_fmessage(
    path: Path,
    *,
    username: str,
    encrypt_username: str,
    ticket: str,
    timestamp: int,
    scene: int = 30,
    is_sender: int = 0,
) -> int:
    with sqlite3.connect(path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO FMessageTable(
                user_name_,type_,timestamp_,encrypt_user_name_,content_,
                is_sender_,ticket_,scene_
            ) VALUES (?,37,?,?,?, ?,?,?)
            """,
            (
                username,
                timestamp,
                encrypt_username,
                "hello",
                is_sender,
                ticket,
                scene,
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def make_fmessage_source(tmp_path: Path, settings: Settings) -> tuple[FMessageSource, Path]:
    source_root = tmp_path / "encrypted"
    database = source_root / "general" / "general.db"
    create_fmessage_database(database)
    key_manifest = tmp_path / "keys.json"
    settings.report_path.write_text(
        json.dumps(
            {
                "account": "wxid_self",
                "source_root": str(source_root),
                "key_manifest": str(key_manifest),
            }
        ),
        encoding="utf-8",
    )
    source = FMessageSource(
        settings.report_path,
        settings.repository_root,
        settings.friend_automation_path.with_name("friend_requests_snapshot.sqlite"),
    )
    return source, database


def friend_event(
    sequence: int,
    *,
    encrypt_username: str = "v3_test@stranger",
    candidate_username: str = "",
) -> dict[str, Any]:
    candidate = f' fromusername="{candidate_username}"' if candidate_username else ""
    return {
        "sequence": sequence,
        "event_id": f"friend-{sequence}",
        "payload": {
            "message_type": 37,
            "is_incoming": True,
            "direction": "incoming",
            "content": (
                f'<msg encryptusername="{encrypt_username}" ticket="ticket"'
                f' scene="17"{candidate}/>'
            ),
        },
    }


def keyword_event(
    sequence: int,
    *,
    wxid: str = "wxid_new",
    content: str = "进群",
    room_id: str = "",
) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "event_id": f"keyword-{sequence}",
        "payload": {
            "message_type": 1,
            "is_incoming": True,
            "direction": "incoming",
            "sender_id": wxid,
            "room_id": room_id,
            "content": content,
        },
    }


def room_contacts(count: int = 2) -> list[dict[str, Any]]:
    return [
        {
            "wxid": f"room-{index}@chatroom",
            "nickname": f"47驿站{index}群",
        }
        for index in range(1, count + 1)
    ]


def read_lead(path: Path, encrypt_username: str = "v3_test@stranger") -> dict[str, Any]:
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM friend_leads WHERE encrypt_username=?",
            (encrypt_username,),
        ).fetchone()
    assert row is not None
    return dict(row)


def update_lead(path: Path, encrypt_username: str, **values: Any) -> None:
    assignments = ",".join(f"{column}=?" for column in values)
    with sqlite3.connect(path) as connection:
        connection.execute(
            f"UPDATE friend_leads SET {assignments} WHERE encrypt_username=?",
            (*values.values(), encrypt_username),
        )
        connection.commit()


def seed_request(
    store: FriendAutomationStore,
    *,
    encrypt_username: str = "v3_test@stranger",
    wxid: str = "wxid_new",
) -> None:
    event = friend_event(1, encrypt_username=encrypt_username)
    request = parse_friend_request(event["payload"]["content"])
    assert request is not None
    store.consume_event(event, request, None)
    store.bind_contact(encrypt_username, wxid)


def seed_join_pending(
    store: FriendAutomationStore,
    *,
    encrypt_username: str = "v3_test@stranger",
    wxid: str = "wxid_new",
) -> None:
    seed_request(store, encrypt_username=encrypt_username, wxid=wxid)
    store.mark_welcome_sent(encrypt_username)
    event = keyword_event(2, wxid=wxid)
    store.consume_event(event, None, wxid)
    assert store.promote_keywords() == 1
    assert read_lead(store.path, encrypt_username)["state"] == "join_pending"


def test_parse_friend_request_and_exact_private_keyword() -> None:
    parsed = parse_friend_request(
        'prefix<msg encryptusername="v3_abc@stranger" fromusername="wxid_abc"/>'
    )
    assert parsed == {
        "encrypt_username": "v3_abc@stranger",
        "candidate_username": "wxid_abc",
        "ticket": "",
        "scene": None,
        "friend_flag": 0,
    }
    assert parse_friend_request("<msg ticket='missing-username'/>") is None

    event = keyword_event(1, wxid="wxid_abc")
    assert event_keyword(event, "进群") == "wxid_abc"
    assert event_keyword(keyword_event(2, content="进群了"), "进群") is None
    assert event_keyword(keyword_event(3, room_id="room-1@chatroom"), "进群") is None
    event["payload"]["direction"] = "outgoing"
    event["payload"]["is_incoming"] = False
    assert event_keyword(event, "进群") is None


def test_fmessage_source_reads_real_schema_and_advances_past_outgoing_rows(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    source, database = make_fmessage_source(tmp_path, settings)
    first_rowid = insert_fmessage(
        database,
        username="wxid_new",
        encrypt_username="v3_new@stranger",
        ticket="v4_ticket",
        timestamp=1_700_000_001,
    )
    outgoing_rowid = insert_fmessage(
        database,
        username="wxid_outgoing",
        encrypt_username="v3_outgoing@stranger",
        ticket="v4_outgoing",
        timestamp=1_700_000_002,
        is_sender=1,
    )

    assert source.latest_rowid() == outgoing_rowid
    batch = source.scan(0)

    assert batch.high_watermark == outgoing_rowid
    assert [request.rowid for request in batch.requests] == [first_rowid]
    event = batch.requests[0].realtime_event("wxid_self")
    assert event["source"] == "fmessage_table"
    assert event["encrypt_username"] == "v3_new@stranger"
    assert event["ticket"] == "v4_ticket"
    assert event["scene"] == 30
    assert event["accept_required"] is True
    assert source.scan(0).requests[0].realtime_event("wxid_self")["event_id"] == event[
        "event_id"
    ]
    assert source.status()["refresh_count"] == 1


def test_encrypted_wal_overlay_applies_only_committed_pages(tmp_path: Path) -> None:
    page_size = 4096
    source = tmp_path / "general.db"
    output = tmp_path / "snapshot.db"
    source.write_bytes(bytes(page_size))
    output.write_bytes(bytes(page_size * 2))
    frame = struct.pack(">6I", 2, 2, 1, 2, 3, 4) + bytes(page_size)
    wal_path = Path(str(source) + "-wal")
    wal_path.write_bytes(bytes(32) + frame)
    wal_stat = wal_path.stat()
    wal_data = wal_path.read_bytes()
    status = {
        "exists": True,
        "size": len(wal_data),
        "mtime_ns": wal_stat.st_mtime_ns,
        "sha256": hashlib.sha256(wal_data).hexdigest(),
        "last_commit_frame": 1,
        "last_commit_database_size": 2,
    }
    verified: list[Path] = []
    tool = SimpleNamespace(
        choose_key=lambda _manifest, _page: {
            "key_hex": "00" * 32,
            "hmac_key_hex": "11" * 32,
        },
        decrypt_page=lambda _page, page_number, *_args: bytes(
            [page_number]
        )
        * page_size,
        inspect_wal=lambda _source, _page_size: dict(status),
        wal_snapshot_id=lambda value: (
            value.get("exists"),
            value.get("size"),
            value.get("sha256"),
        ),
        verify_plain_database=lambda path: verified.append(path),
    )

    FMessageSource._apply_encrypted_wal(
        source,
        output,
        {"sqlcipher": {"page_size": page_size}},
        status,
        tool,
    )

    data = output.read_bytes()
    assert data[:page_size] == bytes(page_size)
    assert data[page_size:] == bytes([2]) * page_size
    assert verified == [output]


def test_fmessage_first_run_skips_history_then_accepts_new_request_once(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path)
    source, database = make_fmessage_source(tmp_path, settings)
    historical_rowid = insert_fmessage(
        database,
        username="wxid_historical",
        encrypt_username="v3_historical@stranger",
        ticket="v4_historical",
        timestamp=1_700_000_001,
    )
    realtime = RealtimeStore(
        tmp_path / "realtime.sqlite",
        account="wxid_self",
        snapshot=None,
        database_root=settings.database_root,
        timezone=ZoneInfo("Asia/Shanghai"),
    )
    repository = SimpleNamespace(realtime=realtime, account="wxid_self")
    bridge = FakeBridge(
        contacts=[
            {
                "wxid": "wxid_new",
                "v3": "v3_new@stranger",
                "category": "friend",
            }
        ]
    )
    automation = FriendAutomation(
        repository,
        settings,
        bridge,
        friend_request_source=source,
    )
    automation.store.initialize(True, 0)
    automation._last_auto_accept_at = time.monotonic()

    asyncio.run(automation.run_once())

    assert automation.store.fmessage_cursor() == historical_rowid
    assert bridge.accept_calls == []
    with sqlite3.connect(settings.friend_automation_path) as connection:
        assert connection.execute("SELECT count(*) FROM friend_leads").fetchone() == (
            0,
        )

    new_rowid = insert_fmessage(
        database,
        username="wxid_new",
        encrypt_username="v3_new@stranger",
        ticket="v4_new",
        timestamp=1_700_000_002,
    )
    asyncio.run(automation.run_once())
    asyncio.run(automation.run_once())

    assert automation.store.fmessage_cursor() == new_rowid
    assert bridge.accept_calls == [
        ("v3_new@stranger", "v4_new", 30, 0),
    ]
    lead = read_lead(settings.friend_automation_path, "v3_new@stranger")
    assert lead["state"] == "awaiting_keyword"
    assert lead["wxid"] == "wxid_new"
    assert bridge.sent_text == [("wxid_new", "回复“进群”加入群聊")]
    status = automation.status()
    assert status["fmessage_source"]["cursor"] == new_rowid
    assert status["fmessage_source"]["last_error"] is None


def test_reasserting_enabled_does_not_skip_unpolled_fmessage(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path)
    source, database = make_fmessage_source(tmp_path, settings)
    realtime = RealtimeStore(
        tmp_path / "realtime.sqlite",
        account="wxid_self",
        snapshot=None,
        database_root=settings.database_root,
        timezone=ZoneInfo("Asia/Shanghai"),
    )
    repository = SimpleNamespace(realtime=realtime, account="wxid_self")
    bridge = FakeBridge()
    automation = FriendAutomation(
        repository,
        settings,
        bridge,
        friend_request_source=source,
    )
    automation.store.initialize(True, 0, 0)
    new_rowid = insert_fmessage(
        database,
        username="wxid_new",
        encrypt_username="v3_new@stranger",
        ticket="v4_new",
        timestamp=1_700_000_002,
    )

    asyncio.run(automation.set_enabled(True))

    assert automation.store.fmessage_cursor() == 0
    automation._last_auto_accept_at = time.monotonic()
    asyncio.run(automation.run_once())
    assert automation.store.fmessage_cursor() == new_rowid
    assert bridge.accept_calls == [("v3_new@stranger", "v4_new", 30, 0)]


def test_restart_verifies_uncertain_friend_accept_before_retry(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path)
    store = FriendAutomationStore(settings.friend_automation_path)
    store.initialize(True, 0)
    event = friend_event(1)
    request = parse_friend_request(event["payload"]["content"])
    assert request is not None
    request["accept_required"] = True
    store.consume_event(event, request, None)
    store.mark_accept_sending("v3_test@stranger")

    store.initialize(True, 0)

    lead = read_lead(settings.friend_automation_path)
    assert lead["state"] == "accept_verifying"
    assert int(lead["retry_at_ms"]) > clock.value
    assert "uncertain after restart" in lead["last_error"]


def test_restart_does_not_resend_uncertain_welcome(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path)
    store = FriendAutomationStore(settings.friend_automation_path)
    store.initialize(True, 0)
    seed_request(store)
    store.mark_welcome_sending("v3_test@stranger")

    bridge = FakeBridge()
    automation = FriendAutomation(FakeRepository(), settings, bridge)
    automation.store.initialize(True, 0)
    recovered = read_lead(settings.friend_automation_path)
    assert recovered["state"] == "awaiting_keyword"
    assert recovered["welcome_sent_at_ms"] is not None
    assert automation.store.due_welcomes(clock.value + 100_000) == []

    automation._last_auto_accept_at = time.monotonic()
    asyncio.run(automation.run_once())
    assert bridge.sent_text == []


@pytest.mark.parametrize(
    (
        "uncertain_state",
        "expected_state",
        "final_state",
        "room_checks",
        "invite_calls",
        "has_verification_grace",
    ),
    [
        ("assigning", "join_pending", "join_pending", [], [], False),
        (
            "add_sending",
            "add_pending",
            "invite_pending",
            ["room-1@chatroom"],
            [("room-1@chatroom", "wxid_new")],
            True,
        ),
        (
            "invite_sending",
            "invite_pending",
            "invite_pending",
            ["room-1@chatroom"],
            [],
            True,
        ),
    ],
)
def test_restart_recovers_room_action_for_verification_only(
    tmp_path: Path,
    clock: Clock,
    uncertain_state: str,
    expected_state: str,
    final_state: str,
    room_checks: list[str],
    invite_calls: list[tuple[str, str]],
    has_verification_grace: bool,
) -> None:
    settings = make_settings(tmp_path, group_count=1)
    store = FriendAutomationStore(settings.friend_automation_path)
    store.initialize(True, 0)
    seed_join_pending(store)
    if uncertain_state == "assigning":
        store.mark_assigning("v3_test@stranger", "room-1@chatroom", "47驿站1群")
    elif uncertain_state == "add_sending":
        store.mark_add_sending("v3_test@stranger", "room-1@chatroom", "47驿站1群")
    else:
        store.mark_invite_sending("v3_test@stranger", "room-1@chatroom", "47驿站1群")

    bridge = FakeBridge(rooms={"room-1@chatroom": []})
    automation = FriendAutomation(FakeRepository(), settings, bridge)
    automation.store.initialize(True, 0)
    recovered = read_lead(settings.friend_automation_path)
    assert recovered["state"] == expected_state
    assert (int(recovered["retry_at_ms"]) > clock.value) is has_verification_grace
    if uncertain_state == "add_sending":
        assert "uncertain after restart" in recovered["last_error"]

    clock.advance(5_001)
    automation._last_auto_accept_at = time.monotonic()
    asyncio.run(automation.run_once())
    assert bridge.room_member_calls == room_checks
    assert bridge.add_calls == []
    assert bridge.invite_calls == invite_calls
    assert read_lead(settings.friend_automation_path)["state"] == final_state


def test_bridge_member_count_ignores_duplicate_and_empty_wxids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = WeChatBridgeClient("http://127.0.0.1:19088")

    async def fake_post(_: str, __: dict[str, Any]) -> dict[str, Any]:
        return {
            "members": [
                {"wxid": "wxid_one"},
                {"wxid": " wxid_one "},
                {"wxid": ""},
                {"nickname": "missing id"},
            ],
            "count": 0,
        }

    monkeypatch.setattr(client, "_post", fake_post)

    async def exercise() -> int:
        try:
            return (await client.room_members("room-1@chatroom")).count
        finally:
            await client.close()

    assert asyncio.run(exercise()) == 1


def test_bridge_send_at_text_uses_native_mention_contract() -> None:
    client = WeChatBridgeClient("http://127.0.0.1:19088")
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_post(path: str, payload: dict[str, Any]) -> None:
        calls.append((path, payload))

    client._post = fake_post  # type: ignore[method-assign]

    async def exercise() -> None:
        try:
            await client.send_at_text(
                "room-1@chatroom",
                "wxid_new",
                "新成员",
                "请查看群公告",
            )
        finally:
            await client.close()

    asyncio.run(exercise())

    assert calls == [
        (
            "/api/sendAtText",
            {
                "chatRoomId": "room-1@chatroom",
                "wxids": "wxid_new",
                "nickname": "新成员",
                "msg": "请查看群公告",
            },
        )
    ]


def test_bridge_contacts_tolerate_invalid_utf8_profile_fields() -> None:
    client = WeChatBridgeClient("http://127.0.0.1:19088")
    raw_body = (
        b'{"code":1,"msg":"success","data":['
        b'{"wxid":"wxid_one","category":"friend","city":"bad \xb0 name"}'
        b"]}"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=raw_body,
            headers={"content-type": "application/json; charset=utf-8"},
            request=request,
        )

    async def exercise() -> list[dict[str, Any]]:
        await client.close()
        client._client = httpx.AsyncClient(  # noqa: SLF001
            base_url="http://127.0.0.1:19088",
            transport=httpx.MockTransport(handler),
            trust_env=False,
        )
        try:
            return await client.contacts()
        finally:
            await client.close()

    contacts = asyncio.run(exercise())
    assert contacts == [
        {
            "wxid": "wxid_one",
            "category": "friend",
            "city": "bad � name",
        }
    ]


def test_bridge_contacts_prefers_established_friend_duplicate() -> None:
    client = WeChatBridgeClient("http://127.0.0.1:19088")

    async def fake_post(_path: str, _payload: dict[str, Any]) -> Any:
        return [
            {
                "wxid": "wxid_one",
                "v3": "v3_one@stranger",
                "category": "friend",
                "state": "03",
            },
            {
                "wxid": "wxid_one",
                "v3": "v3_one@stranger",
                "category": "stranger",
                "state": "00",
            },
        ]

    client._post = fake_post  # type: ignore[method-assign]

    async def exercise() -> list[dict[str, Any]]:
        try:
            return await client.contacts()
        finally:
            await client.close()

    assert asyncio.run(exercise()) == [
        {
            "wxid": "wxid_one",
            "v3": "v3_one@stranger",
            "category": "friend",
            "state": "03",
        }
    ]


def test_pending_contact_prefers_established_friend_duplicate_by_v3(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path)
    bridge = FakeBridge(
        contacts=[
            {
                "wxid": "wxid_established",
                "v3": "v3_test@stranger",
                "category": "friend",
                "state": "03",
            },
            {
                "wxid": "wxid_pending",
                "v3": "v3_test@stranger",
                "category": "stranger",
                "state": "00",
            },
        ]
    )
    automation = FriendAutomation(FakeRepository(), settings, bridge)
    automation.store.initialize(True, 0)
    event = friend_event(1)
    request = parse_friend_request(event["payload"]["content"])
    assert request is not None
    automation.store.consume_event(event, request, None)

    asyncio.run(automation._resolve_pending_contacts())

    lead = read_lead(settings.friend_automation_path)
    assert lead["state"] == "welcome_pending"
    assert lead["wxid"] == "wxid_established"


def test_pending_state_is_not_accepted_when_category_claims_friend(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path)
    bridge = FakeBridge(
        contacts=[
            {
                "wxid": "wxid_pending",
                "v3": "v3_test@stranger",
                "category": "friend",
                "state": "00",
            }
        ]
    )
    automation = FriendAutomation(FakeRepository(), settings, bridge)
    automation.store.initialize(True, 0)
    event = friend_event(1)
    request = parse_friend_request(event["payload"]["content"])
    assert request is not None
    automation.store.consume_event(event, request, None)

    asyncio.run(automation._resolve_pending_contacts())

    lead = read_lead(settings.friend_automation_path)
    assert lead["state"] == "pending_contact"
    assert lead["wxid"] is None


@pytest.mark.parametrize(
    "state", ["awaiting_keyword", "join_pending", "invite_pending"]
)
def test_duplicate_friend_request_does_not_regress_active_state(
    tmp_path: Path, clock: Clock, state: str
) -> None:
    settings = make_settings(tmp_path)
    store = FriendAutomationStore(settings.friend_automation_path)
    store.initialize(True, 0)
    initial = friend_event(1, candidate_username="wxid_new")
    request = parse_friend_request(initial["payload"]["content"])
    assert request is not None
    store.consume_event(initial, request, None)
    update_lead(
        store.path,
        "v3_test@stranger",
        state=state,
        wxid="wxid_new",
        room_id="room-1@chatroom" if state == "invite_pending" else None,
        room_name="47驿站1群" if state == "invite_pending" else None,
    )

    duplicate = friend_event(9, candidate_username="wxid_other")
    duplicate_request = parse_friend_request(duplicate["payload"]["content"])
    assert duplicate_request is not None
    store.consume_event(duplicate, duplicate_request, None)

    lead = read_lead(store.path)
    assert lead["state"] == state
    assert lead["request_sequence"] == 1
    assert lead["request_event_id"] == "friend-1"
    assert lead["candidate_username"] == "wxid_new"
    assert store.cursor() == 9


def test_existing_membership_in_later_group_is_preserved(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path)
    bridge = FakeBridge(
        contacts=room_contacts(),
        rooms={
            "room-1@chatroom": [],
            "room-2@chatroom": [{"wxid": "wxid_new"}],
        },
    )
    automation = FriendAutomation(FakeRepository(), settings, bridge)
    automation.store.initialize(True, 0)
    seed_join_pending(automation.store)
    automation._last_auto_accept_at = time.monotonic()

    asyncio.run(automation.run_once())

    lead = read_lead(settings.friend_automation_path)
    assert lead["state"] == "assigned"
    assert lead["room_id"] == "room-2@chatroom"
    assert lead["room_name"] == "47驿站2群"
    assert bridge.add_calls == []
    assert bridge.invite_calls == []
    assert bridge.remove_calls == []
    assert bridge.sent_at == []


def test_native_member_count_controls_capacity_selection(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path, group_capacity=3)
    bridge = FakeBridge(
        contacts=room_contacts(),
        rooms={
            "room-1@chatroom": {
                "members": [{"wxid": "wxid_only_materialized_member"}],
                "count": 3,
            },
            "room-2@chatroom": {"members": [], "count": 0},
        },
        join_on_add=True,
    )
    automation = FriendAutomation(
        FakeRepository(), settings, bridge, verification_delays=(0,)
    )
    automation.store.initialize(True, 0)
    seed_join_pending(automation.store)
    automation._last_auto_accept_at = time.monotonic()

    asyncio.run(automation.run_once())

    lead = read_lead(settings.friend_automation_path)
    assert lead["state"] == "assigned"
    assert lead["room_id"] == "room-2@chatroom"
    assert bridge.add_calls == [("room-2@chatroom", "wxid_new")]


def test_materialized_pending_member_is_not_counted_as_a_second_reservation(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path, group_capacity=2)
    bridge = FakeBridge(
        contacts=room_contacts(),
        rooms={
            "room-1@chatroom": [{"wxid": "wxid_existing"}],
            "room-2@chatroom": [],
        },
        join_on_add=True,
    )
    automation = FriendAutomation(
        FakeRepository(), settings, bridge, verification_delays=(0,)
    )
    automation.store.initialize(True, 0)
    seed_join_pending(
        automation.store,
        encrypt_username="v3_existing@stranger",
        wxid="wxid_existing",
    )
    automation.store.mark_invite_pending(
        "v3_existing@stranger",
        "room-1@chatroom",
        "47驿站1群",
        30_000,
    )
    seed_join_pending(automation.store)
    automation._last_auto_accept_at = time.monotonic()

    asyncio.run(automation.run_once())

    lead = read_lead(settings.friend_automation_path)
    assert lead["state"] == "assigned"
    assert lead["room_id"] == "room-1@chatroom"
    assert bridge.add_calls == [("room-1@chatroom", "wxid_new")]


def test_all_target_groups_full_moves_lead_to_waitlist(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path, group_capacity=3)
    bridge = FakeBridge(
        contacts=room_contacts(),
        rooms={
            "room-1@chatroom": {"members": [], "count": 3},
            "room-2@chatroom": {"members": [], "count": 3},
        },
    )
    automation = FriendAutomation(FakeRepository(), settings, bridge)
    automation.store.initialize(True, 0)
    seed_join_pending(automation.store)
    automation._last_auto_accept_at = time.monotonic()

    asyncio.run(automation.run_once())

    lead = read_lead(settings.friend_automation_path)
    assert lead["state"] == "waitlist"
    assert lead["room_id"] is None
    assert "full" in lead["last_error"]
    assert bridge.add_calls == []
    assert bridge.invite_calls == []


def test_direct_add_verification_error_does_not_claim_invite_was_sent(
    tmp_path: Path,
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(tmp_path, group_count=1)
    bridge = FakeBridge(
        contacts=room_contacts(1),
        rooms={"room-1@chatroom": []},
    )
    room_checks = 0

    async def flaky_room_members(room_id: str) -> list[dict[str, Any]]:
        nonlocal room_checks
        room_checks += 1
        bridge.room_member_calls.append(room_id)
        if room_checks == 1:
            return []
        raise FriendAutomationError("membership lookup failed")

    monkeypatch.setattr(bridge, "room_members", flaky_room_members)
    automation = FriendAutomation(
        FakeRepository(), settings, bridge, verification_delays=(0,)
    )
    automation.store.initialize(True, 0)
    seed_join_pending(automation.store)
    automation._last_auto_accept_at = time.monotonic()

    asyncio.run(automation.run_once())

    lead = read_lead(settings.friend_automation_path)
    assert bridge.add_calls == [("room-1@chatroom", "wxid_new")]
    assert bridge.invite_calls == []
    assert lead["state"] == "add_pending"
    assert int(lead["retry_at_ms"]) > clock.value


def test_invite_state_is_durable_before_external_call(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path, group_count=1)
    observed_states: list[str] = []

    def fail_invite(_: str, __: str) -> None:
        observed_states.append(read_lead(settings.friend_automation_path)["state"])
        raise FriendAutomationError("uncertain invite result")

    bridge = FakeBridge(
        contacts=room_contacts(1),
        rooms={"room-1@chatroom": []},
        invite_hook=fail_invite,
    )
    automation = FriendAutomation(
        FakeRepository(), settings, bridge, verification_delays=(0,)
    )
    automation.store.initialize(True, 0)
    seed_join_pending(automation.store)
    automation._last_auto_accept_at = time.monotonic()

    asyncio.run(automation.run_once())

    assert observed_states == ["invite_sending"]
    assert bridge.invite_calls == [("room-1@chatroom", "wxid_new")]
    assert read_lead(settings.friend_automation_path)["state"] == "invite_pending"


def test_run_loop_survives_unexpected_runtime_error(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path)
    automation = FriendAutomation(FakeRepository(), settings, FakeBridge())
    automation.store.initialize(True, 0)
    calls = 0

    async def flaky_run_once() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("synthetic iteration failure")
        automation._stop.set()

    automation.run_once = flaky_run_once  # type: ignore[method-assign]

    async def exercise() -> None:
        task = asyncio.create_task(automation._run())
        for _ in range(200):
            if task.done():
                break
            await asyncio.sleep(0.005)
            automation._wake.set()
        await asyncio.wait_for(task, timeout=2.0)

    asyncio.run(exercise())
    assert calls >= 2


def test_first_enable_starts_after_raw_ledger_maximum(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path, enabled=False)
    realtime = FakeRealtime(latest_sequence=731)
    bridge = FakeBridge()
    automation = FriendAutomation(FakeRepository(realtime), settings, bridge)
    automation.store.initialize(False, 4)

    result = asyncio.run(automation.set_enabled(True))

    assert result["enabled"] is True
    assert automation.store.cursor() == 731
    assert realtime.latest_calls == 1
    assert bridge.auto_accept_calls == [True]


@pytest.mark.parametrize(
    ("persisted_enabled", "restart_default"),
    [(True, False), (False, True)],
)
def test_initialize_preserves_runtime_enabled_and_cursor(
    tmp_path: Path,
    persisted_enabled: bool,
    restart_default: bool,
) -> None:
    settings = make_settings(tmp_path)
    store = FriendAutomationStore(settings.friend_automation_path)
    store.initialize(False, 11)
    store.set_enabled(persisted_enabled, cursor=73)

    store.initialize(restart_default, 999)

    assert store.enabled() is persisted_enabled
    assert store.cursor() == 73


def test_standby_start_stop_does_not_toggle_native_auto_accept(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path)
    leader_bridge = FakeBridge()
    standby_bridge = FakeBridge()
    leader = FriendAutomation(FakeRepository(), settings, leader_bridge)
    standby = FriendAutomation(FakeRepository(), settings, standby_bridge)

    async def exercise() -> None:
        await leader.start()
        try:
            await standby.start()
            await asyncio.sleep(0)
            assert leader.store.enabled() is True
        finally:
            await standby.stop()
            await leader.stop()

    asyncio.run(exercise())

    assert leader_bridge.auto_accept_calls == [True, False]
    assert standby_bridge.auto_accept_calls == []


def test_existing_friend_keyword_joins_without_welcome(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path)
    realtime = FakeRealtime([keyword_event(1, wxid="wxid_existing")])
    bridge = FakeBridge(
        contacts=[
            {
                "wxid": "wxid_existing",
                "nickname": "已有好友",
                "category": "friend",
            },
            *room_contacts(),
        ],
        rooms={"room-1@chatroom": [], "room-2@chatroom": []},
        join_on_add=True,
    )
    automation = FriendAutomation(
        FakeRepository(realtime), settings, bridge, verification_delays=(0,)
    )
    automation.store.initialize(True, 0)
    automation._last_auto_accept_at = time.monotonic()

    asyncio.run(automation.run_once())

    lead = read_lead(settings.friend_automation_path, "existing_friend:wxid_existing")
    assert lead["state"] == "assigned"
    assert lead["keyword_sequence"] == 1
    assert lead["room_id"] == "room-1@chatroom"
    assert bridge.sent_text == []
    assert bridge.add_calls == [("room-1@chatroom", "wxid_existing")]
    assert bridge.sent_at == [
        (
            "room-1@chatroom",
            "wxid_existing",
            "已有好友",
            settings.friend_automation_group_welcome,
        )
    ]


def test_assigned_friend_rejoins_when_contact_lookup_is_unavailable(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path)
    realtime = FakeRealtime([keyword_event(3, wxid="wxid_existing")])
    bridge = FakeBridge(
        rooms={"room-1@chatroom": [], "room-2@chatroom": []},
        join_on_add=True,
    )

    async def unavailable_contacts() -> list[dict[str, Any]]:
        raise FriendAutomationError("contact lookup unavailable")

    bridge.contacts = unavailable_contacts  # type: ignore[method-assign]
    automation = FriendAutomation(
        FakeRepository(realtime), settings, bridge, verification_delays=(0,)
    )
    automation.store.initialize(True, 0)
    seed_join_pending(automation.store, wxid="wxid_existing")
    automation.store.mark_assigned("v3_test@stranger", "room-1@chatroom", "47驿站1群")
    automation._contacts_cache = room_contacts()
    automation._contacts_cache_at = time.monotonic()
    automation._last_auto_accept_at = time.monotonic()

    asyncio.run(automation.run_once())

    lead = read_lead(settings.friend_automation_path)
    assert lead["state"] == "assigned"
    assert lead["keyword_sequence"] == 3
    assert lead["room_id"] == "room-1@chatroom"
    assert bridge.add_calls == [("room-1@chatroom", "wxid_existing")]
    assert bridge.invite_calls == []
    assert bridge.remove_calls == []


def test_tracked_rejoin_keyword_is_not_starved_by_missing_contacts(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path)
    orphan_events = [
        keyword_event(sequence, wxid=f"wxid_missing_{sequence}")
        for sequence in range(3, 23)
    ]
    realtime = FakeRealtime([*orphan_events, keyword_event(23, wxid="wxid_existing")])
    bridge = FakeBridge(
        contacts=room_contacts(),
        rooms={"room-1@chatroom": [], "room-2@chatroom": []},
        join_on_add=True,
    )
    automation = FriendAutomation(
        FakeRepository(realtime), settings, bridge, verification_delays=(0,)
    )
    automation.store.initialize(True, 0)
    seed_join_pending(automation.store, wxid="wxid_existing")
    automation.store.mark_assigned("v3_test@stranger", "room-1@chatroom", "47驿站1群")
    automation._last_auto_accept_at = time.monotonic()

    asyncio.run(automation.run_once())

    queued = read_lead(settings.friend_automation_path)
    assert queued["keyword_sequence"] == 23
    assert bridge.add_calls == [("room-1@chatroom", "wxid_existing")]

    asyncio.run(automation.run_once())

    lead = read_lead(settings.friend_automation_path)
    assert lead["state"] == "assigned"
    assert lead["room_id"] == "room-1@chatroom"
    assert bridge.invite_calls == []
    assert bridge.remove_calls == []


def test_new_keyword_resets_invite_pending_and_rechecks_all_groups(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path)
    realtime = FakeRealtime([keyword_event(3, wxid="wxid_existing")])
    bridge = FakeBridge(
        contacts=room_contacts(),
        rooms={
            "room-1@chatroom": [],
            "room-2@chatroom": [{"wxid": "wxid_existing"}],
        },
    )
    automation = FriendAutomation(FakeRepository(realtime), settings, bridge)
    automation.store.initialize(True, 0)
    seed_join_pending(automation.store, wxid="wxid_existing")
    automation.store.mark_invite_pending(
        "v3_test@stranger",
        "room-1@chatroom",
        "47驿站1群",
        30_000,
    )
    automation._last_auto_accept_at = time.monotonic()

    asyncio.run(automation.run_once())

    lead = read_lead(settings.friend_automation_path)
    assert lead["state"] == "assigned"
    assert lead["keyword_sequence"] == 3
    assert lead["room_id"] == "room-2@chatroom"
    assert lead["room_name"] == "47驿站2群"
    assert int(lead["retry_at_ms"]) == 0
    assert bridge.room_member_calls == ["room-1@chatroom", "room-2@chatroom"]
    assert bridge.add_calls == []
    assert bridge.invite_calls == []
    assert bridge.remove_calls == []


def test_non_friend_keyword_does_not_create_join_task(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path)
    realtime = FakeRealtime([keyword_event(1, wxid="wxid_not_friend")])
    bridge = FakeBridge(
        contacts=[
            {"wxid": "wxid_not_friend", "category": "official"},
            *room_contacts(),
        ],
        rooms={"room-1@chatroom": [], "room-2@chatroom": []},
    )
    automation = FriendAutomation(FakeRepository(realtime), settings, bridge)
    automation.store.initialize(True, 0)
    automation._last_auto_accept_at = time.monotonic()

    asyncio.run(automation.run_once())
    asyncio.run(automation.run_once())

    with sqlite3.connect(settings.friend_automation_path) as connection:
        leads = connection.execute("SELECT count(*) FROM friend_leads").fetchone()
        keywords = connection.execute(
            "SELECT count(*) FROM friend_keyword_events"
        ).fetchone()
    assert leads == (0,)
    assert keywords == (0,)
    assert bridge.sent_text == []
    assert bridge.add_calls == []
    assert bridge.invite_calls == []


def test_assigned_friend_can_request_join_again_after_removal(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path)
    realtime = FakeRealtime([keyword_event(1, wxid="wxid_existing")])
    bridge = FakeBridge(
        contacts=[
            {"wxid": "wxid_existing", "category": "friend"},
            *room_contacts(),
        ],
        rooms={"room-1@chatroom": [], "room-2@chatroom": []},
        join_on_add=True,
    )
    automation = FriendAutomation(
        FakeRepository(realtime), settings, bridge, verification_delays=(0,)
    )
    automation.store.initialize(True, 0)
    automation._last_auto_accept_at = time.monotonic()
    asyncio.run(automation.run_once())

    realtime.events.append(keyword_event(2, wxid="wxid_existing"))
    asyncio.run(automation.run_once())
    assert bridge.add_calls == [("room-1@chatroom", "wxid_existing")]

    bridge.rooms["room-1@chatroom"] = []
    realtime.events.append(keyword_event(3, wxid="wxid_existing"))
    asyncio.run(automation.run_once())

    lead = read_lead(settings.friend_automation_path, "existing_friend:wxid_existing")
    assert lead["state"] == "assigned"
    assert lead["keyword_sequence"] == 3
    assert bridge.add_calls == [
        ("room-1@chatroom", "wxid_existing"),
        ("room-1@chatroom", "wxid_existing"),
    ]
    assert bridge.remove_calls == []


def test_membership_audit_marks_leaver_and_next_keyword_rejoins(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path)
    realtime = FakeRealtime()
    bridge = FakeBridge(
        contacts=room_contacts(),
        rooms={"room-1@chatroom": [], "room-2@chatroom": []},
        join_on_add=True,
    )
    automation = FriendAutomation(
        FakeRepository(realtime),
        settings,
        bridge,
        verification_delays=(0,),
        membership_audit_interval=0,
    )
    automation.store.initialize(True, 0)
    seed_join_pending(automation.store, wxid="wxid_existing")
    automation.store.mark_assigned("v3_test@stranger", "room-1@chatroom", "47驿站1群")
    automation._last_auto_accept_at = time.monotonic()

    asyncio.run(automation.run_once())

    left = read_lead(settings.friend_automation_path)
    assert left["state"] == "left_group"
    assert bridge.add_calls == []

    realtime.events.append(keyword_event(3, wxid="wxid_existing"))
    asyncio.run(automation.run_once())

    lead = read_lead(settings.friend_automation_path)
    assert lead["state"] == "assigned"
    assert lead["keyword_sequence"] == 3
    assert lead["room_id"] == "room-1@chatroom"
    assert bridge.add_calls == [("room-1@chatroom", "wxid_existing")]


def test_membership_audit_removes_duplicate_created_after_assignment(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path)
    bridge = FakeBridge(
        contacts=room_contacts(),
        rooms={
            "room-1@chatroom": [{"wxid": "wxid_existing"}],
            "room-2@chatroom": [{"wxid": "wxid_existing"}],
        },
        remove_on_delete=True,
    )
    automation = FriendAutomation(
        FakeRepository(),
        settings,
        bridge,
        verification_delays=(0,),
        membership_audit_interval=0,
    )
    automation.store.initialize(True, 0)
    seed_join_pending(automation.store, wxid="wxid_existing")
    automation.store.mark_assigned("v3_test@stranger", "room-1@chatroom", "47驿站1群")
    automation._last_auto_accept_at = time.monotonic()

    asyncio.run(automation.run_once())

    lead = read_lead(settings.friend_automation_path)
    assert lead["state"] == "assigned"
    assert lead["room_id"] == "room-1@chatroom"
    assert bridge.rooms["room-1@chatroom"] == [{"wxid": "wxid_existing"}]
    assert bridge.rooms["room-2@chatroom"] == []
    assert bridge.remove_calls == [("room-2@chatroom", "wxid_existing")]


@pytest.mark.parametrize("state", ["join_pending", "waitlist"])
def test_new_keyword_immediately_retries_queued_join(
    tmp_path: Path, clock: Clock, state: str
) -> None:
    settings = make_settings(tmp_path)
    realtime = FakeRealtime([keyword_event(3, wxid="wxid_existing")])
    bridge = FakeBridge(
        contacts=room_contacts(),
        rooms={"room-1@chatroom": [], "room-2@chatroom": []},
        join_on_add=True,
    )
    automation = FriendAutomation(
        FakeRepository(realtime), settings, bridge, verification_delays=(0,)
    )
    automation.store.initialize(True, 0)
    seed_join_pending(automation.store, wxid="wxid_existing")
    update_lead(
        settings.friend_automation_path,
        "v3_test@stranger",
        state=state,
        retry_at_ms=clock.value + 60_000,
    )
    automation._last_auto_accept_at = time.monotonic()

    asyncio.run(automation.run_once())

    lead = read_lead(settings.friend_automation_path)
    assert lead["state"] == "assigned"
    assert lead["keyword_sequence"] == 3
    assert lead["room_id"] == "room-1@chatroom"
    assert bridge.add_calls == [("room-1@chatroom", "wxid_existing")]


def test_duplicate_membership_keeps_first_group_and_removes_later_group(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path)
    bridge = FakeBridge(
        contacts=room_contacts(),
        rooms={
            "room-1@chatroom": [{"wxid": "wxid_existing"}],
            "room-2@chatroom": [{"wxid": "wxid_existing"}],
        },
        remove_on_delete=True,
    )
    automation = FriendAutomation(
        FakeRepository(), settings, bridge, verification_delays=(0,)
    )
    automation.store.initialize(True, 0)
    seed_join_pending(automation.store, wxid="wxid_existing")
    automation._last_auto_accept_at = time.monotonic()

    asyncio.run(automation.run_once())
    asyncio.run(automation.run_once())

    lead = read_lead(settings.friend_automation_path)
    assert lead["state"] == "assigned"
    assert lead["room_id"] == "room-1@chatroom"
    assert lead["room_name"] == "47驿站1群"
    assert lead["cleanup_room_id"] is None
    assert bridge.remove_calls == [("room-2@chatroom", "wxid_existing")]
    assert bridge.add_calls == []
    assert bridge.invite_calls == []
    assert bridge.rooms["room-1@chatroom"] == [{"wxid": "wxid_existing"}]
    assert bridge.rooms["room-2@chatroom"] == []


def test_duplicate_created_by_direct_add_is_removed_during_reconciliation(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path)
    duplicate_join_calls: list[tuple[str, str]] = []

    def also_join_second_group(_: str, wxid: str) -> None:
        duplicate_join_calls.append(("room-2@chatroom", wxid))
        bridge._add_member("room-2@chatroom", wxid)

    bridge = FakeBridge(
        contacts=room_contacts(),
        rooms={"room-1@chatroom": [], "room-2@chatroom": []},
        join_on_add=True,
        add_hook=also_join_second_group,
        remove_on_delete=True,
    )
    automation = FriendAutomation(
        FakeRepository(), settings, bridge, verification_delays=(0,)
    )
    automation.store.initialize(True, 0)
    seed_join_pending(automation.store, wxid="wxid_existing")
    automation._last_auto_accept_at = time.monotonic()

    asyncio.run(automation.run_once())

    lead = read_lead(settings.friend_automation_path)
    assert lead["state"] == "assigned"
    assert lead["room_id"] == "room-1@chatroom"
    assert bridge.add_calls == [("room-1@chatroom", "wxid_existing")]
    assert duplicate_join_calls == [("room-2@chatroom", "wxid_existing")]
    assert bridge.remove_calls == [("room-2@chatroom", "wxid_existing")]
    assert bridge.rooms["room-1@chatroom"] == [{"wxid": "wxid_existing"}]
    assert bridge.rooms["room-2@chatroom"] == []
    assert bridge.invite_calls == []
    assert bridge.sent_at == [
        (
            "room-1@chatroom",
            "wxid_existing",
            "wxid_existing",
            settings.friend_automation_group_welcome,
        )
    ]

    asyncio.run(automation.run_once())

    assert bridge.remove_calls == [("room-2@chatroom", "wxid_existing")]
    assert bridge.add_calls == [("room-1@chatroom", "wxid_existing")]
    assert len(bridge.sent_at) == 1


def test_group_welcome_survives_duplicate_cleanup_retry(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path)
    remove_attempts = 0

    def also_join_second_group(_: str, wxid: str) -> None:
        bridge._add_member("room-2@chatroom", wxid)

    def fail_first_remove(_: str, __: str) -> None:
        nonlocal remove_attempts
        remove_attempts += 1
        if remove_attempts == 1:
            raise FriendAutomationError("uncertain duplicate cleanup")

    bridge = FakeBridge(
        contacts=room_contacts(),
        rooms={"room-1@chatroom": [], "room-2@chatroom": []},
        join_on_add=True,
        add_hook=also_join_second_group,
        remove_on_delete=True,
        remove_hook=fail_first_remove,
    )
    automation = FriendAutomation(
        FakeRepository(), settings, bridge, verification_delays=(0,)
    )
    automation.store.initialize(True, 0)
    seed_join_pending(automation.store, wxid="wxid_existing")
    automation._last_auto_accept_at = time.monotonic()

    asyncio.run(automation.run_once())

    pending = read_lead(settings.friend_automation_path)
    assert pending["state"] == "remove_pending"
    assert pending["group_welcome_state"] == "pending"
    assert bridge.sent_at == []

    clock.advance(1_001)
    asyncio.run(automation.run_once())

    assigned = read_lead(settings.friend_automation_path)
    assert assigned["state"] == "assigned"
    assert assigned["group_welcome_state"] == "sent"
    assert bridge.sent_at == [
        (
            "room-1@chatroom",
            "wxid_existing",
            "wxid_existing",
            settings.friend_automation_group_welcome,
        )
    ]


def test_duplicate_cleanup_preserves_only_remaining_membership_on_race(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path)

    def leave_canonical_before_removal(_: str) -> None:
        if bridge.room_member_calls == [
            "room-1@chatroom",
            "room-2@chatroom",
            "room-2@chatroom",
            "room-1@chatroom",
        ]:
            bridge._remove_member("room-1@chatroom", "wxid_existing")

    bridge = FakeBridge(
        contacts=room_contacts(),
        rooms={
            "room-1@chatroom": [{"wxid": "wxid_existing"}],
            "room-2@chatroom": [{"wxid": "wxid_existing"}],
        },
        room_members_hook=leave_canonical_before_removal,
        remove_on_delete=True,
    )
    automation = FriendAutomation(
        FakeRepository(), settings, bridge, verification_delays=(0,)
    )
    automation.store.initialize(True, 0)
    seed_join_pending(automation.store, wxid="wxid_existing")
    automation._last_auto_accept_at = time.monotonic()

    asyncio.run(automation.run_once())

    lead = read_lead(settings.friend_automation_path)
    assert lead["state"] == "assigned"
    assert lead["room_id"] == "room-2@chatroom"
    assert lead["room_name"] == "47驿站2群"
    assert bridge.rooms["room-1@chatroom"] == []
    assert bridge.rooms["room-2@chatroom"] == [{"wxid": "wxid_existing"}]
    assert bridge.remove_calls == []
    assert bridge.add_calls == []
    assert bridge.invite_calls == []


def test_failed_duplicate_removal_is_verified_and_retried(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path)
    remove_attempts = 0

    def fail_first_remove(_: str, __: str) -> None:
        nonlocal remove_attempts
        remove_attempts += 1
        if remove_attempts == 1:
            raise FriendAutomationError("uncertain remove result")

    bridge = FakeBridge(
        contacts=room_contacts(),
        rooms={
            "room-1@chatroom": [{"wxid": "wxid_existing"}],
            "room-2@chatroom": [{"wxid": "wxid_existing"}],
        },
        remove_on_delete=True,
        remove_hook=fail_first_remove,
    )
    automation = FriendAutomation(
        FakeRepository(), settings, bridge, verification_delays=(0,)
    )
    automation.store.initialize(True, 0)
    seed_join_pending(automation.store, wxid="wxid_existing")
    automation._last_auto_accept_at = time.monotonic()

    asyncio.run(automation.run_once())

    pending = read_lead(settings.friend_automation_path)
    assert pending["state"] == "remove_pending"
    assert pending["room_id"] == "room-1@chatroom"
    assert pending["cleanup_room_id"] == "room-2@chatroom"
    assert int(pending["retry_at_ms"]) > clock.value
    assert bridge.remove_calls == [("room-2@chatroom", "wxid_existing")]

    clock.advance(30_001)
    asyncio.run(automation.run_once())
    asyncio.run(automation.run_once())

    lead = read_lead(settings.friend_automation_path)
    assert lead["state"] == "assigned"
    assert lead["room_id"] == "room-1@chatroom"
    assert lead["cleanup_room_id"] is None
    assert bridge.remove_calls == [
        ("room-2@chatroom", "wxid_existing"),
        ("room-2@chatroom", "wxid_existing"),
    ]
    assert bridge.add_calls == []
    assert bridge.invite_calls == []


def test_restart_verifies_uncertain_removal_before_resubmitting(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path)
    store = FriendAutomationStore(settings.friend_automation_path)
    store.initialize(True, 0)
    seed_join_pending(store, wxid="wxid_existing")
    store.mark_remove_sending(
        "v3_test@stranger",
        "room-1@chatroom",
        "47驿站1群",
        "room-2@chatroom",
        "47驿站2群",
    )

    bridge = FakeBridge(
        contacts=room_contacts(),
        rooms={
            "room-1@chatroom": [{"wxid": "wxid_existing"}],
            "room-2@chatroom": [],
        },
        remove_on_delete=True,
    )
    automation = FriendAutomation(FakeRepository(), settings, bridge)
    automation.store.initialize(True, 0)
    recovered = read_lead(settings.friend_automation_path)
    assert recovered["state"] == "remove_pending"
    assert int(recovered["retry_at_ms"]) > clock.value
    assert "uncertain after restart" in recovered["last_error"]

    clock.advance(5_001)
    automation._last_auto_accept_at = time.monotonic()
    asyncio.run(automation.run_once())

    lead = read_lead(settings.friend_automation_path)
    assert lead["state"] == "assigned"
    assert lead["room_id"] == "room-1@chatroom"
    assert lead["cleanup_room_id"] is None
    assert bridge.room_member_calls[0] == "room-2@chatroom"
    assert bridge.remove_calls == []
    assert bridge.add_calls == []
    assert bridge.invite_calls == []


def test_end_to_end_waits_for_keyword_sent_after_welcome(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path)
    request = friend_event(1, candidate_username="wxid_new")
    early_keyword = keyword_event(2)
    realtime = FakeRealtime([request, early_keyword])
    bridge = FakeBridge(
        contacts=[
            {
                "wxid": "wxid_new",
                "v3": "v3_test@stranger",
                "category": "friend",
            },
            *room_contacts(),
        ],
        rooms={"room-1@chatroom": [], "room-2@chatroom": []},
        join_on_add=True,
    )
    automation = FriendAutomation(
        FakeRepository(realtime), settings, bridge, verification_delays=(0,)
    )
    automation.store.initialize(True, 0)
    automation._last_auto_accept_at = time.monotonic()

    asyncio.run(automation.run_once())

    lead = read_lead(settings.friend_automation_path)
    assert lead["state"] == "awaiting_keyword"
    assert lead["wxid"] == "wxid_new"
    assert lead["keyword_sequence"] is None
    assert bridge.sent_text == [("wxid_new", "回复“进群”加入群聊")]
    assert bridge.add_calls == []
    with sqlite3.connect(settings.friend_automation_path) as connection:
        assert connection.execute("SELECT count(*) FROM friend_leads").fetchone() == (
            1,
        )

    realtime.events.append(keyword_event(3))
    asyncio.run(automation.run_once())

    lead = read_lead(settings.friend_automation_path)
    assert lead["state"] == "assigned"
    assert lead["keyword_sequence"] == 3
    assert lead["room_id"] == "room-1@chatroom"
    assert bridge.add_calls == [("room-1@chatroom", "wxid_new")]


def test_group_welcome_waits_for_confirmed_membership_and_mentions_new_member(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path, group_count=1)
    bridge = FakeBridge(
        contacts=[
            {
                "wxid": "wxid_new",
                "nickname": "新成员",
                "category": "friend",
            },
            *room_contacts(1),
        ],
        rooms={"room-1@chatroom": []},
    )
    automation = FriendAutomation(
        FakeRepository(), settings, bridge, verification_delays=(0,)
    )
    automation.store.initialize(True, 0)
    seed_join_pending(automation.store)
    automation._last_auto_accept_at = time.monotonic()

    asyncio.run(automation.run_once())

    pending = read_lead(settings.friend_automation_path)
    assert pending["state"] == "invite_pending"
    assert pending["group_welcome_state"] == ""
    assert bridge.sent_at == []

    bridge._add_member("room-1@chatroom", "wxid_new")
    clock.advance(30_001)
    asyncio.run(automation.run_once())

    assigned = read_lead(settings.friend_automation_path)
    assert assigned["state"] == "assigned"
    assert assigned["group_welcome_state"] == "sent"
    assert assigned["group_welcome_sent_at_ms"] is not None
    assert bridge.sent_at == [
        (
            "room-1@chatroom",
            "wxid_new",
            "新成员",
            settings.friend_automation_group_welcome,
        )
    ]

    automation._membership_audit_interval = 0
    asyncio.run(automation.run_once())
    assert len(bridge.sent_at) == 1


def test_group_welcome_failure_is_retried(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path, group_count=1)
    failed_once = False

    def fail_first_send(_: str, __: str, ___: str, ____: str) -> None:
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise FriendAutomationError("temporary mention send failure")

    bridge = FakeBridge(
        contacts=[
            {"wxid": "wxid_new", "nickname": "新成员", "category": "friend"},
            *room_contacts(1),
        ],
        rooms={"room-1@chatroom": [{"wxid": "wxid_new"}]},
        send_at_hook=fail_first_send,
    )
    automation = FriendAutomation(FakeRepository(), settings, bridge)
    automation.store.initialize(True, 0)
    seed_join_pending(automation.store)
    automation.store.mark_assigned(
        "v3_test@stranger",
        "room-1@chatroom",
        "47驿站1群",
        queue_group_welcome=True,
    )
    automation._last_auto_accept_at = time.monotonic()

    asyncio.run(automation.run_once())

    failed = read_lead(settings.friend_automation_path)
    assert failed["group_welcome_state"] == "pending"
    assert int(failed["group_welcome_retry_at_ms"]) > clock.value
    assert "temporary mention send failure" in failed["group_welcome_error"]

    clock.advance(1_001)
    asyncio.run(automation.run_once())

    recovered = read_lead(settings.friend_automation_path)
    assert recovered["group_welcome_state"] == "sent"
    assert len(bridge.sent_at) == 2


def test_rejoin_queues_a_fresh_group_welcome(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path, group_count=1)
    realtime = FakeRealtime()
    bridge = FakeBridge(
        contacts=[
            {"wxid": "wxid_existing", "nickname": "回归成员", "category": "friend"},
            *room_contacts(1),
        ],
        rooms={"room-1@chatroom": [{"wxid": "wxid_existing"}]},
    )
    automation = FriendAutomation(
        FakeRepository(realtime), settings, bridge, verification_delays=(0,)
    )
    automation.store.initialize(True, 0)
    seed_join_pending(automation.store, wxid="wxid_existing")
    automation.store.mark_assigned(
        "v3_test@stranger",
        "room-1@chatroom",
        "47驿站1群",
        queue_group_welcome=True,
    )
    automation._last_auto_accept_at = time.monotonic()
    asyncio.run(automation.run_once())
    assert len(bridge.sent_at) == 1

    bridge.rooms["room-1@chatroom"] = []
    realtime.events.append(keyword_event(3, wxid="wxid_existing"))
    asyncio.run(automation.run_once())
    assert read_lead(settings.friend_automation_path)["state"] == "invite_pending"

    bridge._add_member("room-1@chatroom", "wxid_existing")
    clock.advance(30_001)
    asyncio.run(automation.run_once())

    lead = read_lead(settings.friend_automation_path)
    assert lead["state"] == "assigned"
    assert lead["group_welcome_state"] == "sent"
    assert len(bridge.sent_at) == 2


def test_restart_does_not_resend_uncertain_group_welcome(
    tmp_path: Path, clock: Clock
) -> None:
    settings = make_settings(tmp_path, group_count=1)
    store = FriendAutomationStore(settings.friend_automation_path)
    store.initialize(True, 0)
    seed_join_pending(store)
    store.mark_assigned(
        "v3_test@stranger",
        "room-1@chatroom",
        "47驿站1群",
        queue_group_welcome=True,
    )
    assert store.mark_group_welcome_sending("v3_test@stranger") is True

    bridge = FakeBridge(
        contacts=[
            {"wxid": "wxid_new", "nickname": "新成员", "category": "friend"},
            *room_contacts(1),
        ],
        rooms={"room-1@chatroom": [{"wxid": "wxid_new"}]},
    )
    automation = FriendAutomation(FakeRepository(), settings, bridge)
    automation.store.initialize(True, 0)

    recovered = read_lead(settings.friend_automation_path)
    assert recovered["group_welcome_state"] == "sent"
    assert recovered["group_welcome_sent_at_ms"] is not None
    assert "uncertain after restart" in recovered["group_welcome_error"]

    automation._last_auto_accept_at = time.monotonic()
    asyncio.run(automation.run_once())
    assert bridge.sent_at == []
