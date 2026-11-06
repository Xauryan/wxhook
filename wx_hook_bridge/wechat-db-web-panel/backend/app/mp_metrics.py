from __future__ import annotations

import html as html_module
import json
import math
import re
import unicodedata
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

import httpx

try:
    from lxml import html as lxml_html
except ImportError:  # pragma: no cover - lxml is a project dependency
    lxml_html = None


GET_APPMSGEXT_URL = "https://mp.weixin.qq.com/mp/getappmsgext"
GET_COMMENT_URL = "https://mp.weixin.qq.com/mp/appmsg_comment"
MAX_HTML_BYTES = 8 * 1024 * 1024
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_PUBLIC_DETAIL_CHARS = 512
STREAM_CHUNK_BYTES = 64 * 1024
MAX_ARTICLE_REDIRECTS = 3
ARTICLE_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36 MicroMessenger/4.1.0"
)
TOKEN_QUERY_ALIASES = {
    "key": "key",
    "uin": "uin",
    "passticket": "pass_ticket",
    "appmsgtoken": "appmsg_token",
    "wxtoken": "wxtoken",
}
SENSITIVE_DETAIL_PATTERN = re.compile(
    r"(?ix)"
    r"(?P<prefix>(?<![a-z0-9_-])['\"]?"
    r"(?:key|uin|pass[-_]?ticket|app[-_]?msg[-_]?token|wx[-_]?token)"
    r"['\"]?\s*[=:]\s*)"
    r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^&\s,;'\"}\]]+)"
)


def _normalized_token_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _redact_sensitive_fields(value: str) -> str:
    return SENSITIVE_DETAIL_PATTERN.sub(r"\g<prefix><redacted>", value)


class WeChatMetricsError(RuntimeError):
    """Base error for official-account metric collection."""


class WeChatArticleUrlError(ValueError):
    """Raised when a live collection URL is not a WeChat article URL."""


class WeChatMetricsAuthError(WeChatMetricsError):
    """Raised when the upstream endpoint rejects the current session token."""


class WeChatMetricsUpstreamError(WeChatMetricsError):
    """Raised for upstream transport or response failures."""


@dataclass(frozen=True, slots=True)
class ArticleAuth:
    key: str
    uin: str
    pass_ticket: str
    appmsg_token: str | None = None
    wxtoken: str = "777"
    device_type: str = "WindowsWechat"
    client_version: str = "63090b13"
    cookie: str | None = None
    user_agent: str = DEFAULT_USER_AGENT

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ArticleAuth":
        def text(*names: str, default: str = "") -> str:
            for name in names:
                item = value.get(name)
                if item is not None and str(item).strip():
                    return str(item).strip()
            return default

        result = cls(
            key=text("key"),
            uin=text("uin"),
            pass_ticket=text("pass_ticket", "passTicket", "pass-ticket"),
            appmsg_token=text(
                "appmsg_token",
                "appmsgToken",
                "appmsg-token",
                "app_msg_token",
                "app-msg-token",
            )
            or None,
            wxtoken=text("wxtoken", "wx_token", "wx-token", default="777"),
            device_type=text("device_type", "devicetype", default="WindowsWechat"),
            client_version=text(
                "client_version", "clientversion", "version", default="63090b13"
            ),
            cookie=text("cookie") or None,
            user_agent=text("user_agent", "userAgent", default=DEFAULT_USER_AGENT),
        )
        result.validate()
        return result

    @classmethod
    def from_url(
        cls,
        url: str,
        fallback: Mapping[str, Any] | None = None,
    ) -> "ArticleAuth | None":
        """Build credentials from URL query values, optionally filling gaps.

        Article links copied from an authenticated browser often carry all
        three fields. The returned object is never serialized in API output.
        """
        parsed = urlsplit(html_module.unescape(url.strip()))
        values: dict[str, Any] = dict(fallback or {})
        fallback_fields = {
            TOKEN_QUERY_ALIASES[normalized]
            for name, item in values.items()
            if (normalized := _normalized_token_key(name)) in TOKEN_QUERY_ALIASES
            and item is not None
            and str(item).strip()
        }
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            canonical = TOKEN_QUERY_ALIASES.get(_normalized_token_key(key))
            # Explicit or captured values are considered fresher. URL fields
            # only fill gaps, and blank placeholders never erase a fallback.
            if canonical and canonical not in fallback_fields and value.strip():
                values[canonical] = value
        try:
            return cls.from_mapping(values)
        except WeChatMetricsAuthError:
            return None

    def validate(self) -> None:
        missing = [
            name
            for name, value in (
                ("key", self.key),
                ("uin", self.uin),
                ("pass_ticket", self.pass_ticket),
            )
            if not str(value).strip()
        ]
        if missing:
            raise WeChatMetricsAuthError(
                "live metric collection requires " + ", ".join(missing)
            )


@dataclass(slots=True)
class ArticleContext:
    url: str | None = None
    biz: str | None = None
    mid: str | None = None
    idx: str | None = None
    sn: str | None = None
    comment_id: str | None = None
    appmsg_token: str | None = None
    title: str | None = None
    author: str | None = None
    publish_time: int | None = None
    appmsg_type: str = "9"
    appmsg_like_type: str | None = None
    item_show_type: str | None = None
    is_only_read: str = "1"
    req_id: str | None = None
    scene: str = "0"

    def merge_missing(self, other: "ArticleContext") -> "ArticleContext":
        for name in self.__dataclass_fields__:
            current = getattr(self, name)
            incoming = getattr(other, name)
            if current in (None, "") and incoming not in (None, ""):
                setattr(self, name, incoming)
        return self

    def require_identity(self) -> None:
        missing = [
            name
            for name in ("biz", "mid", "idx")
            if not getattr(self, name)
        ]
        if missing:
            raise WeChatArticleUrlError(
                "article identity is missing " + ", ".join(missing)
            )

    def public_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "biz": self.biz,
            "mid": self.mid,
            "idx": self.idx,
            "sn": self.sn,
            "comment_id": self.comment_id,
            "title": self.title,
            "author": self.author,
            "publish_time": self.publish_time,
        }


@dataclass(slots=True)
class ArticleComment:
    content_id: str | None
    nickname: str | None
    content: str
    create_time: int | None = None
    like_count: int | None = None
    is_top: bool = False
    reply_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "content_id": self.content_id,
            "nickname": self.nickname,
            "nick_name": self.nickname,
            "content": self.content,
            "create_time": self.create_time,
            "like_count": self.like_count,
            "like_num": self.like_count,
            "is_top": self.is_top,
            "reply_count": self.reply_count,
        }


@dataclass(slots=True)
class MetricSnapshot:
    read_count: int | None = None
    display_read_count: int | None = None
    wow_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    basis: dict[str, str] = field(default_factory=dict)

    def overlay(self, other: "MetricSnapshot") -> "MetricSnapshot":
        for name in (
            "read_count",
            "display_read_count",
            "wow_count",
            "like_count",
            "comment_count",
        ):
            value = getattr(other, name)
            if value is not None:
                setattr(self, name, value)
        self.basis.update(other.basis)
        return self

    def has_values(self) -> bool:
        return any(
            value is not None
            for value in (
                self.read_count,
                self.wow_count,
                self.like_count,
                self.comment_count,
            )
        )


@dataclass(slots=True)
class CommentSnapshot:
    total_count: int | None = None
    comments: list[ArticleComment] = field(default_factory=list)
    enabled: bool | None = None
    basis: str | None = None


@dataclass(slots=True)
class ArticleMetrics:
    context: ArticleContext
    read_count: int | None
    display_read_count: int | None
    wow_count: int | None
    like_count: int | None
    comment_count: int | None
    comments: list[ArticleComment] = field(default_factory=list)
    metric_basis: dict[str, str] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    collected_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def as_dict(self) -> dict[str, Any]:
        result = self.context.public_dict()
        result.update(
            {
                "read_count": self.read_count,
                "display_read_count": self.display_read_count,
                "wow_count": self.wow_count,
                "like_count": self.like_count,
                "comment_count": self.comment_count,
                # Keep upstream names available for existing dashboard code.
                "read_num": self.read_count,
                "like_num": self.wow_count,
                "old_like_num": self.like_count,
                "comments_num": self.comment_count,
                "comments": [item.as_dict() for item in self.comments],
                "metric_basis": dict(self.metric_basis),
                "sources": list(dict.fromkeys(self.sources)),
                "warnings": list(self.warnings),
                "collected_at": self.collected_at,
            }
        )
        return result


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", str(value).casefold())


def redact_metric_detail(
    value: Any,
    *,
    max_length: int = MAX_PUBLIC_DETAIL_CHARS,
) -> str:
    """Return bounded diagnostic text with WeChat session fields removed."""
    detail = str(value).strip() or "article metric collection failed"
    for _ in range(2):
        decoded = unquote(detail)
        if decoded == detail:
            break
        detail = decoded
    detail = _redact_sensitive_fields(detail)
    detail = re.sub(r"[\x00-\x1f\x7f]+", " ", detail).strip()
    limit = max(32, int(max_length))
    if len(detail) > limit:
        detail = detail[: limit - 3].rstrip() + "..."
    return detail


def parse_count(value: Any) -> int | None:
    """Parse exact API counts and common UI forms such as ``1.2万``."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 and math.isfinite(value) else None

    text = unicodedata.normalize("NFKC", html_module.unescape(str(value))).strip()
    if not text:
        return None
    text = text.replace(",", "").replace("，", "").replace(" ", "")
    match = re.fullmatch(
        r"(?:阅读|点赞|在看|评论)?[:：]?(\d+(?:\.\d+)?)([千千万亿kKmMwW]?)(?:\+|以上)?(?:次|人)?",
        text,
    )
    if not match:
        return None
    number_text = match.group(1)
    digits = number_text.replace(".", "")
    if len(digits) > 128:
        return None
    unit = match.group(2).casefold()
    multiplier = {
        "": 1,
        "千": 1_000,
        "k": 1_000,
        "万": 10_000,
        "w": 10_000,
        "m": 1_000_000,
        "亿": 100_000_000,
    }.get(unit)
    if multiplier is None:
        return None
    try:
        whole, _, fraction = number_text.partition(".")
        numerator = int(whole + fraction)
        denominator = 10 ** len(fraction)
        return numerator * multiplier // denominator
    except (OverflowError, ValueError):
        return None


def _parse_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value) if value in (0, 1) else None
    if isinstance(value, float):
        return bool(value) if math.isfinite(value) and value in (0.0, 1.0) else None
    normalized = str(value).strip().casefold()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled", ""}:
        return False
    return None


def _first_count(
    mapping: Mapping[str, Any], aliases: Sequence[str]
) -> tuple[int | None, str | None]:
    normalized = {
        _normalized_key(key): (str(key), value) for key, value in mapping.items()
    }
    for alias in aliases:
        item = normalized.get(_normalized_key(alias))
        if item is None:
            continue
        count = parse_count(item[1])
        if count is not None:
            return count, item[0]
    return None, None


def _decode_payload_text(value: str | bytes) -> Any:
    if isinstance(value, bytes):
        if len(value) > MAX_JSON_BYTES:
            raise WeChatMetricsError("intercepted JSON response is too large")
        text = value.decode("utf-8-sig", errors="replace")
    else:
        text = value
        if len(text.encode("utf-8", errors="ignore")) > MAX_JSON_BYTES:
            raise WeChatMetricsError("intercepted JSON response is too large")
    text = text.strip().lstrip("\ufeff")
    if "\r\n\r\n" in text and text[:16].upper().startswith("HTTP/"):
        text = text.split("\r\n\r\n", 1)[1]
    text = re.sub(r"^\)\]\}',?\s*", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    open_index = min(
        (index for index in (text.find("{"), text.find("[")) if index >= 0),
        default=-1,
    )
    if open_index < 0:
        raise WeChatMetricsError("intercepted response does not contain JSON")
    decoder = json.JSONDecoder()
    try:
        value, _ = decoder.raw_decode(text[open_index:])
        return value
    except json.JSONDecodeError as exc:
        raise WeChatMetricsError("intercepted response contains invalid JSON") from exc


def coerce_payload(value: Any) -> Any:
    if isinstance(value, (str, bytes)):
        return _decode_payload_text(value)
    return value


def _iter_mappings(
    value: Any,
    path: tuple[str, ...] = (),
    depth: int = 0,
    budget: list[int] | None = None,
) -> Iterator[tuple[tuple[str, ...], Mapping[str, Any]]]:
    if budget is None:
        budget = [4_000]
    if depth > 12 or budget[0] <= 0:
        return
    budget[0] -= 1
    if isinstance(value, Mapping):
        yield path, value
        for key, item in value.items():
            if isinstance(item, (Mapping, list, tuple)):
                yield from _iter_mappings(item, path + (str(key),), depth + 1, budget)
            elif isinstance(item, str) and "{" in item and any(
                marker in item
                for marker in (
                    "appmsgstat",
                    "read_num",
                    "elected_comment",
                    "comment_total",
                )
            ):
                try:
                    decoded = _decode_payload_text(item)
                except WeChatMetricsError:
                    continue
                yield from _iter_mappings(
                    decoded, path + (str(key), "decoded"), depth + 1, budget
                )
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value[:1_000]):
            yield from _iter_mappings(item, path + (str(index),), depth + 1, budget)


def _base_response(payload: Any) -> Mapping[str, Any] | None:
    for _, mapping in _iter_mappings(payload):
        for key, value in mapping.items():
            if _normalized_key(key) in {"baseresp", "base_resp"} and isinstance(
                value, Mapping
            ):
                return value
    return None


def _validate_upstream_payload(payload: Any) -> None:
    base = _base_response(payload)
    if base is None:
        return
    ret: int | None = None
    for name in ("ret", "errcode", "error_code"):
        for key, value in base.items():
            if _normalized_key(key) != _normalized_key(name):
                continue
            try:
                ret = int(str(value).strip())
            except (TypeError, ValueError):
                ret = None
            break
        if ret is not None:
            break
    if ret in (None, 0):
        return
    message = redact_metric_detail(
        base.get("errmsg")
        or base.get("err_msg")
        or base.get("message")
        or "token rejected"
    )
    raise WeChatMetricsAuthError(f"WeChat metric session is invalid: {message}")


READ_EXACT_ALIASES = (
    "real_read_num",
    "real_read_count",
    "read_num_real",
    "真实阅读量",
)
READ_DISPLAY_ALIASES = (
    "read_num",
    "read_count",
    "reading_num",
    "view_count",
    "阅读量",
    "阅读数",
)
WOW_EXACT_ALIASES = (
    "real_like_num",
    "real_like_count",
)
WOW_DISPLAY_ALIASES = (
    "wow_count",
    "wow_num",
    "wow_like_num",
    "in_reading_num",
    "在看数",
    "在看",
    "like_num",
)
LIKE_ALIASES = (
    "old_like_num",
    "old_like_count",
    "like_count",
    "likes_count",
    "praise_num",
    "praise_count",
    "thumb_up_count",
    "点赞数",
    "点赞量",
)
COMMENT_ALIASES = (
    "comment_count",
    "comment_num",
    "comment_total",
    "comment_total_cnt",
    "comments_count",
    "评论数",
    "评论量",
)


def _metric_mapping(payload: Any) -> Mapping[str, Any] | None:
    candidates: list[tuple[int, Mapping[str, Any]]] = []
    metric_keys = {
        _normalized_key(alias)
        for alias in (
            *READ_EXACT_ALIASES,
            *READ_DISPLAY_ALIASES,
            *WOW_EXACT_ALIASES,
            *WOW_DISPLAY_ALIASES,
            *LIKE_ALIASES,
            *COMMENT_ALIASES,
        )
    }
    for path, mapping in _iter_mappings(payload):
        normalized = {_normalized_key(key) for key in mapping}
        overlap = len(normalized & metric_keys)
        if not overlap:
            continue
        score = overlap * 10
        if path and _normalized_key(path[-1]) == "appmsgstat":
            score += 100
        if normalized & {
            _normalized_key(alias) for alias in (*READ_EXACT_ALIASES, *READ_DISPLAY_ALIASES)
        }:
            score += 20
        candidates.append((score, mapping))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def parse_appmsgext_payload(value: Any) -> MetricSnapshot:
    payload = coerce_payload(value)
    _validate_upstream_payload(payload)
    mapping = _metric_mapping(payload)
    if mapping is None:
        raise WeChatMetricsError("getappmsgext response does not contain article metrics")

    exact_read, exact_key = _first_count(mapping, READ_EXACT_ALIASES)
    display_read, display_key = _first_count(mapping, READ_DISPLAY_ALIASES)
    exact_wow, exact_wow_key = _first_count(mapping, WOW_EXACT_ALIASES)
    display_wow, display_wow_key = _first_count(mapping, WOW_DISPLAY_ALIASES)
    wow = exact_wow if exact_wow not in (None, 0) else display_wow
    wow_key = exact_wow_key if exact_wow not in (None, 0) else display_wow_key
    likes, likes_key = _first_count(mapping, LIKE_ALIASES)
    comments, comments_key = _first_count(mapping, COMMENT_ALIASES)

    result = MetricSnapshot(
        read_count=exact_read if exact_read not in (None, 0) else display_read,
        display_read_count=display_read,
        wow_count=wow,
        like_count=likes,
        comment_count=comments,
    )
    if result.read_count is not None:
        result.basis["read_count"] = (
            exact_key if exact_read not in (None, 0) else display_key
        ) or "read_num"
    if display_read is not None:
        result.basis["display_read_count"] = display_key or "read_num"
    if wow is not None:
        result.basis["wow_count"] = wow_key or "like_num"
    if likes is not None:
        result.basis["like_count"] = likes_key or "old_like_num"
    if comments is not None:
        result.basis["comment_count"] = comments_key or "comment_count"
    return result


def _comment_from_mapping(value: Mapping[str, Any]) -> ArticleComment | None:
    content = str(
        value.get("content")
        or value.get("comment_content")
        or value.get("text")
        or ""
    ).strip()
    if not content:
        return None
    content_id = value.get("content_id") or value.get("comment_id") or value.get("id")
    nickname = value.get("nick_name") or value.get("nickname") or value.get("author")
    create_time = parse_count(value.get("create_time") or value.get("timestamp"))
    like_count, _ = _first_count(
        value, ("like_num", "like_count", "old_like_num", "点赞数")
    )
    reply = value.get("reply")
    reply_list: list[Any] = []
    if isinstance(reply, Mapping) and isinstance(reply.get("reply_list"), list):
        reply_list = reply["reply_list"]
    elif isinstance(value.get("reply_list"), list):
        reply_list = value["reply_list"]
    return ArticleComment(
        content_id=str(content_id) if content_id is not None else None,
        nickname=str(nickname).strip() if nickname else None,
        content=content,
        create_time=create_time,
        like_count=like_count,
        is_top=bool(_parse_bool(value.get("is_top")) or False),
        reply_count=len(reply_list),
    )


def _comment_mapping(payload: Any) -> Mapping[str, Any] | None:
    best: tuple[int, Mapping[str, Any]] | None = None
    for _, mapping in _iter_mappings(payload):
        normalized = {_normalized_key(key) for key in mapping}
        score = 0
        if "electedcomment" in normalized:
            score += 100
        if "electedcommenttotalcnt" in normalized:
            score += 50
        if "friendcomment" in normalized or "mycomment" in normalized:
            score += 10
        if score and (best is None or score > best[0]):
            best = (score, mapping)
    return best[1] if best else None


def parse_comment_payload(value: Any) -> CommentSnapshot:
    payload = coerce_payload(value)
    _validate_upstream_payload(payload)
    mapping = _comment_mapping(payload)
    if mapping is None:
        raise WeChatMetricsError("comment response does not contain comment data")

    comments: list[ArticleComment] = []
    seen: set[tuple[str, str, str]] = set()
    for name in ("elected_comment", "friend_comment", "my_comment", "comments"):
        values = mapping.get(name)
        if not isinstance(values, list):
            continue
        for raw in values:
            if not isinstance(raw, Mapping):
                continue
            comment = _comment_from_mapping(raw)
            if comment is None:
                continue
            key = (
                comment.content_id or "",
                comment.nickname or "",
                comment.content,
            )
            if key not in seen:
                seen.add(key)
                comments.append(comment)

    total, total_key = _first_count(
        mapping,
        (
            "comment_total_cnt",
            "comment_total",
            "comment_count",
            "elected_comment_total_cnt",
            "comments_count",
        ),
    )
    enabled_value = mapping.get("enabled")
    enabled = _parse_bool(enabled_value)
    if total is None and enabled is False:
        total = 0
        total_key = "enabled"
    if total is None and comments:
        total = len(comments)
        total_key = "returned_comments"
    return CommentSnapshot(total, comments, enabled, total_key)


def _decode_js_string(value: str) -> str:
    value = html_module.unescape(value)
    value = re.sub(
        r"\\u([0-9a-fA-F]{4})",
        lambda match: chr(int(match.group(1), 16)),
        value,
    )
    value = re.sub(
        r"\\x([0-9a-fA-F]{2})",
        lambda match: chr(int(match.group(1), 16)),
        value,
    )
    return (
        value.replace(r"\/", "/")
        .replace(r"\'", "'")
        .replace(r'\"', '"')
        .replace(r"\\", "\\")
        .strip()
    )


JS_CONTEXT_KEYS = {
    "biz",
    "__biz",
    "mid",
    "app_msgid",
    "idx",
    "sn",
    "comment_id",
    "appmsg_token",
    "msg_title",
    "title",
    "nickname",
    "nick_name",
    "ct",
    "appmsg_type",
    "appmsg_like_type",
    "item_show_type",
    "is_only_read",
    "req_id",
    "scene",
}
ASSIGNMENT_PATTERN = re.compile(
    r"(?:\b(?:var|let|const)\s+)?"
    r"(?P<name>__biz|biz|mid|app_msgid|idx|sn|comment_id|appmsg_token|"
    r"msg_title|title|nickname|nick_name|ct|appmsg_type|appmsg_like_type|"
    r"item_show_type|is_only_read|req_id|scene)\s*=\s*"
    r"(?P<value>[^;\r\n]{1,2048})",
    re.IGNORECASE,
)
QUOTED_VALUE_PATTERN = re.compile(r"(?P<quote>['\"])(?P<value>(?:\\.|(?!\1).)*?)\1")


def _expression_value(expression: str) -> str | None:
    for match in QUOTED_VALUE_PATTERN.finditer(expression):
        value = _decode_js_string(match.group("value"))
        if value and value.casefold() not in {"null", "undefined"}:
            return value
    match = re.search(r"(?<![\w.])-?\d+(?![\w.])", expression)
    return match.group(0) if match else None


def _script_context(html_text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for match in ASSIGNMENT_PATTERN.finditer(html_text):
        name = match.group("name").casefold()
        if name not in JS_CONTEXT_KEYS or name in result:
            continue
        value = _expression_value(match.group("value"))
        if value is not None:
            result[name] = value
    if "comment_id" not in result:
        legacy = re.search(r"comment_id\.DATA['\"]?\)\s*:\s*['\"](\d+)", html_text)
        if legacy:
            result["comment_id"] = legacy.group(1)
    return result


def _meta_context(html_text: str) -> dict[str, str]:
    if lxml_html is None or "<" not in html_text:
        return {}
    try:
        root = lxml_html.fromstring(html_text)
    except (ValueError, TypeError):
        return {}
    result: dict[str, str] = {}
    for prop, target in (
        ("og:title", "title"),
        ("twitter:title", "title"),
        ("og:url", "url"),
    ):
        values = root.xpath(
            "//meta[translate(@property,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')=$prop]/@content"
            " | //meta[translate(@name,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')=$prop]/@content",
            prop=prop,
        )
        if values and str(values[0]).strip():
            result.setdefault(target, html_module.unescape(str(values[0]).strip()))
    return result


def _sanitize_url(url: str) -> str:
    parsed = urlsplit(html_module.unescape(url.strip()))
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if _normalized_token_key(key) not in TOKEN_QUERY_ALIASES
    ]
    sanitized = urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), "")
    )
    return _redact_sensitive_fields(sanitized)


def sanitize_article_url(url: str | None) -> str:
    """Return an article URL with credential query fields removed."""

    try:
        return _sanitize_url(str(url or "").strip())
    except (TypeError, ValueError):
        return ""


def _url_context(url: str | None, *, strict: bool = False) -> ArticleContext:
    if not url:
        return ArticleContext()
    normalized = html_module.unescape(str(url).strip())
    if normalized.startswith("//"):
        normalized = "https:" + normalized
    elif normalized.startswith("mp.weixin.qq.com/"):
        normalized = "https://" + normalized
    parsed = urlsplit(normalized)
    try:
        port = parsed.port
    except ValueError as exc:
        raise WeChatArticleUrlError("article URL has an invalid authority") from exc
    if strict and (
        parsed.scheme.casefold() != "https"
        or parsed.hostname != "mp.weixin.qq.com"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise WeChatArticleUrlError(
            "live collection only accepts HTTPS mp.weixin.qq.com URLs"
        )
    values: dict[str, str] = {}
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        values.setdefault(key.casefold().replace("-", "_"), value)
        canonical = TOKEN_QUERY_ALIASES.get(_normalized_token_key(key))
        if canonical:
            values.setdefault(canonical, value)
    return ArticleContext(
        url=_sanitize_url(normalized),
        biz=values.get("__biz") or values.get("biz"),
        mid=values.get("mid") or values.get("app_msgid") or values.get("appmsgid"),
        idx=values.get("idx") or values.get("itemidx"),
        sn=values.get("sn"),
        comment_id=values.get("comment_id"),
        appmsg_token=values.get("appmsg_token") or values.get("appmsgtoken"),
        scene=values.get("scene") or "0",
    )


def _is_supported_live_article_url(url: str) -> bool:
    """Allow canonical query URLs and copied ``/s/<slug>`` article links."""

    try:
        context = _url_context(url, strict=True)
        parsed = urlsplit(html_module.unescape(str(url).strip()))
    except (TypeError, ValueError, WeChatArticleUrlError):
        return False
    if context.biz and context.mid and context.idx:
        return parsed.path == "/s"
    return re.fullmatch(r"/s/[A-Za-z0-9_-]+", parsed.path) is not None


def _mapping_context(value: Any) -> ArticleContext:
    payload = coerce_payload(value) if isinstance(value, (str, bytes)) else value
    result = ArticleContext()
    wanted = {
        "biz": "biz",
        "__biz": "biz",
        "mid": "mid",
        "app_msgid": "mid",
        "appmsgid": "mid",
        "idx": "idx",
        "sn": "sn",
        "comment_id": "comment_id",
        "appmsg_token": "appmsg_token",
        "scene": "scene",
    }
    for _, mapping in _iter_mappings(payload):
        for key, item in mapping.items():
            normalized = str(key).casefold()
            target = wanted.get(normalized)
            current = getattr(result, target) if target else None
            if (
                target
                and (current in (None, "") or (target == "scene" and current == "0"))
                and item is not None
            ):
                setattr(result, target, str(item).strip())
            if normalized in {"url", "request_url", "article_url"} and not result.url:
                candidate = str(item)
                if "mp.weixin.qq.com" in candidate:
                    result.merge_missing(_url_context(candidate))
    return result


def _merge_context_hint(
    context: ArticleContext,
    hint: ArticleContext,
) -> ArticleContext:
    """Merge a coherent Native identity without mixing conflicting articles."""

    for name in ("biz", "mid", "idx"):
        current = getattr(context, name)
        incoming = getattr(hint, name)
        if current not in (None, "") and incoming not in (None, "", current):
            return context
    context.merge_missing(hint)
    if context.scene in (None, "", "0") and hint.scene not in (None, "", "0"):
        context.scene = hint.scene
    return context


def extract_article_context(
    url: str | None = None,
    html: str | None = None,
    payloads: Sequence[Any] = (),
) -> ArticleContext:
    context = _url_context(url)
    if html:
        if len(html.encode("utf-8", errors="ignore")) > MAX_HTML_BYTES:
            raise WeChatMetricsError("article HTML is too large")
        script = _script_context(html)
        meta = _meta_context(html)
        html_url = meta.get("url")
        if html_url:
            context.merge_missing(_url_context(html_url))
        html_context = ArticleContext(
            biz=script.get("__biz") or script.get("biz"),
            mid=script.get("mid") or script.get("app_msgid"),
            idx=script.get("idx"),
            sn=script.get("sn"),
            comment_id=script.get("comment_id"),
            appmsg_token=script.get("appmsg_token"),
            title=script.get("msg_title") or script.get("title") or meta.get("title"),
            author=script.get("nickname") or script.get("nick_name"),
            publish_time=parse_count(script.get("ct")),
            appmsg_type=script.get("appmsg_type") or "9",
            appmsg_like_type=script.get("appmsg_like_type"),
            item_show_type=script.get("item_show_type"),
            is_only_read=script.get("is_only_read") or "1",
            req_id=script.get("req_id"),
            scene=script.get("scene") or "0",
        )
        context.merge_missing(html_context)
    for payload in payloads:
        if payload is not None:
            context.merge_missing(_mapping_context(payload))
    return context


def _balanced_object(text: str, start: int) -> str | None:
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(start, min(len(text), start + MAX_JSON_BYTES)):
        character = text[index]
        if quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {'"', "'"}:
            quote = character
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def extract_metrics_from_html(html_text: str | None) -> MetricSnapshot:
    if not html_text:
        return MetricSnapshot()
    if len(html_text.encode("utf-8", errors="ignore")) > MAX_HTML_BYTES:
        raise WeChatMetricsError("article HTML is too large")
    for marker in ("appmsgstat", "appmsg_stat"):
        for match in re.finditer(marker, html_text, re.IGNORECASE):
            start = html_text.find("{", match.end())
            if start < 0 or start - match.end() > 256:
                continue
            candidate = _balanced_object(html_text, start)
            if not candidate:
                continue
            try:
                return parse_appmsgext_payload(candidate)
            except WeChatMetricsError:
                continue

    fields: dict[str, str] = {}
    field_pattern = re.compile(
        r"['\"]?(real_read_num|read_num|real_like_num|old_like_num|like_num|"
        r"wow_count|comment_count|comment_num)['\"]?\s*[:=]\s*"
        r"['\"]?([0-9][0-9.,+\u4e07\u5343\u4ebfkKmMwW]*)",
        re.IGNORECASE,
    )
    for key, value in field_pattern.findall(html_text):
        fields.setdefault(key, value)
    if not any(key.casefold() in {"read_num", "real_read_num"} for key in fields):
        return MetricSnapshot()
    try:
        return parse_appmsgext_payload({"appmsgstat": fields})
    except WeChatMetricsError:
        return MetricSnapshot()


def _authenticated_article_url(url: str, auth: ArticleAuth) -> str:
    parsed = urlsplit(url)
    query = {
        key: value
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if _normalized_token_key(key) not in TOKEN_QUERY_ALIASES
    }
    query.update(
        {
            "key": auth.key,
            "uin": auth.uin,
            "pass_ticket": auth.pass_ticket,
            "wxtoken": auth.wxtoken,
            "devicetype": auth.device_type,
            "clientversion": auth.client_version,
        }
    )
    if auth.appmsg_token:
        query.setdefault("appmsg_token", auth.appmsg_token)
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


def _canonical_article_url(context: ArticleContext) -> str:
    context.require_identity()
    query = {
        "__biz": context.biz or "",
        "mid": context.mid or "",
        "idx": context.idx or "",
    }
    if context.sn:
        query["sn"] = context.sn
    return "https://mp.weixin.qq.com/s?" + urlencode(query)


def _response_text(response: httpx.Response, limit: int) -> str:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise WeChatMetricsUpstreamError(
            f"WeChat endpoint returned HTTP {response.status_code}"
        ) from exc
    content_length = response.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > limit:
                raise WeChatMetricsUpstreamError("WeChat response is too large")
        except ValueError:
            pass
    content = bytearray()
    for chunk in response.iter_bytes(chunk_size=STREAM_CHUNK_BYTES):
        if len(content) + len(chunk) > limit:
            raise WeChatMetricsUpstreamError("WeChat response is too large")
        content.extend(chunk)
    encoding = response.encoding or "utf-8"
    try:
        return bytes(content).decode(encoding, errors="replace")
    except LookupError:
        return bytes(content).decode("utf-8", errors="replace")


class WeChatArticleMetricsClient:
    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        timeout: float = 20.0,
    ) -> None:
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "WeChatArticleMetricsClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    @staticmethod
    def _headers(auth: ArticleAuth, referer: str | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": auth.user_agent,
        }
        if referer:
            headers["Referer"] = referer
        if auth.cookie:
            headers["Cookie"] = auth.cookie
        return headers

    def fetch_html(self, url: str, auth: ArticleAuth) -> str:
        auth.validate()
        if not _is_supported_live_article_url(url):
            raise WeChatArticleUrlError(
                "live collection requires a WeChat /s article URL"
            )
        current_url = _authenticated_article_url(url, auth)
        referer: str | None = None
        for _ in range(MAX_ARTICLE_REDIRECTS + 1):
            if not _is_supported_live_article_url(current_url):
                raise WeChatMetricsUpstreamError(
                    "WeChat article redirect left the allowed article path"
                )
            try:
                with self.client.stream(
                    "GET",
                    current_url,
                    headers=self._headers(auth, referer),
                    follow_redirects=False,
                ) as response:
                    if response.status_code not in ARTICLE_REDIRECT_STATUSES:
                        return _response_text(response, MAX_HTML_BYTES)
                    location = response.headers.get("location")
            except httpx.HTTPError as exc:
                raise WeChatMetricsUpstreamError(
                    "failed to load WeChat article HTML"
                ) from exc
            if not location:
                raise WeChatMetricsUpstreamError(
                    "WeChat article redirect did not include a location"
                )
            next_url = urljoin(current_url, location)
            if not _is_supported_live_article_url(next_url):
                raise WeChatMetricsUpstreamError(
                    "WeChat article redirect left the allowed HTTPS host"
                )
            referer = current_url
            current_url = next_url
        raise WeChatMetricsUpstreamError("too many WeChat article redirects")

    def fetch_appmsgext(
        self,
        context: ArticleContext,
        auth: ArticleAuth,
        referer: str,
    ) -> MetricSnapshot:
        auth.validate()
        context.require_identity()
        appmsg_token = auth.appmsg_token or context.appmsg_token or ""
        params = {
            "__biz": context.biz or "",
            "appmsg_token": appmsg_token,
            "key": auth.key,
            "uin": auth.uin,
            "pass_ticket": auth.pass_ticket,
            "wxtoken": auth.wxtoken,
            "devicetype": auth.device_type,
            "clientversion": auth.client_version,
            "x5": "0",
            "f": "json",
        }
        data = {
            "r": "0.5",
            "__biz": context.biz or "",
            "appmsg_type": context.appmsg_type or "9",
            "mid": context.mid or "",
            "sn": context.sn or "",
            "idx": context.idx or "",
            "scene": context.scene or "0",
            "subscene": getattr(context, "subscene", "") or "0",
            "ascene": getattr(context, "ascene", "") or "0",
            "title": context.title or "",
            "ct": str(context.publish_time or ""),
            "abtest_cookie": "",
            "is_need_ticket": "0",
            "is_need_ad": "0",
            "comment_id": context.comment_id or "",
            "is_need_reward": "0",
            "both_ad": "0",
            "reward_uin_count": "0",
            "send_time": "",
            "msg_daily_idx": "0",
            "is_original": "0",
            "is_only_read": context.is_only_read or "1",
            "req_id": context.req_id or "",
            "pass_ticket": auth.pass_ticket,
            "is_temp_url": "0",
            "item_show_type": context.item_show_type or "0",
            "tmp_version": "1",
            "more_read_type": "0",
            "appmsg_like_type": context.appmsg_like_type or "2",
            "related_video_sn": "",
            "related_video_num": "0",
            "vid": "",
            "is_pay_subscribe": "0",
            "pay_subscribe_uin_count": "0",
            "has_red_packet_cover": "0",
            "album_video_num": "0",
            "cur_album_id": "",
            "is_public_related_video": "0",
            "encode_info_by_base64": "",
            "exptype": "",
            "export_key": "",
            "export_key_extinfo": "",
            "segment_comment_id": "",
            "business_type": "0",
            "key": auth.key,
            "uin": auth.uin,
            "wxtoken": auth.wxtoken,
            "devicetype": auth.device_type,
            "clientversion": auth.client_version,
            "appmsg_token": appmsg_token,
            "x5": "0",
            "f": "json",
        }
        try:
            with self.client.stream(
                "POST",
                GET_APPMSGEXT_URL,
                params=params,
                data=data,
                headers=self._headers(auth, referer),
                follow_redirects=False,
            ) as response:
                response_text = _response_text(response, MAX_JSON_BYTES)
        except httpx.HTTPError as exc:
            raise WeChatMetricsUpstreamError("getappmsgext request failed") from exc
        return parse_appmsgext_payload(response_text)

    def fetch_comments(
        self,
        context: ArticleContext,
        auth: ArticleAuth,
        referer: str,
        *,
        limit: int = 100,
        max_pages: int = 3,
    ) -> CommentSnapshot:
        auth.validate()
        context.require_identity()
        if not context.comment_id:
            return CommentSnapshot()
        limit = max(1, min(int(limit), 100))
        max_pages = max(1, min(int(max_pages), 10))
        combined = CommentSnapshot()
        seen: set[tuple[str, str, str]] = set()
        for page in range(max_pages):
            offset = page * limit
            params = {
                "action": "getcomment",
                "scene": context.scene or "0",
                "__biz": context.biz or "",
                "app_msgid": context.mid or "",
                "mid": context.mid or "",
                "idx": context.idx or "",
                "comment_id": context.comment_id,
                "offset": str(offset),
                "limit": str(limit),
                "uin": auth.uin,
                "key": auth.key,
                "pass_ticket": auth.pass_ticket,
                "wxtoken": auth.wxtoken,
                "devicetype": auth.device_type,
                "clientversion": auth.client_version,
                "appmsg_token": auth.appmsg_token or context.appmsg_token or "",
                "x5": "0",
                "f": "json",
            }
            try:
                with self.client.stream(
                    "GET",
                    GET_COMMENT_URL,
                    params=params,
                    headers=self._headers(auth, referer),
                    follow_redirects=False,
                ) as response:
                    response_text = _response_text(response, MAX_JSON_BYTES)
            except httpx.HTTPError as exc:
                raise WeChatMetricsUpstreamError("comment request failed") from exc
            page_result = parse_comment_payload(response_text)
            combined.total_count = page_result.total_count
            combined.enabled = page_result.enabled
            combined.basis = page_result.basis
            added = 0
            for comment in page_result.comments:
                key = (
                    comment.content_id or "",
                    comment.nickname or "",
                    comment.content,
                )
                if key not in seen:
                    seen.add(key)
                    combined.comments.append(comment)
                    added += 1
            if not page_result.comments or not added:
                break
            if combined.total_count is not None and len(combined.comments) >= combined.total_count:
                break
            if combined.total_count is None and len(page_result.comments) < limit:
                break
        return combined


def collect_article_metrics(
    *,
    url: str | None = None,
    html: str | None = None,
    appmsgext: Any = None,
    comments: Any = None,
    auth: ArticleAuth | Mapping[str, Any] | None = None,
    context_hint: ArticleContext | Mapping[str, Any] | None = None,
    fetch_missing: bool = False,
    include_comments: bool = True,
    comment_limit: int = 100,
    max_comment_pages: int = 3,
    client: WeChatArticleMetricsClient | None = None,
) -> ArticleMetrics:
    """Collect article metrics from intercepted data, HTML, or live endpoints."""
    context = extract_article_context(url, html, (appmsgext, comments))
    if isinstance(context_hint, ArticleContext):
        hint_context = context_hint
    elif context_hint is not None:
        hint_context = _mapping_context(context_hint)
    else:
        hint_context = None
    if hint_context is not None:
        _merge_context_hint(context, hint_context)
    metrics = extract_metrics_from_html(html)
    sources: list[str] = ["article_html"] if metrics.has_values() else []
    warnings: list[str] = []

    if appmsgext is not None:
        metrics.overlay(parse_appmsgext_payload(appmsgext))
        sources.append("intercepted_getappmsgext")

    auth_value: ArticleAuth | None
    if auth is None:
        auth_value = None
    elif isinstance(auth, ArticleAuth):
        auth.validate()
        auth_value = auth
    else:
        auth_value = ArticleAuth.from_mapping(auth)
    if auth_value is None and fetch_missing and url:
        auth_value = ArticleAuth.from_url(url)

    own_client = False
    metric_client = client
    try:
        if fetch_missing and appmsgext is None:
            if auth_value is None:
                raise WeChatMetricsAuthError("live collection requires current article tokens")
            live_url = url or _canonical_article_url(context)
            if not _is_supported_live_article_url(live_url):
                raise WeChatArticleUrlError(
                    "live collection requires a WeChat /s article URL"
                )
            if metric_client is None:
                metric_client = WeChatArticleMetricsClient()
                own_client = True
            if html is None:
                html = metric_client.fetch_html(live_url, auth_value)
                context = extract_article_context(live_url, html, (appmsgext, comments))
                if hint_context is not None:
                    _merge_context_hint(context, hint_context)
            context.require_identity()
            referer = _authenticated_article_url(live_url, auth_value)
            live_metrics = metric_client.fetch_appmsgext(context, auth_value, referer)
            metrics.overlay(live_metrics)
            sources.append("live_getappmsgext")

        if (
            include_comments
            and comments is None
            and fetch_missing
            and context.comment_id
            and metric_client is None
        ):
            if auth_value is None:
                raise WeChatMetricsAuthError(
                    "live comment collection requires current article tokens"
                )
            metric_client = WeChatArticleMetricsClient()
            own_client = True

        comment_snapshot = CommentSnapshot()
        if comments is not None:
            comment_snapshot = parse_comment_payload(comments)
            sources.append("intercepted_comments")
        elif include_comments and fetch_missing and context.comment_id:
            if auth_value is None or metric_client is None:
                raise WeChatMetricsAuthError(
                    "live comment collection requires current article tokens"
                )
            live_url = url or context.url or _canonical_article_url(context)
            referer = _authenticated_article_url(live_url, auth_value)
            try:
                comment_snapshot = metric_client.fetch_comments(
                    context,
                    auth_value,
                    referer,
                    limit=comment_limit,
                    max_pages=max_comment_pages,
                )
                sources.append("live_comments")
            except WeChatMetricsError as exc:
                warnings.append(redact_metric_detail(exc))
    finally:
        if own_client and metric_client is not None:
            metric_client.close()

    if comment_snapshot.total_count is not None:
        metrics.comment_count = comment_snapshot.total_count
        metrics.basis["comment_count"] = comment_snapshot.basis or "comment_response"

    if not metrics.has_values():
        raise WeChatMetricsError(
            "no article metrics found; submit an intercepted response or enable live fetching"
        )
    return ArticleMetrics(
        context=context,
        read_count=metrics.read_count,
        display_read_count=metrics.display_read_count,
        wow_count=metrics.wow_count,
        like_count=metrics.like_count,
        comment_count=metrics.comment_count,
        comments=comment_snapshot.comments,
        metric_basis=metrics.basis,
        sources=sources,
        warnings=warnings,
    )


# Explicit aliases make the pure parsers easy to reuse from scripts and tests.
parse_metrics_response = parse_appmsgext_payload
parse_comments_response = parse_comment_payload
