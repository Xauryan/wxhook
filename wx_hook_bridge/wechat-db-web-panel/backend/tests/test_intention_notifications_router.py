from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import intention_notifications_router as module


class FakeService:
    def __init__(self) -> None:
        self.configured: dict[str, Any] | None = None

    async def status(self) -> dict[str, Any]:
        return {
            "config": {
                "enabled": bool(self.configured and self.configured["enabled"]),
                "consumer_id": "yunsheng-wx-robot-01",
                "token_configured": True,
                "target_groups": (self.configured or {}).get("target_groups", []),
            },
            "runtime": {"state": "disabled", "running": True},
            "today": {
                "date": "2026-09-03",
                "total": 0,
                "sent": 0,
                "pending": 0,
                "retrying": 0,
            },
            "recent": [],
        }

    async def available_groups(self) -> list[dict[str, str]]:
        return [{"room_id": "room-1@chatroom", "name": "通知一群"}]

    async def configure(self, **values: Any) -> dict[str, Any]:
        self.configured = values
        return values


def test_notification_routes_expose_status_groups_and_config(
    monkeypatch,
) -> None:
    fake = FakeService()
    monkeypatch.setattr(module, "service", fake)
    app = FastAPI()
    app.include_router(module.router)

    with TestClient(app) as client:
        status = client.get("/api/intention-notifications")
        groups = client.get("/api/intention-notifications/groups")
        configured = client.post(
            "/api/intention-notifications/config",
            json={
                "enabled": True,
                "consumer_id": "yunsheng-wx-robot-01",
                "api_token": "secret-token",
                "startup_policy": "new_only",
                "target_groups": [{"room_id": "room-1@chatroom", "name": "通知一群"}],
            },
        )

    assert status.status_code == 200
    assert groups.json() == {
        "items": [{"room_id": "room-1@chatroom", "name": "通知一群"}],
        "total": 1,
    }
    assert configured.status_code == 200
    assert fake.configured is not None
    assert fake.configured["api_token"] == "secret-token"
    assert "secret-token" not in configured.text


def test_notification_config_rejects_invalid_consumer_id(monkeypatch) -> None:
    monkeypatch.setattr(module, "service", FakeService())
    app = FastAPI()
    app.include_router(module.router)

    with TestClient(app) as client:
        response = client.post(
            "/api/intention-notifications/config",
            json={
                "enabled": False,
                "consumer_id": "invalid consumer",
                "target_groups": [],
            },
        )

    assert response.status_code == 422
