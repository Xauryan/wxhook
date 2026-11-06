from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path

from backend.app.mp_tokens import (
    BrowserEventLogTailer,
    MPTokenCaptureService,
    MPTokenStore,
)


def test_capture_cdp_request_and_redact_status(tmp_path: Path) -> None:
    store = MPTokenStore(tmp_path / "tokens.json")
    capture = MPTokenCaptureService(store)

    result = capture.ingest_cdp_event(
        {
            "method": "Network.requestWillBeSent",
            "params": {
                "request": {
                    "url": (
                        "https://mp.weixin.qq.com/s?__biz=abc"
                        "&key=dynamic-key-1234&uin=123456"
                    ),
                    "headers": {
                        "Cookie": "pass_ticket=ticket%2Bvalue; unrelated=yes",
                        "Referer": "https://mp.weixin.qq.com/",
                    },
                }
            },
        }
    )

    assert set(result.captured) == {"key", "uin", "pass_ticket"}
    assert store.current_values() == {
        "key": "dynamic-key-1234",
        "uin": "123456",
        "pass_ticket": "ticket+value",
    }
    serialized_status = json.dumps(store.status(), ensure_ascii=False)
    assert "dynamic-key-1234" not in serialized_status
    assert "ticket+value" not in serialized_status
    assert store.status()["request_host"] == "mp.weixin.qq.com"
    persisted = json.loads((tmp_path / "tokens.json").read_text(encoding="utf-8"))
    assert "key=dynamic-key-1234" not in persisted["request_url"]
    assert "pass_ticket" not in persisted["request_url"]


def test_partial_captures_merge_and_persist(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    store = MPTokenStore(path, default_ttl=timedelta(minutes=5))
    capture = MPTokenCaptureService(store)

    first = capture.ingest(
        {"url": "https://mp.weixin.qq.com/s?key=first-key&uin=42"},
        source="url",
    )
    second = capture.ingest(
        {"headers": {"Cookie": "pass_ticket=second-ticket"}},
        source="headers",
    )

    assert first.changed == ("key", "uin")
    assert second.changed == ("pass_ticket",)
    assert store.current().complete
    reloaded = MPTokenStore(path)
    assert reloaded.current_values() == {
        "key": "first-key",
        "uin": "42",
        "pass_ticket": "second-ticket",
    }
    assert reloaded.current().generation == 2


def test_stale_store_instances_merge_fields_on_disk(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    first = MPTokenStore(path)
    second = MPTokenStore(path)

    first.update({"key": "first-key"}, source="first")
    second.update({"uin": "42"}, source="second")

    assert MPTokenStore(path).current_values() == {
        "key": "first-key",
        "uin": "42",
    }


def test_recreated_cache_keeps_all_in_memory_fields(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    store = MPTokenStore(path)
    store.update({"key": "saved-key", "uin": "42"}, source="initial")
    path.unlink()

    store.update({"pass_ticket": "saved-ticket"}, source="after-delete")

    assert MPTokenStore(path).current_values() == {
        "key": "saved-key",
        "uin": "42",
        "pass_ticket": "saved-ticket",
    }


def test_unrelated_generic_token_is_not_captured() -> None:
    store = MPTokenStore()
    result = MPTokenCaptureService(store).ingest(
        {
            "url": "https://example.test/path?token=oauth-value",
            "headers": {"Authorization": "Bearer secret"},
        },
        source="unrelated",
    )

    assert result.captured == ()
    assert store.current_values() == {}


def test_bounded_memory_snapshot_supports_utf8_and_utf16() -> None:
    store = MPTokenStore()
    capture = MPTokenCaptureService(store)
    result = capture.ingest_memory_snapshot(
        b"prefix&key=memory-key&uin=17&pass_ticket=memory-ticket\x00"
    )
    assert set(result.captured) == {"key", "uin", "pass_ticket"}
    assert store.current_values()["key"] == "memory-key"

    capture.ingest_memory_snapshot(
        "x?key=utf16-key&uin=18&pass_ticket=utf16-ticket".encode("utf-16-le")
    )
    assert store.current_values()["key"] == "utf16-key"

    capture.ingest_memory_snapshot(
        b'{"key":"json-key","uin":"19","pass_ticket":"json-ticket"}'
    )
    assert store.current_values()["key"] == "json-key"


def test_named_credentials_on_unrelated_host_are_not_captured() -> None:
    store = MPTokenStore()
    result = MPTokenCaptureService(store).ingest(
        {
            "url": "https://example.test/path?key=not-wechat&uin=42",
            "headers": {"Cookie": "pass_ticket=also-not-wechat"},
        },
        source="cdp:Network.requestWillBeSent",
    )

    assert result.captured == ()
    assert store.current_values() == {}


def test_native_nested_msg_params_req_json_requires_exact_mp_url() -> None:
    store = MPTokenStore()
    capture = MPTokenCaptureService(store)
    event = {
        "event": "xweb_call_jsapi",
        "payload": json.dumps(
            {
                "msg": json.dumps(
                    {
                        "params": {
                            "req_json": json.dumps(
                                {
                                    "url": (
                                        "https://mp.weixin.qq.com/s?"
                                        "__biz=abc&key=nested-key&uin=77"
                                    ),
                                    "headers": {
                                        "Cookie": "pass_ticket=nested-ticket"
                                    },
                                }
                            )
                        }
                    }
                )
            }
        ),
    }

    result = capture.ingest_browser_event(event)

    assert set(result.captured) == {"key", "uin", "pass_ticket"}
    assert store.current_values() == {
        "key": "nested-key",
        "uin": "77",
        "pass_ticket": "nested-ticket",
    }
    assert store.status()["request_host"] == "mp.weixin.qq.com"


def test_native_event_without_verified_url_does_not_capture_key_or_uin() -> None:
    store = MPTokenStore()
    capture = MPTokenCaptureService(store)

    result = capture.ingest_browser_event(
        {
            "event": "xweb_call_jsapi",
            "payload": json.dumps(
                {"msg": {"params": {"key": "ordinary-key", "uin": "123"}}}
            ),
        }
    )

    assert result.captured == ()
    assert store.current_values() == {}


def test_cdp_extra_info_cookie_can_merge_pass_ticket_without_url() -> None:
    store = MPTokenStore()
    capture = MPTokenCaptureService(store)

    result = capture.ingest_cdp_event(
        {
            "method": "Network.requestWillBeSentExtraInfo",
            "params": {
                "associatedCookies": [
                    {"cookie": {"name": "pass_ticket", "value": "extra-ticket"}}
                ]
            },
        }
    )

    assert result.captured == ("pass_ticket",)
    assert store.current_values() == {"pass_ticket": "extra-ticket"}


def test_cdp_extra_info_cookie_captures_appmsg_token_without_url() -> None:
    store = MPTokenStore()
    capture = MPTokenCaptureService(store)

    result = capture.ingest_cdp_event(
        {
            "method": "Network.requestWillBeSentExtraInfo",
            "params": {
                "associatedCookies": [
                    {"cookie": {"name": "pass_ticket", "value": "extra-ticket"}},
                    {
                        "cookie": {
                            "name": "appmsg_token",
                            "value": "auxiliary-token",
                        }
                    },
                ]
            },
        }
    )

    assert set(result.captured) == {"pass_ticket", "appmsg_token"}
    assert store.current_values() == {
        "pass_ticket": "extra-ticket",
        "appmsg_token": "auxiliary-token",
    }


def test_native_webcgi_header_cookie_captures_appmsg_token_without_url() -> None:
    store = MPTokenStore()
    capture = MPTokenCaptureService(store)

    result = capture.ingest(
        {
            "params": {
                "webcgi_header": [
                    {
                        "key": "Cookie",
                        "value": "pass_ticket=header-ticket; appmsg_token=header-token",
                    }
                ]
            }
        },
        source="native-browser-event",
    )

    assert set(result.captured) == {"pass_ticket", "appmsg_token"}
    assert store.current_values() == {
        "pass_ticket": "header-ticket",
        "appmsg_token": "header-token",
    }


def test_native_getappmsgext_url_captures_appmsg_token(tmp_path: Path) -> None:
    store = MPTokenStore(tmp_path / "tokens.json")
    capture = MPTokenCaptureService(store)

    result = capture.ingest(
        {
            "url": (
                "https://mp.weixin.qq.com/mp/getappmsgext?"
                "appmsg_token=url-token&key=url-key&uin=123"
            )
        },
        source="native-browser-event",
    )

    assert set(result.captured) == {"key", "uin", "appmsg_token"}
    assert store.current_values() == {
        "key": "url-key",
        "uin": "123",
        "appmsg_token": "url-token",
    }
    persisted = json.loads((tmp_path / "tokens.json").read_text(encoding="utf-8"))
    assert persisted["appmsg_token"] == "url-token"
    assert "url-token" not in json.dumps(store.status(), ensure_ascii=False)
    assert store.status()["auxiliary"]["appmsg_token"]["present"] is True


def test_automatic_event_does_not_keep_unverified_appmsg_token_field() -> None:
    store = MPTokenStore()
    result = MPTokenCaptureService(store).ingest(
        {
            "params": {
                "appmsg_token": "unverified-token",
                "pass_ticket": "unverified-ticket",
            }
        },
        source="native-browser-event",
    )

    assert result.captured == ()
    assert store.current_values() == {}


def test_store_discards_legacy_automatic_credentials_without_verified_url(
    tmp_path: Path,
) -> None:
    path = tmp_path / "tokens.json"
    path.write_text(
        json.dumps(
            {
                "key": "legacy-key",
                "uin": "123",
                "pass_ticket": "legacy-ticket",
                "source": "native-browser-event",
                "request_url": "",
                "generation": 3,
            }
        ),
        encoding="utf-8",
    )

    store = MPTokenStore(path)

    assert store.current_values(include_expired=True) == {}
    assert not path.exists()


def test_store_keeps_pass_ticket_only_extra_info_cache(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    path.write_text(
        json.dumps(
            {
                "pass_ticket": "cookie-ticket",
                "source": "cdp:Network.requestWillBeSentExtraInfo",
                "request_url": "",
                "generation": 1,
            }
        ),
        encoding="utf-8",
    )

    store = MPTokenStore(path)

    assert store.current_values(include_expired=True) == {
        "pass_ticket": "cookie-ticket"
    }
    assert path.exists()


def test_token_source_is_redacted_before_status_or_persistence(tmp_path: Path) -> None:
    store = MPTokenStore(tmp_path / "tokens.json")
    result = MPTokenCaptureService(store).ingest(
        {"key": "source-key"},
        source="hook key=source-secret pass_ticket=source-ticket",
    )

    assert "source-secret" not in result.status["source"]
    assert "source-ticket" not in result.status["source"]
    assert "source-secret" not in json.dumps(
        json.loads((tmp_path / "tokens.json").read_text(encoding="utf-8"))
    )


def test_form_like_request_url_is_redacted_before_persistence(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    capture = MPTokenCaptureService(MPTokenStore(path))

    capture.ingest(
        {"url": "key=form-secret&uin=42&pass_ticket=form-ticket&scene=1"},
        source="native-form",
    )

    request_url = json.loads(path.read_text(encoding="utf-8"))["request_url"]
    assert "form-secret" not in request_url
    assert "form-ticket" not in request_url
    assert "scene=1" in request_url


def test_token_metadata_redacts_json_and_hyphenated_aliases(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    store = MPTokenStore(path)
    store.update(
        {"key": "value-key"},
        source=(
            '{"key":"json-secret","wx-token":"wx-secret",'
            '"app-msg-token":"app-secret"}'
        ),
        request_url="key=value-key&wx-token=wx-secret&scene=1",
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "json-secret" not in payload["source"]
    assert "wx-secret" not in payload["source"]
    assert "app-secret" not in payload["source"]
    assert "wx-secret" not in payload["request_url"]
    assert "scene=1" in payload["request_url"]


def test_token_capture_rejects_mp_wechat_subdomains() -> None:
    store = MPTokenStore()
    result = MPTokenCaptureService(store).ingest(
        {"url": "https://evil.mp.weixin.qq.com/s?key=not-captured&uin=1"},
        source="network",
    )

    assert result.captured == ()
    assert store.current_values() == {}


def test_native_browser_event_log_tailer_handles_append_and_rotation(tmp_path: Path) -> None:
    path = tmp_path / "browser_events.jsonl"
    store = MPTokenStore()
    capture = MPTokenCaptureService(store)
    tailer = BrowserEventLogTailer(path, capture, start_at_end=False)
    path.write_text(
        json.dumps(
            {
                "event": "xweb_call_network_request",
                "payload": json.dumps(
                    {"url": "https://mp.weixin.qq.com/s?key=from-log&uin=7"}
                ),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert tailer.poll_once() == 1
    assert store.current_values()["uin"] == "7"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps({"payload": "Cookie: pass_ticket=ignored-prefix"}) + "\n"
        )
        stream.write(
            json.dumps({"cookies": {"pass_ticket": "from-log-ticket"}}) + "\n"
        )
    assert tailer.poll_once() == 1
    assert store.current_values()["pass_ticket"] == "from-log-ticket"

    path.write_text(
        json.dumps({"activeUrl": "https://mp.weixin.qq.com/s?key=rotated"})
        + "\n",
        encoding="utf-8",
    )
    assert tailer.poll_once() == 1
    assert store.current_values()["key"] == "rotated"


def test_browser_event_tailer_observes_identity_events_without_changing_token_count(
    tmp_path: Path,
) -> None:
    path = tmp_path / "browser_events.jsonl"
    observed: list[dict[str, object]] = []
    event = {"event": "identity-only", "payload": {"mid": "1"}}
    path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    tailer = BrowserEventLogTailer(
        path,
        MPTokenCaptureService(MPTokenStore()),
        start_at_end=False,
        event_consumer=observed.append,
    )

    assert tailer.poll_once() == 0
    assert observed == [event]


def test_browser_event_tailer_consumer_failure_does_not_block_token_capture(
    tmp_path: Path,
) -> None:
    path = tmp_path / "browser_events.jsonl"
    store = MPTokenStore()
    path.write_text(
        json.dumps(
            {"url": "https://mp.weixin.qq.com/s?key=kept-key&uin=42"}
        )
        + "\n",
        encoding="utf-8",
    )

    def fail(_: object) -> None:
        raise ValueError("bad identity event")

    tailer = BrowserEventLogTailer(
        path,
        MPTokenCaptureService(store),
        start_at_end=False,
        event_consumer=fail,
    )

    assert tailer.poll_once() == 1
    assert store.current_values()["key"] == "kept-key"


def test_browser_event_tailer_keeps_partial_line_until_completed(tmp_path: Path) -> None:
    path = tmp_path / "browser_events.jsonl"
    store = MPTokenStore()
    tailer = BrowserEventLogTailer(
        path,
        MPTokenCaptureService(store),
        start_at_end=False,
    )
    event = json.dumps(
        {"url": "https://mp.weixin.qq.com/s?key=partial-key&uin=8"}
    ).encode()
    split = len(event) // 2
    path.write_bytes(event[:split])

    assert tailer.poll_once() == 0
    with path.open("ab") as stream:
        stream.write(event[split:] + b"\n")

    assert tailer.poll_once() == 1
    assert store.current_values()["key"] == "partial-key"


def test_browser_event_tailer_detects_same_size_rewrite(tmp_path: Path) -> None:
    path = tmp_path / "browser_events.jsonl"
    store = MPTokenStore()
    tailer = BrowserEventLogTailer(
        path,
        MPTokenCaptureService(store),
        start_at_end=False,
    )
    first = json.dumps(
        {"url": "https://mp.weixin.qq.com/s?key=alpha&uin=9"}
    ) + "\n"
    replacement = json.dumps(
        {"url": "https://mp.weixin.qq.com/s?key=bravo&uin=9"}
    ) + "\n"
    assert len(first.encode()) == len(replacement.encode())
    path.write_text(first, encoding="utf-8")
    assert tailer.poll_once() == 1

    path.write_text(replacement, encoding="utf-8")

    assert tailer.poll_once() == 1
    assert store.current_values()["key"] == "bravo"


def test_browser_event_tailer_skips_bad_records_and_continues(tmp_path: Path) -> None:
    path = tmp_path / "browser_events.jsonl"
    store = MPTokenStore()
    tailer = BrowserEventLogTailer(
        path,
        MPTokenCaptureService(store),
        start_at_end=False,
    )
    path.write_text(
        "{bad json}\n"
        + json.dumps({"url": "https://[invalid"})
        + "\n"
        + json.dumps(
            {"url": "https://mp.weixin.qq.com/s?key=valid-key&uin=10"}
        )
        + "\n",
        encoding="utf-8",
    )

    assert tailer.poll_once() == 1
    assert store.current_values()["key"] == "valid-key"


def test_async_cdp_stream_ingest() -> None:
    store = MPTokenStore()
    capture = MPTokenCaptureService(store)

    async def events():
        yield {
            "method": "Network.requestWillBeSent",
            "params": {
                "request": {
                    "url": "https://mp.weixin.qq.com/s?key=async-key&uin=99"
                }
            },
        }

    assert asyncio.run(capture.ingest_cdp_stream(events())) == 1
    assert store.current_values()["key"] == "async-key"
