from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import zstandard

try:
    from lxml import etree
except ImportError:  # pragma: no cover - requirements include lxml
    etree = None


ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
GROUP_PREFIX = re.compile(r"^([^\n:]{2,160}):\n(.*)$", re.DOTALL)
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
CDATA_PATTERN = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL | re.IGNORECASE)
MARKUP_PATTERN = re.compile(
    r"<\?xml[^>]*\?>|<!--.*?-->|</?[A-Za-z][^>]*>|<![^>]*>",
    re.DOTALL | re.IGNORECASE,
)
WECHAT_ACCOUNT_PATTERN = re.compile(
    r"\b(?:wxid_[A-Za-z0-9_-]+|gh_[A-Za-z0-9_-]+|[0-9]+@chatroom)\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class DecodedValue:
    text: str
    decoded: bool
    error: str | None = None
    compressed: bool = False


@dataclass(slots=True)
class ParsedContent:
    content: str
    summary: str
    category: str
    title: str | None = None
    description: str | None = None
    url: str | None = None
    file_name: str | None = None
    file_size: int | None = None
    app_type: int | None = None
    sender_prefix: str | None = None
    reference_server_id: int | None = None
    reference_from_username: str | None = None
    reference_chat_username: str | None = None
    reference_display_name: str | None = None
    reference_content: str | None = None


def split_local_type(local_type: int) -> tuple[int, int]:
    unsigned = int(local_type) & 0xFFFFFFFFFFFFFFFF
    return unsigned & 0xFFFFFFFF, unsigned >> 32


def normalize_human_text(value: Any) -> str:
    """Remove transport markup while preserving visible human-authored text."""
    if value is None:
        return ""
    text = str(value).replace("\x00", " ")
    for _ in range(2):
        text = html.unescape(text)
    text = CDATA_PATTERN.sub(lambda match: match.group(1), text)
    text = MARKUP_PATTERN.sub(" ", text)
    text = URL_PATTERN.sub(" ", text)
    text = WECHAT_ACCOUNT_PATTERN.sub(" ", text)
    text = unicodedata.normalize("NFKC", text)
    text = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in text
    )
    text = re.sub(r"\b\d{5,}\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _safe_http_url(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip()
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return None
    return candidate if parsed.scheme.lower() in {"http", "https"} and parsed.netloc else None


def is_valid_keyword_token(value: Any) -> bool:
    token = unicodedata.normalize("NFKC", str(value or "")).strip()
    return len(token) > 1 and any(character.isalpha() for character in token)


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(data) and shift < 70:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
    raise ValueError("invalid protobuf varint")


def extract_protobuf_strings(data: bytes | None, max_depth: int = 3) -> list[str]:
    """Best-effort extraction of UTF-8 length-delimited protobuf fields."""
    if not data:
        return []
    results: list[str] = []

    def walk(payload: bytes, depth: int) -> None:
        offset = 0
        while offset < len(payload):
            try:
                tag, offset = _read_varint(payload, offset)
                wire_type = tag & 7
                if wire_type == 0:
                    _, offset = _read_varint(payload, offset)
                elif wire_type == 1:
                    offset += 8
                elif wire_type == 2:
                    length, offset = _read_varint(payload, offset)
                    if length < 0 or offset + length > len(payload):
                        return
                    chunk = payload[offset : offset + length]
                    offset += length
                    try:
                        text = chunk.decode("utf-8")
                    except UnicodeDecodeError:
                        text = ""
                    cleaned = re.sub(r"[\x00-\x1f\x7f]+", " ", text).strip()
                    printable = sum(character.isprintable() for character in cleaned)
                    if cleaned and len(cleaned) >= 2 and printable / len(cleaned) >= 0.9:
                        results.append(cleaned[:1000])
                    if depth < max_depth and chunk:
                        walk(chunk, depth + 1)
                elif wire_type == 5:
                    offset += 4
                else:
                    return
            except (IndexError, ValueError):
                return

    walk(bytes(data), 0)
    return list(dict.fromkeys(results))


def decode_wcdb_value(value: Any) -> DecodedValue:
    if value is None:
        return DecodedValue("", True)
    if isinstance(value, str):
        return DecodedValue(value, True)
    if isinstance(value, memoryview):
        value = value.tobytes()
    if not isinstance(value, bytes):
        return DecodedValue(str(value), True)

    payload = value
    compressed = payload.startswith(ZSTD_MAGIC)
    if compressed:
        try:
            payload = zstandard.ZstdDecompressor().decompress(
                payload, max_output_size=64 * 1024 * 1024
            )
        except zstandard.ZstdError as exc:
            return DecodedValue("", False, f"zstd:{exc}", True)
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return DecodedValue(payload.decode(encoding), True, compressed=compressed)
        except UnicodeDecodeError:
            continue
    return DecodedValue(
        payload.decode("utf-8", errors="replace"),
        False,
        "text:invalid-encoding",
        compressed,
    )


def _first_text(root: Any, *paths: str) -> str | None:
    for path in paths:
        values = root.xpath(path)
        for value in values:
            if hasattr(value, "itertext"):
                text = "".join(value.itertext())
            else:
                text = str(value)
            text = text.strip()
            if text:
                return text
    return None


def _integer(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_xml(text: str) -> Any | None:
    if etree is None or "<" not in text:
        return None
    payload = text.replace("\x00", "").encode("utf-8", errors="replace")
    parser = etree.XMLParser(
        recover=True,
        resolve_entities=False,
        no_network=True,
        remove_blank_text=False,
        huge_tree=False,
    )
    try:
        return etree.fromstring(payload, parser=parser)
    except (ValueError, etree.XMLSyntaxError):
        return None


def _category_from_types(base_type: int, app_subtype: int, app_type: int | None) -> str:
    if base_type == 1:
        return "text"
    if base_type == 3:
        return "image"
    if base_type == 34:
        return "voice"
    if base_type == 37:
        return "friend_request"
    if base_type == 43:
        return "video"
    if base_type == 47:
        return "sticker"
    if base_type == 48:
        return "location"
    if base_type == 50:
        return "call"
    if base_type == 51:
        return "status"
    if base_type == 42:
        return "contact_card"
    if base_type == 49:
        subtype = app_type if app_type is not None else app_subtype
        if app_subtype == 57 or subtype == 57:
            return "quote"
        if subtype == 6:
            return "file"
        if subtype in (3, 5, 33, 36):
            return "link"
        if subtype in (19, 24):
            return "chat_history"
        if subtype in (2000, 2001):
            return "transfer"
        return "app"
    if base_type in (10000, 10002):
        return "system"
    return "other"


def _compact(value: str | None, limit: int = 180) -> str:
    if not value:
        return ""
    value = re.sub(r"\s+", " ", value).strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


def parse_message_content(
    decoded_text: str, base_type: int, app_subtype: int, is_group: bool
) -> ParsedContent:
    sender_prefix = None
    text = decoded_text.strip()
    if is_group:
        match = GROUP_PREFIX.match(text)
        if match:
            sender_prefix = match.group(1).strip()
            text = match.group(2).strip()

    root = _parse_xml(text)
    app_type = None
    title = description = url = file_name = None
    file_size = None
    reference_server_id = None
    reference_from_username = None
    reference_chat_username = None
    reference_display_name = None
    reference_content = None

    if root is not None:
        app_type = _integer(_first_text(root, "//appmsg/type/text()"))
        title = _first_text(root, "//appmsg/title/text()", "//title/text()")
        description = _first_text(
            root, "//appmsg/des/text()", "//description/text()", "//desc/text()"
        )
        url = _safe_http_url(_first_text(root, "//appmsg/url/text()", "//url/text()"))
        file_name = _first_text(
            root,
            "//appmsg/appattach/filename/text()",
            "//appmsg/title/text()",
            "//filename/text()",
        )
        file_size = _integer(
            _first_text(
                root,
                "//appmsg/appattach/totallen/text()",
                "//appattach/filesize/text()",
            )
        )
        reference_server_id = _integer(_first_text(root, "//refermsg/svrid/text()"))
        reference_from_username = _first_text(root, "//refermsg/fromusr/text()")
        reference_chat_username = _first_text(root, "//refermsg/chatusr/text()")
        reference_display_name = _first_text(root, "//refermsg/displayname/text()")
        reference_content = _first_text(root, "//refermsg/content/text()")

        if not url:
            matches = URL_PATTERN.findall(text)
            url = _safe_http_url(matches[0]) if matches else None

    category = _category_from_types(base_type, app_subtype, app_type)
    if base_type == 49 and (app_type == 57 or app_subtype == 57 or (not app_type and not app_subtype and (reference_server_id is not None or reference_content))):
        category = "quote"
    if category == "text":
        summary = _compact(text)
    elif category == "image":
        summary = title or "[图片]"
    elif category == "video":
        summary = title or "[视频]"
    elif category == "voice":
        summary = "[语音]"
    elif category == "sticker":
        summary = "[表情]"
    elif category == "file":
        summary = _compact(file_name or title or "[文件]")
    elif category == "link":
        summary = _compact(title or description or url or "[链接]")
    elif category == "quote":
        summary = _compact(title or reference_content or description or "[引用消息]")
    elif category == "system":
        summary = _compact(text or "[系统消息]")
    else:
        summary = _compact(title or description or text or f"[{category}]")

    return ParsedContent(
        content=text,
        summary=summary,
        category=category,
        title=_compact(title, 500) or None,
        description=_compact(description, 1000) or None,
        url=url,
        file_name=_compact(file_name, 500) or None,
        file_size=file_size,
        app_type=app_type,
        sender_prefix=sender_prefix,
        reference_server_id=reference_server_id,
        reference_from_username=reference_from_username,
        reference_chat_username=reference_chat_username,
        reference_display_name=reference_display_name,
        reference_content=_compact(reference_content, 1000) or None,
    )


def message_quality_score(content: str, category: str) -> int:
    """Deterministic contribution score; >= 2 counts as effective."""
    stripped = content.strip()
    score = 0
    if len(stripped) >= 20:
        score += 1
    if len(stripped) >= 80:
        score += 1
    if category in {"file", "link", "image", "video"}:
        score += 1
    if "\n" in stripped or URL_PATTERN.search(stripped):
        score += 1
    return score
