from __future__ import annotations

import base64
import html
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


FinderPayload = Dict[str, Any]

_MAX_TEXT_BYTES = 32 * 1024 * 1024


class FinderResponseTooLargeError(OSError):
    pass


# Search responses may contain hundreds of small JSON fragments before the
# nested `resp_json`/`Json` result. Keep scanning long enough to reach it when
# the outer response is wrapped in logging text and cannot be decoded whole.
_JSON_SCAN_LIMIT = 2048
_JSON_STRING_SCAN_LIMIT = 262144


def _clean_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for _ in range(3):
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped
    return text.strip()


def _first_nonempty(*values: Any) -> str:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return ""


def _first_nonzero_id(*values: Any) -> str:
    for value in values:
        text = _clean_text(value)
        if text and text != "0":
            return text
    return ""


def _first_text_from_list(value: Any) -> str:
    for item in _as_list(value):
        text = _clean_text(item)
        if text:
            return text
    return ""


def _display_text(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    return _clean_text(text)


def _duration_to_seconds(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    if re.fullmatch(r"\d+", text):
        return text
    parts = text.split(":")
    if not all(re.fullmatch(r"\d+", part or "") for part in parts):
        return text
    total = 0
    for part in parts:
        total = total * 60 + int(part)
    return str(total)


def _encode_uint64_base64url(value: Any) -> str:
    text = _clean_text(value)
    if not re.fullmatch(r"[1-9][0-9]{7,19}", text):
        return ""
    number = int(text)
    if number > (1 << 64) - 1:
        return ""
    return base64.urlsafe_b64encode(number.to_bytes(8, "big")).decode("ascii").rstrip("=")


def _decode_uint64_base64url(value: Any) -> str:
    text = _clean_text(value)
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", text):
        return ""
    try:
        raw = base64.urlsafe_b64decode(text + "=")
    except (ValueError, TypeError):
        return ""
    return str(int.from_bytes(raw, "big")) if len(raw) == 8 else ""


def _finder_nonce_prefix(value: Any) -> str:
    return _clean_text(value).split("_", 1)[0]


def _get_ci(mapping: Dict[str, Any], *keys: str) -> Any:
    if not isinstance(mapping, dict):
        return None
    lowered = {str(k).lower(): k for k in mapping.keys()}
    for key in keys:
        real = lowered.get(key.lower())
        if real is not None:
            return mapping.get(real)
    return None


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _first_stat(mapping: Dict[str, Any], *keys: str) -> str:
    value = _first_nonempty(*(_get_ci(mapping, key) for key in keys))
    if not value:
        return ""
    return re.sub(r"[,\s]+", "", value)


def _stats_from_mapping(mapping: Dict[str, Any], *, source: str = "unknown", exact: bool = False) -> Dict[str, Any]:
    if not isinstance(mapping, dict):
        return {}
    report_ext = _json_object_from_text(
        _first_nonempty(
            _get_ci(mapping, "report_extinfo_str"),
            _get_ci(mapping, "reportExtInfoStr"),
            _get_ci(mapping, "report_extinfo"),
            _get_ci(mapping, "reportExtInfo"),
        )
    )
    if report_ext:
        mapping = {**report_ext, **mapping}
    stats: Dict[str, Any] = {
        "exact": bool(exact),
        "source": source,
    }
    candidates = {
        "playCount": ("playCount", "play_count", "play_cnt", "readCount", "read_count", "viewCount", "view_count", "pv"),
        "thumbCount": ("thumbCount", "thumb_count", "thumb_cnt"),
        "likeCount": ("likeNum", "likeCount", "like_count", "like_cnt"),
        "forwardCount": ("forwardCount", "forward_count", "forward_cnt", "shareCount", "share_count", "share_cnt"),
        "favoriteCount": ("favoriteCount", "favorite_count", "collectCount", "collect_count", "starCount", "star_count"),
        "commentCount": ("commentCount", "comment_count", "comment_cnt", "commentNum", "comment_num"),
        "durationSeconds": ("durationSeconds", "duration_seconds", "duration"),
        "uploadTime": ("uploadTime", "upload_time", "pubTime", "publishTime"),
    }
    for out_key, keys in candidates.items():
        value = _first_stat(mapping, *keys)
        if value:
            if out_key == "durationSeconds":
                value = _duration_to_seconds(value)
            stats[out_key] = value
    if report_ext:
        stats["reportExtInfoDecoded"] = True
    if len(stats) <= 2:
        return {}
    return stats


def _merge_stats(*items: Any) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        if not merged:
            merged = dict(item)
            continue
        for key, value in item.items():
            if value not in ("", None):
                merged[key] = value
    return merged


def _json_object_from_text(value: Any) -> Optional[Dict[str, Any]]:
    text = _clean_text(value)
    if not text or len(text) > _JSON_STRING_SCAN_LIMIT:
        return None
    candidates = [text]
    for _ in range(2):
        decoded = urllib.parse.unquote(text)
        if decoded == text:
            break
        text = decoded.strip()
        candidates.append(text)
    for candidate in candidates:
        if candidate[:1] != "{":
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _live_id_from_mapping(mapping: Dict[str, Any]) -> str:
    if not isinstance(mapping, dict):
        return ""
    live_id = _first_nonzero_id(
        _get_ci(mapping, "liveId"),
        _get_ci(mapping, "live_id"),
        _get_ci(mapping, "finderLiveId"),
        _get_ci(mapping, "finder_live_id"),
    )
    if live_id:
        return live_id
    for key in ("liveInfo", "live_info", "finderLiveInfo", "finder_live_info"):
        nested = _get_ci(mapping, key)
        if not isinstance(nested, dict):
            nested = _json_object_from_text(nested)
        if not isinstance(nested, dict):
            continue
        live_id = _first_nonzero_id(
            _get_ci(nested, "liveId"),
            _get_ci(nested, "live_id"),
            _get_ci(nested, "finderLiveId"),
            _get_ci(nested, "finder_live_id"),
            _get_ci(nested, "id"),
        )
        if live_id:
            return live_id
    return ""


def _strip_group_sender(text: str) -> str:
    marker = text.find("<?xml")
    if marker >= 0:
        return text[marker:]
    marker = text.find("<msg")
    if marker >= 0:
        return text[marker:]
    marker = text.find("<appmsg")
    if marker >= 0:
        return text[marker:]
    return text


def _escape_bare_xml_ampersands(text: str) -> str:
    return re.sub(r"&(?!#\d+;|#x[0-9A-Fa-f]+;|[A-Za-z][A-Za-z0-9]+;)", "&amp;", text)


def parse_finder_url_params(value: str) -> Dict[str, str]:
    """Extract object id, nonce and finder username from common Finder URLs."""
    text = _clean_text(value)
    parsed: Dict[str, str] = {}
    if not text:
        return parsed

    candidates = [text]
    for _ in range(3):
        decoded = urllib.parse.unquote(candidates[-1])
        if decoded == candidates[-1]:
            break
        candidates.append(decoded)

    object_keys = {
        "oid",
        "objectid",
        "object_id",
        "finderobjectid",
        "feedobjectid",
        "feed_object_id",
        "feedid",
        "feed_id",
    }
    encrypted_object_keys = {
        "eid",
        "encryptedobjectid",
        "encrypted_objectid",
        "exportid",
        "export_id",
    }
    nonce_keys = {
        "nid",
        "objectnonceid",
        "object_nonce_id",
        "findernonceid",
        "finderobjectnonceid",
        "nonceid",
        "nonce_id",
    }
    username_keys = {
        "finderusername",
        "finder_username",
        "username",
        "user_name",
        "finder_user_name",
    }

    for candidate in candidates:
        try:
            url = urllib.parse.urlparse(candidate)
        except ValueError:
            url = urllib.parse.urlparse("")
        query = urllib.parse.parse_qs(url.query, keep_blank_values=True)
        if url.fragment:
            query.update(urllib.parse.parse_qs(url.fragment, keep_blank_values=True))
        for key, values in query.items():
            normalized = key.replace("-", "_").lower()
            first = _first_nonempty(*(values or []))
            if not first:
                continue
            if normalized in encrypted_object_keys:
                parsed.setdefault("encryptedObjectId", first)
                parsed.setdefault("objectId", first)
            elif normalized in object_keys and not parsed.get("objectId"):
                parsed["objectId"] = _decode_uint64_base64url(first) or first
            elif normalized in nonce_keys and not parsed.get("objectNonceId"):
                parsed["objectNonceId"] = _decode_uint64_base64url(first) or first
            elif normalized in username_keys and not parsed.get("username"):
                parsed["username"] = first

        for pattern, out_key in (
            (r"(?:encrypted_objectid|encryptedObjectId|exportId)[=/:\"]+([^&\\\"'<>\\s]+)", "encryptedObjectId"),
            (r"(?:object_id|objectId|finderObjectID)[=/:\"]+([0-9]{8,24})", "objectId"),
            (r"(?:object_nonce_id|objectNonceId|finderNonceID)[=/:\"]+([^&\\\"'<>\\s]+)", "objectNonceId"),
            (r"(?:finder_username|finderUsername)[=/:\"]+([^&\\\"'<>\\s]+)", "username"),
        ):
            if parsed.get(out_key):
                continue
            m = re.search(pattern, candidate, re.I)
            if m:
                parsed[out_key] = _clean_text(m.group(1))

    if parsed.get("encryptedObjectId") and not parsed.get("objectId"):
        parsed["objectId"] = parsed["encryptedObjectId"]

    if not parsed.get("objectId"):
        m = re.search(r"\b([1-9][0-9]{15,24})\b", text)
        if m:
            parsed["objectId"] = m.group(1)
    return parsed


def _fetch_text(url: str, timeout: float) -> Tuple[str, str, int]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36 MicroMessenger/4.1.9"
            ),
            "Accept": "text/html,application/json,application/xhtml+xml,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(_MAX_TEXT_BYTES + 1)
        if len(raw) > _MAX_TEXT_BYTES:
            raise FinderResponseTooLargeError(
                f"finder response exceeds {_MAX_TEXT_BYTES} bytes"
            )
        content_type = resp.headers.get("content-type", "")
        charset = "utf-8"
        m = re.search(r"charset=([^;\s]+)", content_type, re.I)
        if m:
            charset = m.group(1).strip("\"'")
        text = raw.decode(charset, errors="replace")
        return text, resp.geturl(), getattr(resp, "status", 200)


def _xml_find_text(root: ET.Element, path: str) -> str:
    found = root.find(path)
    if found is None:
        return ""
    return _clean_text("".join(found.itertext()))


def _xml_find(root: ET.Element, path: str) -> Optional[ET.Element]:
    return root.find(path)


def _parse_xml(text: str) -> Optional[ET.Element]:
    candidate = str(text or "").strip()
    if "<" not in candidate and ("%3C" in candidate.upper() or "%3c" in candidate):
        candidate = urllib.parse.unquote(candidate)
    if "<" not in candidate and "&lt;" in candidate:
        candidate = html.unescape(candidate)
    candidate = _strip_group_sender(candidate)
    if not candidate:
        return None
    if "<appmsg" in candidate and "<msg" not in candidate[: max(candidate.find("<appmsg"), 0) + 1]:
        candidate = f"<msg>{candidate}</msg>"
    candidate = _escape_bare_xml_ampersands(candidate)
    try:
        return ET.fromstring(candidate)
    except ET.ParseError:
        pass
    m = re.search(r"(<msg[\s\S]*?</msg>)", candidate, re.I)
    if m:
        try:
            return ET.fromstring(m.group(1))
        except ET.ParseError:
            return None
    return None


def payload_from_xml(text: str) -> Optional[FinderPayload]:
    root = _parse_xml(text)
    if root is None:
        return None
    finder = root.find(".//finderFeed")
    if finder is None:
        return None
    media = finder.find(".//media")
    payload: FinderPayload = {
        "objectId": _xml_find_text(finder, "objectId") or _xml_find_text(finder, "finderObjectID"),
        "localId": _xml_find_text(finder, "localId"),
        "objectNonceId": _xml_find_text(finder, "objectNonceId") or _xml_find_text(finder, "finderNonceID"),
        "liveId": _first_nonzero_id(_xml_find_text(finder, "liveId"), _xml_find_text(finder, "finderLiveId")),
        "feedType": _xml_find_text(finder, "feedType"),
        "nickname": _xml_find_text(finder, "nickname"),
        "username": _xml_find_text(finder, "username") or _xml_find_text(finder, "finderUsername"),
        "avatar": _xml_find_text(finder, "avatar"),
        "desc": _xml_find_text(finder, "desc"),
        "mediaCount": _xml_find_text(finder, "mediaCount"),
        "webUrl": _xml_find_text(finder, "webUrl"),
        "authIconUrl": _xml_find_text(finder, "authIconUrl"),
        "authIconType": _xml_find_text(finder, "authIconType"),
        "bizAvatar": _xml_find_text(finder, "bizAvatar"),
        "bizNickname": _xml_find_text(finder, "bizNickname"),
        "bizUsername": _xml_find_text(finder, "bizUsername"),
        "contentType": _xml_find_text(finder, "contentType"),
        "sourceCommentScene": _xml_find_text(finder, "sourceCommentScene"),
        "finderShareExtInfo": _xml_find_text(finder, "finderShareExtInfo"),
        "media": {},
    }
    if media is not None:
        payload["media"] = {
            "title": _xml_find_text(media, "title"),
            "mediaType": _xml_find_text(media, "mediaType"),
            "url": _xml_find_text(media, "url"),
            "thumbUrl": _xml_find_text(media, "thumbUrl"),
            "coverUrl": _xml_find_text(media, "coverUrl"),
            "fullCoverUrl": _xml_find_text(media, "fullCoverUrl"),
            "fullClipInset": _xml_find_text(media, "fullClipInset"),
            "width": _xml_find_text(media, "width"),
            "height": _xml_find_text(media, "height"),
            "videoPlayDuration": _xml_find_text(media, "videoPlayDuration"),
        }
    normalize_payload(payload)
    return payload


def _extract_xml_candidates(text: str) -> List[str]:
    candidates: List[str] = []
    seen = set()
    variants = [text, html.unescape(text), urllib.parse.unquote(text)]
    for variant in variants:
        if not variant or variant in seen:
            continue
        seen.add(variant)
        for pattern in (
            r"<\?xml[\s\S]*?<msg[\s\S]*?</msg>",
            r"<msg[\s\S]*?</msg>",
            r"<appmsg[\s\S]*?</appmsg>",
        ):
            for m in re.finditer(pattern, variant, re.I):
                block = m.group(0)
                if "finderFeed" in block:
                    candidates.append(block)
    return candidates


def _iter_json_values(text: str) -> Iterable[Any]:
    variants = [text, html.unescape(text), urllib.parse.unquote(text)]
    decoder = json.JSONDecoder()
    for variant in variants:
        if not variant:
            continue
        variant_yielded = 0
        stripped = variant.strip()
        if stripped[:1] in ("{", "["):
            try:
                yield json.loads(stripped)
                variant_yielded += 1
            except json.JSONDecodeError:
                pass
        for script in re.findall(r"<script[^>]*>([\s\S]*?)</script>", variant, flags=re.I):
            body = html.unescape(script).strip()
            if body[:1] in ("{", "["):
                try:
                    yield json.loads(body)
                    variant_yielded += 1
                except json.JSONDecodeError:
                    pass
        starts = [m.start() for m in re.finditer(r"[\{\[]", variant)]
        for start in starts:
            if variant_yielded >= _JSON_SCAN_LIMIT:
                break
            try:
                value, end = decoder.raw_decode(variant[start:])
            except json.JSONDecodeError:
                continue
            if end <= 2:
                continue
            yield value
            variant_yielded += 1


def _walk_json(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)
    elif isinstance(value, str):
        text = _clean_text(value)
        if not text or len(text) > _JSON_STRING_SCAN_LIMIT:
            return
        candidates = [text]
        for _ in range(2):
            decoded = urllib.parse.unquote(text)
            if decoded == text:
                break
            text = decoded.strip()
            candidates.append(text)
        for candidate in candidates:
            if candidate[:1] not in ("{", "["):
                continue
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            yield from _walk_json(parsed)
            return


def _find_nested_mapping(mapping: Dict[str, Any], *keys: str) -> Optional[Dict[str, Any]]:
    for key in keys:
        value = _get_ci(mapping, key)
        for item in _as_list(value):
            if isinstance(item, dict):
                return item
    return None


def _candidate_media_objects(mapping: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for key in ("media", "mediaList", "media_list", "mediaInfo", "media_info", "objectDesc", "object_desc"):
        value = _get_ci(mapping, key)
        for item in _as_list(value):
            if isinstance(item, dict):
                if key.lower() in {"medialist", "media_list"}:
                    nested = _get_ci(item, "media")
                    for nested_item in _as_list(nested):
                        if isinstance(nested_item, dict):
                            yield nested_item
                yield item
            elif isinstance(item, list):
                for nested_item in item:
                    if isinstance(nested_item, dict):
                        yield nested_item
    for node in _walk_json(mapping):
        if isinstance(node, dict):
            media_url = _first_nonempty(
                _get_ci(node, "url"),
                _get_ci(node, "mediaUrl"),
                _get_ci(node, "media_url"),
                _get_ci(node, "videoUrl"),
                _get_ci(node, "video_url"),
                _get_ci(node, "cdnUrl"),
                _get_ci(node, "cdn_url"),
                _get_ci(node, "playUrl"),
                _get_ci(node, "play_url"),
            )
            cover = _first_nonempty(_get_ci(node, "coverUrl"), _get_ci(node, "thumbUrl"), _get_ci(node, "fullCoverUrl"))
            cover = _first_nonempty(cover, _get_ci(node, "imageUrl"), _get_ci(node, "picUrl"), _get_ci(node, "poster"))
            cover = _first_nonempty(cover, _get_ci(node, "image"))
            if media_url or cover:
                yield node


def _media_from_mapping(mapping: Dict[str, Any]) -> Dict[str, str]:
    media: Dict[str, str] = {}
    media["title"] = _first_nonempty(_get_ci(mapping, "title"))
    media["mediaType"] = _first_nonempty(_get_ci(mapping, "mediaType"), _get_ci(mapping, "media_type"), _get_ci(mapping, "type"))
    media["url"] = _first_nonempty(
        _get_ci(mapping, "url"),
        _get_ci(mapping, "mediaUrl"),
        _get_ci(mapping, "media_url"),
        _get_ci(mapping, "videoUrl"),
        _get_ci(mapping, "video_url"),
        _get_ci(mapping, "cdnUrl"),
        _get_ci(mapping, "cdn_url"),
        _get_ci(mapping, "playUrl"),
        _get_ci(mapping, "play_url"),
        _get_ci(mapping, "videoPlayUrl"),
        _get_ci(mapping, "video_play_url"),
        _get_ci(mapping, "downloadUrl"),
    )
    media["thumbUrl"] = _first_nonempty(
        _get_ci(mapping, "thumbUrl"),
        _get_ci(mapping, "thumb_url"),
        _get_ci(mapping, "thumbnail"),
        _get_ci(mapping, "thumbnailUrl"),
        _get_ci(mapping, "fullThumbUrl"),
        _get_ci(mapping, "image"),
        _get_ci(mapping, "imageUrl"),
        _get_ci(mapping, "image_url"),
        _get_ci(mapping, "picUrl"),
        _get_ci(mapping, "poster"),
    )
    media["coverUrl"] = _first_nonempty(
        _get_ci(mapping, "coverUrl"),
        _get_ci(mapping, "cover_url"),
        _get_ci(mapping, "cover"),
        _get_ci(mapping, "fullCoverUrl"),
        _get_ci(mapping, "shareCoverUrl"),
        _get_ci(mapping, "image"),
        _get_ci(mapping, "imageUrl"),
        _get_ci(mapping, "image_url"),
        _get_ci(mapping, "picUrl"),
        _get_ci(mapping, "poster"),
        media.get("thumbUrl"),
    )
    media["fullCoverUrl"] = _first_nonempty(_get_ci(mapping, "fullCoverUrl"), _get_ci(mapping, "full_cover_url"), media.get("coverUrl"))
    media["fullClipInset"] = _first_nonempty(_get_ci(mapping, "fullClipInset"), _get_ci(mapping, "full_clip_inset"))
    media["width"] = _first_nonempty(_get_ci(mapping, "width"), _get_ci(mapping, "videoWidth"), _get_ci(mapping, "video_width"))
    media["height"] = _first_nonempty(_get_ci(mapping, "height"), _get_ci(mapping, "videoHeight"), _get_ci(mapping, "video_height"))
    media["videoPlayDuration"] = _first_nonempty(
        _get_ci(mapping, "videoPlayDuration"),
        _get_ci(mapping, "video_play_duration"),
        _get_ci(mapping, "duration"),
        _get_ci(mapping, "videoDuration"),
        _get_ci(mapping, "playDuration"),
        _get_ci(mapping, "videoPlayLen"),
        _get_ci(mapping, "video_play_len"),
    )
    return {k: v for k, v in media.items() if _clean_text(v)}


def _best_media(mapping: Dict[str, Any]) -> Dict[str, str]:
    best: Dict[str, str] = {}
    best_score = -1
    for candidate in _candidate_media_objects(mapping):
        media = _media_from_mapping(candidate)
        score = 0
        for key in ("url", "thumbUrl", "coverUrl", "width", "height", "videoPlayDuration"):
            if media.get(key):
                score += 2
        if media.get("mediaType"):
            score += 1
        if score > best_score:
            best = media
            best_score = score
    return best


def _payload_from_mapping(mapping: Dict[str, Any]) -> Optional[FinderPayload]:
    merged = dict(mapping)
    for key in ("extInfo", "extraInfo", "reportExtraInfo", "finderShareItem", "finderShareExtInfo"):
        nested = _json_object_from_text(_get_ci(mapping, key))
        if not nested:
            continue
        for nested_key, nested_value in nested.items():
            merged.setdefault(str(nested_key), nested_value)
    jump_info = _get_ci(mapping, "jumpInfo", "jump_info")
    if isinstance(jump_info, dict):
        for nested_key, nested_value in jump_info.items():
            merged.setdefault(str(nested_key), nested_value)
        nested = _json_object_from_text(_get_ci(jump_info, "extInfo", "ext_info"))
        if nested:
            for nested_key, nested_value in nested.items():
                merged.setdefault(str(nested_key), nested_value)
    mapping = merged

    search_doc_id = _first_nonempty(
        _get_ci(mapping, "docID"),
        _get_ci(mapping, "docId"),
        _get_ci(mapping, "doc_id"),
    )

    object_id = _first_nonempty(
        _get_ci(mapping, "objectId"),
        _get_ci(mapping, "object_id"),
        _get_ci(mapping, "finderObjectID"),
        _get_ci(mapping, "feedObjectId"),
        _get_ci(mapping, "feed_object_id"),
        _get_ci(mapping, "feedId"),
        _get_ci(mapping, "feed_id"),
        _get_ci(mapping, "exportId"),
        _get_ci(mapping, "export_id"),
    )
    if object_id.startswith(("finderobj", "finderlive")):
        object_id = ""
    if not object_id:
        maybe_id = _first_nonempty(_get_ci(mapping, "id"))
        if re.fullmatch(r"[1-9][0-9]{12,24}", maybe_id):
            object_id = maybe_id
    nonce = _first_nonempty(
        _get_ci(mapping, "objectNonceId"),
        _get_ci(mapping, "object_nonce_id"),
        _get_ci(mapping, "finderNonceID"),
        _get_ci(mapping, "finderObjectNonceId"),
        _get_ci(mapping, "nonceId"),
        _get_ci(mapping, "nonce_id"),
        _get_ci(mapping, "feedNonceId"),
        _get_ci(mapping, "feed_nonce_id"),
    )
    if not object_id and not nonce:
        return None

    user = _find_nested_mapping(
        mapping,
        "user",
        "author",
        "publisher",
        "finderUser",
        "finder_user",
        "finderInfo",
        "finder_info",
        "userInfo",
        "user_info",
        "contact",
        "contactInfo",
        "contact_info",
        "accountInfo",
        "account_info",
        "source",
        "jumpInfo",
    )
    media = _best_media(mapping)
    source = _find_nested_mapping(mapping, "source")
    payload: FinderPayload = {
        "objectId": object_id,
        "searchDocId": search_doc_id if search_doc_id.startswith(("finderobj", "finderlive")) else "",
        "localId": _first_nonempty(_get_ci(mapping, "localId"), _get_ci(mapping, "local_id")),
        "objectNonceId": nonce,
        "liveId": _live_id_from_mapping(mapping),
        "feedType": _first_nonempty(_get_ci(mapping, "feedType"), _get_ci(mapping, "feed_type"), _get_ci(mapping, "objectType")),
        "nickname": _first_nonempty(
            _get_ci(mapping, "nickname"),
            _get_ci(mapping, "nickName"),
            _get_ci(mapping, "finderNickname"),
            _get_ci(mapping, "finder_nickname"),
            _get_ci(mapping, "displayName"),
            _get_ci(user or {}, "nickname"),
            _get_ci(user or {}, "nickName"),
            _get_ci(user or {}, "displayName"),
            _display_text(_get_ci(source or {}, "title")),
        ),
        "username": _first_nonempty(
            _get_ci(mapping, "username"),
            _get_ci(mapping, "userName"),
            _get_ci(mapping, "finderUsername"),
            _get_ci(mapping, "finder_username"),
            _get_ci(mapping, "finderUserName"),
            _get_ci(user or {}, "username"),
            _get_ci(user or {}, "userName"),
            _get_ci(user or {}, "finderUsername"),
        ),
        "avatar": _first_nonempty(
            _get_ci(mapping, "avatar"),
            _get_ci(mapping, "avatarUrl"),
            _get_ci(mapping, "avatar_url"),
            _get_ci(mapping, "headUrl"),
            _get_ci(mapping, "headImgUrl"),
            _get_ci(mapping, "finderAvatar"),
            _get_ci(user or {}, "avatar"),
            _get_ci(user or {}, "avatarUrl"),
            _get_ci(user or {}, "headUrl"),
            _get_ci(user or {}, "headImgUrl"),
            _get_ci(source or {}, "iconUrl"),
        ),
        "desc": _first_nonempty(
            _get_ci(mapping, "desc"),
            _get_ci(mapping, "description"),
            _get_ci(mapping, "objectDesc"),
            _get_ci(mapping, "feedDesc"),
            _get_ci(mapping, "contentDesc"),
            _get_ci(mapping, "summary"),
            _get_ci(mapping, "content"),
            _display_text(_get_ci(mapping, "title")),
        ),
        "mediaCount": _first_nonempty(_get_ci(mapping, "mediaCount"), _get_ci(mapping, "media_count")),
        "webUrl": _first_nonempty(
            _get_ci(mapping, "webUrl"),
            _get_ci(mapping, "web_url"),
            _get_ci(mapping, "shareUrl"),
            _get_ci(mapping, "share_url"),
            _get_ci(mapping, "url"),
        ),
        "authIconUrl": _first_nonempty(
            _get_ci(mapping, "authIconUrl"),
            _get_ci(mapping, "auth_icon_url"),
            _first_text_from_list(_get_ci(source or {}, "mark")),
        ),
        "authIconType": _first_nonempty(_get_ci(mapping, "authIconType"), _get_ci(mapping, "auth_icon_type")),
        "bizAvatar": _first_nonempty(_get_ci(mapping, "bizAvatar"), _get_ci(mapping, "biz_avatar")),
        "bizNickname": _first_nonempty(_get_ci(mapping, "bizNickname"), _get_ci(mapping, "biz_nickname")),
        "bizUsername": _first_nonempty(_get_ci(mapping, "bizUsername"), _get_ci(mapping, "biz_username")),
        "contentType": _first_nonempty(_get_ci(mapping, "contentType"), _get_ci(mapping, "content_type")),
        "sourceCommentScene": _first_nonempty(
            _get_ci(mapping, "sourceCommentScene"),
            _get_ci(mapping, "source_comment_scene"),
            _get_ci(mapping, "commentScene"),
            _get_ci(mapping, "comment_scene"),
        ),
        "finderShareExtInfo": _first_nonempty(_get_ci(mapping, "finderShareExtInfo"), _get_ci(mapping, "finder_share_ext_info")),
        "media": media,
    }
    normalize_payload(payload)
    stats = _stats_from_mapping(mapping, source="generic_payload_json", exact=False)
    if stats:
        payload["stats"] = stats
    return payload


def _norm_display_key(value: Any) -> str:
    return re.sub(r"\s+", "", _display_text(value)).lower()


def _first_auth_icon(mapping: Dict[str, Any]) -> str:
    title_tag = _get_ci(mapping, "titleTag", "title_tag")
    if isinstance(title_tag, dict):
        for item in _as_list(_get_ci(title_tag, "append")):
            if isinstance(item, dict):
                icon = _first_nonempty(_get_ci(item, "iconUrl"), _get_ci(item, "icon_url"))
                if icon:
                    return icon
    source = _get_ci(mapping, "source")
    if isinstance(source, dict):
        icon = _first_text_from_list(_get_ci(source, "mark"))
        if icon:
            return icon
    return ""


def _finder_accounts_from_mmpcwebsearch(mapping: Dict[str, Any]) -> List[Dict[str, str]]:
    if not isinstance(mapping, dict) or not isinstance(_get_ci(mapping, "data"), list):
        return []

    accounts: List[Dict[str, str]] = []
    seen = set()
    for node in _walk_json(mapping):
        if not isinstance(node, dict):
            continue
        doc_id = _first_nonempty(_get_ci(node, "docID"), _get_ci(node, "docId"), _get_ci(node, "doc_id"))
        jump = _get_ci(node, "jumpInfo", "jump_info")
        jump = jump if isinstance(jump, dict) else {}
        username = _first_nonempty(_get_ci(jump, "userName"), _get_ci(jump, "username"), _get_ci(node, "finderUsername"))
        if not (doc_id.startswith("finderacct") or username.endswith("@finder")):
            continue
        nickname = _display_text(_first_nonempty(_get_ci(node, "title"), _get_ci(node, "nickname")))
        key = (_norm_display_key(nickname), username)
        if not key[0] or not username or key in seen:
            continue
        seen.add(key)
        accounts.append(
            {
                "nickname": nickname,
                "username": username,
                "avatar": _first_nonempty(_get_ci(node, "thumbUrl"), _get_ci(node, "avatar"), _get_ci(node, "avatarUrl")),
                "desc": _display_text(_first_nonempty(_get_ci(node, "desc"), _get_ci(node, "authInfo"))),
                "authIconUrl": _first_auth_icon(node),
                "authIconType": "1" if _first_auth_icon(node) else "0",
                "docID": doc_id,
            }
        )
    return accounts


def _search_doc_id(node: Dict[str, Any]) -> str:
    return _first_nonempty(_get_ci(node, "docID"), _get_ci(node, "docId"), _get_ci(node, "doc_id"))


def _search_object_id(node: Dict[str, Any], jump: Dict[str, Any], ext: Dict[str, Any]) -> str:
    return _first_nonempty(
        _get_ci(ext, "objectId"),
        _get_ci(ext, "feedId"),
        _get_ci(ext, "feedObjectId"),
        _get_ci(ext, "exportId"),
        _get_ci(ext, "export_id"),
        _get_ci(jump, "objectId"),
        _get_ci(jump, "feedId"),
        _get_ci(jump, "exportId"),
        _get_ci(jump, "export_id"),
        _get_ci(node, "objectId"),
        _get_ci(node, "feedId"),
        _get_ci(node, "exportId"),
        _get_ci(node, "export_id"),
    )


def _search_username(node: Dict[str, Any], jump: Dict[str, Any], account: Dict[str, str]) -> str:
    return _first_nonempty(
        _get_ci(jump, "userName"),
        _get_ci(jump, "username"),
        _get_ci(jump, "finderUsername"),
        _get_ci(node, "userName"),
        _get_ci(node, "username"),
        _get_ci(node, "finderUsername"),
        account.get("username"),
    )


def _finder_account_indexes(
    mapping: Dict[str, Any],
) -> Tuple[Dict[str, List[Dict[str, str]]], Dict[str, Dict[str, str]]]:
    by_nickname: Dict[str, List[Dict[str, str]]] = {}
    by_username: Dict[str, Dict[str, str]] = {}
    for account in _finder_accounts_from_mmpcwebsearch(mapping):
        nickname_key = _norm_display_key(account.get("nickname"))
        username = str(account.get("username") or "")
        if nickname_key:
            by_nickname.setdefault(nickname_key, []).append(account)
        if username:
            by_username[username] = account
    return by_nickname, by_username


def _account_for_search_result(
    node: Dict[str, Any],
    jump: Dict[str, Any],
    nickname: str,
    by_nickname: Dict[str, List[Dict[str, str]]],
    by_username: Dict[str, Dict[str, str]],
) -> Dict[str, str]:
    explicit_username = _search_username(node, jump, {})
    if explicit_username:
        return by_username.get(explicit_username, {})
    candidates = by_nickname.get(_norm_display_key(nickname), [])
    return candidates[0] if len(candidates) == 1 else {}


def _payloads_from_mmpcwebsearch(mapping: Dict[str, Any]) -> List[FinderPayload]:
    if not isinstance(mapping, dict) or not isinstance(_get_ci(mapping, "data"), list):
        return []

    accounts_by_nickname, accounts_by_username = _finder_account_indexes(mapping)

    payloads: List[FinderPayload] = []
    for node in _walk_json(mapping):
        if not isinstance(node, dict):
            continue
        doc_id = _search_doc_id(node)
        if not doc_id.startswith("finderobj"):
            continue
        video_url = _first_nonempty(_get_ci(node, "videoUrl"), _get_ci(node, "video_url"))
        cover_url = _first_nonempty(_get_ci(node, "image"), _get_ci(node, "imageUrl"), _get_ci(node, "thumbUrl"))
        if not video_url or not cover_url:
            continue
        jump = _get_ci(node, "jumpInfo", "jump_info")
        jump = jump if isinstance(jump, dict) else {}
        ext = _json_object_from_text(_get_ci(jump, "extInfo", "ext_info")) or {}
        object_id = _search_object_id(node, jump, ext)
        source = _get_ci(node, "source")
        source = source if isinstance(source, dict) else {}
        nickname = _display_text(_first_nonempty(_get_ci(source, "title"), _get_ci(node, "sourceName"), _get_ci(node, "nickname")))
        account = _account_for_search_result(
            node,
            jump,
            nickname,
            accounts_by_nickname,
            accounts_by_username,
        )
        desc = _display_text(_first_nonempty(_get_ci(node, "title"), _get_ci(node, "desc"), account.get("desc")))
        auth_icon = _first_nonempty(_first_text_from_list(_get_ci(source, "mark")), account.get("authIconUrl"), _first_auth_icon(node))
        payload: FinderPayload = {
            "objectId": object_id,
            "searchDocId": doc_id,
            "localId": _first_nonempty(_get_ci(node, "localId"), _get_ci(node, "hashDocID"), _get_ci(node, "hashDocId")),
            "exportId": _first_nonempty(_get_ci(node, "exportId"), _get_ci(node, "export_id")),
            "searchObjectId": doc_id,
            "hashDocID": _first_nonempty(_get_ci(node, "hashDocID"), _get_ci(node, "hashDocId")),
            "objectNonceId": _first_nonempty(_get_ci(ext, "feedNonceId"), _get_ci(ext, "objectNonceId"), _get_ci(node, "objectNonceId")),
            "liveId": _first_nonzero_id(_live_id_from_mapping(ext), _live_id_from_mapping(jump), _live_id_from_mapping(node)),
            "contextId": _first_nonempty(
                _get_ci(ext, "contextId"), _get_ci(ext, "context_id"),
                _get_ci(jump, "contextId"), _get_ci(jump, "context_id"),
                _get_ci(node, "contextId"), _get_ci(node, "context_id"),
            ),
            "entranceId": _first_nonempty(
                _get_ci(ext, "entranceId"), _get_ci(ext, "entrance_id"), _get_ci(ext, "reportScene"),
                _get_ci(jump, "entranceId"), _get_ci(jump, "entrance_id"), _get_ci(jump, "commentScene"),
                "88",
            ),
            "subEntranceId": _first_nonempty(
                _get_ci(ext, "subEntranceId"), _get_ci(ext, "sub_entranceid"),
                _get_ci(jump, "subEntranceId"), _get_ci(jump, "sub_entranceid"),
            ),
            "feedType": "4",
            "nickname": _first_nonempty(nickname, account.get("nickname")),
            "username": _search_username(node, jump, account),
            "avatar": _first_nonempty(_get_ci(source, "iconUrl"), account.get("avatar")),
            "desc": desc,
            "mediaCount": "1",
            "authIconUrl": auth_icon,
            "authIconType": "1" if auth_icon else "0",
            "contentType": "4",
            "sourceCommentScene": _first_nonempty(_get_ci(ext, "reportScene"), _get_ci(jump, "commentScene"), "20"),
            "media": {
                "title": desc,
                "mediaType": "4",
                "url": video_url,
                "thumbUrl": cover_url,
                "coverUrl": cover_url,
                "fullCoverUrl": cover_url,
                "width": _first_nonempty(_get_ci(node, "width"), _get_ci(node, "videoWidth")),
                "height": _first_nonempty(_get_ci(node, "height"), _get_ci(node, "videoHeight")),
                "videoPlayDuration": _duration_to_seconds(_first_nonempty(_get_ci(node, "duration"), _get_ci(ext, "duration"))),
            },
        }
        normalize_payload(payload)
        stats = _merge_stats(
            _stats_from_mapping(node, source="search_partial_stats", exact=False),
            _stats_from_mapping(ext, source="search_partial_stats", exact=False),
        )
        if stats:
            payload["stats"] = stats
        payloads.append(payload)
    return payloads


def _live_payloads_from_mmpcwebsearch(mapping: Dict[str, Any]) -> List[FinderPayload]:
    if not isinstance(mapping, dict) or not isinstance(_get_ci(mapping, "data"), list):
        return []

    accounts_by_nickname, accounts_by_username = _finder_account_indexes(mapping)
    payloads: List[FinderPayload] = []
    for node in _walk_json(mapping):
        if not isinstance(node, dict):
            continue
        doc_id = _search_doc_id(node)
        if not doc_id.startswith(("finderobj", "finderlive")):
            continue
        jump = _get_ci(node, "jumpInfo", "jump_info")
        jump = jump if isinstance(jump, dict) else {}
        ext = _json_object_from_text(_get_ci(jump, "extInfo", "ext_info")) or {}
        report_ext = _json_object_from_text(
            _first_nonempty(
                _get_ci(node, "report_extinfo_str"),
                _get_ci(node, "reportExtInfoStr"),
                _get_ci(node, "report_extinfo"),
                _get_ci(node, "reportExtInfo"),
            )
        ) or {}
        live_id = _first_nonzero_id(
            _live_id_from_mapping(ext),
            _live_id_from_mapping(jump),
            _live_id_from_mapping(node),
            _live_id_from_mapping(report_ext),
        )
        if not live_id:
            continue
        source = _get_ci(node, "source")
        source = source if isinstance(source, dict) else {}
        live_info = _get_ci(node, "liveInfo", "live_info", "finderLiveInfo", "finder_live_info")
        if not isinstance(live_info, dict):
            live_info = _json_object_from_text(live_info) or {}
        nickname = _display_text(
            _first_nonempty(
                _get_ci(source, "title"),
                _get_ci(node, "sourceName"),
                _get_ci(node, "nickname"),
                _get_ci(live_info, "nickname"),
            )
        )
        account = _account_for_search_result(
            node,
            jump,
            nickname,
            accounts_by_nickname,
            accounts_by_username,
        )
        desc = _display_text(
            _first_nonempty(
                _get_ci(node, "title"),
                _get_ci(node, "desc"),
                _get_ci(live_info, "title"),
                _get_ci(live_info, "desc"),
                account.get("desc"),
            )
        )
        cover_url = _first_nonempty(
            _get_ci(node, "image"),
            _get_ci(node, "imageUrl"),
            _get_ci(node, "thumbUrl"),
            _get_ci(node, "coverUrl"),
            _get_ci(live_info, "coverUrl"),
            _get_ci(live_info, "thumbUrl"),
        )
        payload: FinderPayload = {
            "kind": "live",
            "objectId": _search_object_id(node, jump, ext),
            "searchDocId": doc_id,
            "searchObjectId": doc_id,
            "objectNonceId": _first_nonempty(
                _get_ci(ext, "feedNonceId"),
                _get_ci(ext, "objectNonceId"),
                _get_ci(node, "objectNonceId"),
                _get_ci(node, "nonceId"),
                _get_ci(node, "nonce_id"),
            ),
            "liveId": live_id,
            "contextId": _first_nonempty(
                _get_ci(ext, "contextId"), _get_ci(ext, "context_id"),
                _get_ci(jump, "contextId"), _get_ci(jump, "context_id"),
                _get_ci(node, "contextId"), _get_ci(node, "context_id"),
            ),
            "entranceId": _first_nonempty(
                _get_ci(ext, "entranceId"), _get_ci(ext, "entrance_id"), _get_ci(ext, "reportScene"),
                _get_ci(jump, "entranceId"), _get_ci(jump, "entrance_id"), _get_ci(jump, "commentScene"),
                "88",
            ),
            "subEntranceId": _first_nonempty(
                _get_ci(ext, "subEntranceId"), _get_ci(ext, "sub_entranceid"),
                _get_ci(jump, "subEntranceId"), _get_ci(jump, "sub_entranceid"),
            ),
            "feedType": _first_nonempty(_get_ci(node, "feedType"), _get_ci(ext, "feedType"), "9"),
            "nickname": _first_nonempty(nickname, account.get("nickname")),
            "username": _first_nonempty(
                _search_username(node, jump, account),
                _get_ci(report_ext, "export_username"),
                _get_ci(report_ext, "exportUsername"),
            ),
            "avatar": _first_nonempty(
                _get_ci(source, "iconUrl"),
                _get_ci(node, "avatar"),
                _get_ci(live_info, "avatar"),
                account.get("avatar"),
            ),
            "desc": desc,
            "mediaCount": "1" if cover_url else "0",
            "contentType": _first_nonempty(_get_ci(node, "contentType"), _get_ci(ext, "contentType"), "9"),
            "sourceCommentScene": _first_nonempty(_get_ci(ext, "reportScene"), _get_ci(jump, "commentScene"), "20"),
            "liveStatus": _first_nonempty(
                _get_ci(node, "liveStatus"),
                _get_ci(node, "live_status"),
                _get_ci(live_info, "liveStatus"),
                _get_ci(live_info, "status"),
            ),
            "onlineCount": _first_nonempty(
                _get_ci(node, "onlineCount"),
                _get_ci(node, "online_count"),
                _get_ci(live_info, "onlineCount"),
                _get_ci(live_info, "online_count"),
            ),
            "media": {
                "title": desc,
                "mediaType": "9",
                "thumbUrl": cover_url,
                "coverUrl": cover_url,
                "fullCoverUrl": cover_url,
            },
        }
        normalize_payload(payload)
        payloads.append(payload)
    return payloads


def payloads_from_text(text: str) -> List[FinderPayload]:
    payloads: List[FinderPayload] = []
    for xml in _extract_xml_candidates(text):
        payload = payload_from_xml(xml)
        if payload:
            payloads.append(payload)
    for value in _iter_json_values(text):
        for node in _walk_json(value):
            if isinstance(node, dict):
                payloads.extend(_payloads_from_mmpcwebsearch(node))
                payloads.extend(_live_payloads_from_mmpcwebsearch(node))
                payload = _payload_from_mapping(node)
                if payload:
                    payloads.append(payload)
    return _dedupe_payloads(payloads)


def finder_accounts_from_text(text: str) -> List[Dict[str, str]]:
    accounts: List[Dict[str, str]] = []
    seen = set()
    for value in _iter_json_values(text):
        for node in _walk_json(value):
            if not isinstance(node, dict):
                continue
            for account in _finder_accounts_from_mmpcwebsearch(node):
                key = (_norm_display_key(account.get("nickname")), account.get("username", ""))
                if key in seen:
                    continue
                seen.add(key)
                accounts.append(account)
    return accounts


def finder_live_results_from_text(text: str) -> List[FinderPayload]:
    return [payload for payload in payloads_from_text(text) if _first_nonzero_id(payload.get("liveId"))]


def _dedupe_payloads(payloads: List[FinderPayload]) -> List[FinderPayload]:
    seen = set()
    out: List[FinderPayload] = []
    for payload in sorted(payloads, key=_payload_score, reverse=True):
        live_id = _first_nonzero_id(payload.get("liveId"))
        if live_id:
            key = ("live", live_id)
        elif payload.get("objectId") or payload.get("objectNonceId"):
            key = ("feed", payload.get("objectId", ""), payload.get("objectNonceId", ""))
        else:
            key = ("search", payload.get("searchDocId", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(payload)
    return out


def _payload_score(payload: FinderPayload) -> int:
    media = payload.get("media") if isinstance(payload.get("media"), dict) else {}
    score = 0
    for key in ("objectId", "objectNonceId", "liveId", "feedType", "nickname", "username", "avatar", "desc", "mediaCount", "sourceCommentScene"):
        if payload.get(key):
            score += 2
    for key in ("url", "thumbUrl", "coverUrl", "width", "height", "videoPlayDuration"):
        if media.get(key):
            score += 3
    return score


def normalize_payload(payload: FinderPayload) -> FinderPayload:
    media = payload.get("media")
    if not isinstance(media, dict):
        media = {}
        payload["media"] = media
    for key in list(payload.keys()):
        if key not in ("media", "stats"):
            payload[key] = _clean_text(payload.get(key))
    for key in list(media.keys()):
        media[key] = _clean_text(media.get(key))

    if payload.get("encryptedObjectId") and not payload.get("objectId"):
        payload["objectId"] = payload["encryptedObjectId"]
    if str(payload.get("objectId") or "").startswith("export/"):
        payload["encryptedObjectId"] = payload["objectId"]

    for key in ("nickname", "desc", "bizNickname"):
        if payload.get(key):
            payload[key] = _display_text(payload.get(key))
    if media.get("title"):
        media["title"] = _display_text(media.get("title"))
    if media.get("videoPlayDuration"):
        media["videoPlayDuration"] = _duration_to_seconds(media.get("videoPlayDuration"))

    if not payload.get("feedType"):
        payload["feedType"] = media.get("mediaType") or "4"
    if not payload.get("mediaCount"):
        payload["mediaCount"] = "1" if media else "0"
    if not payload.get("authIconType"):
        payload["authIconType"] = "1" if payload.get("authIconUrl") else "0"
    if not payload.get("sourceCommentScene"):
        payload["sourceCommentScene"] = "20"
    if not media.get("mediaType"):
        media["mediaType"] = payload.get("feedType") or "4"
    if not media.get("thumbUrl") and media.get("coverUrl"):
        media["thumbUrl"] = media["coverUrl"]
    if not media.get("coverUrl") and media.get("thumbUrl"):
        media["coverUrl"] = media["thumbUrl"]
    if not media.get("fullCoverUrl") and media.get("coverUrl"):
        media["fullCoverUrl"] = media["coverUrl"]
    if not payload.get("contentType"):
        payload["contentType"] = payload.get("feedType") or media.get("mediaType") or "4"
    if payload.get("objectId") and payload.get("objectNonceId"):
        if _first_nonzero_id(payload.get("liveId")):
            object_id = _clean_text(payload.get("objectId"))
            nonce_id = _clean_text(payload.get("objectNonceId"))
            if object_id.startswith("export/"):
                live_params = {"eid": object_id}
            else:
                encoded_object_id = _encode_uint64_base64url(object_id)
                encoded_nonce_id = _encode_uint64_base64url(_finder_nonce_prefix(nonce_id))
                live_params = {
                    "oid": encoded_object_id or object_id,
                    "nid": encoded_nonce_id or nonce_id,
                }
            live_params["entrance_id"] = (
                payload.get("entranceId") or payload.get("sourceCommentScene") or "88"
            )
            if payload.get("contextId"):
                live_params["context_id"] = payload.get("contextId", "")
            if payload.get("subEntranceId"):
                live_params["sub_entranceid"] = payload.get("subEntranceId", "")
            live_url = "https://channels.weixin.qq.com/web/pages/live?" + urllib.parse.urlencode(live_params)
            payload["liveUrl"] = live_url
            payload["webUrl"] = live_url
        elif not payload.get("webUrl"):
            object_id = _clean_text(payload.get("objectId"))
            nonce_id = _clean_text(payload.get("objectNonceId"))
            if object_id.startswith("export/"):
                feed_params = {"eid": object_id, "entrance_id": "1015"}
            else:
                feed_params = {
                    "oid": _encode_uint64_base64url(object_id) or object_id,
                    "nid": _encode_uint64_base64url(_finder_nonce_prefix(nonce_id)) or nonce_id,
                    "entrance_id": "1015",
                }
            payload["webUrl"] = (
                "https://channels.weixin.qq.com/web/pages/feed?"
                + urllib.parse.urlencode(feed_params)
            )
    return payload


def validate_payload(payload: FinderPayload) -> Tuple[bool, List[str]]:
    normalize_payload(payload)
    if payload.get("kind") == "live":
        missing = [
            key for key in ("liveId", "objectId", "objectNonceId") if not payload.get(key)
        ]
        return not missing, missing
    media = payload.get("media") if isinstance(payload.get("media"), dict) else {}
    missing: List[str] = []
    required_top = ("objectId", "objectNonceId", "feedType", "nickname", "username", "avatar", "mediaCount", "sourceCommentScene")
    for key in required_top:
        if not payload.get(key):
            missing.append(key)
    for key in ("url", "thumbUrl", "coverUrl", "width", "height", "videoPlayDuration"):
        if not media.get(key):
            missing.append(f"media.{key}")
    return not missing, missing


def build_finder_feed_xml(payload: FinderPayload) -> str:
    normalize_payload(payload)
    media = payload.get("media") if isinstance(payload.get("media"), dict) else {}

    def esc(value: Any) -> str:
        return html.escape(str(value or ""), quote=False)

    def tag(name: str, value: Any, indent: str = "\t\t\t") -> str:
        text = esc(value)
        if text:
            return f"{indent}<{name}>{text}</{name}>"
        return f"{indent}<{name} />"

    title = media.get("title") or payload.get("desc") or payload.get("nickname") or "当前版本不支持展示该内容，请升级至最新版本。"
    app_url = payload.get("webUrl") or "https://support.weixin.qq.com/security/readtemplate?t=w_security_center_website/upgrade"
    finder_ext = payload.get("finderShareExtInfo") or '{"hasInput":false,"shareSrcScene":3}'
    lines = [
        '<?xml version="1.0"?>',
        "<msg>",
        "\t<appmsg>",
        tag("title", title, "\t\t"),
        "\t\t<action>view</action>",
        "\t\t<type>51</type>",
        tag("url", app_url, "\t\t"),
        "\t\t<finderFeed>",
        tag("objectId", payload.get("objectId")),
        tag("localId", payload.get("localId")),
        tag("objectNonceId", payload.get("objectNonceId")),
        tag("liveId", payload.get("liveId")),
        tag("feedType", payload.get("feedType")),
        tag("nickname", payload.get("nickname")),
        tag("username", payload.get("username")),
        tag("avatar", payload.get("avatar")),
        tag("desc", payload.get("desc")),
        tag("mediaCount", payload.get("mediaCount")),
        tag("webUrl", payload.get("webUrl")),
        tag("authIconType", payload.get("authIconType")),
        tag("authIconUrl", payload.get("authIconUrl")),
        tag("bizAvatar", payload.get("bizAvatar")),
        tag("bizNickname", payload.get("bizNickname")),
        tag("bizUsername", payload.get("bizUsername")),
        tag("contentType", payload.get("contentType")),
        "\t\t\t<mediaList>",
        "\t\t\t\t<media>",
        tag("title", media.get("title"), "\t\t\t\t\t"),
        tag("url", media.get("url"), "\t\t\t\t\t"),
        tag("mediaType", media.get("mediaType"), "\t\t\t\t\t"),
        tag("thumbUrl", media.get("thumbUrl"), "\t\t\t\t\t"),
        tag("coverUrl", media.get("coverUrl"), "\t\t\t\t\t"),
        tag("fullCoverUrl", media.get("fullCoverUrl"), "\t\t\t\t\t"),
        tag("fullClipInset", media.get("fullClipInset"), "\t\t\t\t\t"),
        tag("width", media.get("width"), "\t\t\t\t\t"),
        tag("height", media.get("height"), "\t\t\t\t\t"),
        tag("videoPlayDuration", media.get("videoPlayDuration"), "\t\t\t\t\t"),
        "\t\t\t\t</media>",
        "\t\t\t</mediaList>",
        tag("sourceCommentScene", payload.get("sourceCommentScene")),
        tag("finderShareExtInfo", finder_ext),
        "\t\t</finderFeed>",
        "\t\t<finderLiveProductShare>",
        "\t\t\t<isPriceBeginShow>false</isPriceBeginShow>",
        "\t\t</finderLiveProductShare>",
        "\t\t<gameshare>",
        "\t\t\t<appbrandext>",
        "\t\t\t\t<priority>-1</priority>",
        "\t\t\t</appbrandext>",
        "\t\t\t<duration>-1</duration>",
        "\t\t</gameshare>",
        "\t\t<appattach />",
        "\t</appmsg>",
        "\t<fromusername />",
        "\t<scene>0</scene>",
        "\t<appinfo>",
        "\t\t<version>1</version>",
        "\t\t<appname />",
        "\t</appinfo>",
        "\t<commenturl />",
        "</msg>",
    ]
    return "\n".join(lines)


def _result_from_payload(payload: Optional[FinderPayload], source: str, **extra: Any) -> Dict[str, Any]:
    if not payload:
        return {"ok": False, "error": "need_full_finder_payload", "source": source, **extra}
    ok, missing = validate_payload(payload)
    result: Dict[str, Any] = {
        "ok": ok,
        "source": source,
        "payload": payload,
        "missing": missing,
        **extra,
    }
    if ok:
        if payload.get("kind") != "live":
            result["xml"] = build_finder_feed_xml(payload)
    else:
        result["error"] = "need_full_finder_payload"
    return result


def _best_payload(payloads: List[FinderPayload], object_id: str = "", nonce: str = "", username: str = "") -> Optional[FinderPayload]:
    if object_id:
        payloads = [p for p in payloads if str(p.get("objectId") or "") == str(object_id)] or payloads
    if nonce:
        payloads = [p for p in payloads if str(p.get("objectNonceId") or "") == str(nonce)] or payloads
    if username:
        payloads = [p for p in payloads if str(p.get("username") or "") == str(username)] or payloads
    valid_payloads = [payload for payload in payloads if validate_payload(payload)[0]]
    if valid_payloads:
        payloads = valid_payloads
    return max(payloads, key=_payload_score) if payloads else None


def lookup_payload_in_db(
    db_path: Path,
    *,
    object_id: str = "",
    nonce: str = "",
    username: str = "",
    query: str = "",
) -> Optional[FinderPayload]:
    if not db_path or not Path(db_path).exists():
        return None
    object_id = str(object_id or "").strip()
    nonce = str(nonce or "").strip()
    username = str(username or "").strip()
    query = str(query or "").strip()
    if not object_id and not query:
        return None
    clauses = ["(content LIKE '%<finderFeed%' OR content_xml LIKE '%<finderFeed%' OR raw_json LIKE '%finderFeed%' OR normalized_json LIKE '%finderFeed%')"]
    params: List[str] = []
    if object_id:
        clauses.append("(content LIKE ? OR content_xml LIKE ? OR raw_json LIKE ? OR normalized_json LIKE ?)")
        like = f"%{object_id}%"
        params.extend([like, like, like, like])
    if query:
        clauses.append("(content LIKE ? OR content_xml LIKE ? OR raw_json LIKE ? OR normalized_json LIKE ?)")
        like = f"%{query}%"
        params.extend([like, like, like, like])
    sql = (
        "SELECT content, content_xml, raw_json, normalized_json FROM messages "
        f"WHERE {' AND '.join(clauses)} ORDER BY CAST(timestamp AS INTEGER) DESC LIMIT 80"
    )
    payloads: List[FinderPayload] = []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute(sql, params):
            for col in ("content_xml", "content", "raw_json", "normalized_json"):
                text = str(row[col] or "")
                if not text:
                    continue
                if col.endswith("json"):
                    try:
                        obj = json.loads(text)
                    except json.JSONDecodeError:
                        obj = None
                    if isinstance(obj, dict):
                        text = json.dumps(obj, ensure_ascii=False)
                        raw_content = obj.get("content")
                        if raw_content:
                            payloads.extend(payloads_from_text(str(raw_content)))
                payloads.extend(payloads_from_text(text))
    return _best_payload(payloads, object_id=object_id, nonce=nonce, username=username)


def _resolve_with_endpoint(endpoint: str, payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    endpoint = str(endpoint or "").strip()
    if not endpoint:
        return {"ok": False, "error": "endpoint_not_configured"}
    if "{" in endpoint:
        url = endpoint.format(**{k: urllib.parse.quote(str(v or "")) for k, v in payload.items()})
        text, final_url, status = _fetch_text(url, timeout)
    else:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=data,
            headers={"Content-Type": "application/json", "Accept": "application/json,text/html,*/*"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(_MAX_TEXT_BYTES + 1)
            if len(raw) > _MAX_TEXT_BYTES:
                raise FinderResponseTooLargeError(
                    f"finder response exceeds {_MAX_TEXT_BYTES} bytes"
                )
            text = raw.decode("utf-8", errors="replace")
            final_url = resp.geturl()
            status = getattr(resp, "status", 200)
    candidates = payloads_from_text(text)
    best = _best_payload(candidates, object_id=str(payload.get("objectId") or ""), nonce=str(payload.get("objectNonceId") or ""))
    return _result_from_payload(best, "endpoint", final_url=final_url, http_status=status)


def resolve_finder_from_url(
    url: str,
    *,
    timeout: float = 15.0,
    db_path: Optional[Path] = None,
    detail_endpoint: str = "",
) -> Dict[str, Any]:
    params = parse_finder_url_params(url)
    direct_payload = _best_payload(payloads_from_text(url), object_id=params.get("objectId", ""))
    if direct_payload:
        return _result_from_payload(direct_payload, "input_text", parsed_params=params)

    fetched_error = ""
    final_url = ""
    http_status = 0
    if re.match(r"^https?://", url, re.I):
        try:
            text, final_url, http_status = _fetch_text(url, timeout)
            params = {**parse_finder_url_params(final_url), **params}
            best = _best_payload(payloads_from_text(text), object_id=params.get("objectId", ""))
            if best:
                return _result_from_payload(best, "url_fetch", parsed_params=params, final_url=final_url, http_status=http_status)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            fetched_error = str(exc)

    if detail_endpoint and params.get("objectId"):
        endpoint_result = _resolve_with_endpoint(detail_endpoint, params, timeout)
        if endpoint_result.get("ok"):
            endpoint_result["parsed_params"] = params
            return endpoint_result

    cached = (
        lookup_payload_in_db(
            Path(db_path),
            object_id=params.get("objectId", ""),
            nonce=params.get("objectNonceId", ""),
            username=params.get("username", ""),
        )
        if db_path and params.get("objectId")
        else None
    )
    if cached:
        return _result_from_payload(cached, "local_db_cache", parsed_params=params, final_url=final_url, http_status=http_status)
    return {
        "ok": False,
        "error": "need_full_finder_payload",
        "source": "url",
        "message": "URL/object params were parsed, but complete finderFeed media/avatar fields were not available.",
        "parsed_params": params,
        "missing": ["media.url", "media.thumbUrl", "media.coverUrl", "avatar"],
        "fetch_error": fetched_error,
        "final_url": final_url,
        "http_status": http_status,
    }


def resolve_finder_from_object(
    *,
    object_id: str,
    nonce: str = "",
    finder_username: str = "",
    timeout: float = 15.0,
    db_path: Optional[Path] = None,
    detail_endpoint: str = "",
) -> Dict[str, Any]:
    params = {
        "objectId": str(object_id or "").strip(),
        "objectNonceId": str(nonce or "").strip(),
        "username": str(finder_username or "").strip(),
    }
    if not params["objectId"]:
        return {"ok": False, "error": "missing_object_id"}
    if detail_endpoint:
        endpoint_result = _resolve_with_endpoint(detail_endpoint, params, timeout)
        if endpoint_result.get("ok"):
            endpoint_result["parsed_params"] = params
            return endpoint_result
    cached = (
        lookup_payload_in_db(
            Path(db_path),
            object_id=params["objectId"],
            nonce=params.get("objectNonceId", ""),
            username=params.get("username", ""),
        )
        if db_path
        else None
    )
    if cached:
        return _result_from_payload(cached, "local_db_cache", parsed_params=params)
    return {
        "ok": False,
        "error": "need_full_finder_payload",
        "source": "object",
        "message": "objectId alone is insufficient; provide a Finder detail endpoint or a URL/JSON/XML that contains full finderFeed fields.",
        "parsed_params": params,
        "missing": ["objectNonceId", "nickname", "username", "avatar", "media.url", "media.thumbUrl", "media.coverUrl"],
    }


def resolve_finder_from_text(
    text: str,
    *,
    source: str = "input_text",
    object_id: str = "",
    nonce: str = "",
    username: str = "",
    query: str = "",
    **extra: Any,
) -> Dict[str, Any]:
    best = _best_payload(
        payloads_from_text(text),
        object_id=str(object_id or ""),
        nonce=str(nonce or ""),
        username=str(username or ""),
    )
    if query:
        extra.setdefault("query", query)
    return _result_from_payload(best, source, **extra)


def resolve_finder_from_search(
    *,
    query: str,
    timeout: float = 15.0,
    db_path: Optional[Path] = None,
    search_endpoint: str = "",
    detail_endpoint: str = "",
    offset: int = 0,
    scene: int = 88,
    business_type: int = 0,
    sub_type: int = 0,
    current_page: Optional[int] = None,
    session_id: str = "",
    request_id: str = "",
) -> Dict[str, Any]:
    query = str(query or "").strip()
    if not query:
        return {"ok": False, "error": "missing_query"}
    if int(offset) > 0 and current_page is None:
        return {
            "ok": False,
            "error": "missing_current_page_for_offset",
            "message": "current_page is required when offset is greater than zero.",
            "query": query,
            "offset": int(offset),
        }
    search_endpoint = str(search_endpoint or os.environ.get("WX_FINDER_SEARCH_ENDPOINT", "")).strip()
    endpoint_error = ""
    if search_endpoint:
        endpoint_payload: Dict[str, Any] = {
            "query": query,
            "offset": int(offset),
            "scene": int(scene),
            "businessType": int(business_type),
            "subType": int(sub_type),
        }
        if current_page is not None:
            endpoint_payload["currentPage"] = int(current_page)
        if session_id:
            endpoint_payload["sessionId"] = str(session_id)
        if request_id:
            endpoint_payload["requestId"] = str(request_id)
        try:
            endpoint_result = _resolve_with_endpoint(search_endpoint, endpoint_payload, timeout)
            if endpoint_result.get("ok"):
                return endpoint_result
            endpoint_error = str(endpoint_result.get("error") or endpoint_result.get("message") or "")
            params = endpoint_result.get("parsed_params") if isinstance(endpoint_result.get("parsed_params"), dict) else {}
            object_id = str(params.get("objectId") or "")
            if object_id and detail_endpoint:
                return resolve_finder_from_object(object_id=object_id, timeout=timeout, db_path=db_path, detail_endpoint=detail_endpoint)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            endpoint_error = str(exc)
    cached = lookup_payload_in_db(Path(db_path), query=query) if db_path else None
    if cached:
        return _result_from_payload(cached, "local_db_cache", query=query, endpoint_error=endpoint_error)
    return {
        "ok": False,
        "error": "finder_search_needs_endpoint",
        "source": "search",
        "message": "No internal Finder search endpoint is configured; set WX_FINDER_SEARCH_ENDPOINT or pass a share URL.",
        "query": query,
        "endpoint_error": endpoint_error,
    }


def send_ready_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Return only fields useful to command output before native dispatch."""
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    media = payload.get("media") if isinstance(payload.get("media"), dict) else {}
    output = {
        "payload_source": result.get("source", ""),
        "objectId": payload.get("objectId", ""),
        "encryptedObjectId": payload.get("encryptedObjectId", ""),
        "searchDocId": payload.get("searchDocId", ""),
        "objectNonceId": payload.get("objectNonceId", ""),
        "liveId": payload.get("liveId", ""),
        "contextId": payload.get("contextId", ""),
        "entranceId": payload.get("entranceId", ""),
        "subEntranceId": payload.get("subEntranceId", ""),
        "liveUrl": payload.get("liveUrl", ""),
        "webUrl": payload.get("webUrl", ""),
        "anchorFinderUsername": payload.get("username", ""),
        "finderUsername": payload.get("username", ""),
        "nickname": payload.get("nickname", ""),
        "desc": payload.get("desc", ""),
        "mediaUrl": media.get("url", ""),
        "thumbUrl": media.get("thumbUrl", ""),
        "coverUrl": media.get("coverUrl", ""),
        "width": media.get("width", ""),
        "height": media.get("height", ""),
        "videoPlayDuration": media.get("videoPlayDuration", ""),
        "resolved_at": int(time.time()),
    }
    stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
    if stats:
        output["stats"] = stats
        output["statsExact"] = bool(stats.get("exact"))
        output["statsSource"] = stats.get("source", "")
    return output
