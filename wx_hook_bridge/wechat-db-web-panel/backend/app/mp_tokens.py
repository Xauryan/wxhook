from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import html
import json
import logging
import os
import re
import threading
import time
from collections.abc import AsyncIterable, Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit


_MEMORY_TOKEN_PAIR = re.compile(
    r"(?:^|[?&;,\s{\[\(])['\"]?(?P<name>key|uin|pass[_-]?ticket|app[-_]?msg[-_]?token)['\"]?"
    r"\s*(?:=|:)\s*['\"]?(?P<value>[^\x00-\x20&;,\"'<>}\]\)]{1,4096})",
    re.IGNORECASE,
)
_SOURCE_TOKEN_PAIR = re.compile(
    r"(?i)((?:key|uin|wxuin|pass[_-]?ticket|app[-_]?msg[-_]?token|wx[-_]?token)"
    r"\s*[=:]\s*)"
    r"[^\s&;,]+"
)
_JSON_TOKEN_PAIR = re.compile(
    r"(?i)([\"']?(?:key|uin|wxuin|pass[_-]?ticket|app[-_]?msg[-_]?token|wx[-_]?token)"
    r"[\"']?\s*:\s*[\"']?)([^\"'&,}\s]+)"
)

# Native bridge events commonly wrap JSON several times (payload -> msg ->
# params -> req_json). Keep this traversal bounded because the bridge also
# records large opaque response bodies and base64 blobs.
_MAX_TOKEN_WALK_DEPTH = 12
_MAX_NESTED_JSON_TEXT = 128 * 1024
_JSON_CONTAINER_FIELDS = frozenset(
    {
        "body",
        "postdata",
        "payload",
        "data",
        "msg",
        "params",
        "req_json",
        "reqjson",
    }
)
_COOKIE_CONTAINER_FIELDS = frozenset(
    {"cookie", "cookies", "set_cookie", "associatedcookies", "associated_cookies"}
)
_URL_FIELD_RANK = {
    "url": 4,
    "requesturl": 4,
    "request_url": 4,
    "x_original_url": 4,
    ":path": 4,
    "activeurl": 3,
    "documenturl": 3,
    "referer": 1,
    "referrer": 1,
}


TOKEN_FIELDS = ("key", "uin", "pass_ticket")
AUXILIARY_TOKEN_FIELDS = ("appmsg_token",)
CAPTURE_FIELDS = TOKEN_FIELDS + AUXILIARY_TOKEN_FIELDS
TOKEN_ALIASES = {
    "key": "key",
    "uin": "uin",
    "wxuin": "uin",
    "pass_ticket": "pass_ticket",
    "passticket": "pass_ticket",
    "appmsg_token": "appmsg_token",
    "appmsgtoken": "appmsg_token",
    "app_msg_token": "appmsg_token",
}
SENSITIVE_TOKEN_QUERY_NAMES = frozenset(
    {
        *TOKEN_ALIASES,
        "appmsg_token",
        "appmsgtoken",
        "app_msg_token",
        "wxtoken",
        "wx_token",
    }
)
_REDACT_KEEP = 4
logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


def _parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if isinstance(value, (int, float)):
        # WeChat payloads mix Unix seconds and milliseconds.
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _normalize_name(value: Any) -> str:
    return str(value or "").strip().casefold().replace("-", "_")


def _redact_sensitive_text(value: Any, *, max_length: int = 512) -> str:
    text = str(value or "").strip()
    for _ in range(2):
        decoded = unquote(text)
        if decoded == text:
            break
        text = decoded
    text = _SOURCE_TOKEN_PAIR.sub(r"\1<redacted>", text)
    text = _JSON_TOKEN_PAIR.sub(r"\1<redacted>", text)
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text).strip()
    if len(text) > max_length:
        text = text[: max(1, max_length - 3)].rstrip() + "..."
    return text


def _is_wechat_host(hostname: str | None) -> bool:
    host = (hostname or "").casefold().rstrip(".")
    return host == "mp.weixin.qq.com"


def _is_precise_mp_url(raw_url: Any) -> bool:
    """Return whether *raw_url* is an exact official-account HTTPS URL."""

    try:
        parsed = urlsplit(str(raw_url or "").strip())
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme.casefold() == "https"
        and bool(parsed.hostname)
        and _is_wechat_host(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
    )


def _is_automatic_capture_source(source: Any) -> bool:
    normalized = str(source or "").strip().casefold()
    return normalized == "native-browser-event" or normalized.startswith("cdp:")


@contextmanager
def _exclusive_file_lock(path: Path):
    """Best-effort cross-process lock for the small token cache file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    acquired = False
    try:
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:  # pragma: no cover - exercised on non-Windows CI only
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except OSError:
            # Capture can continue in memory when the advisory lock is unavailable.
            pass
        else:
            acquired = True
        yield
    finally:
        if acquired:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:  # pragma: no cover - exercised on non-Windows CI only
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def _scrub_request_url(raw_url: Any) -> str:
    """Keep article identity while removing credentials from disk metadata."""
    value = html.unescape(str(raw_url or "")).strip()
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    query = [
        (name, item)
        for name, item in parse_qsl(parsed.query, keep_blank_values=True)
        if _normalize_name(name) not in SENSITIVE_TOKEN_QUERY_NAMES
    ]
    if not parsed.query and "=" in value and not parsed.scheme and not parsed.netloc:
        # Native bridges sometimes label a form body as a URL. Keep the event
        # useful while ensuring the loose form spelling is not persisted raw.
        return _redact_sensitive_text(value.split("#", 1)[0])
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query, doseq=True), "")
    )


def _clean_token(field_name: str, value: Any) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    text = html.unescape(str(value or "")).strip().strip('"\'')
    for _ in range(2):
        decoded = unquote(text)
        if decoded == text:
            break
        text = decoded
    text = text.strip().strip('"\'')
    if not text or text.casefold() in {"null", "none", "undefined", "false"}:
        return ""
    if any(ord(character) < 0x20 for character in text):
        return ""
    limits = {
        "key": 2048,
        "uin": 128,
        "pass_ticket": 4096,
        "appmsg_token": 4096,
    }
    if len(text) > limits[field_name]:
        return ""
    if field_name == "uin" and any(character.isspace() for character in text):
        return ""
    return text


def redact_token(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= _REDACT_KEEP * 2:
        return "*" * len(value)
    return f"{value[:_REDACT_KEEP]}...{value[-_REDACT_KEEP:]}"


def _redact_source(value: Any) -> str:
    source = str(value or "").strip()
    if not source:
        return "unknown"
    return _redact_sensitive_text(source, max_length=80)


@dataclass(slots=True)
class MPTokenSnapshot:
    key: str = ""
    uin: str = ""
    pass_ticket: str = ""
    appmsg_token: str = ""
    captured_at: datetime = field(default_factory=_utc_now)
    expires_at: datetime | None = None
    source: str = "unknown"
    request_url: str = ""
    generation: int = 0

    @property
    def complete(self) -> bool:
        return all(getattr(self, field_name) for field_name in TOKEN_FIELDS)

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and _utc_now() >= self.expires_at

    def values(self, *, include_expired: bool = False) -> dict[str, str]:
        if self.expired and not include_expired:
            return {}
        return {
            field_name: value
            for field_name in CAPTURE_FIELDS
            if (value := getattr(self, field_name))
        }

    def status(self) -> dict[str, Any]:
        values = self.values(include_expired=True)
        return {
            "available": bool(values),
            "complete": self.complete,
            "expired": self.expired,
            "fields": {
                field_name: {
                    "present": bool(values.get(field_name)),
                    "value": redact_token(values.get(field_name)),
                }
                for field_name in TOKEN_FIELDS
            },
            "auxiliary": {
                field_name: {
                    "present": bool(values.get(field_name)),
                    "value": redact_token(values.get(field_name)),
                }
                for field_name in AUXILIARY_TOKEN_FIELDS
            },
            "captured_at": _iso(self.captured_at),
            "expires_at": _iso(self.expires_at),
            "source": self.source,
            "request_host": urlsplit(self.request_url).hostname or "",
            "generation": self.generation,
        }

    def serialize(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["captured_at"] = _iso(self.captured_at)
        payload["expires_at"] = _iso(self.expires_at)
        return payload

    @classmethod
    def deserialize(cls, payload: Mapping[str, Any]) -> "MPTokenSnapshot":
        return cls(
            key=_clean_token("key", payload.get("key")),
            uin=_clean_token("uin", payload.get("uin")),
            pass_ticket=_clean_token("pass_ticket", payload.get("pass_ticket")),
            appmsg_token=_clean_token(
                "appmsg_token", payload.get("appmsg_token")
            ),
            captured_at=_parse_time(payload.get("captured_at")) or _utc_now(),
            expires_at=_parse_time(payload.get("expires_at")),
            source=_redact_sensitive_text(payload.get("source") or "persisted"),
            request_url=_scrub_request_url(payload.get("request_url")),
            generation=int(payload.get("generation") or 0),
        )


@dataclass(frozen=True, slots=True)
class TokenCaptureResult:
    captured: tuple[str, ...]
    changed: tuple[str, ...]
    source: str
    status: dict[str, Any]


@dataclass(slots=True)
class _TokenExtractionContext:
    """Evidence collected while walking one browser/native event."""

    request_url: str = ""
    request_url_rank: int = 0
    request_url_is_mp: bool = False
    request_url_is_absolute: bool = False
    explicit_pass_ticket: bool = False
    explicit_appmsg_token: bool = False


class MPTokenStore:
    """Thread-safe local token cache with atomic persistence.

    The API-facing representation is always redacted. Raw values are exposed
    only through ``current_values`` for the local fetch/export clients.
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        default_ttl: timedelta = timedelta(hours=2),
    ) -> None:
        self.path = path.resolve() if path else None
        self.default_ttl = default_ttl
        self._lock = threading.RLock()
        self._snapshot = MPTokenSnapshot()
        if self.path and self.path.is_file():
            self._load()

    def _load(self) -> None:
        assert self.path is not None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(payload, Mapping):
                snapshot = MPTokenSnapshot.deserialize(payload)
                if (
                    _is_automatic_capture_source(snapshot.source)
                    and (snapshot.key or snapshot.uin)
                    and not _is_precise_mp_url(snapshot.request_url)
                ):
                    # Older builds could persist generic key/uin pairs from
                    # unrelated native events. Remove that legacy cache once;
                    # pass-ticket-only ExtraInfo records remain valid.
                    self.clear()
                else:
                    self._snapshot = snapshot
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            # A corrupt token cache must never prevent the local API starting.
            self._snapshot = MPTokenSnapshot()

    def _persist(self, fields: Iterable[str] | None = None) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Include the thread id and a nanosecond timestamp: more than one
        # local API/test instance may tail the same native event log on
        # Windows, where replacing a shared temp file otherwise races.
        temp = self.path.with_name(
            f".{self.path.name}.{os.getpid()}"
            f".{threading.get_ident()}.{time.time_ns()}.tmp"
        )
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        try:
            with _exclusive_file_lock(lock_path):
                disk_snapshot = MPTokenSnapshot()
                disk_loaded = False
                try:
                    payload = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(payload, Mapping):
                        disk_snapshot = MPTokenSnapshot.deserialize(payload)
                        disk_loaded = True
                except (OSError, json.JSONDecodeError, TypeError, ValueError):
                    pass

                current = self._snapshot
                selected = set(fields or CAPTURE_FIELDS)
                if not disk_loaded:
                    # A deleted or corrupt cache has no authoritative fields to merge.
                    selected.update(CAPTURE_FIELDS)
                merged = MPTokenSnapshot(
                    key=current.key if "key" in selected else disk_snapshot.key,
                    uin=current.uin if "uin" in selected else disk_snapshot.uin,
                    pass_ticket=(
                        current.pass_ticket
                        if "pass_ticket" in selected
                        else disk_snapshot.pass_ticket
                    ),
                    appmsg_token=(
                        current.appmsg_token
                        if "appmsg_token" in selected
                        else disk_snapshot.appmsg_token
                    ),
                    captured_at=current.captured_at,
                    expires_at=current.expires_at,
                    source=current.source,
                    request_url=current.request_url,
                    generation=max(current.generation, disk_snapshot.generation),
                )
                self._snapshot = merged
                temp.write_text(
                    json.dumps(merged.serialize(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                try:
                    os.chmod(temp, 0o600)
                except OSError:
                    pass
                for attempt in range(3):
                    try:
                        os.replace(temp, self.path)
                        break
                    except PermissionError:
                        if attempt == 2:
                            raise
                        time.sleep(0.02 * (attempt + 1))
        except OSError:
            # The in-memory snapshot remains usable; persistence is best
            # effort and must not kill the browser-event capture thread.
            logger.debug("could not persist MP token cache", exc_info=True)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def update(
        self,
        values: Mapping[str, Any],
        *,
        source: str,
        request_url: str = "",
        expires_at: datetime | None = None,
        captured_at: datetime | None = None,
    ) -> TokenCaptureResult:
        cleaned = {
            field_name: _clean_token(field_name, values.get(field_name))
            for field_name in CAPTURE_FIELDS
        }
        cleaned = {key: value for key, value in cleaned.items() if value}
        safe_source = _redact_source(source)
        with self._lock:
            changed = tuple(
                field_name
                for field_name, value in cleaned.items()
                if getattr(self._snapshot, field_name) != value
            )
            if cleaned:
                now = captured_at or _utc_now()
                resolved_expiry = expires_at or (now + self.default_ttl)
                previous = self._snapshot
                self._snapshot = MPTokenSnapshot(
                    key=cleaned.get("key", self._snapshot.key),
                    uin=cleaned.get("uin", self._snapshot.uin),
                    pass_ticket=cleaned.get(
                        "pass_ticket", self._snapshot.pass_ticket
                    ),
                    appmsg_token=cleaned.get(
                        "appmsg_token", self._snapshot.appmsg_token
                    ),
                    captured_at=now,
                    expires_at=resolved_expiry,
                    source=safe_source,
                    request_url=_scrub_request_url(
                        request_url or self._snapshot.request_url
                    ),
                    generation=self._snapshot.generation + (1 if changed else 0),
                )
                # A native event log can replay thousands of identical
                # requests when the panel starts. Avoid rewriting the secret
                # cache for every duplicate event.
                if changed or previous.expired or not self.path or not self.path.exists():
                    self._persist(cleaned.keys())
            return TokenCaptureResult(
                captured=tuple(cleaned),
                changed=changed,
                source=safe_source,
                status=self._snapshot.status(),
            )

    def current(self) -> MPTokenSnapshot:
        with self._lock:
            return MPTokenSnapshot.deserialize(self._snapshot.serialize())

    def current_values(self, *, include_expired: bool = False) -> dict[str, str]:
        with self._lock:
            return self._snapshot.values(include_expired=include_expired)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot.status()

    def clear(self) -> None:
        with self._lock:
            self._snapshot = MPTokenSnapshot()
            if self.path:
                lock_path = self.path.with_name(f".{self.path.name}.lock")
                with _exclusive_file_lock(lock_path):
                    try:
                        self.path.unlink()
                    except FileNotFoundError:
                        pass


class MPTokenCaptureService:
    """Extract official-account request credentials from browser traffic.

    Accepted inputs include a plain request description, a native browser event
    log entry, and Chrome DevTools Protocol ``Network.requestWillBeSent`` or
    ``Network.requestWillBeSentExtraInfo`` events.
    """

    def __init__(self, store: MPTokenStore) -> None:
        self.store = store

    @staticmethod
    def _is_automatic_source(source: str) -> bool:
        return _is_automatic_capture_source(source)

    @staticmethod
    def _put(values: dict[str, str], name: Any, value: Any) -> None:
        field_name = TOKEN_ALIASES.get(_normalize_name(name))
        if not field_name:
            return
        cleaned = _clean_token(field_name, value)
        if cleaned:
            values[field_name] = cleaned

    def _extract_pairs(
        self, pairs: Iterable[tuple[Any, Any]], values: dict[str, str]
    ) -> None:
        for name, value in pairs:
            self._put(values, name, value)

    @staticmethod
    def _record_url(
        context: _TokenExtractionContext,
        candidate: str,
        field_name: str = "url",
    ) -> None:
        """Record the strongest absolute URL evidence seen in an event."""

        try:
            parsed = urlsplit(candidate)
        except ValueError:
            return
        if not parsed.scheme or not parsed.hostname:
            # Relative/form-like values are useful metadata for explicit
            # ingestion, but cannot establish an official-account origin.
            if not context.request_url:
                context.request_url = candidate
            return
        normalized = _normalize_name(field_name)
        rank = _URL_FIELD_RANK.get(normalized, 2)
        is_mp = _is_precise_mp_url(candidate)
        if (
            rank > context.request_url_rank
            or (rank == context.request_url_rank and is_mp and not context.request_url_is_mp)
        ):
            context.request_url = candidate
            context.request_url_rank = rank
            context.request_url_is_mp = is_mp
            context.request_url_is_absolute = True

    def _extract_url(
        self,
        raw_url: Any,
        values: dict[str, str],
        context: _TokenExtractionContext | None = None,
        field_name: str = "url",
    ) -> str:
        url = html.unescape(str(raw_url or "")).strip()
        if not url:
            return ""
        try:
            parsed = urlsplit(url)
        except ValueError:
            return ""
        self._extract_pairs(parse_qsl(parsed.query, keep_blank_values=False), values)
        # Some bridge events contain only an escaped path or form body.
        if not parsed.query and "=" in url and not parsed.scheme:
            self._extract_pairs(parse_qsl(url.lstrip("?"), keep_blank_values=False), values)
        if context is not None:
            self._record_url(context, url, field_name)
        return url

    def _extract_cookie(
        self,
        raw_cookie: Any,
        values: dict[str, str],
        context: _TokenExtractionContext | None = None,
    ) -> None:
        def put_cookie(name: Any, value: Any) -> None:
            normalized = _normalize_name(name)
            self._put(values, normalized, value)
            if (
                context is not None
                and TOKEN_ALIASES.get(normalized) == "pass_ticket"
                and _clean_token("pass_ticket", value)
            ):
                context.explicit_pass_ticket = True
            if (
                context is not None
                and TOKEN_ALIASES.get(normalized) == "appmsg_token"
                and _clean_token("appmsg_token", value)
            ):
                context.explicit_appmsg_token = True

        if isinstance(raw_cookie, Mapping):
            if "name" in raw_cookie and "value" in raw_cookie:
                put_cookie(raw_cookie.get("name"), raw_cookie.get("value"))
                return
            for name, value in raw_cookie.items():
                put_cookie(name, value)
            return
        if isinstance(raw_cookie, list):
            for item in raw_cookie:
                if isinstance(item, Mapping):
                    put_cookie(item.get("name"), item.get("value"))
                    # CDP associatedCookies may wrap the actual cookie.
                    if "cookie" in item:
                        self._extract_cookie(item.get("cookie"), values, context)
            return
        cookie = str(raw_cookie or "")
        for part in cookie.split(";"):
            if "=" in part:
                name, value = part.strip().split("=", 1)
                put_cookie(name, value)

    def _extract_headers(
        self,
        raw_headers: Any,
        values: dict[str, str],
        context: _TokenExtractionContext | None = None,
    ) -> tuple[str, str]:
        request_url = ""
        cookie = ""
        if isinstance(raw_headers, Mapping):
            pairs = raw_headers.items()
        elif isinstance(raw_headers, list):
            pairs = (
                (item.get("name") or item.get("key"), item.get("value"))
                for item in raw_headers
                if isinstance(item, Mapping)
            )
        else:
            pairs = ()
        for name, value in pairs:
            normalized = _normalize_name(name)
            if normalized in {"cookie", "set_cookie"}:
                cookie = str(value or "")
                self._extract_cookie(value, values, context)
            elif normalized in {
                "pass_ticket",
                "passticket",
                "x_pass_ticket",
            } or TOKEN_ALIASES.get(normalized) == "appmsg_token" or normalized in {
                "x_appmsg_token",
                "x_appmsgtoken",
                "x_app_msg_token",
            }:
                field_name = TOKEN_ALIASES.get(normalized)
                if field_name is None and normalized.startswith("x_"):
                    field_name = TOKEN_ALIASES.get(normalized[2:])
                if field_name not in {"pass_ticket", "appmsg_token"}:
                    field_name = None
                if field_name:
                    self._put(values, field_name, value)
                    if context is not None and _clean_token(field_name, value):
                        if field_name == "pass_ticket":
                            context.explicit_pass_ticket = True
                        elif field_name == "appmsg_token":
                            context.explicit_appmsg_token = True
            elif normalized in {"referer", "referrer", ":path", "x_original_url"}:
                candidate = self._extract_url(value, values, context, normalized)
                if candidate:
                    request_url = candidate
        return request_url, cookie

    def _extract_text(
        self,
        raw: Any,
        values: dict[str, str],
        context: _TokenExtractionContext | None = None,
        *,
        depth: int = 0,
        parse_json: bool = True,
    ) -> str:
        if isinstance(raw, bytes):
            text = raw.decode("utf-8", errors="ignore")
        else:
            text = str(raw or "")
        text = html.unescape(text).strip()
        if not text:
            return ""
        if parse_json and len(text) <= _MAX_NESTED_JSON_TEXT and text[:1] in {"{", "["}:
            try:
                decoded = json.loads(text)
            except (TypeError, json.JSONDecodeError):
                decoded = None
            if decoded is not None:
                # Do not parse the JSON source again as a form body: doing so
                # can overwrite a clean value with trailing JSON punctuation.
                return self._walk(decoded, values, depth=depth + 1, context=context)
        self._extract_pairs(parse_qsl(text.lstrip("?"), keep_blank_values=False), values)
        return ""

    def _walk(
        self,
        payload: Any,
        values: dict[str, str],
        *,
        depth: int = 0,
        context: _TokenExtractionContext | None = None,
    ) -> str:
        if context is None:
            context = _TokenExtractionContext()
        if depth > _MAX_TOKEN_WALK_DEPTH:
            return ""
        if isinstance(payload, Mapping):
            for name, value in payload.items():
                normalized = _normalize_name(name)
                self._put(values, normalized, value)
                if normalized in _URL_FIELD_RANK:
                    self._extract_url(value, values, context, normalized)
                elif normalized in {
                    "headers",
                    "requestheaders",
                    "responseheaders",
                    "webcgi_header",
                    "webcgi_headers",
                }:
                    self._extract_headers(value, values, context)
                elif normalized in _COOKIE_CONTAINER_FIELDS:
                    self._extract_cookie(value, values, context)
                elif normalized in _JSON_CONTAINER_FIELDS:
                    if isinstance(value, (Mapping, list, tuple)):
                        self._walk(value, values, depth=depth + 1, context=context)
                    else:
                        self._extract_text(
                            value,
                            values,
                            context,
                            depth=depth,
                            parse_json=True,
                        )
                elif isinstance(value, (Mapping, list, tuple)):
                    self._walk(value, values, depth=depth + 1, context=context)
            return context.request_url
        if isinstance(payload, (list, tuple)):
            for item in payload:
                self._walk(item, values, depth=depth + 1, context=context)
            return context.request_url
        return self._extract_text(payload, values, context, depth=depth)

    @staticmethod
    def _expiry_from(payload: Any, captured_at: datetime) -> datetime | None:
        if not isinstance(payload, Mapping):
            return None
        for key in ("expires_at", "expire_at", "expiry", "expiration"):
            if key in payload and (parsed := _parse_time(payload.get(key))):
                return parsed
        for key in ("expires_in", "expire_in", "ttl"):
            try:
                seconds = int(payload.get(key) or 0)
            except (TypeError, ValueError):
                continue
            if 0 < seconds <= 7 * 24 * 60 * 60:
                return captured_at + timedelta(seconds=seconds)
        for value in payload.values():
            if isinstance(value, Mapping):
                if expiry := MPTokenCaptureService._expiry_from(value, captured_at):
                    return expiry
        return None

    def ingest(self, payload: Any, *, source: str = "browser") -> TokenCaptureResult:
        values: dict[str, str] = {}
        context = _TokenExtractionContext()
        request_url = self._walk(payload, values, context=context)
        # A named credential in an unrelated request is not an official
        # account session. Automatic native/CDP events are stricter: key/uin
        # require an exact HTTPS mp.weixin.qq.com request URL. A pass_ticket
        # from an explicit Cookie/Header can still be merged when the event
        # has no URL (CDP ExtraInfo commonly arrives as a separate record).
        automatic = self._is_automatic_source(source)
        if context.request_url_is_absolute and not context.request_url_is_mp:
            values.clear()
        elif automatic and not context.request_url_is_mp:
            retained: dict[str, str] = {}
            pass_ticket = values.get("pass_ticket")
            appmsg_token = values.get("appmsg_token")
            if context.explicit_pass_ticket and pass_ticket:
                retained["pass_ticket"] = pass_ticket
            if context.explicit_appmsg_token and appmsg_token:
                retained["appmsg_token"] = appmsg_token
            values = retained
        captured_at = _utc_now()
        return self.store.update(
            values,
            source=source,
            request_url=request_url,
            expires_at=self._expiry_from(payload, captured_at),
            captured_at=captured_at,
        )

    def ingest_request(
        self,
        *,
        url: str = "",
        headers: Mapping[str, Any] | list[Mapping[str, Any]] | None = None,
        cookies: Mapping[str, Any] | list[Mapping[str, Any]] | str | None = None,
        body: Any = None,
        source: str = "browser-request",
    ) -> TokenCaptureResult:
        """Capture from an already intercepted HTTP request description."""

        payload: dict[str, Any] = {}
        if url:
            payload["url"] = url
        if headers is not None:
            payload["headers"] = headers
        if cookies is not None:
            payload["cookies"] = cookies
        if body is not None:
            payload["body"] = body
        return self.ingest(payload, source=source)

    def ingest_memory_snapshot(
        self, snapshot: bytes | bytearray | memoryview | str, *, source: str = "memory-snapshot"
    ) -> TokenCaptureResult:
        """Extract pairs from a caller-provided bounded memory snapshot.

        This deliberately does not walk a process address space. A native or
        Frida hook can pass the bytes it intercepted here, keeping scanning
        bounded and auditable while still handling UTF-8/UTF-16 payloads.
        """

        if isinstance(snapshot, (bytes, bytearray, memoryview)):
            raw = bytes(snapshot)
            texts = [raw.decode("utf-8", errors="ignore")]
            texts.append(raw.decode("utf-16-le", errors="ignore"))
        else:
            texts = [str(snapshot)]
        values: dict[str, str] = {}
        for text in texts:
            self._extract_text(text, values)
            for match in _MEMORY_TOKEN_PAIR.finditer(text):
                self._put(values, match.group("name"), match.group("value"))
        return self.store.update(values, source=source)

    def ingest_browser_event(
        self, event: Mapping[str, Any], *, source: str = "native-browser-event"
    ) -> TokenCaptureResult:
        return self.ingest(event, source=source)

    def ingest_cdp_event(self, event: Mapping[str, Any]) -> TokenCaptureResult:
        method = str(event.get("method") or "")
        source = f"cdp:{method or 'event'}"
        params = event.get("params")
        payload: Any = params if isinstance(params, Mapping) else event
        return self.ingest(payload, source=source)

    async def ingest_cdp_stream(
        self, events: AsyncIterable[Mapping[str, Any]]
    ) -> int:
        captured = 0
        async for event in events:
            result = self.ingest_cdp_event(event)
            if result.captured:
                captured += 1
        return captured


class BrowserEventLogTailer:
    """Incrementally consume ``bridge/control/browser_events.jsonl``.

    The tailer handles log truncation/rotation and never persists raw event
    payloads; only extracted token material reaches ``MPTokenStore``.
    """

    def __init__(
        self,
        path: Path,
        capture: MPTokenCaptureService,
        *,
        poll_interval: float = 0.5,
        start_at_end: bool = True,
        initial_tail_bytes: int = 0,
        event_consumer: Callable[[Mapping[str, Any]], Any] | None = None,
    ) -> None:
        self.path = path.resolve()
        self.capture = capture
        self.poll_interval = max(0.05, poll_interval)
        try:
            current_size = self.path.stat().st_size
        except FileNotFoundError:
            current_size = 0
        self._offset = (
            max(0, current_size - max(0, initial_tail_bytes))
            if start_at_end
            else 0
        )
        self._file_marker = self._marker()
        self._skip_partial = self._offset > 0 and not self._at_line_boundary()
        self.event_consumer = event_consumer
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _marker(self, prefix_length: int = 128) -> tuple[int, int, int, bytes] | None:
        try:
            stat = self.path.stat()
            with self.path.open("rb") as stream:
                prefix = stream.read(max(0, prefix_length))
        except OSError:
            return None
        return stat.st_dev, stat.st_ino, len(prefix), hashlib.sha256(prefix).digest()

    def _at_line_boundary(self) -> bool:
        if self._offset <= 0:
            return True
        try:
            with self.path.open("rb") as stream:
                stream.seek(self._offset - 1)
                return stream.read(1) == b"\n"
        except OSError:
            return False

    def poll_once(self) -> int:
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            self._offset = 0
            return 0
        marker = self._marker()
        marker_changed = False
        if marker is not None and self._file_marker is not None:
            old_device, old_inode, old_prefix_length, old_digest = self._file_marker
            new_device, new_inode, _, _ = marker
            if (new_device, new_inode) != (old_device, old_inode):
                marker_changed = True
            else:
                stable_prefix = self._marker(old_prefix_length)
                marker_changed = (
                    stable_prefix is None
                    or stable_prefix[2] != old_prefix_length
                    or stable_prefix[3] != old_digest
                )
        if size < self._offset or marker_changed:
            self._offset = 0
            self._skip_partial = False
        captured = 0
        with self.path.open("rb") as stream:
            stream.seek(self._offset)
            if self._skip_partial:
                # A byte-range tail can begin in the middle of a JSONL line.
                stream.readline()
                self._offset = stream.tell()
                self._skip_partial = False
            while True:
                line_start = stream.tell()
                line = stream.readline()
                if not line:
                    self._offset = stream.tell()
                    break
                if not line.endswith(b"\n"):
                    # Keep an incomplete final record for the next poll.
                    self._offset = line_start
                    break
                try:
                    event = json.loads(line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    self._offset = stream.tell()
                    continue
                if isinstance(event, Mapping):
                    if self.event_consumer is not None:
                        try:
                            self.event_consumer(event)
                        except Exception:
                            logger.debug(
                                "ignoring malformed MP browser event context",
                                exc_info=True,
                            )
                    try:
                        result = self.capture.ingest(event, source="native-browser-event")
                    except (TypeError, ValueError, KeyError):
                        logger.debug("ignoring malformed MP browser event", exc_info=True)
                    else:
                        if result.captured:
                            captured += 1
                self._offset = stream.tell()
        self._file_marker = self._marker() or marker
        return captured

    def _run(self) -> None:
        while not self._stop.wait(self.poll_interval):
            try:
                self.poll_once()
            except Exception:
                # A malformed native event or transient file race must not
                # permanently disable automatic token capture.
                logger.debug("MP browser event polling failed", exc_info=True)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="mp-token-browser-event-tailer",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
            self._thread = None


class CDPTokenInterceptor:
    """Optional Chrome DevTools Protocol websocket listener.

    ``websockets`` is imported lazily so token ingestion remains usable without
    that optional runtime dependency. A caller owns reconnect/backoff policy.
    """

    def __init__(self, capture: MPTokenCaptureService) -> None:
        self.capture = capture
        self._stop = asyncio.Event()

    async def run(self, websocket_url: str) -> int:
        import websockets

        self._stop.clear()
        captured = 0
        async with websockets.connect(
            websocket_url,
            max_size=4 * 1024 * 1024,
            open_timeout=10,
        ) as socket:
            await socket.send(
                json.dumps({"id": 1, "method": "Network.enable", "params": {}})
            )
            while not self._stop.is_set():
                try:
                    message = await asyncio.wait_for(socket.recv(), timeout=0.5)
                except TimeoutError:
                    continue
                if isinstance(message, bytes):
                    message = message.decode("utf-8", errors="replace")
                try:
                    event = json.loads(message)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, Mapping) or not str(
                    event.get("method") or ""
                ).startswith("Network."):
                    continue
                result = self.capture.ingest_cdp_event(event)
                if result.captured:
                    captured += 1
        return captured

    def stop(self) -> None:
        self._stop.set()


def wait_for_complete_token(
    store: MPTokenStore,
    *,
    timeout: float = 10.0,
    interval: float = 0.1,
) -> MPTokenSnapshot | None:
    deadline = time.monotonic() + max(0, timeout)
    while time.monotonic() <= deadline:
        snapshot = store.current()
        if snapshot.complete and not snapshot.expired:
            return snapshot
        time.sleep(max(0.01, interval))
    return None


# Stable descriptive aliases for callers that do not need the MP-prefixed
# implementation names.
TokenStore = MPTokenStore
DynamicTokenInterceptor = MPTokenCaptureService
