from __future__ import annotations

import html
import json
import re
import threading
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit


_BATCH_ARTICLE_PATH = "/cgi-bin/mmbiz-bin/batchgetappmsgh5"
_BATCH_ARTICLE_SCOPE = "mmbizmp_comm"
_BATCH_ARTICLE_EVENT = "xweb_call_jsapi"
_BATCH_ARTICLE_FUNCTION = "H5ExtTransfer"
_SHORT_ARTICLE_PATH = re.compile(r"/s/[A-Za-z0-9_-]+")
_MAX_NESTED_JSON_BYTES = 128 * 1024
_MAX_LOG_LINE_BYTES = 512 * 1024
_DEFAULT_LOG_TAIL_BYTES = 2 * 1024 * 1024
_CANONICAL_FIELDS = {
    "__biz": "biz",
    "biz": "biz",
    "mid": "mid",
    "app_msgid": "mid",
    "idx": "idx",
    "sn": "sn",
    "scene": "scene",
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _mapping_value(value: Mapping[str, Any], *names: str) -> Any:
    wanted = {name.casefold().replace("-", "_") for name in names}
    for key, item in value.items():
        normalized = str(key).casefold().replace("-", "_")
        if normalized in wanted:
            return item
    return None


def _decoded_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw.startswith("{") or len(raw.encode("utf-8", errors="ignore")) > _MAX_NESTED_JSON_BYTES:
        return None
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, Mapping) else None


def _absolute_mp_url(value: Any) -> str | None:
    raw = html.unescape(str(value or "").strip())
    if not raw:
        return None
    if raw.startswith("/"):
        raw = "https://mp.weixin.qq.com" + raw
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold().rstrip(".") != "mp.weixin.qq.com"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    return raw


def _short_article_url(value: Any) -> str | None:
    absolute = _absolute_mp_url(value)
    if not absolute:
        return None
    parsed = urlsplit(absolute)
    if _SHORT_ARTICLE_PATH.fullmatch(parsed.path) is None:
        return None
    return urlunsplit(("https", "mp.weixin.qq.com", parsed.path, "", ""))


def _clean_context_value(name: str, value: Any) -> str:
    text = html.unescape(str(value or "")).strip()
    if not text or any(character.isspace() or ord(character) < 0x20 for character in text):
        return ""
    limits = {
        "biz": 256,
        "mid": 32,
        "idx": 8,
        "sn": 128,
        "scene": 16,
    }
    if len(text) > limits[name] or any(character in "&#?" for character in text):
        return ""
    if name in {"mid", "idx", "scene"} and not text.isdecimal():
        return ""
    return text


def _canonical_article_context(value: Any) -> dict[str, str] | None:
    absolute = _absolute_mp_url(value)
    if not absolute:
        return None
    parsed = urlsplit(absolute)
    if parsed.path != "/s":
        return None
    result: dict[str, str] = {}
    for key, item in parse_qsl(parsed.query, keep_blank_values=False):
        target = _CANONICAL_FIELDS.get(key.casefold().replace("-", "_"))
        if target and target not in result:
            cleaned = _clean_context_value(target, item)
            if cleaned:
                result[target] = cleaned
    if not all(result.get(name) for name in ("biz", "mid", "idx")):
        return None
    return result


def extract_native_article_identity(
    event: Mapping[str, Any],
) -> tuple[str, dict[str, str]] | None:
    """Extract one trusted short-link identity from a Native H5 batch event."""

    if str(_mapping_value(event, "event") or "").strip() != _BATCH_ARTICLE_EVENT:
        return None
    payload = _decoded_mapping(_mapping_value(event, "payload"))
    if payload is None:
        return None
    message = _decoded_mapping(_mapping_value(payload, "msg"))
    if message is None:
        return None
    if str(_mapping_value(message, "func") or "").strip() != _BATCH_ARTICLE_FUNCTION:
        return None
    params = _decoded_mapping(_mapping_value(message, "params"))
    if params is None:
        return None
    if str(_mapping_value(params, "scope") or "").strip() != _BATCH_ARTICLE_SCOPE:
        return None
    endpoint = str(_mapping_value(params, "url") or "").strip()
    try:
        endpoint_path = urlsplit(endpoint).path
    except ValueError:
        return None
    if endpoint_path != _BATCH_ARTICLE_PATH:
        return None

    short_url = _short_article_url(
        _mapping_value(event, "activeUrl", "active_url")
        or _mapping_value(payload, "activeUrl", "active_url")
    )
    if not short_url:
        return None
    request_json = _decoded_mapping(
        _mapping_value(params, "req_json", "reqJson")
    )
    if request_json is None:
        return None
    request_info = _mapping_value(
        request_json,
        "ReqInfoList",
        "req_info_list",
        "reqinfolist",
    )
    if not isinstance(request_info, list) or len(request_info) != 1:
        return None
    mapping = _decoded_mapping(request_info[0])
    if mapping is None:
        return None
    context = _canonical_article_context(_mapping_value(mapping, "url"))
    return (short_url, context) if context else None


@dataclass(frozen=True, slots=True)
class NativeArticleIdentity:
    short_url: str
    biz: str
    mid: str
    idx: str
    captured_at: datetime
    sn: str = ""
    scene: str = ""

    def context(self) -> dict[str, str]:
        return {
            name: value
            for name in ("biz", "mid", "idx", "sn", "scene")
            if (value := getattr(self, name))
        }


class NativeArticleContextStore:
    """Bounded in-memory mapping from copied short links to public identity."""

    def __init__(
        self,
        *,
        max_entries: int = 256,
        ttl: timedelta = timedelta(minutes=15),
    ) -> None:
        self.max_entries = max(1, int(max_entries))
        self.ttl = max(ttl, timedelta(minutes=1))
        self._lock = threading.RLock()
        self._items: OrderedDict[str, NativeArticleIdentity] = OrderedDict()

    def _purge_expired(self, now: datetime) -> None:
        expired = [
            key
            for key, item in self._items.items()
            if now - item.captured_at >= self.ttl
        ]
        for key in expired:
            self._items.pop(key, None)

    def ingest(self, event: Mapping[str, Any]) -> bool:
        extracted = extract_native_article_identity(event)
        if extracted is None:
            return False
        short_url, context = extracted
        captured_at = _utc_now()
        item = NativeArticleIdentity(
            short_url=short_url,
            biz=context["biz"],
            mid=context["mid"],
            idx=context["idx"],
            sn=context.get("sn", ""),
            scene=context.get("scene", ""),
            captured_at=captured_at,
        )
        with self._lock:
            self._purge_expired(captured_at)
            self._items.pop(short_url, None)
            self._items[short_url] = item
            while len(self._items) > self.max_entries:
                self._items.popitem(last=False)
        return True

    def resolve(self, url: str) -> dict[str, str] | None:
        short_url = _short_article_url(url)
        if not short_url:
            return None
        now = _utc_now()
        with self._lock:
            self._purge_expired(now)
            item = self._items.get(short_url)
            if item is None:
                return None
            self._items.move_to_end(short_url)
            return item.context()

    def status(self) -> dict[str, Any]:
        now = _utc_now()
        with self._lock:
            self._purge_expired(now)
            latest = next(reversed(self._items.values()), None) if self._items else None
            return {
                "available": latest is not None,
                "count": len(self._items),
                "captured_at": latest.captured_at.isoformat() if latest else None,
            }

    def scan_recent_log(
        self,
        path: Path,
        *,
        max_bytes: int = _DEFAULT_LOG_TAIL_BYTES,
    ) -> int:
        """Replay only public identity from a bounded log tail, never tokens."""

        try:
            size = path.stat().st_size
        except OSError:
            return 0
        start = max(0, size - max(0, int(max_bytes)))
        captured = 0
        try:
            with path.open("rb") as stream:
                stream.seek(start)
                if start:
                    stream.readline()
                for raw_line in stream:
                    if len(raw_line) > _MAX_LOG_LINE_BYTES:
                        continue
                    try:
                        event = json.loads(raw_line.decode("utf-8", errors="replace"))
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, Mapping) and self.ingest(event):
                        captured += 1
        except OSError:
            return captured
        return captured


__all__ = [
    "NativeArticleContextStore",
    "NativeArticleIdentity",
    "extract_native_article_identity",
]
