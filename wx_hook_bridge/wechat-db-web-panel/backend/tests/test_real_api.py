import pytest
import sqlite3
from datetime import datetime
from urllib.parse import quote
from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.app.database import open_readonly
from backend.app.main import app


pytestmark = [
    pytest.mark.real_snapshot,
    pytest.mark.skipif(
        not (settings.database_root / "message" / "message_0.db").is_file(),
        reason="local decrypted snapshot is unavailable",
    ),
]


def test_real_snapshot_api_and_fts_integrity() -> None:
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["message_count"] == 48_092

        profile = client.get("/api/profile").json()
        assert profile["profile"]["username"]
        assert profile["stats"]["contacts"] > 0

        message_search = client.get("/api/search", params={"q": "项目", "limit": 5})
        assert message_search.status_code == 200
        assert message_search.json()["fts_used"] is True

        pinyin_search = client.get(
            "/api/search",
            params={"q": "su shi qi", "scope": "contacts", "limit": 5},
        )
        assert pinyin_search.status_code == 200
        assert pinyin_search.json()["fts_used"] is True

        checks = client.get("/api/fts/validate").json()
        assert all(value["quick_check"] == ["ok"] for value in checks.values())
        assert all(value["integrity_check"] == ["ok"] for value in checks.values())

        media = client.get("/api/media", params={"limit": 5}).json()
        forbidden = {"lt", "gt", "cdata", "type", "version", "msgsource"}
        words = {item["word"].casefold() for item in media["keywords"]}
        assert words.isdisjoint(forbidden)
        assert all(any(character.isalpha() for character in word) for word in words)
        assert media["keyword_basis"]["documents"] > 0


def test_source_database_uri_is_not_cache_path() -> None:
    assert settings.database_root.resolve() != settings.cache_path.parent.resolve()
    assert settings.cache_path.is_relative_to(settings.panel_root / ".cache")


def test_source_connection_rejects_writes() -> None:
    with open_readonly(settings.database_root / "contact" / "contact.db") as connection:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("CREATE TABLE codex_must_not_write(value INTEGER)")


def test_conversation_hour_filter_and_export_preserve_current_filters() -> None:
    with TestClient(app) as client:
        overview = client.get("/api/conversations", params={"limit": 1}).json()
        peak = max(overview["hourly"], key=lambda item: item["count"])
        hour = peak["hour"]

        filtered_response = client.get(
            "/api/conversations", params={"hour": hour, "limit": 5}
        )
        assert filtered_response.status_code == 200
        filtered = filtered_response.json()
        assert filtered["total"] > 0
        for episode in filtered["items"][:3]:
            context = client.get(f"/api/conversations/{episode['id']}").json()
            assert any(
                datetime.fromisoformat(message["create_time"]).hour == hour
                for message in context["messages"]
            )

        assert client.get("/api/conversations", params={"hour": 24}).status_code == 422

        search = client.get(
            "/api/search", params={"q": "项目", "scope": "messages", "limit": 10}
        ).json()
        hit = next(item for item in search["items"] if item["kind"] == "message")
        hit_hour = datetime.fromisoformat(hit["create_time"]).hour
        exported_response = client.get(
            "/api/conversations/export",
            params={
                "format": "json",
                "q": "项目",
                "hour": hit_hour,
                "session": hit["session_username"],
                "order": "desc",
            },
        )
        assert exported_response.status_code == 200
        exported = exported_response.json()
        assert exported
        assert all(
            datetime.fromisoformat(row["create_time_iso"]).hour == hit_hour
            for row in exported
        )
        assert all("项目" in f"{row['summary']} {row['content']}" for row in exported)
        timestamps = [row["create_time"] for row in exported]
        assert timestamps == sorted(timestamps, reverse=True)


def test_message_endpoint_exposes_bounded_quote_chain_and_episode_links() -> None:
    with TestClient(app) as client:
        conversations = client.get(
            "/api/conversations", params={"limit": 1}
        ).json()
        original = conversations["top_quoted"][0]

        response = client.get(f"/api/messages/{original['id']}")
        assert response.status_code == 200
        payload = response.json()
        assert payload["episode_id"]
        assert payload["quote_chain_limit"] == 50
        assert 0 < len(payload["quoting_messages"]) <= 50

        quoting = payload["quoting_messages"][0]
        assert quoting["id"]
        assert quoting["session_username"]
        assert quoting["date"]
        assert "summary" in quoting
        assert quoting["episode_id"]

        quoting_response = client.get(f"/api/messages/{quoting['id']}")
        assert quoting_response.status_code == 200
        quoting_payload = quoting_response.json()
        assert quoting_payload["episode_id"] == quoting["episode_id"]
        assert quoting_payload["referenced_message"]["id"] == original["id"]
        assert (
            quoting_payload["referenced_message"]["episode_id"]
            == payload["episode_id"]
        )


def test_chat_list_uses_real_sessions_messages_and_daily_metrics() -> None:
    with TestClient(app) as client:
        response = client.get("/api/chat-list", params={"limit": 8})
        assert response.status_code == 200
        listing = response.json()
        assert listing["total"] == listing["counts"]["all"]
        assert listing["counts"]["groups"] + listing["counts"]["contacts"] == listing["counts"]["all"]
        assert listing["items"]
        timestamps = [datetime.fromisoformat(item["last_active"]) for item in listing["items"]]
        assert timestamps == sorted(timestamps, reverse=True)

        first = listing["items"][0]
        detail_response = client.get(
            f"/api/chat-list/{quote(first['username'], safe='')}",
            params={"limit": 40},
        )
        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert detail["session"]["username"] == first["username"]
        assert detail["stats"]["messages"] == first["message_count"]
        assert 0 < len(detail["messages"]) <= 40
        message_times = [datetime.fromisoformat(item["create_time"]) for item in detail["messages"]]
        assert message_times == sorted(message_times)
        assert all(point["date"].startswith(str(detail["year"])) for point in detail["heatmap"])

        selected_date = detail["heatmap"][-1]["date"]
        selected = client.get(
            f"/api/chat-list/{quote(first['username'], safe='')}",
            params={"limit": 20, "year": detail["year"], "date": selected_date},
        ).json()
        assert selected["selected_day"]["date"] == selected_date
        assert selected["selected_day"]["messages"] == detail["heatmap"][-1]["count"]

        contacts_only = client.get(
            "/api/chat-list", params={"scope": "contacts", "limit": 20}
        ).json()
        assert contacts_only["items"]
        assert all(not item["is_group"] for item in contacts_only["items"])
