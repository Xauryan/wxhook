from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import mp_tools_router
from backend.app.mp_tokens import (
    BrowserEventLogTailer,
    MPTokenCaptureService,
    MPTokenStore,
)


def test_token_router_ingests_cdp_without_exposing_values(
    tmp_path: Path, monkeypatch
) -> None:
    store = MPTokenStore(tmp_path / "tokens.json")
    capture = MPTokenCaptureService(store)
    tailer = BrowserEventLogTailer(tmp_path / "events.jsonl", capture)
    monkeypatch.setattr(mp_tools_router, "token_store", store)
    monkeypatch.setattr(mp_tools_router, "token_capture", capture)
    monkeypatch.setattr(mp_tools_router, "browser_event_tailer", tailer)

    app = FastAPI()
    app.include_router(mp_tools_router.router)
    with TestClient(app) as client:
        response = client.post(
            "/api/mp/tokens/cdp",
            json={
                "method": "Network.requestWillBeSent",
                "params": {
                    "request": {
                        "url": "https://mp.weixin.qq.com/s?key=router-key&uin=88",
                        "headers": {"Cookie": "pass_ticket=router-ticket"},
                    }
                },
            },
        )
        assert response.status_code == 200
        assert set(response.json()["captured"]) == {"key", "uin", "pass_ticket"}
        assert "router-key" not in response.text
        assert "router-ticket" not in response.text

        status = client.get("/api/mp/tokens")
        assert status.status_code == 200
        assert status.json()["token"]["complete"] is True
        assert "native_browser_event_log" not in status.json()["sources"]

        cleared = client.delete("/api/mp/tokens")
        assert cleared.status_code == 200
        assert cleared.json()["token"]["available"] is False


def test_token_router_ingests_bounded_memory_snapshot_and_enforces_limit(
    tmp_path: Path, monkeypatch
) -> None:
    store = MPTokenStore(tmp_path / "tokens.json")
    capture = MPTokenCaptureService(store)
    tailer = BrowserEventLogTailer(tmp_path / "events.jsonl", capture)
    monkeypatch.setattr(mp_tools_router, "token_store", store)
    monkeypatch.setattr(mp_tools_router, "token_capture", capture)
    monkeypatch.setattr(mp_tools_router, "browser_event_tailer", tailer)

    app = FastAPI()
    app.include_router(mp_tools_router.router)
    with TestClient(app) as client:
        response = client.post(
            "/api/mp/tokens/memory",
            content=b"prefix&key=memory-router-key&uin=91&pass_ticket=memory-router-ticket",
            headers={"content-type": "application/octet-stream"},
        )
        assert response.status_code == 200
        assert set(response.json()["captured"]) == {"key", "uin", "pass_ticket"}
        assert "memory-router-key" not in response.text
        assert "memory-router-ticket" not in response.text

        unsupported = client.post(
            "/api/mp/tokens/memory",
            content=b"key=wrong-media",
            headers={"content-type": "application/json"},
        )
        assert unsupported.status_code == 415

        oversized = client.post(
            "/api/mp/tokens/memory",
            content=b"x" * (mp_tools_router.MP_MEMORY_SNAPSHOT_LIMIT + 1),
            headers={"content-type": "application/octet-stream"},
        )
        assert oversized.status_code == 413


def test_token_json_ingest_enforces_raw_body_limit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = MPTokenStore(tmp_path / "tokens.json")
    capture = MPTokenCaptureService(store)
    monkeypatch.setattr(mp_tools_router, "token_store", store)
    monkeypatch.setattr(mp_tools_router, "token_capture", capture)
    monkeypatch.setattr(mp_tools_router, "MP_JSON_BODY_LIMIT", 64)

    app = FastAPI()
    app.include_router(mp_tools_router.router)
    with TestClient(app) as client:
        response = client.post(
            "/api/mp/tokens/ingest",
            content=b'{"padding":"' + b"x" * 128 + b'"}',
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 413


def test_export_rejects_noncanonical_article_authority() -> None:
    app = FastAPI()
    app.include_router(mp_tools_router.router)
    with TestClient(app) as client:
        userinfo = client.post(
            "/api/mp/exports",
            json={"url": "https://user@mp.weixin.qq.com/s?__biz=x"},
        )
        custom_port = client.post(
            "/api/mp/exports",
            json={"url": "https://mp.weixin.qq.com:444/s?__biz=x"},
        )

    assert userinfo.status_code == 422
    assert custom_port.status_code == 422


def test_unexpected_export_failure_removes_allocated_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FailingExporter:
        def export(self, *_: object, **__: object) -> None:
            raise RuntimeError("boom")

    export_root = tmp_path / "exports"
    monkeypatch.setattr(mp_tools_router, "MP_EXPORT_ROOT", export_root)
    monkeypatch.setattr(mp_tools_router, "article_exporter", FailingExporter())
    app = FastAPI()
    app.include_router(mp_tools_router.router)
    with TestClient(app) as client:
        response = client.post(
            "/api/mp/exports",
            json={"url": "https://mp.weixin.qq.com/s?__biz=x"},
        )

    assert response.status_code == 500
    assert not list(export_root.iterdir())
