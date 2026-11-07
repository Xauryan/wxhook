from __future__ import annotations

import json
import shutil
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

import backend.app.main as main_module
from backend.app.config import Settings, settings
from backend.app.main import app
from backend.app.realtime import (
    REALTIME_BODY_LIMIT,
    RealtimeStore,
    RealtimeValidationError,
    normalize_event,
)
from backend.app.repository import WeChatRepository


def make_repository(tmp_path: Path) -> WeChatRepository:
    main_module.repository.ensure_cache()
    panel_root = tmp_path / "panel"
    cache_path = panel_root / ".cache" / "analytics.sqlite"
    cache_path.parent.mkdir(parents=True)
    shutil.copy2(settings.cache_path, cache_path)
    config = Settings(
        panel_root=panel_root,
        repository_root=settings.repository_root,
        database_root=settings.database_root,
        report_path=settings.report_path,
        cache_path=cache_path,
        fts_extension=settings.fts_extension,
        weixin_dll=settings.weixin_dll,
        timezone=settings.timezone,
    )
    return WeChatRepository(config)


@pytest.fixture
def realtime_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repository = make_repository(tmp_path)
    monkeypatch.setattr(main_module, "repository", repository)
    return repository


def native_event(
    event_id: str,
    *,
    message_id: int = 9_900_000_001,
    local_id: int = 900_001,
    content: str = "realtimeuniquetoken message",
    peer_id: str = "wxid_realtime_test",
) -> dict:
    return {
        "event_id": event_id,
        "event_type": "message.added",
        "source": "wx_hook_bridge",
        "schema_version": 1,
        "msgId": str(message_id),
        "localId": str(local_id),
        "type": "1",
        "subType": "0",
        "timestamp_unix": int(time.time()),
        "direction": "incoming",
        "isIncoming": True,
        "peerWxId": peer_id,
        "senderWxId": peer_id,
        "selfWxId": "wxid_self",
        "rawFrom": peer_id,
        "rawTo": "wxid_self",
        "content": content,
        "signature": "pytest",
    }


# Materializing through the reconcile loop into a full real-snapshot cache
# (~4 GB) regularly takes more than 10 s.
def wait_until(predicate, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition did not become true before timeout")


def test_normalize_native_envelope_and_legacy_aliases(
    realtime_repository: WeChatRepository,
) -> None:
    prepared = normalize_event(
        native_event("normalize-event"),
        realtime_repository.account,
        realtime_repository.timezone,
    )
    message = prepared["normalized"]
    assert prepared["event_type"] == "message.added"
    assert prepared["quality"]["state"] == "ready"
    assert message["server_id"] == 9_900_000_001
    assert message["local_id"] == 900_001
    assert message["local_type"] == 1
    assert message["base_type"] == 1
    assert message["app_subtype"] == 0
    assert message["session_username"] == "wxid_realtime_test"


def test_normalize_rejects_event_id_longer_than_contract_limit(
    realtime_repository: WeChatRepository,
) -> None:
    with pytest.raises(RealtimeValidationError, match="exceeds 200 characters"):
        normalize_event(
            native_event("x" * 201),
            realtime_repository.account,
            realtime_repository.timezone,
        )


def test_normalize_infers_direction_from_account_and_raw_route(
    realtime_repository: WeChatRepository,
) -> None:
    outgoing = native_event(
        "infer-outgoing", message_id=9_900_000_011, local_id=900_011
    )
    for key in ("peerWxId", "senderWxId"):
        outgoing.pop(key, None)
    outgoing["direction"] = "unknown"
    outgoing["isIncoming"] = False
    outgoing["is_outgoing"] = False
    outgoing["selfWxId"] = ""
    outgoing["self_identity_known"] = False
    outgoing["rawFrom"] = realtime_repository.account
    outgoing["rawTo"] = "wxid_direction_peer"
    prepared = normalize_event(
        outgoing, realtime_repository.account, realtime_repository.timezone
    )
    assert prepared["quality"]["state"] == "ready"
    assert prepared["normalized"]["session_username"] == "wxid_direction_peer"
    assert prepared["normalized"]["sender_username"] == realtime_repository.account
    assert prepared["normalized"]["is_self"] == 1

    incoming = {
        **outgoing,
        "event_id": "infer-incoming",
        "msgId": "9900000012",
        "localId": "900012",
        "rawFrom": "wxid_direction_peer",
        "rawTo": realtime_repository.account,
    }
    prepared = normalize_event(
        incoming, realtime_repository.account, realtime_repository.timezone
    )
    assert prepared["quality"]["state"] == "ready"
    assert prepared["normalized"]["session_username"] == "wxid_direction_peer"
    assert prepared["normalized"]["sender_username"] == "wxid_direction_peer"
    assert prepared["normalized"]["is_self"] == 0


def test_ingest_materializes_for_all_read_models_and_is_idempotent(
    realtime_repository: WeChatRepository,
) -> None:
    with TestClient(app) as client:
        before = client.get("/api/health").json()["message_count"]
        event = native_event("materialize-event")
        response = client.post("/api/realtime/ingest", json=event)
        assert response.status_code == 201
        applied = response.json()
        assert applied["status"] == "pending"
        assert applied["duplicate"] is False
        wait_until(
            lambda: realtime_repository.realtime.event(applied["sequence"])["status"]
            == "applied"
        )

        same_event = client.post("/api/realtime/ingest", json=event)
        assert same_event.status_code == 200
        assert same_event.json()["dedupe_basis"] == "event_id"

        same_message = client.post(
            "/api/realtime/ingest", json={**event, "event_id": "other-event-id"}
        )
        assert same_message.status_code == 200
        assert same_message.json()["dedupe_basis"] == "message_identity"
        wait_until(
            lambda: client.get("/api/health").json()["message_count"] == before + 1
        )

        overview = client.get("/api/overview").json()
        assert overview["kpis"]["messages"] == before + 1
        conversations = client.get(
            "/api/conversations", params={"session": "wxid_realtime_test"}
        ).json()
        assert conversations["total"] == 1
        chats = client.get(
            "/api/chat-list", params={"q": "wxid_realtime_test"}
        ).json()
        assert chats["total"] == 1
        search = client.get(
            "/api/search",
            params={"q": "realtimeuniquetoken", "scope": "messages"},
        ).json()
        assert any("realtimeuniquetoken" in item["snippet"] for item in search["items"])


def test_event_and_message_identity_are_scoped_by_session(
    realtime_repository: WeChatRepository,
) -> None:
    with TestClient(app) as client:
        before = client.get("/api/health").json()["message_count"]
        first = client.post(
            "/api/realtime/ingest",
            json=native_event(
                "same-native-event",
                message_id=9_900_000_101,
                local_id=901_101,
                peer_id="wxid_session_a",
            ),
        )
        second = client.post(
            "/api/realtime/ingest",
            json=native_event(
                "same-native-event",
                message_id=9_900_000_101,
                local_id=901_101,
                peer_id="wxid_session_b",
            ),
        )
        assert first.status_code == 201
        assert second.status_code == 201
        assert second.json()["duplicate"] is False
        wait_until(
            lambda: client.get("/api/health").json()["message_count"] == before + 2
        )


def test_realtime_ledger_is_scoped_by_account_and_snapshot(tmp_path: Path) -> None:
    path = tmp_path / ".cache" / "realtime.sqlite"
    timezone = ZoneInfo("Asia/Shanghai")
    database_root = tmp_path / "db"
    first = RealtimeStore(
        path,
        account="account-a",
        snapshot="snapshot-a",
        database_root=database_root,
        timezone=timezone,
    )
    assert first.ingest(native_event("scoped-event"))["duplicate"] is False

    other_snapshot = RealtimeStore(
        path,
        account="account-a",
        snapshot="snapshot-b",
        database_root=database_root,
        timezone=timezone,
    )
    other_account = RealtimeStore(
        path,
        account="account-b",
        snapshot="snapshot-a",
        database_root=database_root,
        timezone=timezone,
    )
    other_database = RealtimeStore(
        path,
        account="account-a",
        snapshot="snapshot-a",
        database_root=tmp_path / "other-db",
        timezone=timezone,
    )
    assert other_snapshot.status()["total"] == 1
    assert other_account.status()["total"] == 0
    assert other_database.status()["total"] == 0
    assert other_snapshot.ingest(native_event("scoped-event"))["duplicate"] is True
    assert other_account.ingest(native_event("scoped-event"))["duplicate"] is False
    assert other_database.ingest(native_event("scoped-event"))["duplicate"] is False
    assert first.status()["total"] == 1


def test_pending_event_is_durable_but_not_materialized(
    realtime_repository: WeChatRepository,
) -> None:
    with TestClient(app) as client:
        before = client.get("/api/health").json()["message_count"]
        response = client.post(
            "/api/realtime/ingest",
            json={"event_id": "pending-event", "event_type": "bridge.raw", "content": "?"},
        )
        assert response.status_code == 201
        payload = response.json()
        assert payload["status"] == "pending"
        assert payload["quality"]["state"] == "pending"
        assert payload["quality"]["errors"]
        assert client.get("/api/health").json()["message_count"] == before
        status = client.get("/api/realtime/status").json()
        assert status["counts"]["pending"] == 1


def test_sse_replay_last_event_id_and_reset(
    realtime_repository: WeChatRepository,
) -> None:
    with TestClient(app) as client:
        first = client.post(
            "/api/realtime/ingest", json=native_event("sse-1", local_id=910_001)
        ).json()
        second = client.post(
            "/api/realtime/ingest",
            json=native_event("sse-2", message_id=9_900_000_002, local_id=910_002),
        ).json()
        third = client.post(
            "/api/realtime/ingest",
            json=native_event("sse-3", message_id=9_900_000_003, local_id=910_003),
        ).json()

        wait_until(
            lambda: all(
                realtime_repository.realtime.event(item["sequence"])["status"]
                == "applied"
                for item in (first, second, third)
            )
        )

        replay = client.get("/api/realtime/events", params={"after": 0, "once": True})
        assert replay.status_code == 200
        assert "event: realtime.message" in replay.text
        assert f"id: {first['sequence']}" in replay.text

        resumed = client.get(
            "/api/realtime/events",
            params={"once": True},
            headers={"Last-Event-ID": str(first["sequence"])},
        )
        assert f"id: {first['sequence']}\n" not in resumed.text
        assert f"id: {second['sequence']}" in resumed.text

        with realtime_repository.realtime._lock, realtime_repository.realtime._connection() as connection:
            connection.execute(
                "DELETE FROM realtime_events WHERE sequence<?", (third["sequence"],)
            )
            connection.commit()
        reset = client.get(
            "/api/realtime/events",
            params={"after": first["sequence"], "once": True},
        )
        assert "event: realtime.reset" in reset.text
        assert '"event_type":"realtime.reset"' in reset.text
        assert '"event_id":"realtime-reset"' in reset.text

        ahead = client.get(
            "/api/realtime/events",
            params={"after": third["sequence"] + 1000, "once": True},
        )
        assert "event: realtime.reset" in ahead.text
        assert "ahead of the published event ledger" in ahead.text


def test_sse_waits_for_pending_materialization_and_duplicate_retries_it(
    realtime_repository: WeChatRepository,
) -> None:
    first_event = native_event(
        "ordered-pending", message_id=9_900_000_201, local_id=902_201
    )
    first = realtime_repository.realtime.ingest(first_event)
    assert first["status"] == "accepted"
    second_event = native_event(
        "ordered-applied", message_id=9_900_000_202, local_id=902_202
    )
    second = realtime_repository.realtime.ingest(second_event)
    assert realtime_repository._materialize_realtime_message(second["normalized"])
    realtime_repository.realtime.mark(second["sequence"], "applied")
    assert realtime_repository.realtime_events_since(0) == []

    retried = realtime_repository.ingest_realtime(first_event)
    assert retried["duplicate"] is True
    assert retried["status"] == "applied"
    rows = realtime_repository.realtime_events_since(0)
    assert [row["sequence"] for row in rows] == [
        first["sequence"],
        second["sequence"],
    ]


def test_malformed_pending_does_not_block_later_applied_event(
    realtime_repository: WeChatRepository,
) -> None:
    malformed = realtime_repository.realtime.ingest(
        {"event_id": "malformed-pending", "event_type": "bridge.raw"}
    )
    assert malformed["normalized"] is None
    applied = realtime_repository.ingest_realtime(
        native_event(
            "after-malformed", message_id=9_900_000_211, local_id=902_211
        )
    )
    assert applied["status"] == "applied"
    rows = realtime_repository.realtime_events_since(0)
    assert [row["sequence"] for row in rows] == [applied["sequence"]]


def test_materialization_failure_is_recovered_by_duplicate_retry(
    realtime_repository: WeChatRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    event = native_event(
        "retry-materialization", message_id=9_900_000_221, local_id=902_221
    )
    materialize = realtime_repository._materialize_realtime_message

    def fail_once(_: dict) -> bool:
        raise sqlite3.OperationalError("synthetic materialization failure")

    monkeypatch.setattr(realtime_repository, "_materialize_realtime_message", fail_once)
    pending = realtime_repository.ingest_realtime(event)
    assert pending["status"] == "pending"
    assert realtime_repository.realtime_events_since(0) == []

    monkeypatch.setattr(realtime_repository, "_materialize_realtime_message", materialize)
    recovered = realtime_repository.ingest_realtime(event)
    assert recovered["duplicate"] is True
    assert recovered["status"] == "applied"
    assert [row["sequence"] for row in realtime_repository.realtime_events_since(0)] == [
        recovered["sequence"]
    ]


def test_body_limit_and_loopback_guard(realtime_repository: WeChatRepository) -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/realtime/ingest",
            content=b"x" * (REALTIME_BODY_LIMIT + 1),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/realtime/ingest",
            "headers": [],
            "client": ("203.0.113.10", 12345),
            "server": ("127.0.0.1", 8000),
            "scheme": "http",
        }
    )
    with pytest.raises(HTTPException) as error:
        main_module._loopback_request(request)
    assert error.value.status_code == 403


def test_ingest_requires_json_and_rejects_nonlocal_origin(
    realtime_repository: WeChatRepository,
) -> None:
    event = native_event(
        "header-guard-event", message_id=9_900_000_301, local_id=903_301
    )
    with TestClient(app) as client:
        wrong_type = client.post(
            "/api/realtime/ingest",
            content=json.dumps(event),
            headers={"Content-Type": "text/plain"},
        )
        assert wrong_type.status_code == 415

        foreign_origin = client.post(
            "/api/realtime/ingest",
            json=event,
            headers={"Origin": "https://example.test"},
        )
        assert foreign_origin.status_code == 403

        unknown_local_port = client.post(
            "/api/realtime/ingest",
            json=event,
            headers={"Origin": "http://localhost:9999"},
        )
        assert unknown_local_port.status_code == 403

        local_origin = client.post(
            "/api/realtime/ingest",
            json=event,
            headers={"Origin": "http://127.0.0.1:5173"},
        )
        assert local_origin.status_code == 201


@pytest.mark.real_snapshot
def test_replay_recovers_event_accepted_before_materialization(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    before = repository.health()["message_count"]
    accepted = repository.realtime.ingest(
        native_event("replay-event", local_id=920_001, content="replayrecoverytoken")
    )
    assert accepted["status"] == "accepted"

    restarted = WeChatRepository(repository.settings)
    assert restarted.replay_realtime() == 1
    assert restarted.health()["message_count"] == before + 1
    assert restarted.realtime.event(accepted["sequence"])["status"] == "applied"
    results = restarted.search("replayrecoverytoken", scope="messages", limit=5)
    assert any("replayrecoverytoken" in item["snippet"] for item in results["items"])


@pytest.mark.real_snapshot
def test_concurrent_message_identity_deduplication(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    before = repository.health()["message_count"]

    def ingest(index: int) -> dict:
        return repository.ingest_realtime(
            native_event(f"concurrent-{index}", local_id=930_001)
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(ingest, range(8)))
    assert sum(not result["duplicate"] for result in results) == 1
    assert repository.health()["message_count"] == before + 1
