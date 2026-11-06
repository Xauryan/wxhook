from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import mp_metrics_router, mp_tools_router
from backend.app.mp_metrics import (
    MAX_PUBLIC_DETAIL_CHARS,
    WeChatMetricsUpstreamError,
    extract_article_context,
)


@pytest.fixture(autouse=True)
def clear_batch_history() -> None:
    with mp_metrics_router._batch_history_lock:
        mp_metrics_router._batch_history.clear()
    yield
    with mp_metrics_router._batch_history_lock:
        mp_metrics_router._batch_history.clear()


def test_batch_metrics_limits_concurrency_and_isolates_item_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    urls = [
        "https://mp.weixin.qq.com/s?__biz=A&mid=1&idx=1&key=url-secret",
        "https://mp.weixin.qq.com/s?__biz=B&mid=2&idx=1",
        "https://mp.weixin.qq.com/s?__biz=C&mid=3&idx=1",
        "https://mp.weixin.qq.com/s?__biz=D&mid=4&idx=1",
    ]
    state = {"active": 0, "maximum": 0}
    state_lock = threading.Lock()

    def fake_collect(**kwargs: Any) -> SimpleNamespace:
        url = kwargs["url"]
        assert kwargs["fetch_missing"] is True
        assert kwargs["include_comments"] is False
        assert kwargs["auth"].key == "batch-key"
        with state_lock:
            state["active"] += 1
            state["maximum"] = max(state["maximum"], state["active"])
        try:
            time.sleep(0.04)
            if "__biz=B" in url:
                raise WeChatMetricsUpstreamError(
                    "upstream failed at key=leaked-key&uin=123"
                )
            public_url = url.split("&key=", 1)[0]
            data = {
                "url": public_url,
                "read_count": int(url.split("mid=", 1)[1].split("&", 1)[0]),
                "comments": [],
                "collected_at": "2026-08-18T00:00:00+00:00",
            }
            return SimpleNamespace(as_dict=lambda: data)
        finally:
            with state_lock:
                state["active"] -= 1

    monkeypatch.setattr(mp_metrics_router, "collect_article_metrics", fake_collect)
    app = FastAPI()
    app.include_router(mp_metrics_router.router)

    with TestClient(app) as client:
        response = client.post(
            "/api/mp/articles/metrics/batch",
            json={
                "urls": urls,
                "tokens": {
                    "key": "batch-key",
                    "uin": "9",
                    "pass_ticket": "batch-ticket",
                },
                "include_comments": False,
                "concurrency": 2,
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 4
        assert payload["succeeded"] == 3
        assert payload["failed"] == 1
        assert [item["index"] for item in payload["items"]] == [0, 1, 2, 3]
        assert payload["items"][0]["data"]["read_count"] == 1
        assert payload["items"][1]["ok"] is False
        assert payload["items"][1]["error"]["status_code"] == 502
        assert payload["items"][2]["ok"] is True
        assert "results" not in payload
        assert "comments" not in payload["items"][0]
        assert "url-secret" not in response.text
        assert "leaked-key" not in response.text

        history = client.get(
            "/api/mp/articles/history",
            params={"url": urls[0], "limit": 10},
        )
        assert history.status_code == 200
        assert history.json()["count"] == 1
        assert history.json()["items"][0]["read_count"] == 1
        assert "url-secret" not in history.text

    assert state["maximum"] == 2


def test_batch_metrics_rejects_unbounded_work() -> None:
    app = FastAPI()
    app.include_router(mp_metrics_router.router)
    article_url = "https://mp.weixin.qq.com/s?__biz=A&mid=1&idx=1"

    with TestClient(app) as client:
        too_many = client.post(
            "/api/mp/articles/metrics/batch",
            json={"urls": [article_url] * (mp_metrics_router.MAX_BATCH_URLS + 1)},
        )
        excessive_concurrency = client.post(
            "/api/mp/articles/metrics/batch",
            json={
                "urls": [article_url],
                "concurrency": mp_metrics_router.MAX_BATCH_CONCURRENCY + 1,
            },
        )

    assert too_many.status_code == 422
    assert excessive_concurrency.status_code == 422


def test_batch_metrics_passes_each_native_short_link_context_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    urls = [
        "https://mp.weixin.qq.com/s/firstArticle",
        "https://mp.weixin.qq.com/s/secondArticle",
    ]
    hints = {
        urls[0]: {"biz": "A", "mid": "1", "idx": "1"},
        urls[1]: {"biz": "B", "mid": "2", "idx": "1"},
    }
    seen: dict[str, object] = {}

    class ContextStore:
        def resolve(self, url: str) -> dict[str, str] | None:
            return hints.get(url)

    def fake_collect(**kwargs: Any) -> SimpleNamespace:
        seen[kwargs["url"]] = kwargs["context_hint"]
        return SimpleNamespace(
            as_dict=lambda: {
                "url": kwargs["url"],
                "read_count": 1,
                "comments": [],
            }
        )

    monkeypatch.setattr(mp_tools_router, "article_context_store", ContextStore())
    monkeypatch.setattr(mp_metrics_router, "collect_article_metrics", fake_collect)
    app = FastAPI()
    app.include_router(mp_metrics_router.router)
    with TestClient(app) as client:
        response = client.post(
            "/api/mp/articles/metrics/batch",
            json={"urls": urls, "fetch_missing": False},
        )

    assert response.status_code == 200
    assert seen == hints


def test_batch_public_urls_redact_camel_case_token_aliases() -> None:
    context = extract_article_context(
        "https://mp.weixin.qq.com/s?__biz=A&mid=1&idx=1"
        "&passTicket=camel-ticket&appmsgToken=camel-token"
        "&app-msg-token=hyphen-token&wx-token=wx-secret"
    )

    assert context.url is not None
    assert "camel-ticket" not in context.url
    assert "camel-token" not in context.url
    assert "passTicket" not in context.url
    assert "appmsgToken" not in context.url
    assert "hyphen-token" not in context.url
    assert "wx-secret" not in context.url


def test_batch_error_url_is_redacted_when_article_url_is_malformed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_collect(**_: Any) -> None:
        raise WeChatMetricsUpstreamError("failed key=secret-value")

    monkeypatch.setattr(mp_metrics_router, "collect_article_metrics", fail_collect)
    app = FastAPI()
    app.include_router(mp_metrics_router.router)
    with TestClient(app) as client:
        response = client.post(
            "/api/mp/articles/metrics/batch",
            json={
                "urls": [
                    "https://mp.weixin.qq.com/%ZZ?key=url-secret&passTicket=ticket-secret"
                ],
                "fetch_missing": False,
            },
        )

    assert response.status_code == 200
    assert "url-secret" not in response.text
    assert "ticket-secret" not in response.text
    assert "secret-value" not in response.text
    form_like_url = mp_metrics_router._public_article_url(
        "not a url key=form-secret&app-msg-token=app-secret"
    )
    assert "form-secret" not in form_like_url
    assert "app-secret" not in form_like_url
    error_detail = mp_metrics_router._redact_error_detail(
        'failed passTicket=camel-ticket&"appmsgToken":"camel-token"'
        "&app-msg-token=hyphen-token&wx-token=wx-secret "
        + "x" * 1_000
    )
    assert "camel-ticket" not in error_detail
    assert "camel-token" not in error_detail
    assert "hyphen-token" not in error_detail
    assert "wx-secret" not in error_detail
    assert len(error_detail) <= MAX_PUBLIC_DETAIL_CHARS
