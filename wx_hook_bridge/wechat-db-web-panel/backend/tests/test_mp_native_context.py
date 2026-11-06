from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from backend.app import mp_native_context
from backend.app.mp_native_context import (
    NativeArticleContextStore,
    extract_native_article_identity,
)


SHORT_URL = "https://mp.weixin.qq.com/s/rlUYxInHmv9gGHsNaGjpGsw"
CANONICAL_URL = (
    "/s?__biz=MzA1&mid=2247484000&idx=2&sn=abc123&scene=126"
)


def native_event(**overrides: object) -> dict[str, object]:
    event: dict[str, object] = {
        "event": "xweb_call_jsapi",
        "activeUrl": SHORT_URL + "?key=must-not-be-kept",
        "payload": json.dumps(
            {
                "msg": json.dumps(
                    {
                        "func": "H5ExtTransfer",
                        "params": {
                            "scope": "mmbizmp_comm",
                            "url": "/cgi-bin/mmbiz-bin/batchgetappmsgh5",
                            "req_json": json.dumps(
                                {"ReqInfoList": [{"Url": CANONICAL_URL}]}
                            ),
                        },
                    }
                ),
                "cookie": "pass_ticket=must-not-be-kept",
            }
        ),
    }
    event.update(overrides)
    return event


def test_extracts_strict_wrapped_native_short_link_identity() -> None:
    extracted = extract_native_article_identity(native_event())

    assert extracted == (
        SHORT_URL,
        {
            "biz": "MzA1",
            "mid": "2247484000",
            "idx": "2",
            "sn": "abc123",
            "scene": "126",
        },
    )


def test_rejects_ambiguous_or_untrusted_native_events() -> None:
    cases = [
        native_event(event="other"),
        native_event(activeUrl="http://mp.weixin.qq.com/s/slug"),
        native_event(activeUrl="https://user@mp.weixin.qq.com/s/slug"),
        native_event(activeUrl="https://mp.weixin.qq.com:444/s/slug"),
        native_event(activeUrl="https://mp.weixin.qq.com/s/"),
    ]
    for event in cases:
        assert extract_native_article_identity(event) is None

    payload = json.loads(str(native_event()["payload"]))
    message = json.loads(payload["msg"])
    message["params"]["req_json"] = {
        "ReqInfoList": [{"Url": CANONICAL_URL}, {"Url": CANONICAL_URL}]
    }
    payload["msg"] = message
    assert extract_native_article_identity(native_event(payload=payload)) is None

    for pair in ("__biz=MzA1&", "mid=2247484000&", "idx=2&"):
        broken_url = CANONICAL_URL.replace(pair, "")
        payload = json.loads(str(native_event()["payload"]))
        message = json.loads(payload["msg"])
        message["params"]["req_json"] = {"ReqInfoList": [{"Url": broken_url}]}
        payload["msg"] = message
        assert extract_native_article_identity(native_event(payload=payload)) is None


def test_store_is_bounded_expires_and_never_returns_secrets(
    monkeypatch,
) -> None:
    now = mp_native_context._utc_now()
    monkeypatch.setattr(mp_native_context, "_utc_now", lambda: now)
    store = NativeArticleContextStore(max_entries=1, ttl=timedelta(minutes=15))
    assert store.ingest(native_event()) is True
    resolved = store.resolve(SHORT_URL + "?pass_ticket=secret")
    assert resolved == {
        "biz": "MzA1",
        "mid": "2247484000",
        "idx": "2",
        "sn": "abc123",
        "scene": "126",
    }
    assert "secret" not in repr(store)
    assert "must-not-be-kept" not in repr(store)

    second = native_event(
        activeUrl="https://mp.weixin.qq.com/s/secondArticle",
    )
    assert store.ingest(second) is True
    assert store.resolve(SHORT_URL) is None
    assert store.resolve("https://mp.weixin.qq.com/s/secondArticle") is not None

    monkeypatch.setattr(
        mp_native_context,
        "_utc_now",
        lambda: now + timedelta(minutes=16),
    )
    assert store.resolve("https://mp.weixin.qq.com/s/secondArticle") is None


def test_scans_only_a_bounded_recent_log_tail(tmp_path: Path) -> None:
    path = tmp_path / "browser_events.jsonl"
    path.write_text(
        json.dumps({"event": "old"}) + "\n" + json.dumps(native_event()) + "\n",
        encoding="utf-8",
    )
    store = NativeArticleContextStore()

    assert store.scan_recent_log(path, max_bytes=64 * 1024) == 1
    assert store.resolve(SHORT_URL) is not None
