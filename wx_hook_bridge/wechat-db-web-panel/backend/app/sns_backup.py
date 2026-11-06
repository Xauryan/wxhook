"""Read-only WeChat Moments (SNS) parsing and backup service.

The source database is never opened for writing.  Backups contain a SQLite
snapshot plus normalized JSONL files; remote media URLs are retained as
metadata and are deliberately not fetched.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import uuid
import zipfile
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Mapping

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

try:  # lxml gives us recovery for a few damaged historical XML records.
    from lxml import etree
except ImportError:  # pragma: no cover - the panel ships lxml, fallback is useful for embedders.
    etree = None  # type: ignore[assignment]
import xml.etree.ElementTree as std_etree

from .config import Settings, settings as default_settings
from .sns_media import MediaResolver, ResolvedMedia, normalize_identifier
from .sns_sync import SnsSnapshotSync, SnsSnapshotSyncError


BACKUP_SCHEMA_VERSION = 1
BACKUP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RUNTIME_ARTIFACT_LIMIT = 16 * 1024 * 1024
RUNTIME_ARTIFACT_TOTAL_LIMIT = 64 * 1024 * 1024
SNS_RUNTIME_ARTIFACTS = (
    "sns_timeline_objects.jsonl",
    "sns_timeline_response.jsonl",
    "sns_media_decrypt.jsonl",
    "sns_media_probe.jsonl",
    "sns_upload_trace.jsonl",
    "sns_publish_trace.jsonl",
    "sns_like_probe.jsonl",
    "sns_model_lookup_probe.jsonl",
    "sns_model_validate_capture.jsonl",
    "sns_publish_video_thumb_map.tsv",
    "sns_image_dl_param.bin",
    "sns_full_cdn_param.bin",
)
_backup_semaphore = threading.BoundedSemaphore(1)


class SnsBackupError(RuntimeError):
    """Raised when an SNS read or backup operation cannot be completed."""


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _id(value: Any) -> str:
    """Normalize every identifier, including values larger than int64."""

    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return str(value).strip()


def _unsigned_64_id(value: Any) -> str:
    """Return SQLite's signed INTEGER bit pattern as a uint64 string."""

    text = _id(value)
    try:
        number = int(text)
    except ValueError:
        return text
    if number < 0:
        number += 1 << 64
    return str(number)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(_text(value).strip() or default)
    except (TypeError, ValueError, OverflowError):
        return default


def _float(value: Any) -> float | None:
    try:
        return float(_text(value).strip())
    except (TypeError, ValueError):
        return None


def _local_name(tag: Any) -> str:
    value = _text(tag)
    if "}" in value:
        value = value.rsplit("}", 1)[-1]
    if ":" in value:
        value = value.rsplit(":", 1)[-1]
    return value


def _element_text(element: Any) -> str:
    if element is None:
        return ""
    if etree is not None and hasattr(element, "itertext"):
        return "".join(str(part) for part in element.itertext()).strip()
    return "".join(element.itertext()).strip()


def _children(element: Any, name: str) -> list[Any]:
    wanted = name.casefold()
    if element is None:
        return []
    return [child for child in element.iter() if _local_name(child.tag).casefold() == wanted]


def _first(element: Any, name: str) -> Any | None:
    matches = _children(element, name)
    return matches[0] if matches else None


def _value(element: Any, name: str, default: str = "") -> str:
    match = _first(element, name)
    return _element_text(match) if match is not None else default


def _attr(element: Any, name: str, default: str = "") -> str:
    if element is None:
        return default
    wanted = name.casefold()
    for key, value in getattr(element, "attrib", {}).items():
        if _local_name(key).casefold() == wanted:
            return _text(value)
    return default


def _parse_xml(raw_xml: str) -> Any:
    payload = raw_xml.encode("utf-8", errors="replace")
    if etree is not None:
        parser = etree.XMLParser(recover=True, resolve_entities=False, no_network=True)
        try:
            return etree.fromstring(payload, parser=parser)
        except (etree.XMLSyntaxError, ValueError):
            pass
    try:
        return std_etree.fromstring(raw_xml)
    except (std_etree.ParseError, ValueError) as exc:
        raise SnsBackupError(f"invalid SnsTimeLine XML: {exc}") from exc


def _parse_media(content_object: Any) -> list[dict[str, Any]]:
    media_list = _first(content_object, "mediaList")
    result: list[dict[str, Any]] = []
    if media_list is None:
        return result
    for media in [item for item in media_list.iter() if _local_name(item.tag).casefold() == "media"]:
        thumb = _first(media, "thumb")
        url = _first(media, "url")
        size = _first(media, "size")
        item: dict[str, Any] = {
            "id": _id(_value(media, "id")),
            "type": _int(_value(media, "type")),
            "sub_type": _int(_value(media, "sub_type")),
            "thumb_url": _element_text(thumb),
            "url": _element_text(url),
            "md5": _attr(url, "md5"),
            "key": _attr(url, "key") or _attr(thumb, "key"),
            "width": _int(_attr(size, "width")),
            "height": _int(_attr(size, "height")),
            "total_size": _int(_attr(size, "totalSize")),
            "size": _int(_attr(size, "totalSize")),
            "video_duration": _int(_value(media, "videoDuration")),
            "duration": _int(_value(media, "videoDuration")),
            "remote_only": True,
            "available": False,
            "status": "remote_only",
        }
        # Preserve useful transport metadata without ever attempting a fetch.
        for field in ("enc", "predownload_percent", "predown_net_type"):
            item[field] = _int(_value(media, field))
        result.append(item)
    return result


def _parse_interaction(element: Any, *, kind: str, post_id: str) -> dict[str, Any]:
    comment_id = _id(_value(element, "comment_id"))
    comment64 = _id(_value(element, "comment_64id"))
    return {
        "id": comment64 or comment_id or _id(_value(element, "username")),
        "post_id": _id(post_id),
        "kind": kind,
        "username": _text(_value(element, "username")),
        "nickname": _text(_value(element, "nickname")),
        "content": _value(element, "content"),
        "comment_id": comment_id,
        "comment_64id": comment64,
        "ref_comment_id": _id(_value(element, "ref_comment_id")),
        "ref_comment_64id": _id(_value(element, "ref_comment_64id")),
        "source": _int(_value(element, "source")),
        "type": _int(_value(element, "type")),
        "create_time": _int(_value(element, "create_time")),
        "is_local_added": _int(_value(element, "is_local_added")),
        "is_rich_text": _int(_value(element, "is_rich_text")),
        "deleted": _int(_value(element, "b_deleted")),
        "comment_flag": _int(_value(element, "comment_flag")),
    }


def parse_timeline_xml(
    raw_xml: str | bytes,
    *,
    row_tid: Any = None,
    row_username: Any = None,
) -> dict[str, Any]:
    """Parse one ``SnsDataItem`` XML value into a JSON-compatible mapping.

    ``raw_xml`` is retained verbatim (after UTF-8 decoding for a bytes value),
    while all fields named as identifiers are represented as strings.
    """

    raw = _text(raw_xml)
    root = _parse_xml(raw)
    timeline = _first(root, "TimelineObject")
    if timeline is None:
        timeline = root
    local = _first(root, "LocalExtraInfo")
    post_id = _id(_value(timeline, "id")) or _unsigned_64_id(row_tid)
    username = _value(timeline, "username") or _text(row_username)
    nickname = _value(local, "nickname") or _value(timeline, "nickname")
    create_time = _int(_value(timeline, "createTime"))

    location_element = _first(timeline, "location")
    location: dict[str, Any] | None = None
    if location_element is not None:
        location = {}
        for key in (
            "city",
            "country",
            "poiName",
            "poiAddress",
            "poiAddressName",
            "poiClassifyId",
            "poiClassifyType",
            "poiScale",
            "poiClickableStatus",
        ):
            value = _attr(location_element, key)
            if value:
                location[key] = _id(value) if key == "poiClassifyId" else value
        for key in ("latitude", "longitude"):
            value = _attr(location_element, key)
            if value:
                location[key] = _float(value) if _float(value) is not None else value
        name = _attr(location_element, "poiName") or _attr(
            location_element, "poiAddressName"
        )
        address = _attr(location_element, "poiAddress")
        if name:
            location["name"] = name
        if address:
            location["address"] = address
        latitude = location.get("latitude")
        longitude = location.get("longitude")
        meaningful_coordinates = bool(
            isinstance(latitude, (int, float))
            and isinstance(longitude, (int, float))
            and (latitude != 0 or longitude != 0)
        )
        meaningful_attrs = any(
            location.get(key) not in {None, "", "0", 0, 0.0}
            for key in (
                "city",
                "country",
                "poiName",
                "poiAddress",
                "poiAddressName",
                "name",
                "address",
            )
        )
        if not meaningful_coordinates and not meaningful_attrs:
            location = None

    content_object = _first(timeline, "ContentObject")
    comments: list[dict[str, Any]] = []
    likes: list[dict[str, Any]] = []
    interaction_root = local if local is not None else root
    for container in _children(interaction_root, "comment_user_list"):
        for item in [child for child in container.iter() if _local_name(child.tag).casefold() == "user_comment"]:
            comments.append(_parse_interaction(item, kind="comment", post_id=post_id))
    for container in _children(interaction_root, "like_user_list"):
        for item in [child for child in container.iter() if _local_name(child.tag).casefold() == "user_comment"]:
            likes.append(_parse_interaction(item, kind="like", post_id=post_id))

    privacy = _int(_value(timeline, "private"))
    media = _parse_media(content_object)
    raw_tid = _id(row_tid)
    post: dict[str, Any] = {
        "id": post_id,
        "sns_id": post_id,
        "tid": post_id,
        "raw_tid": raw_tid or post_id,
        "username": username,
        "nickname": nickname,
        "content": _value(timeline, "contentDesc"),
        "create_time": create_time,
        "timestamp": create_time,
        "create_timestamp": create_time,
        "privacy": privacy,
        "private": bool(privacy),
        "visibility": "private" if privacy else "public",
        "is_owner": False,
        "public_username": _value(timeline, "publicUserName"),
        "source_username": _value(timeline, "sourceUserName"),
        "is_top": _int(_value(timeline, "isTop")),
        "content_type": _int(_value(content_object, "type")),
        "content_sub_style": _int(_value(content_object, "contentSubStyle")),
        "location": location,
        "type": _int(_value(content_object, "type")),
        "media": media,
        "comments": comments,
        "likes": likes,
        "media_count": len(media),
        "comment_count": len(comments),
        "like_count": len(likes),
        "like_flag": _int(_value(local, "like_flag")),
        "is_rich_text": _int(_value(local, "is_rich_text")),
        "raw_xml": raw,
    }
    # A few clients put an XML escaped content description in a CDATA node;
    # retaining whitespace is intentional because it is user-authored text.
    return post


# Compatibility aliases used by callers that prefer an explicit SNS prefix.
parse_sns_timeline_xml = parse_timeline_xml
parse_sns_xml = parse_timeline_xml


def validate_backup_id(backup_id: str) -> str:
    value = _text(backup_id)
    if (
        not BACKUP_ID_RE.fullmatch(value)
        or value in {".", ".."}
        or ".." in value
        or "/" in value
        or "\\" in value
    ):
        raise ValueError("invalid SNS backup id")
    return value


def _json_default(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _json_line(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=_json_default) + "\n").encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


class SnsBackupService:
    """Read and back up the Moments database without mutating source files."""

    def __init__(self, settings: Settings = default_settings, *, backup_root: Path | str | None = None) -> None:
        self.settings = settings
        self._source_lock = threading.RLock()
        database_root = Path(getattr(settings, "database_root"))
        candidate = database_root / "sns" / "sns.db"
        if not candidate.is_file() and database_root.name.casefold() == "sns":
            candidate = database_root / "sns.db"
        self.baseline_database_path = candidate.resolve()
        self.database_path = self.baseline_database_path
        panel_root = Path(getattr(settings, "panel_root", Path.cwd()))
        self.backup_root = Path(backup_root or (panel_root / ".cache" / "sns-backups")).resolve()
        self.backup_root.mkdir(parents=True, exist_ok=True)
        self._report = self._load_report()
        self.account = _text(self._report.get("account"))
        self._report_snapshot_time = _text(self._report.get("created_utc")) or datetime.fromtimestamp(
            self.baseline_database_path.stat().st_mtime
            if self.baseline_database_path.is_file()
            else 0,
            UTC,
        ).isoformat()
        self._media_token_secret = os.urandom(32)
        self._media_key_path = panel_root / ".cache" / "sns-media-keys.json"
        account_root = self._source_account_root()
        self.media_resolver: MediaResolver | None = None
        if account_root is not None:
            try:
                self.media_resolver = MediaResolver(
                    account_root,
                    decode_root=panel_root / ".cache" / "sns-media",
                    key_provider=self._media_keys,
                )
            except (FileNotFoundError, OSError, ValueError):
                self.media_resolver = None
        repository_root = Path(
            getattr(settings, "repository_root", panel_root.parent)
        )
        self.snapshot_sync: SnsSnapshotSync | None = None
        if _text(self._report.get("source_root")):
            configured_snapshot = _text(os.getenv("WECHAT_SNS_LIVE_SNAPSHOT"))
            live_snapshot = (
                Path(configured_snapshot).expanduser()
                if configured_snapshot
                else panel_root / ".cache" / "sns-live.sqlite"
            )
            if not live_snapshot.is_absolute():
                live_snapshot = panel_root / live_snapshot
            self.database_path = live_snapshot.resolve()
            self.snapshot_sync = SnsSnapshotSync(
                Path(getattr(settings, "report_path")),
                repository_root,
                self.database_path,
                lock=self._source_lock,
            )

    @property
    def snapshot_time(self) -> str:
        if self.snapshot_sync is not None:
            last_success = self.snapshot_sync.status().get("last_success")
            if last_success:
                return str(last_success)
        return self._report_snapshot_time

    def refresh_snapshot(self, *, force: bool = False) -> dict[str, Any]:
        if self.snapshot_sync is None:
            return {
                "configured": False,
                "enabled": False,
                "changed": False,
                "snapshot_time": self.snapshot_time,
                "last_error": "live SNS source is not configured",
            }
        previous_generation = int(self.snapshot_sync.status().get("generation") or 0)
        status = self.snapshot_sync.refresh(force=force)
        current_generation = int(status.get("generation") or 0)
        return {
            **status,
            "changed": current_generation > previous_generation,
            "enabled": True,
            "snapshot_time": self.snapshot_time,
        }

    def sync_status(self) -> dict[str, Any]:
        if self.snapshot_sync is None:
            return {
                "configured": False,
                "enabled": False,
                "changed": False,
                "snapshot_time": self.snapshot_time,
                "last_error": "live SNS source is not configured",
            }
        return {
            **self.snapshot_sync.status(),
            "enabled": True,
            "snapshot_time": self.snapshot_time,
        }

    def _media_keys(self) -> Mapping[str, Any] | None:
        """Load account image keys from the ignored local panel cache."""

        try:
            payload = json.loads(self._media_key_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, Mapping):
            return None
        accounts = payload.get("accounts")
        if isinstance(accounts, Mapping):
            account_keys = accounts.get(self.account)
            if isinstance(account_keys, Mapping):
                return account_keys
        return payload

    @staticmethod
    def _media_kind(media: Mapping[str, Any]) -> str:
        media_type = _text(media.get("type")).casefold()
        mime_type = _text(media.get("mime_type")).casefold()
        if (
            mime_type.startswith("video/")
            or media_type in {"6", "video", "movie", "video_message"}
            or _int(media.get("duration") or media.get("video_duration")) > 0
        ):
            return "video"
        return "image"

    def _encode_media_token(self, sns_id: Any, media: Mapping[str, Any]) -> str:
        payload = json.dumps(
            {
                "s": normalize_identifier(sns_id),
                "m": normalize_identifier(media.get("id")),
                "k": "v" if self._media_kind(media) == "video" else "i",
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        body = base64.urlsafe_b64encode(payload).rstrip(b"=")
        signature = hmac.new(self._media_token_secret, body, hashlib.sha256).digest()[:16]
        return (
            body.decode("ascii")
            + "."
            + base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
        )

    def _decode_media_token(self, token: str) -> tuple[str, str, str]:
        if len(token) > 512 or "." not in token:
            raise ValueError("invalid SNS media token")
        body_text, signature_text = token.split(".", 1)
        try:
            body = body_text.encode("ascii")
            expected = hmac.new(
                self._media_token_secret, body, hashlib.sha256
            ).digest()[:16]
            signature = base64.urlsafe_b64decode(
                signature_text + "=" * (-len(signature_text) % 4)
            )
            if not hmac.compare_digest(signature, expected):
                raise ValueError("invalid SNS media token")
            payload = json.loads(
                base64.urlsafe_b64decode(
                    body_text + "=" * (-len(body_text) % 4)
                ).decode("ascii")
            )
        except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("invalid SNS media token") from exc
        if not isinstance(payload, Mapping):
            raise ValueError("invalid SNS media token")
        sns_id = normalize_identifier(payload.get("s"))
        media_id = normalize_identifier(payload.get("m"))
        kind = _text(payload.get("k"))
        if not sns_id or not media_id or kind not in {"i", "v"}:
            raise ValueError("invalid SNS media token")
        return sns_id, media_id, kind

    def _resolve_media(
        self,
        sns_id: Any,
        media: Mapping[str, Any],
        *,
        variant: str,
    ) -> ResolvedMedia | None:
        if self.media_resolver is None or not media.get("id"):
            return None
        return self.media_resolver.resolve(
            sns_id,
            media.get("id"),
            media_type=media.get("type"),
            variant=variant,
            media_meta=media,
        )

    def _api_media(self, sns_id: Any, media: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(media)
        preview = self._resolve_media(sns_id, result, variant="preview")
        source = self._resolve_media(sns_id, result, variant="source")
        local = source or preview
        if local is None:
            return result
        token = self._encode_media_token(sns_id, result)
        base_url = f"/api/sns/media/{token}"
        kind = self._media_kind(result)
        result.update(
            {
                "token": token,
                "media_token": token,
                "available": True,
                "resource_available": True,
                "browser_supported": bool(local.browser_supported),
                "remote_only": False,
                "status": "local_cache",
                "mime_type": local.mime_type,
                "file_name": local.file_name,
                "size": local.size or result.get("size") or 0,
                "preview_url": f"{base_url}?variant=preview" if preview else None,
                "media_url": f"{base_url}?variant=source" if source else None,
                "download_url": (
                    f"{base_url}?variant=source&download=true" if source else None
                ),
            }
        )
        if kind == "video" and preview is not None:
            result["poster_url"] = f"{base_url}?variant=preview"
        dimensions = preview or source
        if dimensions is not None:
            result["width"] = dimensions.width or result.get("width") or 0
            result["height"] = dimensions.height or result.get("height") or 0
        return result

    def media_file(self, token: str, *, variant: str = "source") -> ResolvedMedia | None:
        if self.media_resolver is None:
            return None
        sns_id, media_id, kind = self._decode_media_token(token)
        media_type = 6 if kind == "v" else 2
        return self.media_resolver.resolve(
            sns_id,
            media_id,
            media_type=media_type,
            variant=variant,
            media_meta={"type": media_type, "duration": 1 if kind == "v" else 0},
        )

    def _load_report(self) -> dict[str, Any]:
        report_path = getattr(self.settings, "report_path", None)
        if not report_path or not Path(report_path).is_file():
            return {}
        try:
            payload = json.loads(Path(report_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _active_database_path(self) -> Path:
        if self.database_path.is_file():
            return self.database_path
        return self.baseline_database_path

    @contextmanager
    def _source(self) -> Iterator[sqlite3.Connection]:
        with self._source_lock:
            database_path = self._active_database_path()
            if not database_path.is_file():
                raise FileNotFoundError(database_path)
            # The published snapshot is complete and never has to consult a
            # sidecar WAL. Immutable mode also prevents SQLite from creating
            # auxiliary files next to the atomically replaced snapshot.
            uri = database_path.as_uri() + "?mode=ro&immutable=1"
            connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
            connection.text_factory = bytes
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            try:
                yield connection
            finally:
                connection.close()

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        return row is not None

    def _source_inventory(
        self, connection: sqlite3.Connection
    ) -> tuple[dict[str, Any], dict[str, int]]:
        tables: dict[str, Any] = {}
        row_counts: dict[str, int] = {}
        rows = connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        for row in rows:
            name = _text(row["name"])
            if name.startswith("sqlite_"):
                continue
            columns = []
            for column in connection.execute(
                f"PRAGMA table_info({_quote_identifier(name)})"
            ):
                columns.append(
                    {
                        "cid": int(column[0]),
                        "name": _text(column[1]),
                        "type": _text(column[2]),
                        "not_null": bool(column[3]),
                        "default": _text(column[4]) if column[4] is not None else None,
                        "primary_key": bool(column[5]),
                    }
                )
            tables[name] = {"sql": _text(row["sql"]), "columns": columns}
            row_counts[name] = int(
                connection.execute(
                    f"SELECT count(*) FROM {_quote_identifier(name)}"
                ).fetchone()[0]
            )
        return tables, row_counts

    def _source_fingerprint(self) -> dict[str, Any]:
        stat = self._active_database_path().stat()
        report_entry: dict[str, Any] = {}
        for item in self._report.get("files", []):
            if _text(item.get("path")).replace("\\", "/") == "sns/sns.db":
                report_entry = item
                break
        return {
            "source_file_size": stat.st_size,
            "source_file_mtime_ns": stat.st_mtime_ns,
            "reported_sha256": _text(report_entry.get("sha256")),
        }

    def _source_account_root(self) -> Path | None:
        source_root = _text(self._report.get("source_root"))
        if not source_root:
            return None
        root = Path(source_root).expanduser().resolve().parent
        return root if root.is_dir() else None

    @staticmethod
    def _copy_tree_files(source_root: Path, output_root: Path) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        if source_root.is_symlink() or not source_root.is_dir():
            return records
        resolved_root = source_root.resolve()
        for source in sorted(source_root.rglob("*")):
            relative = source.relative_to(source_root)
            if source.is_symlink():
                records.append(
                    {
                        "path": relative.as_posix(),
                        "size": 0,
                        "status": "skipped_symlink",
                    }
                )
                continue
            if not source.is_file():
                continue
            try:
                source.resolve(strict=True).relative_to(resolved_root)
            except (OSError, RuntimeError, ValueError):
                records.append(
                    {
                        "path": relative.as_posix(),
                        "size": 0,
                        "status": "skipped_outside_source_root",
                    }
                )
                continue
            target = output_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(source, target)
            except OSError as exc:
                records.append(
                    {
                        "path": relative.as_posix(),
                        "size": source.stat().st_size if source.exists() else 0,
                        "status": "skipped_io_error",
                        "error": type(exc).__name__,
                    }
                )
                continue
            records.append(
                {
                    "path": relative.as_posix(),
                    "size": target.stat().st_size,
                    "sha256": _sha256(target),
                    "status": "copied",
                }
            )
        return records

    def _copy_local_media(self, payload_dir: Path) -> list[dict[str, Any]]:
        account_root = self._source_account_root()
        if account_root is None:
            return []
        output = payload_dir / "local_media"
        records: list[dict[str, Any]] = []
        cache_root = account_root / "cache"
        if cache_root.is_dir():
            for month in sorted(path for path in cache_root.iterdir() if path.is_dir()):
                source = month / "Sns"
                for item in self._copy_tree_files(source, output / "cache" / month.name):
                    item["path"] = f"local_media/cache/{month.name}/{item['path']}"
                    item["source_kind"] = "sns_cache"
                    records.append(item)
        background = account_root / "business" / "sns"
        for item in self._copy_tree_files(background, output / "business"):
            item["path"] = f"local_media/business/{item['path']}"
            item["source_kind"] = "sns_business"
            records.append(item)
        return records

    def _local_media_summary(self) -> tuple[int, int]:
        account_root = self._source_account_root()
        if account_root is None:
            return 0, 0
        roots: list[Path] = []
        cache_root = account_root / "cache"
        if cache_root.is_dir():
            roots.extend(
                month / "Sns"
                for month in cache_root.iterdir()
                if month.is_dir() and (month / "Sns").is_dir()
            )
        roots.append(account_root / "business" / "sns")
        count = 0
        total = 0
        for root in roots:
            if root.is_symlink() or not root.is_dir():
                continue
            resolved_root = root.resolve()
            for path in root.rglob("*"):
                if path.is_symlink() or not path.is_file():
                    continue
                try:
                    path.resolve(strict=True).relative_to(resolved_root)
                    size = path.stat().st_size
                except (OSError, RuntimeError, ValueError):
                    continue
                count += 1
                total += size
        return count, total

    def _copy_runtime_artifacts(self, payload_dir: Path) -> list[dict[str, Any]]:
        repository_root = getattr(self.settings, "repository_root", None)
        if not repository_root:
            return []
        control_root = Path(repository_root) / "bridge" / "control"
        output = payload_dir / "runtime_artifacts"
        total = 0
        records: list[dict[str, Any]] = []
        for name in SNS_RUNTIME_ARTIFACTS:
            source = control_root / name
            if not source.is_file():
                continue
            if source.is_symlink():
                records.append(
                    {
                        "path": name,
                        "size": 0,
                        "status": "skipped_symlink",
                    }
                )
                continue
            try:
                size_hint = source.stat().st_size
            except OSError as exc:
                records.append(
                    {
                        "path": name,
                        "size": 0,
                        "status": "skipped_io_error",
                        "error": type(exc).__name__,
                    }
                )
                continue
            allowed = min(
                RUNTIME_ARTIFACT_LIMIT,
                max(0, RUNTIME_ARTIFACT_TOTAL_LIMIT - total),
            )
            if size_hint > allowed:
                records.append(
                    {
                        "path": name,
                        "size": size_hint,
                        "status": "skipped_size_limit",
                    }
                )
                continue
            output.mkdir(parents=True, exist_ok=True)
            target = output / name
            copied = 0
            too_large = False
            observed_size = size_hint
            try:
                with source.open("rb") as source_stream, target.open("xb") as target_stream:
                    while True:
                        remaining = allowed - copied
                        chunk = source_stream.read(min(1024 * 1024, remaining + 1))
                        if not chunk:
                            break
                        observed_size = max(observed_size, copied + len(chunk))
                        if len(chunk) > remaining:
                            too_large = True
                            break
                        target_stream.write(chunk)
                        copied += len(chunk)
                if too_large:
                    target.unlink(missing_ok=True)
                    records.append(
                        {
                            "path": name,
                            "size": observed_size,
                            "status": "skipped_size_limit",
                        }
                    )
                    continue
                shutil.copystat(source, target)
            except OSError as exc:
                target.unlink(missing_ok=True)
                records.append(
                    {
                        "path": name,
                        "size": observed_size,
                        "status": "skipped_io_error",
                        "error": type(exc).__name__,
                    }
                )
                continue
            total += copied
            records.append(
                {
                    "path": f"runtime_artifacts/{name}",
                    "size": copied,
                    "sha256": _sha256(target),
                    "status": "copied",
                }
            )
        return records

    def _iter_raw_interactions(
        self, connection: sqlite3.Connection
    ) -> Iterator[dict[str, Any]]:
        if not self._table_exists(connection, "SnsMessage_tmp3"):
            return
        rows = connection.execute("SELECT * FROM SnsMessage_tmp3 ORDER BY local_id")
        blob_fields = {"serialized_comment_buf", "serialized_ref_buf"}
        id_fields = {
            "local_id",
            "feed_id",
            "comment_id",
            "comment64_id",
        }
        for row in rows:
            item: dict[str, Any] = {"record_source": "SnsMessage_tmp3"}
            for key in row.keys():
                value = row[key]
                if key in blob_fields:
                    payload = bytes(value or b"")
                    item[key] = {
                        "encoding": "base64",
                        "base64": base64.b64encode(payload).decode("ascii"),
                        "hex": payload.hex(),
                        "bytes": len(payload),
                    }
                elif key in id_fields:
                    item[key] = _id(value)
                elif isinstance(value, bytes):
                    item[key] = _text(value)
                else:
                    item[key] = value
            item["post_id"] = _unsigned_64_id(row["feed_id"])
            yield item

    @staticmethod
    def _decode_row(value: Any) -> str:
        return _text(value)

    def _iter_posts(self, connection: sqlite3.Connection) -> Iterator[dict[str, Any]]:
        try:
            rows = connection.execute(
                "SELECT tid, user_name, content FROM SnsTimeLine ORDER BY tid DESC"
            )
        except sqlite3.DatabaseError as exc:
            raise SnsBackupError(f"cannot read SnsTimeLine: {exc}") from exc
        for row in rows:
            raw = self._decode_row(row["content"])
            try:
                post = parse_timeline_xml(
                    raw, row_tid=row["tid"], row_username=row["user_name"]
                )
                post["is_owner"] = bool(
                    self.account and post.get("username") == self.account
                )
                yield post
            except SnsBackupError:
                # Keep malformed records available to the backup instead of
                # dropping them; the raw XML remains searchable/auditable.
                raw_tid = _id(row["tid"])
                fallback_id = _unsigned_64_id(row["tid"])
                yield {
                    "id": fallback_id,
                    "sns_id": fallback_id,
                    "tid": fallback_id,
                    "raw_tid": raw_tid,
                    "username": self._decode_row(row["user_name"]),
                    "nickname": "",
                    "content": "",
                    "create_time": 0,
                    "timestamp": 0,
                    "create_timestamp": 0,
                    "privacy": 0,
                    "private": False,
                    "visibility": "public",
                    "is_owner": False,
                    "public_username": "",
                    "source_username": "",
                    "is_top": 0,
                    "content_type": 0,
                    "content_sub_style": 0,
                    "type": 0,
                    "location": None,
                    "media": [],
                    "comments": [],
                    "likes": [],
                    "media_count": 0,
                    "comment_count": 0,
                    "like_count": 0,
                    "like_flag": 0,
                    "is_rich_text": 0,
                    "raw_xml": raw,
                    "parse_error": True,
                }

    def _api_post(self, post: Mapping[str, Any], *, include_raw: bool) -> dict[str, Any]:
        result = dict(post)
        timestamp = _int(result.get("timestamp") or result.get("create_timestamp"))
        result["create_timestamp"] = timestamp * 1000 if timestamp else 0
        result["create_time"] = (
            datetime.fromtimestamp(timestamp, UTC).isoformat() if timestamp else None
        )
        if not include_raw:
            result.pop("raw_xml", None)
        result["media"] = [
            self._api_media(result.get("sns_id"), item)
            for item in result.get("media", [])
        ]
        return result

    @staticmethod
    def _matches(
        post: Mapping[str, Any],
        *,
        query: str,
        from_ts: int | None,
        to_ts: int | None,
    ) -> bool:
        timestamp = _int(post.get("timestamp"))
        if from_ts is not None and timestamp < int(from_ts):
            return False
        if to_ts is not None and timestamp > int(to_ts):
            return False
        if not query:
            return True
        haystack = "\n".join(
            _text(post.get(field))
            for field in ("sns_id", "username", "nickname", "content")
        ).casefold()
        return query in haystack

    def search_posts(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        q: str | None = None,
        from_ts: int | None = None,
        to_ts: int | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        query = (q or "").casefold().strip()
        items: list[dict[str, Any]] = []
        total = 0
        with self._source() as connection:
            for post in self._iter_posts(connection):
                if not self._matches(
                    post, query=query, from_ts=from_ts, to_ts=to_ts
                ):
                    continue
                if offset <= total < offset + limit:
                    items.append(self._api_post(post, include_raw=False))
                total += 1
        return items, total

    def list_posts(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        q: str | None = None,
        from_ts: int | None = None,
        to_ts: int | None = None,
    ) -> list[dict[str, Any]]:
        return self.search_posts(
            limit=limit, offset=offset, q=q, from_ts=from_ts, to_ts=to_ts
        )[0]

    def get_post(self, sns_id: str) -> dict[str, Any] | None:
        wanted = _id(sns_id)
        with self._source() as connection:
            for post in self._iter_posts(connection):
                if wanted in {
                    post["id"],
                    post["tid"],
                    post.get("raw_tid", ""),
                }:
                    result = self._api_post(post, include_raw=True)
                    raw_interactions = [
                        interaction
                        for interaction in self._iter_raw_interactions(connection)
                        if interaction.get("post_id") == post["sns_id"]
                        or interaction.get("feed_id") == post.get("raw_tid")
                    ]
                    result["interactions"] = [
                        *post["comments"],
                        *post["likes"],
                        *raw_interactions,
                    ]
                    result["raw_interaction_count"] = len(raw_interactions)
                    return result
        return None

    def stats(self) -> dict[str, Any]:
        counts = {
            "posts": 0,
            "comments": 0,
            "likes": 0,
            "media": 0,
            "raw_interactions": 0,
        }
        users: set[str] = set()
        first: int | None = None
        last: int | None = None
        with self._source() as connection:
            for post in self._iter_posts(connection):
                counts["posts"] += 1
                counts["comments"] += len(post["comments"])
                counts["likes"] += len(post["likes"])
                counts["media"] += len(post["media"])
                if post.get("username"):
                    users.add(post["username"])
                timestamp = int(post.get("create_time") or 0)
                if timestamp:
                    first = timestamp if first is None else min(first, timestamp)
                    last = timestamp if last is None else max(last, timestamp)
            counts["raw_interactions"] = sum(
                1 for _ in self._iter_raw_interactions(connection)
            )
        local_media, local_media_bytes = self._local_media_summary()
        return {
            **counts,
            "interactions": counts["comments"]
            + counts["likes"]
            + counts["raw_interactions"],
            "users": len(users),
            "remote_media": counts["media"],
            "local_media": local_media,
            "local_media_bytes": local_media_bytes,
            "source_rows": counts["posts"],
            "first_create_time": first,
            "last_create_time": last,
            "snapshot_time": self.snapshot_time,
        }

    def _new_backup_id(self) -> str:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        return validate_backup_id(f"{timestamp}-{uuid.uuid4().hex[:12]}")

    def create_backup(
        self,
        backup_id: str | None = None,
        *,
        include_local_media: bool = True,
        include_runtime_artifacts: bool = True,
    ) -> dict[str, Any]:
        identifier = validate_backup_id(backup_id) if backup_id else self._new_backup_id()
        archive_path = self.backup_root / f"{identifier}.zip"
        if archive_path.exists():
            raise FileExistsError(f"SNS backup already exists: {identifier}")
        work = Path(tempfile.mkdtemp(prefix=f".{identifier}-", dir=self.backup_root))
        zip_tmp = self.backup_root / f".{identifier}.{uuid.uuid4().hex}.zip.tmp"
        try:
            source_fingerprint = self._source_fingerprint()
            payload_dir = work / "payload"
            payload_dir.mkdir()
            posts_path = payload_dir / "posts.jsonl"
            comments_path = payload_dir / "comments.jsonl"
            likes_path = payload_dir / "likes.jsonl"
            media_path = payload_dir / "media.jsonl"
            interactions_path = payload_dir / "interactions.jsonl"
            counts = {
                "posts": 0,
                "comments": 0,
                "likes": 0,
                "media": 0,
                "raw_interactions": 0,
                "interactions": 0,
                "local_media_files": 0,
                "runtime_artifacts": 0,
            }
            source_tables: dict[str, Any] = {}
            source_row_counts: dict[str, int] = {}
            snapshot_path = payload_dir / "sns.db"
            with self._source() as source, closing(
                sqlite3.connect(snapshot_path)
            ) as snapshot:
                # SQLite's online backup API takes a consistent snapshot while
                # the source remains read-only.
                try:
                    source.backup(snapshot)
                    snapshot.commit()
                except sqlite3.DatabaseError as exc:
                    raise SnsBackupError(f"cannot snapshot sns.db: {exc}") from exc

            # The logical database may include live WAL pages that are absent
            # from both the base file and an older decryption report. Fingerprint
            # the consistent snapshot that is actually stored in the archive.
            source_fingerprint.update(
                {
                    "size": snapshot_path.stat().st_size,
                    "sha256": _sha256(snapshot_path),
                    "basis": "sqlite_online_backup",
                }
            )

            # Every structured export is derived from the copied database so
            # the SQLite snapshot and JSONL records describe one exact point.
            with closing(sqlite3.connect(snapshot_path)) as snapshot_reader:
                snapshot_reader.text_factory = bytes
                snapshot_reader.row_factory = sqlite3.Row
                snapshot_reader.execute("PRAGMA query_only=ON")
                source_tables, source_row_counts = self._source_inventory(
                    snapshot_reader
                )
                with (
                    posts_path.open("wb") as posts_stream,
                    comments_path.open("wb") as comments_stream,
                    likes_path.open("wb") as likes_stream,
                    media_path.open("wb") as media_stream,
                    interactions_path.open("wb") as interactions_stream,
                ):
                    for post in self._iter_posts(snapshot_reader):
                        counts["posts"] += 1
                        posts_stream.write(_json_line(post))
                        for media in post["media"]:
                            counts["media"] += 1
                            media_stream.write(_json_line({**media, "post_id": post["id"]}))
                        for kind, stream in (("comment", comments_stream), ("like", likes_stream)):
                            for interaction in post[f"{kind}s"]:
                                counts[f"{kind}s"] += 1
                                stream.write(_json_line(interaction))
                                interactions_stream.write(
                                    _json_line(
                                        {**interaction, "record_source": "timeline_xml"}
                                    )
                                )
                                counts["interactions"] += 1
                    for interaction in self._iter_raw_interactions(snapshot_reader):
                        interactions_stream.write(_json_line(interaction))
                        counts["raw_interactions"] += 1
                        counts["interactions"] += 1

            local_media = (
                self._copy_local_media(payload_dir) if include_local_media else []
            )
            runtime_artifacts = (
                self._copy_runtime_artifacts(payload_dir)
                if include_runtime_artifacts
                else []
            )
            counts["local_media_files"] = sum(
                item.get("status") == "copied" for item in local_media
            )
            counts["runtime_artifacts"] = sum(
                item.get("status") == "copied" for item in runtime_artifacts
            )
            media_policy = (
                "local_cache_copy_plus_remote_metadata"
                if counts["local_media_files"]
                else "remote_metadata_only"
            )

            schema = {
                "schema_version": BACKUP_SCHEMA_VERSION,
                "format": "wechat-sns-backup",
                "encoding": "utf-8",
                "records": {
                    "posts": "posts.jsonl",
                    "comments": "comments.jsonl",
                    "likes": "likes.jsonl",
                    "media": "media.jsonl",
                    "interactions": "interactions.jsonl",
                    "sqlite_snapshot": "sns.db",
                    "local_media": "local_media/ (optional source cache copy)",
                    "runtime_artifacts": "runtime_artifacts/ (bounded native SNS logs)",
                },
                "ids": "all identifiers are JSON strings",
                "media_policy": media_policy,
                "sensitivity": (
                    "local private archive; raw XML and media metadata may contain "
                    "temporary signed URLs"
                ),
                "source_tables": source_tables,
                "local_media": local_media,
                "runtime_artifacts": runtime_artifacts,
            }
            (payload_dir / "schema.json").write_text(
                json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            file_hashes: dict[str, dict[str, Any]] = {}
            for path in sorted(payload_dir.rglob("*")):
                if path.is_file():
                    rel = path.relative_to(payload_dir).as_posix()
                    file_hashes[rel] = {"size": path.stat().st_size, "sha256": _sha256(path)}
            manifest = {
                "schema_version": BACKUP_SCHEMA_VERSION,
                "format": "wechat-sns-backup",
                "backup_id": identifier,
                "created_at": datetime.now(UTC).isoformat(),
                "source_database": "sns.db",
                "source_database_relative": "sns/sns.db",
                "source_fingerprint": source_fingerprint,
                "source_row_counts": source_row_counts,
                "account": self.account,
                "snapshot_time": self.snapshot_time,
                "media_policy": media_policy,
                "remote_only": not bool(local_media),
                "include_local_media": include_local_media,
                "include_runtime_artifacts": include_runtime_artifacts,
                "sensitivity": "local_private_archive",
                "local_media": local_media,
                "runtime_artifacts": runtime_artifacts,
                "counts": counts,
                "files": file_hashes,
                "sha256": {name: info["sha256"] for name, info in file_hashes.items()},
            }
            (payload_dir / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            with zipfile.ZipFile(zip_tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(payload_dir.rglob("*")):
                    if path.is_file():
                        archive.write(path, path.relative_to(payload_dir).as_posix())
            try:
                # A hard-link publish is atomic and never replaces an existing
                # archive, including when another process uses the same ID.
                os.link(zip_tmp, archive_path)
            except FileExistsError as exc:
                raise FileExistsError(
                    f"SNS backup already exists: {identifier}"
                ) from exc
            except OSError as exc:
                raise SnsBackupError(f"cannot publish SNS backup: {exc}") from exc
            zip_tmp.unlink()
            metadata = {
                **manifest,
                "archive": archive_path.name,
                "path": archive_path.name,
                "archive_size": archive_path.stat().st_size,
                "bytes": archive_path.stat().st_size,
                "archive_sha256": _sha256(archive_path),
                "manifest_sha256": _sha256(payload_dir / "manifest.json"),
                "download_url": f"/api/sns/backups/{identifier}/download",
                "manifest": manifest,
            }
            metadata["sha256"] = metadata["archive_sha256"]
            meta_path = self.backup_root / f"{identifier}.meta.json"
            temporary_meta = self.backup_root / f".{identifier}.{uuid.uuid4().hex}.meta.tmp"
            temporary_meta.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary_meta, meta_path)
            return metadata
        finally:
            zip_tmp.unlink(missing_ok=True)
            shutil.rmtree(work, ignore_errors=True)

    def list_backups(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for candidate in sorted(self.backup_root.glob("*.zip"), reverse=True):
            if candidate.is_symlink():
                continue
            try:
                archive = candidate.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if archive.parent != self.backup_root or not archive.is_file():
                continue
            try:
                identifier = validate_backup_id(candidate.stem)
            except ValueError:
                continue
            metadata_path = self.backup_root / f"{identifier}.meta.json"
            metadata: dict[str, Any] = {}
            safe_metadata_path: Path | None = None
            if metadata_path.is_file() and not metadata_path.is_symlink():
                try:
                    resolved_metadata = metadata_path.resolve(strict=True)
                except (OSError, RuntimeError):
                    resolved_metadata = None
                if (
                    resolved_metadata is not None
                    and resolved_metadata.parent == self.backup_root
                ):
                    safe_metadata_path = resolved_metadata
            if safe_metadata_path is not None:
                try:
                    metadata = json.loads(
                        safe_metadata_path.read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError):
                    metadata = {}
            if not metadata:
                try:
                    with zipfile.ZipFile(archive) as stream:
                        metadata = json.loads(stream.read("manifest.json"))
                except (OSError, KeyError, zipfile.BadZipFile, json.JSONDecodeError):
                    metadata = {"backup_id": identifier, "format": "wechat-sns-backup"}
            metadata.update(
                {
                    "backup_id": identifier,
                    "archive": archive.name,
                    "path": archive.name,
                    "archive_size": archive.stat().st_size,
                    "bytes": archive.stat().st_size,
                    "download_url": f"/api/sns/backups/{identifier}/download",
                }
            )
            if "archive_sha256" in metadata:
                metadata.setdefault("sha256", metadata["archive_sha256"])
            else:
                metadata["sha256"] = _sha256(archive)
            result.append(metadata)
        return result

    def backup_metadata(self, backup_id: str) -> dict[str, Any] | None:
        identifier = validate_backup_id(backup_id)
        try:
            archive = self.backup_path(identifier)
        except FileNotFoundError:
            return None
        for item in self.list_backups():
            if item.get("backup_id") == identifier:
                return item
        return {
            "backup_id": identifier,
            "archive": archive.name,
            "path": archive.name,
            "archive_size": archive.stat().st_size,
            "bytes": archive.stat().st_size,
            "sha256": _sha256(archive),
            "download_url": f"/api/sns/backups/{identifier}/download",
        }

    def backup_path(self, backup_id: str) -> Path:
        identifier = validate_backup_id(backup_id)
        candidate = self.backup_root / f"{identifier}.zip"
        if candidate.is_symlink():
            raise FileNotFoundError(identifier)
        try:
            path = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise FileNotFoundError(identifier) from exc
        if path.parent != self.backup_root or not path.is_file():
            raise FileNotFoundError(identifier)
        return path

    def delete_backup(self, backup_id: str) -> bool:
        identifier = validate_backup_id(backup_id)
        archive = self.backup_root / f"{identifier}.zip"
        metadata = self.backup_root / f"{identifier}.meta.json"
        existed = (
            archive.is_file()
            or archive.is_symlink()
            or metadata.is_file()
            or metadata.is_symlink()
        )
        if archive.is_file() or archive.is_symlink():
            archive.unlink()
        if metadata.is_file() or metadata.is_symlink():
            metadata.unlink()
        return existed


# Common capitalization alias for external integrations.
SNSBackupService = SnsBackupService


def _require_loopback(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(status_code=403, detail="SNS backup API is loopback-only")


class SnsBackupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backup_id: str | None = Field(default=None, max_length=128)
    include_local_media: bool = True
    include_runtime_artifacts: bool = True


def create_router(service: SnsBackupService | None = None) -> APIRouter:
    """Build the Moments API router; applications may include it in main."""

    target = service or SnsBackupService()
    api = APIRouter(prefix="/api")

    @api.get("/sns")
    def sns_list(
        request: Request,
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        q: str | None = None,
        from_ts: int | None = None,
        to_ts: int | None = None,
    ) -> dict[str, Any]:
        _require_loopback(request)
        posts, total = target.search_posts(
            limit=limit,
            offset=offset,
            q=q,
            from_ts=from_ts,
            to_ts=to_ts,
        )
        return {
            "items": posts,
            "posts": posts,
            "total": total,
            "limit": limit,
            "offset": offset,
            "query": q or "",
            "snapshot_time": target.snapshot_time,
            "sync": target.sync_status(),
            "stats": target.stats(),
            "metric_basis": (
                "动态来自自动刷新的本地只读 sns.db 快照；精确匹配到缓存的图片和视频可直接预览，"
                "未命中的媒体仍保留远程元数据。"
            ),
        }

    @api.get("/sns/stats")
    def sns_stats(request: Request) -> dict[str, Any]:
        _require_loopback(request)
        return target.stats()

    @api.get("/sns/sync/status")
    def sns_sync_status(request: Request) -> dict[str, Any]:
        _require_loopback(request)
        return target.sync_status()

    @api.post("/sns/sync")
    def sns_sync(request: Request) -> dict[str, Any]:
        _require_loopback(request)
        try:
            return target.refresh_snapshot(force=True)
        except (OSError, sqlite3.DatabaseError, SnsSnapshotSyncError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @api.get("/sns/media/{token}")
    def sns_media(
        request: Request,
        token: str,
        variant: str = Query("source", pattern=r"^(preview|poster|source|original)$"),
        download: bool = False,
    ) -> FileResponse:
        _require_loopback(request)
        try:
            media = target.media_file(token, variant=variant)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="SNS media not found") from exc
        if media is None:
            raise HTTPException(status_code=404, detail="SNS media not found")
        return FileResponse(
            media.path,
            media_type=media.mime_type,
            filename=media.file_name if download else None,
            content_disposition_type="attachment" if download else "inline",
            headers={
                "Cache-Control": "private, max-age=3600",
                "X-Content-Type-Options": "nosniff",
                "Accept-Ranges": "bytes",
            },
        )

    @api.get("/sns/backups")
    def sns_backups(request: Request) -> dict[str, Any]:
        _require_loopback(request)
        items = target.list_backups()
        return {"items": items, "backups": items, "total": len(items)}

    @api.post("/sns/backups", status_code=201)
    def sns_create_backup(
        request: Request, payload: SnsBackupRequest | None = None
    ) -> dict[str, Any]:
        _require_loopback(request)
        options = payload or SnsBackupRequest()
        try:
            with _backup_semaphore:
                return target.create_backup(
                    options.backup_id,
                    include_local_media=options.include_local_media,
                    include_runtime_artifacts=options.include_runtime_artifacts,
                )
        except (ValueError, FileExistsError, FileNotFoundError, SnsBackupError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.get("/sns/backups/{backup_id}")
    def sns_backup_metadata(request: Request, backup_id: str) -> dict[str, Any]:
        _require_loopback(request)
        try:
            metadata = target.backup_metadata(backup_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if metadata is None:
            raise HTTPException(status_code=404, detail="SNS backup not found")
        return metadata

    @api.get("/sns/backups/{backup_id}/download")
    def sns_backup_download(request: Request, backup_id: str) -> FileResponse:
        _require_loopback(request)
        try:
            path = target.backup_path(backup_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="SNS backup not found") from exc
        return FileResponse(path, media_type="application/zip", filename=path.name)

    @api.delete("/sns/backups/{backup_id}")
    def sns_backup_delete(request: Request, backup_id: str) -> dict[str, Any]:
        _require_loopback(request)
        try:
            deleted = target.delete_backup(backup_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="SNS backup not found")
        return {"deleted": True, "backup_id": backup_id}

    @api.get("/sns/{sns_id}")
    def sns_detail(request: Request, sns_id: str) -> dict[str, Any]:
        _require_loopback(request)
        post = target.get_post(sns_id)
        if post is None:
            raise HTTPException(status_code=404, detail="SNS post not found")
        return post

    return api


service = SnsBackupService()
router = create_router(service)


__all__ = [
    "BACKUP_SCHEMA_VERSION",
    "SnsBackupError",
    "SnsBackupService",
    "SNSBackupService",
    "create_router",
    "parse_sns_timeline_xml",
    "parse_sns_xml",
    "parse_timeline_xml",
    "router",
    "service",
    "validate_backup_id",
]
