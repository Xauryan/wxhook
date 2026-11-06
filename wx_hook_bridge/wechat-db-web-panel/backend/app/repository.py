from __future__ import annotations

import csv
import hashlib
import html
import importlib.util
import io
import json
import mimetypes
import os
import re
import sqlite3
import statistics
import threading
import uuid
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator, Sequence
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .analytics import (
    bidirectional_ratio,
    count_turns,
    relationship_strength,
    response_durations,
    split_episodes,
)
from .config import Settings, settings as default_settings
from .database import open_fts, open_readonly, quote_identifier
from .parsing import (
    decode_wcdb_value,
    extract_protobuf_strings,
    is_valid_keyword_token,
    message_quality_score,
    normalize_human_text,
    parse_message_content,
    split_local_type,
)
from .realtime import RealtimeStore


CACHE_SCHEMA_VERSION = 8
CACHE_MESSAGE_COLUMNS = (
    "message_key",
    "session_username",
    "session_id",
    "session_name",
    "local_id",
    "server_id",
    "local_type",
    "base_type",
    "app_subtype",
    "app_type",
    "sort_seq",
    "sender_id",
    "sender_username",
    "sender_name",
    "is_self",
    "create_time",
    "local_date",
    "local_hour",
    "category",
    "decoded",
    "decode_error",
    "content",
    "summary",
    "title",
    "description",
    "url",
    "file_name",
    "file_size",
    "reference_server_id",
    "reference_from_username",
    "reference_chat_username",
    "reference_display_name",
    "reference_content",
    "quote_count",
    "quality_score",
)
CACHE_SOURCE_DATABASES = (
    "contact/contact.db",
    "contact/contact_fts.db",
    "favorite/favorite_fts.db",
    "hardlink/hardlink.db",
    "head_image/head_image.db",
    "message/media_0.db",
    "message/message_0.db",
    "message/message_fts.db",
    "message/message_resource.db",
    "session/session.db",
)
SYSTEM_USERNAMES = {
    "brandsessionholder",
    "fmessage",
    "filehelper",
    "floatbottle",
    "medianote",
    "newsapp",
    "notifymessage",
    "notification_messages",
    "officialaccounts",
    "qmessage",
    "qqmail",
    "tmessage",
    "weixin",
}
WORD_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_.+-]{1,31}|[\u4e00-\u9fff]{2,8}")
STOP_WORDS = {
    "这个",
    "那个",
    "可以",
    "就是",
    "还是",
    "没有",
    "什么",
    "怎么",
    "一个",
    "我们",
    "你们",
    "他们",
    "然后",
    "已经",
    "不是",
    "好的",
    "知道",
    "因为",
    "所以",
    "但是",
    "如果",
    "一下",
    "这样",
    "现在",
    "今天",
    "明天",
    "哈哈",
    "嗯嗯",
    "http",
    "https",
    "amp",
    "apos",
    "appattach",
    "appid",
    "appmsg",
    "body",
    "cdata",
    "cdn",
    "cdnurl",
    "chatusr",
    "content",
    "dataurl",
    "des",
    "description",
    "displayname",
    "div",
    "encoding",
    "fromusr",
    "gt",
    "html",
    "lt",
    "msg",
    "msgsource",
    "nbsp",
    "quot",
    "refermsg",
    "sdkver",
    "source",
    "span",
    "svrid",
    "thumburl",
    "title",
    "type",
    "url",
    "utf",
    "version",
    "xml",
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _display_name(contact: dict[str, Any] | None, username: str) -> str:
    if contact:
        return (
            contact.get("remark")
            or contact.get("nick_name")
            or contact.get("alias")
            or username
        )
    return username


def _safe_timestamp(value: Any) -> int:
    try:
        result = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return result if 0 < result < 32_503_680_000 else 0


def _is_system(username: str, account: str) -> bool:
    return username == account or username in SYSTEM_USERNAMES or username.startswith("sys_")


def _chunks(values: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _csv_cell(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.lstrip()
    return "'" + value if stripped.startswith(("=", "+", "-", "@")) else value



class WeChatRepository:
    def __init__(self, config: Settings = default_settings) -> None:
        self.settings = config
        self._lock = threading.RLock()
        self._active_cache_path: Path | None = None
        self._cache_error: str | None = None
        self._report_error: str | None = None
        self._database_root = config.database_root
        try:
            self._report = self._load_report()
            database_root = self._database_root_from_report(self._report)
            account_root = self._account_root_from_report(self._report)
        except (json.JSONDecodeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            self._report = {}
            self._report_error = f"Unable to read decryption report: {exc}"
            database_root = config.database_root
            account_root = None
        self._report_signature = self._report_file_signature()
        self.account = str(self._report.get("account") or "")
        self._database_root = database_root
        self.account_root = account_root
        self.timezone = ZoneInfo(config.timezone)
        self._bind_realtime()

    def _bind_realtime(self) -> None:
        self.realtime = RealtimeStore(
            self.settings.realtime_path,
            account=self.account,
            snapshot=self.snapshot_time,
            database_root=self.database_root,
            timezone=self.timezone,
        )

    @property
    def snapshot_time(self) -> str | None:
        return self._report.get("created_utc")

    @property
    def missing_sources(self) -> list[str]:
        missing = []
        if not self.settings.report_path.is_file():
            missing.append(str(self.settings.report_path))
        for relative in CACHE_SOURCE_DATABASES:
            path = self.database(relative)
            if not path.is_file():
                missing.append(str(path))
        return missing

    @property
    def configured(self) -> bool:
        return bool(
            self.account
            and not self.snapshot_publication_in_progress
            and self._report_file_signature() == self._report_signature
            and not self.missing_sources
        )

    @property
    def snapshot_publication_marker(self) -> Path:
        root = self._database_root
        return root.with_name(f".{root.name}.publishing")

    @property
    def snapshot_publication_in_progress(self) -> bool:
        return self.snapshot_publication_marker.is_file()

    @property
    def cache_error(self) -> str | None:
        return self._cache_error

    @property
    def active_cache_path(self) -> Path | None:
        return self._active_cache_path

    @property
    def database_root(self) -> Path:
        return self._database_root

    @staticmethod
    def _validated_report(report: Any) -> dict[str, Any]:
        if not isinstance(report, dict):
            raise RuntimeError("decryption report must be a JSON object")

        schema_version = report.get("schema_version")
        versioned = schema_version is not None
        if versioned and (
            not isinstance(schema_version, int)
            or isinstance(schema_version, bool)
            or schema_version != 1
        ):
            raise RuntimeError("decryption report schema_version must be 1")
        if versioned:
            allowed = {
                "schema_version",
                "created_utc",
                "account",
                "source_root",
                "key_manifest",
                "output_root",
                "decrypted_database_count",
                "total_bytes",
                "unresolved",
                "files",
            }
            unknown = sorted(set(report) - allowed)
            if unknown:
                raise RuntimeError(
                    f"decryption report contains unknown field: {unknown[0]}"
                )
            missing = sorted(allowed - set(report))
            if missing:
                raise RuntimeError(
                    f"decryption report is missing required field: {missing[0]}"
                )

        account = report.get("account")
        if not isinstance(account, str) or not account.strip():
            raise RuntimeError("decryption report account must be a non-empty string")
        created_utc = report.get("created_utc")
        if created_utc is not None:
            if not isinstance(created_utc, str) or not created_utc.strip():
                raise RuntimeError(
                    "decryption report created_utc must be a non-empty string"
                )
            if versioned:
                try:
                    created_at = datetime.fromisoformat(created_utc)
                except ValueError as exc:
                    raise RuntimeError(
                        "decryption report created_utc must be an ISO-8601 timestamp"
                    ) from exc
                if created_at.tzinfo is None:
                    raise RuntimeError(
                        "decryption report created_utc must include a timezone"
                    )
        for key in ("source_root", "output_root", "key_manifest"):
            value = report.get(key)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise RuntimeError(f"decryption report {key} must be a path string")
            if versioned and value is not None and not Path(value).expanduser().is_absolute():
                raise RuntimeError(f"decryption report {key} must be an absolute path")

        def normalize_relative_path(value: Any, label: str) -> str:
            if not isinstance(value, str) or not value.strip():
                raise RuntimeError(f"decryption report {label} must be a non-empty string")
            normalized = value.replace("\\", "/")
            segments = normalized.split("/")
            if (
                PurePosixPath(normalized).is_absolute()
                or any(segment in {"", ".", ".."} for segment in segments)
                or any(":" in segment for segment in segments)
            ):
                raise RuntimeError(f"decryption report {label} must be a safe relative path")
            return PurePosixPath(*segments).as_posix()

        files = report.get("files", [])
        if not isinstance(files, list):
            raise RuntimeError("decryption report files must be a list")
        normalized_files: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        for index, item in enumerate(files):
            if not isinstance(item, dict):
                raise RuntimeError(f"decryption report files[{index}] must be an object")
            if versioned:
                allowed_file_fields = {
                    "path",
                    "bytes",
                    "pages",
                    "sha256",
                    "sqlite_header",
                    "reserve_size",
                    "quick_check",
                    "integrity_check",
                    "page_count",
                    "page_size",
                    "freelist_count",
                    "schema_objects",
                    "schema_preview",
                    "source_wal",
                    "fts_extension_loaded",
                }
                unknown = sorted(set(item) - allowed_file_fields)
                if unknown:
                    raise RuntimeError(
                        f"decryption report files[{index}] contains unknown field: {unknown[0]}"
                    )
                missing = sorted(allowed_file_fields - set(item))
                if missing:
                    raise RuntimeError(
                        f"decryption report files[{index}] is missing required field: {missing[0]}"
                    )
            relative = normalize_relative_path(item.get("path"), f"files[{index}].path")
            path_key = relative.casefold()
            if path_key in seen_paths:
                raise RuntimeError(f"decryption report contains duplicate path: {relative}")
            sha256 = item.get("sha256")
            if sha256 is not None and (
                not isinstance(sha256, str)
                or re.fullmatch(r"[0-9a-fA-F]{64}", sha256) is None
            ):
                raise RuntimeError(
                    f"decryption report files[{index}].sha256 must be 64 hex characters"
                )
            for key in (
                "bytes",
                "pages",
                "reserve_size",
                "page_count",
                "page_size",
                "freelist_count",
                "schema_objects",
            ):
                value = item.get(key)
                if value is not None and (
                    not isinstance(value, int) or isinstance(value, bool) or value < 0
                ):
                    raise RuntimeError(
                        f"decryption report files[{index}].{key} must be a non-negative integer"
                    )
            for key in ("quick_check", "integrity_check"):
                value = item.get(key)
                if value is not None and (not isinstance(value, str) or not value):
                    raise RuntimeError(
                        f"decryption report files[{index}].{key} must be a non-empty string"
                    )
            sqlite_header = item.get("sqlite_header")
            if sqlite_header is not None and sqlite_header is not True:
                raise RuntimeError(
                    f"decryption report files[{index}].sqlite_header must be true"
                )
            fts_loaded = item.get("fts_extension_loaded")
            if fts_loaded is not None and not isinstance(fts_loaded, bool):
                raise RuntimeError(
                    f"decryption report files[{index}].fts_extension_loaded must be a boolean"
                )
            schema_preview = item.get("schema_preview")
            if schema_preview is not None and (
                not isinstance(schema_preview, list)
                or any(
                    not isinstance(row, list)
                    or len(row) != 2
                    or any(not isinstance(value, str) for value in row)
                    for row in schema_preview
                )
            ):
                raise RuntimeError(
                    f"decryption report files[{index}].schema_preview must contain string pairs"
                )
            source_wal = item.get("source_wal")
            if source_wal is not None and not isinstance(source_wal, dict):
                raise RuntimeError(
                    f"decryption report files[{index}].source_wal must be an object"
                )
            seen_paths.add(path_key)
            normalized_files.append({**item, "path": relative})

        unresolved = report.get("unresolved", [])
        if not isinstance(unresolved, list):
            raise RuntimeError("decryption report unresolved must be a list")
        normalized_unresolved: list[dict[str, str]] = []
        for index, item in enumerate(unresolved):
            if isinstance(item, str) and not versioned:
                legacy_path = item.strip().replace("\\", "/")
                if not legacy_path:
                    raise RuntimeError(
                        f"decryption report unresolved[{index}] must be a non-empty string"
                    )
                normalized_unresolved.append(
                    {
                        "path": legacy_path,
                        "error": "legacy unresolved entry",
                    }
                )
                continue
            if not isinstance(item, dict):
                raise RuntimeError(
                    f"decryption report unresolved[{index}] must be an object"
                )
            if versioned and set(item) != {"path", "error"}:
                raise RuntimeError(
                    f"decryption report unresolved[{index}] must contain path and error"
                )
            path = normalize_relative_path(item.get("path"), f"unresolved[{index}].path")
            error = item.get("error")
            if not isinstance(error, str) or not error.strip():
                raise RuntimeError(
                    f"decryption report unresolved[{index}].error must be a non-empty string"
                )
            normalized_unresolved.append({"path": path, "error": error.strip()})

        count = report.get("decrypted_database_count")
        if count is not None and (
            not isinstance(count, int) or isinstance(count, bool) or count < 0
        ):
            raise RuntimeError(
                "decryption report decrypted_database_count must be a non-negative integer"
            )
        if count is not None and count != len(normalized_files):
            raise RuntimeError(
                "decryption report decrypted_database_count must match files length"
            )
        total_bytes = report.get("total_bytes")
        if total_bytes is not None and (
            not isinstance(total_bytes, int)
            or isinstance(total_bytes, bool)
            or total_bytes < 0
        ):
            raise RuntimeError("decryption report total_bytes must be a non-negative integer")
        if total_bytes is not None and all("bytes" in item for item in normalized_files):
            if total_bytes != sum(int(item["bytes"]) for item in normalized_files):
                raise RuntimeError(
                    "decryption report total_bytes must match the sum of file bytes"
                )
        return {
            **report,
            "account": account.strip(),
            "files": normalized_files,
            "unresolved": normalized_unresolved,
        }

    def _load_report(self) -> dict[str, Any]:
        if not self.settings.report_path.is_file():
            return {}
        with self.settings.report_path.open("r", encoding="utf-8") as stream:
            report = json.load(stream)
        return self._validated_report(report)

    def _report_file_signature(self) -> tuple[int, int] | None:
        try:
            stat = self.settings.report_path.stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size

    @staticmethod
    def _account_root_from_report(report: dict[str, Any]) -> Path | None:
        source_root = report.get("source_root")
        if source_root is None:
            return None
        if not isinstance(source_root, str) or not source_root.strip():
            raise RuntimeError("decryption report source_root must be a path string")
        source_path = Path(source_root).expanduser().resolve()
        account = str(report.get("account") or "")
        account_directory = source_path.parent.name
        if source_path.name.casefold() != "db_storage" or not (
            account_directory == account or account_directory.startswith(f"{account}_")
        ):
            return None
        return source_path.parent

    def _database_root_from_report(self, report: dict[str, Any]) -> Path:
        output_root = report.get("output_root")
        if output_root is None:
            return self.settings.database_root
        path = Path(output_root).expanduser().resolve()
        configured_parent = self.settings.database_root.resolve().parent
        if path.parent != configured_parent:
            raise RuntimeError(
                "decryption report output_root must be a sibling of the configured database root"
            )
        configured_name = self.settings.database_root.name
        if path.name != configured_name and not path.name.startswith(f"{configured_name}."):
            raise RuntimeError(
                "decryption report output_root must use the configured database root name"
            )
        return path

    def reload_configuration_if_available(self) -> bool:
        if self.snapshot_publication_in_progress:
            self._report_error = "A decrypted snapshot is currently being published."
            return False
        signature = self._report_file_signature()
        if signature == self._report_signature and (self.account or signature is None):
            return self.configured
        with self._lock:
            signature = self._report_file_signature()
            if signature == self._report_signature:
                return self.configured
            if signature is None:
                self._report = {}
                self._report_signature = None
                self._report_error = "Decryption report is missing."
                self._database_root = self.settings.database_root
                self.account_root = None
                self.account = ""
                return self.configured
            try:
                report = self._load_report()
                account = str(report.get("account") or "")
                database_root = self._database_root_from_report(report)
                account_root = self._account_root_from_report(report)
            except (json.JSONDecodeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                self._report = {}
                self._report_signature = signature
                self._report_error = f"Unable to read decryption report: {exc}"
                self._database_root = self.settings.database_root
                self.account_root = None
                self.account = ""
                return False

            # Publish the account last so readers never observe a partially updated setup.
            self._report = report
            self._report_signature = signature
            self._report_error = None
            self._cache_error = None
            self._database_root = database_root
            self.account_root = account_root
            self.account = account
            self._active_cache_path = None
            self._bind_realtime()
            return self.configured

    def database(self, relative: str) -> Path:
        return self._database_root / Path(relative)

    def _message_databases(self) -> list[Path]:
        return sorted(
            (path for path in self.database('message').glob('message_*.db')
             if re.fullmatch(r'message_\d+\.db', path.name)),
            key=lambda path: int(path.stem.split('_')[-1]),
        )

    def _build_progress(self, **values) -> None:
        callback = getattr(self, 'build_progress', None)
        if callback:
            callback(**values)

    def _cache_sql_progress(self) -> int:
        self._build_progress()
        return 0

    def _source_fingerprint(self) -> str:
        report_files = {
            str(item.get("path")): item for item in self._report.get("files", [])
        }
        values: list[dict[str, Any]] = []
        source_names = set(CACHE_SOURCE_DATABASES)
        source_names.update(path.relative_to(self.database_root).as_posix() for path in self._message_databases())
        for relative in sorted(source_names):
            path = self.database(relative)
            try:
                stat = path.stat()
            except OSError:
                stat = None
            values.append(
                {
                    "path": str(path.resolve()),
                    "size": stat.st_size if stat is not None else None,
                    "mtime_ns": stat.st_mtime_ns if stat is not None else None,
                    "sha256": report_files.get(relative, {}).get("sha256"),
                }
            )
        payload = {
            "schema": CACHE_SCHEMA_VERSION,
            "account": self.account,
            "snapshot": self.snapshot_time,
            "sources": values,
        }
        return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()

    def _cache_candidates(self, fingerprint: str) -> list[Path]:
        configured = self.settings.cache_path
        suffix = configured.suffix or ".sqlite"
        stem = configured.name[: -len(suffix)] if configured.suffix else configured.name
        pattern = f"{stem}.{fingerprint}.*{suffix}"
        try:
            candidates = list(configured.parent.glob(pattern))
        except OSError:
            return []
        return sorted(
            candidates,
            key=lambda path: path.stat().st_mtime_ns if path.is_file() else 0,
            reverse=True,
        )


    def _new_cache_path(self, fingerprint: str) -> Path:
        configured = self.settings.cache_path
        if not configured.exists():
            return configured
        suffix = configured.suffix or ".sqlite"
        stem = configured.name[: -len(suffix)] if configured.suffix else configured.name
        return configured.with_name(
            f"{stem}.{fingerprint}.{uuid.uuid4().hex}{suffix}"
        )


    def ensure_cache(self, force: bool = False) -> Path:
        with self._lock:
            if self.snapshot_publication_in_progress:
                raise RuntimeError("decrypted snapshot publication is in progress")
            fingerprint = self._source_fingerprint()
            if not force:
                candidates = []
                if self._active_cache_path is not None:
                    candidates.append(self._active_cache_path)
                if self.settings.cache_path.is_file():
                    candidates.append(self.settings.cache_path)
                candidates.extend(self._cache_candidates(fingerprint))
                seen: set[Path] = set()
                for candidate in candidates:
                    if candidate in seen:
                        continue
                    seen.add(candidate)
                    if self._cache_matches(candidate, fingerprint):
                        self._active_cache_path = candidate
                        self._cache_error = None
                        return candidate
            self.settings.cache_path.parent.mkdir(parents=True, exist_ok=True)
            target = self._new_cache_path(fingerprint)
            temporary = target.with_name(
                f".{target.name}.{os.getpid()}.{threading.get_ident()}.building"
            )
            temporary.unlink(missing_ok=True)
            try:
                self._build_cache(temporary, fingerprint)
                os.replace(temporary, target)
                self._active_cache_path = target
                self._cache_error = None
                return target
            except Exception as exc:
                self._cache_error = f"Unable to build analytics cache: {exc}"
                raise
            finally:
                temporary.unlink(missing_ok=True)

    def _cache_matches(self, path: Path, fingerprint: str) -> bool:
        if not path.is_file():
            return False
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True
            )
            row = connection.execute(
                "SELECT value FROM meta WHERE key='source_fingerprint'"
            ).fetchone()
            version = connection.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
            return bool(
                row
                and row[0] == fingerprint
                and version
                and int(version[0]) == CACHE_SCHEMA_VERSION
            )
        except (sqlite3.DatabaseError, OSError, ValueError):
            return False
        finally:
            if connection is not None:
                connection.close()

    def _cache_connection(self) -> sqlite3.Connection:
        path = self.ensure_cache()
        connection = sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro", uri=True
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    @contextmanager
    def cache(self) -> Iterator[sqlite3.Connection]:
        connection = self._cache_connection()
        try:
            yield connection
        finally:
            connection.close()

    def _create_cache_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            PRAGMA journal_mode=DELETE;
            PRAGMA synchronous=OFF;
            PRAGMA temp_store=MEMORY;
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE contacts (
                id INTEGER,
                username TEXT PRIMARY KEY,
                alias TEXT,
                remark TEXT,
                nick_name TEXT,
                display_name TEXT NOT NULL,
                local_type INTEGER,
                verify_flag INTEGER,
                delete_flag INTEGER,
                big_head_url TEXT,
                small_head_url TEXT,
                is_group INTEGER NOT NULL,
                is_system INTEGER NOT NULL
            );
            CREATE INDEX contacts_id ON contacts(id);
            CREATE INDEX contacts_display_name ON contacts(display_name);
            CREATE TABLE messages (
                message_key TEXT PRIMARY KEY,
                session_username TEXT NOT NULL,
                session_id INTEGER,
                session_name TEXT NOT NULL,
                local_id INTEGER NOT NULL,
                server_id INTEGER,
                local_type INTEGER NOT NULL,
                base_type INTEGER NOT NULL,
                app_subtype INTEGER NOT NULL,
                app_type INTEGER,
                sort_seq INTEGER,
                sender_id INTEGER,
                sender_username TEXT,
                sender_name TEXT,
                is_self INTEGER NOT NULL,
                create_time INTEGER NOT NULL,
                local_date TEXT NOT NULL,
                local_hour INTEGER NOT NULL,
                category TEXT NOT NULL,
                decoded INTEGER NOT NULL,
                decode_error TEXT,
                content TEXT NOT NULL,
                summary TEXT NOT NULL,
                title TEXT,
                description TEXT,
                url TEXT,
                file_name TEXT,
                file_size INTEGER,
                reference_server_id INTEGER,
                reference_from_username TEXT,
                reference_chat_username TEXT,
                reference_display_name TEXT,
                reference_content TEXT,
                quote_count INTEGER NOT NULL DEFAULT 0,
                quality_score INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX messages_session_time ON messages(session_username, create_time, sort_seq);
            CREATE INDEX messages_time ON messages(create_time);
            CREATE INDEX messages_date ON messages(local_date);
            CREATE INDEX messages_server ON messages(server_id);
            CREATE INDEX messages_reference ON messages(reference_server_id);
            CREATE INDEX messages_type ON messages(category);
            CREATE INDEX messages_fts_identity ON messages(session_id, local_id);
            CREATE TABLE sessions (
                username TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                is_group INTEGER NOT NULL,
                is_system INTEGER NOT NULL,
                message_count INTEGER NOT NULL,
                active_days INTEGER NOT NULL,
                first_time INTEGER NOT NULL,
                last_time INTEGER NOT NULL,
                sent_count INTEGER NOT NULL,
                received_count INTEGER NOT NULL,
                episode_count INTEGER NOT NULL,
                turn_count INTEGER NOT NULL,
                response_median_seconds REAL,
                bidirectional_ratio REAL NOT NULL,
                relationship_strength REAL NOT NULL,
                categories TEXT NOT NULL,
                latest_summary TEXT NOT NULL
            );
            CREATE INDEX sessions_rank ON sessions(message_count DESC);
            CREATE TABLE episodes (
                episode_id TEXT PRIMARY KEY,
                session_username TEXT NOT NULL,
                session_name TEXT NOT NULL,
                local_date TEXT NOT NULL,
                start_time INTEGER NOT NULL,
                end_time INTEGER NOT NULL,
                message_count INTEGER NOT NULL,
                turn_count INTEGER NOT NULL,
                summary TEXT NOT NULL,
                categories TEXT NOT NULL
            );
            CREATE INDEX episodes_time ON episodes(start_time DESC);
            CREATE INDEX episodes_session ON episodes(session_username, start_time DESC);
            CREATE TABLE daily (
                local_date TEXT PRIMARY KEY,
                message_count INTEGER NOT NULL,
                sent_count INTEGER NOT NULL,
                received_count INTEGER NOT NULL,
                episode_count INTEGER NOT NULL,
                turn_count INTEGER NOT NULL,
                quoted_count INTEGER NOT NULL,
                active_sessions INTEGER NOT NULL,
                quality_count INTEGER NOT NULL
            );
            CREATE TABLE hourly (hour INTEGER PRIMARY KEY, message_count INTEGER NOT NULL);
            CREATE TABLE resources (
                resource_key TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                rowid INTEGER NOT NULL,
                md5 TEXT,
                file_name TEXT NOT NULL,
                file_size INTEGER,
                modify_time INTEGER,
                file_path TEXT,
                available INTEGER NOT NULL,
                browser_supported INTEGER NOT NULL,
                mime_type TEXT
            );
            CREATE UNIQUE INDEX resources_kind_rowid ON resources(kind, rowid);
            CREATE INDEX resources_time ON resources(modify_time DESC);
            CREATE TABLE message_resources (
                resource_id INTEGER PRIMARY KEY,
                message_id INTEGER NOT NULL,
                message_key TEXT,
                chat_id INTEGER,
                message_local_id INTEGER,
                message_server_id INTEGER,
                detail_type INTEGER,
                size INTEGER,
                status INTEGER,
                data_index TEXT,
                create_time INTEGER,
                metadata_text TEXT NOT NULL
            );
            CREATE INDEX message_resources_message ON message_resources(message_key);
            CREATE INDEX message_resources_identity ON message_resources(chat_id,message_local_id);
            """
        )

    def _load_contacts(self) -> tuple[dict[str, dict[str, Any]], list[tuple[Any, ...]]]:
        contacts: dict[str, dict[str, Any]] = {}
        inserts: list[tuple[Any, ...]] = []
        with open_readonly(self.database("contact/contact.db")) as source:
            cursor = source.execute(
                """
                SELECT id, username, alias, remark, nick_name, local_type,
                       verify_flag, delete_flag, big_head_url, small_head_url
                FROM contact
                """
            )
            for row in cursor:
                item = dict(row)
                username = str(item.get("username") or "")
                if not username:
                    continue
                item["display_name"] = _display_name(item, username)
                item["is_group"] = username.endswith("@chatroom")
                item["is_system"] = _is_system(username, self.account)
                contacts[username] = item
                inserts.append(
                    (
                        item.get("id"),
                        username,
                        item.get("alias"),
                        item.get("remark"),
                        item.get("nick_name"),
                        item["display_name"],
                        item.get("local_type"),
                        item.get("verify_flag"),
                        item.get("delete_flag"),
                        item.get("big_head_url"),
                        item.get("small_head_url"),
                        int(item["is_group"]),
                        int(item["is_system"]),
                    )
                )
        return contacts, inserts

    def _build_cache(self, path: Path, fingerprint: str) -> None:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        if getattr(self, 'build_progress', None):
            connection.set_progress_handler(self._cache_sql_progress, 10000)
        try:
            self._build_progress(message='正在读取联系人并解析全部消息分片。')
            self._create_cache_schema(connection)
            contacts, contact_rows = self._load_contacts()
            connection.executemany(
                "INSERT INTO contacts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", contact_rows
            )
            build = self._scan_messages(connection, contacts)
            realtime_count = self._append_realtime_messages(
                connection, build, contacts
            )
            connection.execute(
                """
                UPDATE messages AS original
                SET quote_count = (
                    SELECT count(*) FROM messages AS quoted
                    WHERE quoted.reference_server_id = original.server_id
                      AND original.server_id IS NOT NULL
                      AND original.server_id != 0
                )
                """
            )
            self._build_progress(message='正在关联资源并计算会话统计。')
            indexed_resources, linked_resources = self._build_message_resources(connection)
            self._build_aggregates(connection, build, contacts)
            self._build_progress(message='正在整理媒体资源索引。')
            resource_count = self._build_resources(connection)
            metadata = self._source_counts()
            metadata.update(
                {
                    "schema_version": str(CACHE_SCHEMA_VERSION),
                    "source_fingerprint": fingerprint,
                    "built_at": datetime.now(self.timezone).isoformat(),
                    "account": self.account,
                    "message_count": str(build["message_count"]),
                    "message_table_count": str(build["message_table_count"]),
                    "decoded_count": str(build["decoded_count"]),
                    "decode_error_count": str(build["decode_error_count"]),
                    "resource_count": str(resource_count),
                    "message_resource_detail_count": str(indexed_resources),
                    "linked_message_resource_count": str(linked_resources),
                    "realtime_message_count": str(realtime_count),
                }
            )
            connection.executemany(
                "INSERT INTO meta(key,value) VALUES (?,?)", metadata.items()
            )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _message_values(message: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(message.get(column) for column in CACHE_MESSAGE_COLUMNS)

    def _resolve_realtime_names(
        self,
        message: dict[str, Any],
        contacts: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        resolved = dict(message)
        session_username = str(resolved.get("session_username") or "")
        sender_username = str(resolved.get("sender_username") or "")
        resolved["session_name"] = _display_name(
            contacts.get(session_username), session_username
        )
        resolved["sender_name"] = _display_name(
            contacts.get(sender_username),
            sender_username or resolved["session_name"],
        )
        return resolved

    def _append_realtime_messages(
        self,
        connection: sqlite3.Connection,
        build: dict[str, Any],
        contacts: dict[str, dict[str, Any]],
    ) -> int:
        inserted = 0
        insert_sql = "INSERT OR IGNORE INTO messages VALUES (" + ",".join(
            "?" for _ in CACHE_MESSAGE_COLUMNS
        ) + ")"
        for _, raw_message in self.realtime.normalized_messages():
            message = self._resolve_realtime_names(raw_message, contacts)
            cursor = connection.execute(insert_sql, self._message_values(message))
            if cursor.rowcount != 1:
                continue
            inserted += 1
            build["message_count"] += 1
            build["decoded_count" if message.get("decoded") else "decode_error_count"] += 1
            build["session_messages"][message["session_username"]].append(
                {
                    "message_key": message["message_key"],
                    "time": int(message["create_time"]),
                    "sort_seq": int(message.get("sort_seq") or 0),
                    "sender": message.get("sender_username")
                    or str(message.get("sender_id") or ""),
                    "is_self": bool(message.get("is_self")),
                    "date": message["local_date"],
                    "hour": int(message["local_hour"]),
                    "category": message["category"],
                    "summary": message.get("summary") or "",
                    "quality": int(message.get("quality_score") or 0),
                }
            )
        return inserted

    def _scan_messages(
        self, connection: sqlite3.Connection, contacts: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        combined: dict[str, Any] = {
            'session_messages': defaultdict(list), 'message_count': 0,
            'message_table_count': 0, 'decoded_count': 0, 'decode_error_count': 0,
        }
        seen: set[tuple[str, int]] = set()
        for database in self._message_databases():
            self._build_progress(file=f'message/{database.name}', message_count=combined['message_count'])
            part = self._scan_message_shard(connection, contacts, database, seen, combined['message_count'])
            for name, messages in part.pop('session_messages').items():
                combined['session_messages'][name].extend(messages)
            for key, value in part.items():
                combined[key] += value
        for messages in combined['session_messages'].values():
            messages.sort(key=lambda item: (item['time'], item['sort_seq'], item['message_key']))
        self._build_progress(file='', message_count=combined['message_count'])
        return combined

    def _scan_message_shard(
        self, connection: sqlite3.Connection, contacts: dict[str, dict[str, Any]],
        message_db: Path, seen: set[tuple[str, int]], count_before: int,
    ) -> dict[str, Any]:
        session_messages: dict[str, list[dict[str, Any]]] = defaultdict(list)
        decoded_count = 0
        decode_error_count = 0
        message_count = 0
        insert_sql = "INSERT INTO messages VALUES (" + ",".join("?" for _ in range(35)) + ")"
        pending: list[tuple[Any, ...]] = []
        with open_readonly(message_db) as source:
            name_rows = source.execute(
                "SELECT rowid, user_name, is_session FROM Name2Id"
            ).fetchall()
            names_by_id = {int(row["rowid"]): str(row["user_name"]) for row in name_rows}
            ids_by_name = {value: key for key, value in names_by_id.items()}
            account_id = ids_by_name.get(self.account)
            existing_tables = {
                row[0]
                for row in source.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"
                )
            }
            session_rows = [row for row in name_rows if int(row["is_session"] or 0)]
            for session_row in session_rows:
                session_username = str(session_row["user_name"])
                table = "Msg_" + hashlib.md5(
                    session_username.encode("utf-8")
                ).hexdigest()
                if table not in existing_tables:
                    continue
                is_group = session_username.endswith("@chatroom")
                session_contact = contacts.get(session_username)
                session_name = _display_name(session_contact, session_username)
                query = (
                    "SELECT local_id,server_id,local_type,sort_seq,real_sender_id,"
                    "create_time,CAST(source AS BLOB) source,"
                    "CAST(message_content AS BLOB) message_content,"
                    "CAST(compress_content AS BLOB) compress_content,packed_info_data "
                    f"FROM {quote_identifier(table)} ORDER BY create_time,sort_seq,local_id"
                )
                for row in source.execute(query):
                    server_id = int(row['server_id'] or 0)
                    identity = (session_username, server_id)
                    if server_id and identity in seen:
                        continue
                    if server_id:
                        seen.add(identity)
                    local_type = int(row["local_type"] or 0)
                    base_type, app_subtype = split_local_type(local_type)
                    primary = row["message_content"]
                    if primary in (None, "", b"") and row["compress_content"] not in (
                        None,
                        "",
                        b"",
                    ):
                        primary = row["compress_content"]
                    decoded = decode_wcdb_value(primary)
                    parsed = parse_message_content(
                        decoded.text, base_type, app_subtype, is_group
                    )
                    sender_id = int(row["real_sender_id"] or 0)
                    sender_username = parsed.sender_prefix or names_by_id.get(sender_id, "")
                    if not sender_username and not is_group:
                        sender_username = (
                            self.account if sender_id == account_id else session_username
                        )
                    sender_name = _display_name(
                        contacts.get(sender_username), sender_username or session_name
                    )
                    is_self = bool(
                        sender_username == self.account
                        or (account_id is not None and sender_id == account_id)
                    )
                    timestamp = _safe_timestamp(row["create_time"])
                    local_dt = datetime.fromtimestamp(timestamp, self.timezone)
                    server_id = int(row["server_id"] or 0)
                    local_id = int(row["local_id"] or 0)
                    key_source = (f'{session_username}\0server\0{server_id}' if server_id else
                                  f'{session_username}\0{message_db.name}\0{local_id}')
                    message_key = hashlib.sha1(key_source.encode('utf-8')).hexdigest()
                    quality_score = message_quality_score(
                        parsed.content, parsed.category
                    )
                    values = (
                        message_key,
                        session_username,
                        int(session_row["rowid"]),
                        session_name,
                        local_id,
                        server_id,
                        local_type,
                        base_type,
                        app_subtype,
                        parsed.app_type,
                        int(row["sort_seq"] or 0),
                        sender_id,
                        sender_username,
                        sender_name,
                        int(is_self),
                        timestamp,
                        local_dt.date().isoformat(),
                        local_dt.hour,
                        parsed.category,
                        int(decoded.decoded),
                        decoded.error,
                        parsed.content,
                        parsed.summary,
                        parsed.title,
                        parsed.description,
                        parsed.url,
                        parsed.file_name,
                        parsed.file_size,
                        parsed.reference_server_id,
                        parsed.reference_from_username,
                        parsed.reference_chat_username,
                        parsed.reference_display_name,
                        parsed.reference_content,
                        0,
                        quality_score,
                    )
                    pending.append(values)
                    session_messages[session_username].append(
                        {
                            "message_key": message_key,
                            "time": timestamp,
                            "sort_seq": int(row["sort_seq"] or 0),
                            "sender": sender_username or str(sender_id),
                            "is_self": is_self,
                            "date": local_dt.date().isoformat(),
                            "hour": local_dt.hour,
                            "category": parsed.category,
                            "summary": parsed.summary,
                            "quality": quality_score,
                        }
                    )
                    message_count += 1
                    if decoded.decoded:
                        decoded_count += 1
                    else:
                        decode_error_count += 1
                    if len(pending) >= 1000:
                        connection.executemany(insert_sql, pending)
                        pending.clear()
                        self._build_progress(message_count=count_before + message_count)
            if pending:
                connection.executemany(insert_sql, pending)
        return {
            "session_messages": session_messages,
            "message_count": message_count,
            "message_table_count": len(existing_tables),
            "decoded_count": decoded_count,
            "decode_error_count": decode_error_count,
        }

    def _build_message_resources(self, connection: sqlite3.Connection) -> tuple[int, int]:
        # Local IDs can repeat across shards. Only unambiguous identities may
        # serve as a fallback when the resource has no matching server ID.
        message_keys = {
            (int(row["session_id"]), int(row["local_id"])): row["message_key"]
            for row in connection.execute(
                "SELECT session_id,local_id,min(message_key) message_key FROM messages "
                "WHERE session_id IS NOT NULL GROUP BY session_id,local_id HAVING count(*)=1"
            )
        }
        message_keys_by_server = {
            int(row["server_id"]): row["message_key"]
            for row in connection.execute(
                "SELECT server_id,message_key FROM messages WHERE server_id IS NOT NULL AND server_id!=0"
            )
        }
        rows: list[tuple[Any, ...]] = []
        linked = 0
        with open_readonly(self.database("message/message_resource.db")) as source:
            info = {
                int(row["message_id"]): dict(row)
                for row in source.execute(
                    """
                    SELECT message_id,chat_id,message_local_id,message_svr_id,
                           message_create_time,packed_info
                    FROM MessageResourceInfo
                    """
                )
            }
            for detail in source.execute(
                """
                SELECT resource_id,message_id,type,size,status,data_index,
                       create_time,packed_info FROM MessageResourceDetail
                """
            ):
                parent = info.get(int(detail["message_id"]), {})
                identity = (
                    int(parent.get("chat_id") or 0),
                    int(parent.get("message_local_id") or 0),
                )
                message_key = message_keys_by_server.get(
                    int(parent.get("message_svr_id") or 0)
                ) or message_keys.get(identity)
                if message_key:
                    linked += 1
                strings = extract_protobuf_strings(parent.get("packed_info"))
                strings.extend(extract_protobuf_strings(detail["packed_info"]))
                rows.append(
                    (
                        int(detail["resource_id"]),
                        int(detail["message_id"]),
                        message_key,
                        identity[0],
                        identity[1],
                        int(parent.get("message_svr_id") or 0),
                        int(detail["type"] or 0),
                        int(detail["size"] or 0),
                        int(detail["status"] or 0),
                        str(detail["data_index"] or ""),
                        int(detail["create_time"] or parent.get("message_create_time") or 0),
                        _json(list(dict.fromkeys(strings))),
                    )
                )
        connection.executemany(
            "INSERT INTO message_resources VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows
        )
        return len(rows), linked

    def _build_aggregates(
        self,
        connection: sqlite3.Connection,
        build: dict[str, Any],
        contacts: dict[str, dict[str, Any]],
    ) -> None:
        session_messages: dict[str, list[dict[str, Any]]] = build["session_messages"]
        raw_stats: list[dict[str, Any]] = []
        episode_rows: list[tuple[Any, ...]] = []
        daily: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "message_count": 0,
                "sent_count": 0,
                "received_count": 0,
                "episode_count": 0,
                "turn_count": 0,
                "active_sessions": set(),
                "quality_count": 0,
            }
        )
        hourly: Counter[int] = Counter()

        for username, messages in session_messages.items():
            self._build_progress()
            if not messages:
                continue
            messages.sort(key=lambda item: (item["time"], item["sort_seq"]))
            contact = contacts.get(username)
            display_name = _display_name(contact, username)
            is_group = username.endswith("@chatroom")
            sent_count = sum(1 for item in messages if item["is_self"])
            received_count = len(messages) - sent_count
            dates = {item["date"] for item in messages}
            categories = Counter(item["category"] for item in messages)
            responses = (
                response_durations(messages, self.settings.response_max_seconds)
                if not is_group
                else []
            )
            episodes = split_episodes(messages, self.settings.episode_gap_seconds)
            total_turns = 0
            for episode_index, current in enumerate(episodes):
                turns = count_turns(current)
                total_turns += turns
                start_time = current[0]["time"]
                end_time = current[-1]["time"]
                local_date = current[0]["date"]
                episode_id = hashlib.sha1(
                    f"{username}|{start_time}|{episode_index}".encode("utf-8")
                ).hexdigest()
                summaries = [item["summary"] for item in current if item["summary"]]
                summary = summaries[-1] if summaries else ""
                episode_categories = Counter(item["category"] for item in current)
                episode_rows.append(
                    (
                        episode_id,
                        username,
                        display_name,
                        local_date,
                        start_time,
                        end_time,
                        len(current),
                        turns,
                        summary,
                        _json(episode_categories),
                    )
                )
                daily[local_date]["episode_count"] += 1
                daily[local_date]["turn_count"] += turns

            for item in messages:
                bucket = daily[item["date"]]
                bucket["message_count"] += 1
                bucket["sent_count" if item["is_self"] else "received_count"] += 1
                bucket["active_sessions"].add(username)
                if item["is_self"] and item["quality"] >= 2:
                    bucket["quality_count"] += 1
                hourly[item["hour"]] += 1

            balance = bidirectional_ratio(sent_count, received_count)
            raw_stats.append(
                {
                    "username": username,
                    "display_name": display_name,
                    "is_group": int(is_group),
                    "is_system": int(_is_system(username, self.account)),
                    "message_count": len(messages),
                    "active_days": len(dates),
                    "first_time": messages[0]["time"],
                    "last_time": messages[-1]["time"],
                    "sent_count": sent_count,
                    "received_count": received_count,
                    "episode_count": len(episodes),
                    "turn_count": total_turns,
                    "response_median_seconds": statistics.median(responses)
                    if responses
                    else None,
                    "bidirectional_ratio": balance,
                    "categories": _json(categories),
                    "latest_summary": messages[-1]["summary"],
                }
            )

        max_messages = max((item["message_count"] for item in raw_stats), default=1)
        max_days = max((item["active_days"] for item in raw_stats), default=1)
        latest_time = max((item["last_time"] for item in raw_stats), default=0)
        session_rows: list[tuple[Any, ...]] = []
        for item in raw_stats:
            strength = relationship_strength(
                item["message_count"],
                item["active_days"],
                item["last_time"],
                item["sent_count"],
                item["received_count"],
                max_messages=max_messages,
                max_active_days=max_days,
                latest_time=latest_time,
            )
            session_rows.append(
                (
                    item["username"],
                    item["display_name"],
                    item["is_group"],
                    item["is_system"],
                    item["message_count"],
                    item["active_days"],
                    item["first_time"],
                    item["last_time"],
                    item["sent_count"],
                    item["received_count"],
                    item["episode_count"],
                    item["turn_count"],
                    item["response_median_seconds"],
                    item["bidirectional_ratio"],
                    strength,
                    item["categories"],
                    item["latest_summary"],
                )
            )
        connection.executemany(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            session_rows,
        )
        connection.executemany(
            "INSERT INTO episodes VALUES (?,?,?,?,?,?,?,?,?,?)", episode_rows
        )
        quoted_by_date = {
            row[0]: int(row[1])
            for row in connection.execute(
                "SELECT local_date,coalesce(sum(quote_count),0) FROM messages GROUP BY local_date"
            )
        }
        daily_rows = []
        for local_date, item in sorted(daily.items()):
            daily_rows.append(
                (
                    local_date,
                    item["message_count"],
                    item["sent_count"],
                    item["received_count"],
                    item["episode_count"],
                    item["turn_count"],
                    quoted_by_date.get(local_date, 0),
                    len(item["active_sessions"]),
                    item["quality_count"],
                )
            )
        connection.executemany(
            "INSERT INTO daily VALUES (?,?,?,?,?,?,?,?,?)", daily_rows
        )
        connection.executemany(
            "INSERT INTO hourly VALUES (?,?)",
            [(hour, hourly.get(hour, 0)) for hour in range(24)],
        )

    @staticmethod
    def _contacts_from_cache(
        connection: sqlite3.Connection,
    ) -> dict[str, dict[str, Any]]:
        return {
            str(row["username"]): dict(row)
            for row in connection.execute("SELECT * FROM contacts")
        }

    @staticmethod
    def _aggregate_input_from_cache(
        connection: sqlite3.Connection,
    ) -> dict[str, Any]:
        session_messages: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in connection.execute(
            """
            SELECT message_key,session_username,create_time,sort_seq,
                   sender_username,sender_id,is_self,local_date,local_hour,
                   category,summary,quality_score
            FROM messages ORDER BY session_username,create_time,sort_seq,local_id
            """
        ):
            session_messages[row["session_username"]].append(
                {
                    "message_key": row["message_key"],
                    "time": int(row["create_time"]),
                    "sort_seq": int(row["sort_seq"] or 0),
                    "sender": row["sender_username"] or str(row["sender_id"] or ""),
                    "is_self": bool(row["is_self"]),
                    "date": row["local_date"],
                    "hour": int(row["local_hour"]),
                    "category": row["category"],
                    "summary": row["summary"] or "",
                    "quality": int(row["quality_score"] or 0),
                }
            )
        return {"session_messages": session_messages}

    def _refresh_derived_aggregates(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            UPDATE messages AS original
            SET quote_count = (
                SELECT count(*) FROM messages AS quoted
                WHERE quoted.reference_server_id = original.server_id
                  AND original.server_id IS NOT NULL
                  AND original.server_id != 0
            )
            """
        )
        build = self._aggregate_input_from_cache(connection)
        contacts = self._contacts_from_cache(connection)
        for table in ("sessions", "episodes", "daily", "hourly"):
            connection.execute(f"DELETE FROM {table}")
        self._build_aggregates(connection, build, contacts)
        totals = connection.execute(
            """
            SELECT count(*) messages,
                   sum(CASE WHEN decoded=1 THEN 1 ELSE 0 END) decoded,
                   sum(CASE WHEN decoded=0 THEN 1 ELSE 0 END) decode_errors
            FROM messages
            """
        ).fetchone()
        realtime_count_row = connection.execute(
            "SELECT value FROM meta WHERE key='realtime_message_count'"
        ).fetchone()
        metadata = {
            "message_count": str(int(totals["messages"] or 0)),
            "decoded_count": str(int(totals["decoded"] or 0)),
            "decode_error_count": str(int(totals["decode_errors"] or 0)),
            "realtime_message_count": str(
                int(realtime_count_row[0]) if realtime_count_row else 0
            ),
        }
        metadata["session_count"] = str(
            int(connection.execute("SELECT count(*) FROM sessions").fetchone()[0])
        )
        connection.executemany(
            "INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)", metadata.items()
        )

    @staticmethod
    def _session_aggregate_input(
        connection: sqlite3.Connection, username: str
    ) -> list[dict[str, Any]]:
        return [
            {
                "message_key": row["message_key"],
                "time": int(row["create_time"]),
                "sort_seq": int(row["sort_seq"] or 0),
                "sender": row["sender_username"] or str(row["sender_id"] or ""),
                "is_self": bool(row["is_self"]),
                "date": row["local_date"],
                "hour": int(row["local_hour"]),
                "category": row["category"],
                "summary": row["summary"] or "",
                "quality": int(row["quality_score"] or 0),
            }
            for row in connection.execute(
                """
                SELECT message_key,create_time,sort_seq,sender_username,sender_id,
                       is_self,local_date,local_hour,category,summary,quality_score
                FROM messages WHERE session_username=?
                ORDER BY create_time,sort_seq,local_id
                """,
                (username,),
            )
        ]

    def _refresh_relationship_strengths(
        self, connection: sqlite3.Connection
    ) -> None:
        rows = connection.execute(
            """
            SELECT username,message_count,active_days,last_time,sent_count,received_count
            FROM sessions
            """
        ).fetchall()
        if not rows:
            return
        max_messages = max(int(row["message_count"]) for row in rows)
        max_days = max(int(row["active_days"]) for row in rows)
        latest_time = max(int(row["last_time"]) for row in rows)
        connection.executemany(
            "UPDATE sessions SET relationship_strength=? WHERE username=?",
            [
                (
                    relationship_strength(
                        int(row["message_count"]),
                        int(row["active_days"]),
                        int(row["last_time"]),
                        int(row["sent_count"]),
                        int(row["received_count"]),
                        max_messages=max_messages,
                        max_active_days=max_days,
                        latest_time=latest_time,
                    ),
                    row["username"],
                )
                for row in rows
            ],
        )

    def _refresh_session_aggregate(
        self,
        connection: sqlite3.Connection,
        username: str,
        contacts: dict[str, dict[str, Any]],
    ) -> None:
        messages = self._session_aggregate_input(connection, username)
        connection.execute("DELETE FROM episodes WHERE session_username=?", (username,))
        connection.execute("DELETE FROM sessions WHERE username=?", (username,))
        if not messages:
            self._refresh_relationship_strengths(connection)
            return

        contact = contacts.get(username)
        display_name = _display_name(contact, username)
        is_group = username.endswith("@chatroom")
        sent_count = sum(1 for item in messages if item["is_self"])
        received_count = len(messages) - sent_count
        categories = Counter(item["category"] for item in messages)
        responses = (
            response_durations(messages, self.settings.response_max_seconds)
            if not is_group
            else []
        )
        episodes = split_episodes(messages, self.settings.episode_gap_seconds)
        episode_rows: list[tuple[Any, ...]] = []
        total_turns = 0
        for episode_index, current in enumerate(episodes):
            turns = count_turns(current)
            total_turns += turns
            start_time = int(current[0]["time"])
            end_time = int(current[-1]["time"])
            summaries = [item["summary"] for item in current if item["summary"]]
            episode_rows.append(
                (
                    hashlib.sha1(
                        f"{username}|{start_time}|{episode_index}".encode("utf-8")
                    ).hexdigest(),
                    username,
                    display_name,
                    current[0]["date"],
                    start_time,
                    end_time,
                    len(current),
                    turns,
                    summaries[-1] if summaries else "",
                    _json(Counter(item["category"] for item in current)),
                )
            )
        connection.executemany(
            "INSERT INTO episodes VALUES (?,?,?,?,?,?,?,?,?,?)", episode_rows
        )
        connection.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                username,
                display_name,
                int(is_group),
                int(_is_system(username, self.account)),
                len(messages),
                len({item["date"] for item in messages}),
                int(messages[0]["time"]),
                int(messages[-1]["time"]),
                sent_count,
                received_count,
                len(episodes),
                total_turns,
                statistics.median(responses) if responses else None,
                bidirectional_ratio(sent_count, received_count),
                0.0,
                _json(categories),
                messages[-1]["summary"],
            ),
        )
        self._refresh_relationship_strengths(connection)

    @staticmethod
    def _refresh_daily_aggregates(connection: sqlite3.Connection) -> None:
        connection.execute("DELETE FROM daily")
        connection.execute(
            """
            WITH episode_daily AS (
                SELECT local_date,count(*) episode_count,
                       coalesce(sum(turn_count),0) turn_count
                FROM episodes GROUP BY local_date
            )
            INSERT INTO daily(
                local_date,message_count,sent_count,received_count,episode_count,
                turn_count,quoted_count,active_sessions,quality_count
            )
            SELECT messages.local_date,
                   count(*),
                   sum(CASE WHEN is_self=1 THEN 1 ELSE 0 END),
                   sum(CASE WHEN is_self=0 THEN 1 ELSE 0 END),
                   coalesce(max(episode_daily.episode_count),0),
                   coalesce(max(episode_daily.turn_count),0),
                   coalesce(sum(quote_count),0),
                   count(DISTINCT session_username),
                   sum(CASE WHEN is_self=1 AND quality_score>=2 THEN 1 ELSE 0 END)
            FROM messages
            LEFT JOIN episode_daily USING(local_date)
            GROUP BY messages.local_date
            """
        )

    @staticmethod
    def _refresh_quote_counts(
        connection: sqlite3.Connection, message: dict[str, Any]
    ) -> None:
        server_ids = {
            int(value)
            for value in (
                message.get("server_id"),
                message.get("reference_server_id"),
            )
            if value
        }
        for server_id in server_ids:
            connection.execute(
                """
                UPDATE messages AS original
                SET quote_count = (
                    SELECT count(*) FROM messages AS quoted
                    WHERE quoted.reference_server_id=original.server_id
                      AND original.server_id IS NOT NULL
                      AND original.server_id!=0
                )
                WHERE original.server_id=?
                """,
                (server_id,),
            )

    @staticmethod
    def _refresh_realtime_metadata(
        connection: sqlite3.Connection, realtime_increment: int
    ) -> None:
        totals = connection.execute(
            """
            SELECT count(*) messages,
                   sum(CASE WHEN decoded=1 THEN 1 ELSE 0 END) decoded,
                   sum(CASE WHEN decoded=0 THEN 1 ELSE 0 END) decode_errors
            FROM messages
            """
        ).fetchone()
        realtime_row = connection.execute(
            "SELECT value FROM meta WHERE key='realtime_message_count'"
        ).fetchone()
        realtime_count = int(realtime_row[0]) if realtime_row else 0
        metadata = {
            "message_count": str(int(totals["messages"] or 0)),
            "decoded_count": str(int(totals["decoded"] or 0)),
            "decode_error_count": str(int(totals["decode_errors"] or 0)),
            "realtime_message_count": str(realtime_count + realtime_increment),
            "session_count": str(
                int(connection.execute("SELECT count(*) FROM sessions").fetchone()[0])
            ),
        }
        connection.executemany(
            "INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)", metadata.items()
        )

    def _materialize_realtime_message(self, raw_message: dict[str, Any]) -> bool:
        self.ensure_cache()
        with self._lock:
            connection = sqlite3.connect(self.ensure_cache(), timeout=10.0)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA busy_timeout=10000")
                connection.execute("BEGIN IMMEDIATE")
                contacts = self._contacts_from_cache(connection)
                message = self._resolve_realtime_names(raw_message, contacts)
                insert_sql = "INSERT OR IGNORE INTO messages VALUES (" + ",".join(
                    "?" for _ in CACHE_MESSAGE_COLUMNS
                ) + ")"
                cursor = connection.execute(insert_sql, self._message_values(message))
                inserted = cursor.rowcount == 1
                if inserted:
                    self._refresh_quote_counts(connection, message)
                    self._refresh_session_aggregate(
                        connection,
                        str(message["session_username"]),
                        contacts,
                    )
                    self._refresh_daily_aggregates(connection)
                    connection.execute(
                        """
                        INSERT INTO hourly(hour,message_count) VALUES (?,1)
                        ON CONFLICT(hour) DO UPDATE
                        SET message_count=message_count+1
                        """,
                        (int(message["local_hour"]),),
                    )
                    self._refresh_realtime_metadata(connection, 1)
                connection.commit()
                return inserted
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    def ingest_realtime(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.ensure_cache()
        result = self.realtime.ingest(payload)
        retry_pending = bool(
            result["duplicate"]
            and result.get("normalized") is not None
            and result.get("status") in {"accepted", "pending"}
        )
        if result["duplicate"] and not retry_pending:
            return self._public_realtime_result(result)
        sequence = int(result["sequence"])
        normalized = result.get("normalized")
        if normalized is not None:
            try:
                inserted = self._materialize_realtime_message(normalized)
            except (OSError, sqlite3.DatabaseError, ValueError) as exc:
                self.realtime.mark(sequence, "pending", str(exc))
                result["status"] = "pending"
                result["error"] = str(exc)
                result["quality"]["state"] = "pending"
                result["quality"]["errors"].append(f"materialization failed: {exc}")
            else:
                self.realtime.mark(sequence, "applied")
                result["status"] = "applied"
                result["error"] = None
                if not inserted and not result["duplicate"]:
                    result.update(
                        {
                            "duplicate": True,
                            "dedupe_basis": "cache_message_identity",
                        }
                    )
                self.realtime.publish(sequence)
        return self._public_realtime_result(result)

    def accept_realtime(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Durably accept an event without blocking the relay on analytics work."""
        result = self.realtime.ingest(payload)
        public = self._public_realtime_result(result)
        if public.get("status") == "accepted":
            public["status"] = "pending"
        return public

    @staticmethod
    def _public_realtime_result(result: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in result.items()
            if key not in {"payload", "normalized", "received_at"}
        }

    def realtime_status(self) -> dict[str, Any]:
        status = self.realtime.status()
        with self.cache() as connection:
            status["materialized_messages"] = int(
                connection.execute("SELECT count(*) FROM messages").fetchone()[0]
            )
            metadata = self._metadata(connection)
            status["base_message_tables"] = int(
                metadata.get("message_table_count", 0)
            )
        return status

    def realtime_events_since(self, after: int, limit: int = 256) -> list[dict[str, Any]]:
        return self.realtime.events_since(after, limit)

    def replay_realtime(self) -> int:
        """Reconcile accepted/pending events after a process or cache restart."""
        self.ensure_cache()
        records = self.realtime.pending_messages()
        if not records:
            return 0
        inserted = 0
        with self._lock:
            connection = sqlite3.connect(self.ensure_cache(), timeout=10.0)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA busy_timeout=10000")
                connection.execute("BEGIN IMMEDIATE")
                contacts = self._contacts_from_cache(connection)
                insert_sql = "INSERT OR IGNORE INTO messages VALUES (" + ",".join(
                    "?" for _ in CACHE_MESSAGE_COLUMNS
                ) + ")"
                for _, raw_message in records:
                    message = self._resolve_realtime_names(raw_message, contacts)
                    cursor = connection.execute(insert_sql, self._message_values(message))
                    inserted += int(cursor.rowcount == 1)
                if inserted:
                    self._refresh_derived_aggregates(connection)
                    self._refresh_realtime_metadata(connection, inserted)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()
        for sequence, _ in records:
            try:
                self.realtime.mark(sequence, "applied")
                self.realtime.publish(sequence)
            except OSError:
                # The event remains durable and will be reconciled next start.
                pass
        return inserted

    def _scan_source_files(self, required_names: set[str] | None = None) -> dict[str, list[tuple[Path, int]]]:
        index: dict[str, list[tuple[Path, int]]] = defaultdict(list)
        scanned = 0
        if self.account_root is None or not self.account_root.is_dir():
            return index
        roots = [
            self.account_root / "msg",
            self.account_root / "resource",
            self.account_root / "business" / "favorite",
        ]
        for root in roots:
            if not root.is_dir():
                continue
            for directory, _, names in os.walk(root):
                self._build_progress()
                base = Path(directory)
                for name in names:
                    scanned += 1
                    if scanned % 1000 == 0:
                        self._build_progress(resource_files=scanned)
                    if required_names is not None and name.casefold() not in required_names:
                        continue
                    path = base / name
                    try:
                        size = path.stat().st_size
                    except OSError:
                        continue
                    index[name.casefold()].append((path.resolve(), size))
        self._build_progress(resource_files=scanned)
        return index

    @staticmethod
    def _select_file(
        file_index: dict[str, list[tuple[Path, int]]], file_name: str, size: int | None
    ) -> Path | None:
        candidates = file_index.get(file_name.casefold(), [])
        if not candidates:
            return None
        if size:
            for path, actual_size in candidates:
                if actual_size == size:
                    return path
        return candidates[0][0]

    @staticmethod
    def _resource_mime(path: Path | None, kind: str) -> tuple[str, bool]:
        if path is None:
            return "application/octet-stream", False
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        try:
            with path.open("rb") as stream:
                header = stream.read(16)
        except OSError:
            return mime, False
        if header.startswith(b"\xff\xd8\xff"):
            return "image/jpeg", True
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png", True
        if header.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif", True
        if header[:4] in (b"RIFF",) and header[8:12] == b"WEBP":
            return "image/webp", True
        if kind == "video" and mime.startswith("video/"):
            return mime, True
        if kind == "file":
            return mime, True
        return mime, False

    def _build_resources(self, connection: sqlite3.Connection) -> int:
        rows: list[tuple[Any, ...]] = []
        table_map = {
            "image": "image_hardlink_info_v4",
            "video": "video_hardlink_info_v4",
            "file": "file_hardlink_info_v4",
        }
        with open_readonly(self.database('hardlink/hardlink.db')) as source:
            required_names = {
                str(row[0]).casefold()
                for table in table_map.values()
                for row in source.execute(f'SELECT file_name FROM {table} WHERE file_name IS NOT NULL')
            }
        file_index = self._scan_source_files(required_names)
        with open_readonly(self.database("hardlink/hardlink.db")) as source:
            for kind, table in table_map.items():
                for row in source.execute(
                    f"SELECT _rowid_,md5,file_name,file_size,modify_time FROM {table}"
                ):
                    if len(rows) % 1000 == 0:
                        self._build_progress()
                    file_name = str(row["file_name"] or "")
                    file_size = int(row["file_size"] or 0)
                    path = self._select_file(file_index, file_name, file_size)
                    mime, supported = self._resource_mime(path, kind)
                    rowid = int(row["_rowid_"])
                    rows.append(
                        (
                            f"{kind}:{rowid}",
                            kind,
                            rowid,
                            row["md5"],
                            file_name,
                            file_size,
                            int(row["modify_time"] or 0),
                            str(path) if path else None,
                            int(path is not None and path.is_file()),
                            int(supported),
                            mime,
                        )
                    )
        connection.executemany(
            "INSERT INTO resources VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows
        )
        return len(rows)

    def _source_counts(self) -> dict[str, str]:
        counts: dict[str, str] = {}
        queries = {
            "contact_count": ("contact/contact.db", "SELECT count(*) FROM contact"),
            "chatroom_count": ("contact/contact.db", "SELECT count(*) FROM chat_room"),
            "session_count": ("session/session.db", "SELECT count(*) FROM SessionTable"),
            "resource_info_count": (
                "message/message_resource.db",
                "SELECT count(*) FROM MessageResourceInfo",
            ),
            "resource_detail_count": (
                "message/message_resource.db",
                "SELECT count(*) FROM MessageResourceDetail",
            ),
            "voice_info_count": ("message/media_0.db", "SELECT count(*) FROM VoiceInfo"),
            "avatar_count": ("head_image/head_image.db", "SELECT count(*) FROM head_image"),
        }
        grouped: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for key, (relative, sql) in queries.items():
            grouped[relative].append((key, sql))
        for relative, items in grouped.items():
            try:
                with open_readonly(self.database(relative)) as source:
                    for key, sql in items:
                        try:
                            counts[key] = str(source.execute(sql).fetchone()[0])
                        except sqlite3.OperationalError:
                            counts.setdefault(key, "0")
            except (FileNotFoundError, sqlite3.OperationalError):
                for key, _sql in items:
                    counts.setdefault(key, "0")
        return counts

    def _avatar_url(self, username: str) -> str:
        return f"/api/avatars/{quote(username, safe='')}"

    def _iso_time(self, timestamp: int | float | None) -> str | None:
        if not timestamp:
            return None
        return datetime.fromtimestamp(float(timestamp), self.timezone).isoformat()

    def _metadata(self, connection: sqlite3.Connection) -> dict[str, str]:
        return {row[0]: row[1] for row in connection.execute("SELECT key,value FROM meta")}

    def _data_quality(self, metadata: dict[str, str]) -> dict[str, Any]:
        files = self._report.get("files", [])
        quick_ok = sum(1 for item in files if item.get("quick_check") == "ok")
        integrity_ok = sum(
            1 for item in files if item.get("integrity_check") == "ok"
        )
        wal_files = [item.get("source_wal", {}) for item in files]
        return {
            "source_read_only": True,
            "snapshot_time": self.snapshot_time,
            "snapshot_mode": "wal-merged" if any(item.get('applied_committed_frames') for item in wal_files) else "base-only",
            "decrypted_database_count": self._report.get("decrypted_database_count", 0),
            "unresolved": self._report.get("unresolved", []),
            "quick_check_ok": quick_ok,
            "integrity_check_ok": integrity_ok,
            "source_wal_present": sum(1 for item in wal_files if item.get("exists")),
            "source_wal_valid_commits": sum(
                int(item.get("commit_frames") or 0) for item in wal_files
            ),
            "message_tables": int(metadata.get("message_table_count", 0)),
            "decoded_messages": int(metadata.get("decoded_count", 0)),
            "decode_errors": int(metadata.get("decode_error_count", 0)),
            "message_resource_details": int(
                metadata.get("message_resource_detail_count", 0)
            ),
            "linked_message_resource_details": int(
                metadata.get("linked_message_resource_count", 0)
            ),
            "cache_built_at": metadata.get("built_at"),
        }

    def health(self) -> dict[str, Any]:
        if not self.configured:
            return {
                "status": "setup_required",
                "configured": False,
                "detail": self._report_error or "No decrypted snapshot is configured.",
                "database_root": str(self.database_root),
                "report_path": str(self.settings.report_path),
                "missing": self.missing_sources,
            }
        try:
            with self.cache() as connection:
                metadata = self._metadata(connection)
                message_count = connection.execute("SELECT count(*) FROM messages").fetchone()[0]
        except (OSError, RuntimeError, sqlite3.DatabaseError, ValueError) as exc:
            self._cache_error = f"Unable to load decrypted snapshot: {exc}"
            return {
                "status": "setup_required",
                "configured": False,
                "detail": self._cache_error,
                "database_root": str(self.database_root),
                "report_path": str(self.settings.report_path),
                "missing": self.missing_sources,
            }
        return {
            "status": "ok",
            "configured": True,
            "database_root": str(self.database_root),
            "message_count": message_count,
            "snapshot_time": self.snapshot_time,
            "data_quality": self._data_quality(metadata),
        }

    def profile(self) -> dict[str, Any]:
        with self.cache() as connection:
            metadata = self._metadata(connection)
            contact = connection.execute(
                "SELECT * FROM contacts WHERE username=?", (self.account,)
            ).fetchone()
            totals = connection.execute(
                """
                SELECT count(*) AS messages,
                       count(DISTINCT local_date) AS active_days,
                       max(create_time) AS last_active
                FROM messages
                """
            ).fetchone()
        contact_dict = dict(contact) if contact else {}
        display_name = contact_dict.get("display_name") or self.account
        profile = {
            "username": self.account,
            "display_name": display_name,
            "nickname": contact_dict.get("nick_name") or display_name,
            "remark": contact_dict.get("remark") or "",
            "alias": contact_dict.get("alias") or "",
            "avatar_url": self._avatar_url(self.account),
            "remote_avatar_available": bool(
                contact_dict.get("big_head_url") or contact_dict.get("small_head_url")
            ),
        }
        return {
            "profile": profile,
            "stats": {
                "messages": int(totals["messages"] or 0),
                "active_days": int(totals["active_days"] or 0),
                "contacts": int(metadata.get("contact_count", 0)),
                "sessions": int(metadata.get("session_count", 0)),
                "chatrooms": int(metadata.get("chatroom_count", 0)),
                "last_active": self._iso_time(totals["last_active"]),
            },
            "snapshot_time": self.snapshot_time,
            "metric_basis": "All rows from verified Msg_* tables in the immutable decrypted snapshot.",
            "data_quality": self._data_quality(metadata),
        }

    def _format_message(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        return {
            "id": item.get("message_key"),
            "session_username": item.get("session_username"),
            "session_name": item.get("session_name"),
            "sender_username": item.get("sender_username"),
            "sender_name": item.get("sender_name"),
            "avatar_url": self._avatar_url(
                item.get("sender_username") or item.get("session_username") or self.account
            ),
            "create_time": self._iso_time(item.get("create_time")),
            "timestamp": item.get("create_time"),
            "summary": item.get("summary") or "",
            "content": item.get("content") or "",
            "category": item.get("category") or "other",
            "decoded": bool(item.get("decoded")),
            "decode_error": item.get("decode_error"),
            "local_type": item.get("local_type"),
            "base_type": item.get("base_type"),
            "app_subtype": item.get("app_subtype"),
            "title": item.get("title"),
            "description": item.get("description"),
            "url": item.get("url"),
            "file_name": item.get("file_name"),
            "file_size": item.get("file_size"),
            "is_self": bool(item.get("is_self")),
            "quote_count": int(item.get("quote_count") or 0),
            "reference": {
                "server_id": item.get("reference_server_id"),
                "from_username": item.get("reference_from_username"),
                "chat_username": item.get("reference_chat_username"),
                "display_name": item.get("reference_display_name"),
                "content": item.get("reference_content"),
            }
            if item.get("reference_server_id") or item.get("reference_content")
            else None,
        }

    def overview(self, days: int = 30) -> dict[str, Any]:
        days = max(1, min(days, 366))
        with self.cache() as connection:
            metadata = self._metadata(connection)
            latest_date_row = connection.execute("SELECT max(local_date) FROM messages").fetchone()
            latest_date = date.fromisoformat(latest_date_row[0]) if latest_date_row[0] else date.today()
            start_date = (latest_date - timedelta(days=days - 1)).isoformat()
            trend_by_date = {
                row["local_date"]: dict(row)
                for row in connection.execute(
                    "SELECT local_date,message_count,sent_count,received_count "
                    "FROM daily WHERE local_date>=? ORDER BY local_date",
                    (start_date,),
                )
            }
            totals = connection.execute(
                "SELECT count(*) messages,count(DISTINCT local_date) active_days,max(create_time) last_active FROM messages"
            ).fetchone()
            top_rows = connection.execute(
                """
                SELECT * FROM sessions
                WHERE is_system=0
                ORDER BY message_count DESC, last_time DESC LIMIT 8
                """
            ).fetchall()
            type_rows = connection.execute(
                "SELECT category,count(*) count FROM messages GROUP BY category ORDER BY count DESC"
            ).fetchall()
            recent_rows = connection.execute(
                "SELECT * FROM messages ORDER BY create_time DESC,sort_seq DESC LIMIT 10"
            ).fetchall()
            heatmap_rows = connection.execute(
                """
                SELECT local_date,message_count FROM daily
                WHERE local_date>=? ORDER BY local_date
                """,
                ((latest_date - timedelta(days=365)).isoformat(),),
            ).fetchall()
            profile_payload = self.profile()

        trend = []
        for offset in range(days):
            day = (date.fromisoformat(start_date) + timedelta(days=offset)).isoformat()
            row = trend_by_date.get(day, {})
            trend.append(
                {
                    "date": day,
                    "count": int(row.get("message_count") or 0),
                    "sent": int(row.get("sent_count") or 0),
                    "received": int(row.get("received_count") or 0),
                }
            )
        top_contacts = []
        for row in top_rows:
            top_contacts.append(
                {
                    "username": row["username"],
                    "display_name": row["display_name"],
                    "avatar_url": self._avatar_url(row["username"]),
                    "message_count": row["message_count"],
                    "active_days": row["active_days"],
                    "last_active": self._iso_time(row["last_time"]),
                    "summary": row["latest_summary"],
                    "relationship_strength": row["relationship_strength"],
                }
            )
        labels = {
            "text": "文本",
            "image": "图片",
            "video": "视频",
            "voice": "语音",
            "file": "文件",
            "link": "链接",
            "quote": "引用",
            "sticker": "表情",
            "system": "系统",
            "app": "应用消息",
            "other": "其他",
        }
        return {
            "profile": profile_payload["profile"],
            "kpis": {
                "messages": int(totals["messages"] or 0),
                "active_days": int(totals["active_days"] or 0),
                "contacts": int(metadata.get("contact_count", 0)),
                "sessions": int(metadata.get("session_count", 0)),
                "last_active": self._iso_time(totals["last_active"]),
            },
            "trend": trend,
            "top_contacts": top_contacts,
            "message_types": [
                {
                    "type": row["category"],
                    "label": labels.get(row["category"], row["category"]),
                    "count": row["count"],
                }
                for row in type_rows
            ],
            "recent_messages": [self._format_message(row) for row in recent_rows],
            "heatmap": [
                {"date": row["local_date"], "count": row["message_count"]}
                for row in heatmap_rows
            ],
            "snapshot_time": self.snapshot_time,
            "metric_basis": "Counts are derived from normalized real messages; dates use Asia/Shanghai local time.",
            "data_quality": self._data_quality(metadata),
        }

    def contacts(
        self, limit: int = 50, offset: int = 0, query: str = ""
    ) -> dict[str, Any]:
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        parameters: list[Any] = []
        where = "WHERE s.is_system=0"
        if query.strip():
            where += " AND (s.display_name LIKE ? OR s.username LIKE ?)"
            pattern = f"%{query.strip()}%"
            parameters.extend((pattern, pattern))
        with self.cache() as connection:
            metadata = self._metadata(connection)
            total = connection.execute(
                f"SELECT count(*) FROM sessions s {where}", parameters
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT s.* FROM sessions s {where}
                ORDER BY s.relationship_strength DESC,s.message_count DESC
                LIMIT ? OFFSET ?
                """,
                (*parameters, limit, offset),
            ).fetchall()
            all_rows = connection.execute(
                "SELECT * FROM sessions WHERE is_system=0"
            ).fetchall()
        items = []
        for row in rows:
            categories = _loads(row["categories"], {})
            channels = [key for key, value in categories.items() if value]
            items.append(
                {
                    "username": row["username"],
                    "display_name": row["display_name"],
                    "avatar_url": self._avatar_url(row["username"]),
                    "is_group": bool(row["is_group"]),
                    "message_count": row["message_count"],
                    "active_days": row["active_days"],
                    "first_interaction": self._iso_time(row["first_time"]),
                    "last_active": self._iso_time(row["last_time"]),
                    "sent_count": row["sent_count"],
                    "received_count": row["received_count"],
                    "bidirectional_ratio": round(row["bidirectional_ratio"], 4),
                    "response_median_seconds": row["response_median_seconds"],
                    "relationship_strength": row["relationship_strength"],
                    "interaction_frequency": round(
                        row["message_count"] / max(row["active_days"], 1), 2
                    ),
                    "channels": channels,
                    "summary": row["latest_summary"],
                }
            )
        first_counts: Counter[str] = Counter()
        response_buckets = {
            "within_5m": 0,
            "5m_to_1h": 0,
            "1h_to_24h": 0,
            "unavailable": 0,
        }
        for row in all_rows:
            first_day = datetime.fromtimestamp(row["first_time"], self.timezone).date().isoformat()
            first_counts[first_day] += 1
            response = row["response_median_seconds"]
            if response is None or row["is_group"]:
                response_buckets["unavailable"] += 1
            elif response <= 300:
                response_buckets["within_5m"] += 1
            elif response <= 3600:
                response_buckets["5m_to_1h"] += 1
            else:
                response_buckets["1h_to_24h"] += 1
        cumulative = 0
        growth = []
        for day, count in sorted(first_counts.items()):
            cumulative += count
            growth.append({"date": day, "count": count, "cumulative": cumulative})
        return {
            "items": items,
            "total": total,
            "interacting_total": total,
            "contact_total": int(metadata.get("contact_count", 0)),
            "stats": {
                "interacting_contacts": len(all_rows),
                "groups": sum(int(row["is_group"]) for row in all_rows),
                "high_strength": sum(
                    float(row["relationship_strength"]) >= 70 for row in all_rows
                ),
            },
            "growth": growth,
            "response_distribution": [
                {"bucket": key, "count": value}
                for key, value in response_buckets.items()
            ],
            "limit": limit,
            "offset": offset,
            "snapshot_time": self.snapshot_time,
            "metric_basis": "Only sessions with real messages are ranked; internal system accounts are excluded.",
        }

    def chat_sessions(
        self,
        limit: int = 120,
        offset: int = 0,
        query: str = "",
        scope: str = "all",
    ) -> dict[str, Any]:
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        scope = scope if scope in {"all", "groups", "contacts"} else "all"
        where = ["s.is_system=0"]
        parameters: list[Any] = []
        if scope == "groups":
            where.append("s.is_group=1")
        elif scope == "contacts":
            where.append("s.is_group=0")
        if query.strip():
            pattern = f"%{query.strip()}%"
            where.append(
                "(s.display_name LIKE ? OR s.username LIKE ? OR "
                "s.latest_summary LIKE ? OR c.alias LIKE ? OR c.remark LIKE ?)"
            )
            parameters.extend((pattern, pattern, pattern, pattern, pattern))
        clause = " AND ".join(where)

        with self.cache() as connection:
            counts = connection.execute(
                """
                SELECT count(*) total,
                       sum(CASE WHEN is_group=1 THEN 1 ELSE 0 END) groups,
                       sum(CASE WHEN is_group=0 THEN 1 ELSE 0 END) contacts
                FROM sessions WHERE is_system=0
                """
            ).fetchone()
            total = connection.execute(
                f"""
                SELECT count(*)
                FROM sessions s
                LEFT JOIN contacts c ON c.username=s.username
                WHERE {clause}
                """,
                parameters,
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT s.*,c.alias,c.remark,c.nick_name,
                       latest.category latest_category,
                       latest.is_self latest_is_self
                FROM sessions s
                LEFT JOIN contacts c ON c.username=s.username
                LEFT JOIN messages latest ON latest.message_key=(
                    SELECT message_key FROM messages candidate
                    WHERE candidate.session_username=s.username
                    ORDER BY candidate.create_time DESC,
                             candidate.sort_seq DESC,
                             candidate.local_id DESC
                    LIMIT 1
                )
                WHERE {clause}
                ORDER BY s.last_time DESC,s.message_count DESC
                LIMIT ? OFFSET ?
                """,
                (*parameters, limit, offset),
            ).fetchall()

        return {
            "items": [
                {
                    "username": row["username"],
                    "display_name": row["display_name"],
                    "avatar_url": self._avatar_url(row["username"]),
                    "is_group": bool(row["is_group"]),
                    "message_count": int(row["message_count"]),
                    "active_days": int(row["active_days"]),
                    "last_active": self._iso_time(row["last_time"]),
                    "summary": row["latest_summary"],
                    "latest_category": row["latest_category"] or "other",
                    "latest_is_self": bool(row["latest_is_self"]),
                    "alias": row["alias"] or "",
                    "remark": row["remark"] or "",
                    "relationship_strength": round(
                        float(row["relationship_strength"]), 2
                    ),
                }
                for row in rows
            ],
            "total": int(total),
            "counts": {
                "all": int(counts["total"] or 0),
                "groups": int(counts["groups"] or 0),
                "contacts": int(counts["contacts"] or 0),
            },
            "limit": limit,
            "offset": offset,
            "scope": scope,
            "query": query,
            "snapshot_time": self.snapshot_time,
            "metric_basis": "Sessions are ordered by the latest real message in the immutable local snapshot.",
        }

    def chat_session(
        self,
        username: str,
        limit: int = 180,
        year: int | None = None,
        selected_date: str | None = None,
    ) -> dict[str, Any] | None:
        limit = max(20, min(limit, 500))
        with self.cache() as connection:
            session = connection.execute(
                """
                SELECT s.*,c.alias,c.remark,c.nick_name,c.big_head_url,
                       c.small_head_url
                FROM sessions s
                LEFT JOIN contacts c ON c.username=s.username
                WHERE s.username=? AND s.is_system=0
                """,
                (username,),
            ).fetchone()
            if session is None:
                return None

            year_rows = connection.execute(
                """
                SELECT DISTINCT cast(substr(local_date,1,4) AS INTEGER) year
                FROM messages WHERE session_username=? ORDER BY year DESC
                """,
                (username,),
            ).fetchall()
            available_years = [int(row["year"]) for row in year_rows]
            latest_date = connection.execute(
                "SELECT max(local_date) FROM messages WHERE session_username=?",
                (username,),
            ).fetchone()[0]
            selected_year = year or (
                int(latest_date[:4]) if latest_date else datetime.now(self.timezone).year
            )
            year_start = f"{selected_year:04d}-01-01"
            year_end = f"{selected_year:04d}-12-31"
            year_latest_date = connection.execute(
                """
                SELECT max(local_date) FROM messages
                WHERE session_username=? AND local_date BETWEEN ? AND ?
                """,
                (username, year_start, year_end),
            ).fetchone()[0]
            day = selected_date or year_latest_date or latest_date

            message_rows = connection.execute(
                """
                SELECT * FROM (
                    SELECT * FROM messages WHERE session_username=?
                    ORDER BY create_time DESC,sort_seq DESC,local_id DESC
                    LIMIT ?
                ) recent
                ORDER BY create_time,sort_seq,local_id
                """,
                (username, limit),
            ).fetchall()
            file_rows = connection.execute(
                """
                SELECT * FROM messages
                WHERE session_username=?
                  AND category IN ('file','image','video','voice','link','chat_history')
                ORDER BY create_time DESC,sort_seq DESC,local_id DESC
                LIMIT 60
                """,
                (username,),
            ).fetchall()
            resource_keys = list(
                dict.fromkeys(
                    [row["message_key"] for row in message_rows]
                    + [row["message_key"] for row in file_rows]
                )
            )
            resources = self._resource_details_for_messages(
                connection, resource_keys
            )
            heatmap_rows = connection.execute(
                """
                SELECT local_date,count(*) count,
                       sum(CASE WHEN is_self=1 THEN 1 ELSE 0 END) sent,
                       sum(CASE WHEN is_self=0 THEN 1 ELSE 0 END) received
                FROM messages
                WHERE session_username=? AND local_date BETWEEN ? AND ?
                GROUP BY local_date ORDER BY local_date
                """,
                (username, year_start, year_end),
            ).fetchall()
            totals = connection.execute(
                """
                SELECT count(*) messages,
                       count(DISTINCT local_date) active_days,
                       sum(CASE WHEN quality_score>=2 THEN 1 ELSE 0 END) quality,
                       coalesce(sum(quote_count),0) quoted
                FROM messages WHERE session_username=?
                """,
                (username,),
            ).fetchone()
            day_stats = (
                connection.execute(
                    """
                    SELECT count(*) messages,
                           sum(CASE WHEN is_self=1 THEN 1 ELSE 0 END) sent,
                           sum(CASE WHEN is_self=0 THEN 1 ELSE 0 END) received,
                           sum(CASE WHEN quality_score>=2 THEN 1 ELSE 0 END) quality,
                           coalesce(sum(quote_count),0) quoted,
                           count(DISTINCT coalesce(sender_username,session_username)) speakers,
                           min(create_time) first_time,
                           max(create_time) last_time
                    FROM messages
                    WHERE session_username=? AND local_date=?
                    """,
                    (username, day),
                ).fetchone()
                if day
                else None
            )
            category_rows = connection.execute(
                """
                SELECT category,count(*) count FROM messages
                WHERE session_username=? GROUP BY category
                ORDER BY count DESC,category
                """,
                (username,),
            ).fetchall()

        resource_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in resources:
            if item.get("message_key"):
                resource_map[item["message_key"]].append(item)

        def format_rows(rows: Sequence[sqlite3.Row]) -> list[dict[str, Any]]:
            formatted: list[dict[str, Any]] = []
            for row in rows:
                item = self._format_message(row)
                item["resources"] = resource_map.get(row["message_key"], [])
                formatted.append(item)
            return formatted

        category_counts = {
            row["category"]: int(row["count"]) for row in category_rows
        }
        tags = ["群聊" if session["is_group"] else "联系人"]
        if float(session["relationship_strength"] or 0) >= 70:
            tags.append("高频互动")
        if float(session["bidirectional_ratio"] or 0) >= 0.65:
            tags.append("双向活跃")
        if int(session["active_days"] or 0) >= 30:
            tags.append("长期联系")
        category_labels = {
            "image": "图片往来",
            "file": "文件往来",
            "video": "视频往来",
            "voice": "语音往来",
            "link": "链接分享",
        }
        for category, label in category_labels.items():
            if category_counts.get(category) and len(tags) < 4:
                tags.append(label)

        return {
            "session": {
                "username": session["username"],
                "display_name": session["display_name"],
                "avatar_url": self._avatar_url(session["username"]),
                "is_group": bool(session["is_group"]),
                "alias": session["alias"] or "",
                "remark": session["remark"] or "",
                "nickname": session["nick_name"] or session["display_name"],
                "remote_avatar_available": bool(
                    session["big_head_url"] or session["small_head_url"]
                ),
                "message_count": int(session["message_count"]),
                "active_days": int(session["active_days"]),
                "first_interaction": self._iso_time(session["first_time"]),
                "last_active": self._iso_time(session["last_time"]),
                "sent_count": int(session["sent_count"]),
                "received_count": int(session["received_count"]),
                "bidirectional_ratio": round(
                    float(session["bidirectional_ratio"]), 4
                ),
                "relationship_strength": round(
                    float(session["relationship_strength"]), 2
                ),
                "tags": tags,
            },
            "messages": format_rows(message_rows),
            "files": format_rows(file_rows),
            "year": selected_year,
            "available_years": available_years,
            "heatmap": [
                {
                    "date": row["local_date"],
                    "count": int(row["count"]),
                    "sent": int(row["sent"] or 0),
                    "received": int(row["received"] or 0),
                }
                for row in heatmap_rows
            ],
            "stats": {
                "messages": int(totals["messages"] or 0),
                "quality": int(totals["quality"] or 0),
                "quoted": int(totals["quoted"] or 0),
                "active_days": int(totals["active_days"] or 0),
            },
            "selected_day": {
                "date": day,
                "messages": int(day_stats["messages"] or 0) if day_stats else 0,
                "sent": int(day_stats["sent"] or 0) if day_stats else 0,
                "received": int(day_stats["received"] or 0) if day_stats else 0,
                "quality": int(day_stats["quality"] or 0) if day_stats else 0,
                "quoted": int(day_stats["quoted"] or 0) if day_stats else 0,
                "speakers": int(day_stats["speakers"] or 0) if day_stats else 0,
                "first_time": self._iso_time(day_stats["first_time"])
                if day_stats
                else None,
                "last_time": self._iso_time(day_stats["last_time"])
                if day_stats
                else None,
            },
            "categories": [
                {"category": row["category"], "count": int(row["count"])}
                for row in category_rows
            ],
            "snapshot_time": self.snapshot_time,
            "metric_basis": "All session messages, heatmap cells, files, and metrics come from the immutable local snapshot.",
        }

    def contact_graph(self, limit: int = 30) -> dict[str, Any]:
        limit = max(2, min(limit, 100))
        payload = self.contacts(limit=limit, offset=0)
        profile = self.profile()["profile"]
        nodes = [
            {
                "id": self.account,
                "name": profile["display_name"],
                "value": 100,
                "category": "self",
                "avatar_url": profile["avatar_url"],
            }
        ]
        links = []
        for item in payload["items"]:
            nodes.append(
                {
                    "id": item["username"],
                    "name": item["display_name"],
                    "value": item["relationship_strength"],
                    "category": "group" if item["is_group"] else "contact",
                    "avatar_url": item["avatar_url"],
                    "message_count": item["message_count"],
                }
            )
            links.append(
                {
                    "source": self.account,
                    "target": item["username"],
                    "value": item["relationship_strength"],
                    "message_count": item["message_count"],
                }
            )
        return {
            "nodes": nodes,
            "links": links,
            "snapshot_time": self.snapshot_time,
            "metric_basis": "Star graph centered on the current account; edge weight is deterministic relationship strength.",
        }

    def _extract_keywords(self, texts: Iterable[str], limit: int = 40) -> list[dict[str, Any]]:
        joined = "\n".join(text for text in texts if text)
        if not joined:
            return []
        try:
            import jieba  # type: ignore[import-not-found]
            import jieba.analyse  # type: ignore[import-not-found]

            weighted = jieba.analyse.extract_tags(
                joined, topK=limit * 2, withWeight=True
            )
            counts = Counter(
                token.strip().casefold()
                for token in jieba.cut(joined)
                if len(token.strip()) > 1
            )
            return [
                {
                    "word": word,
                    "count": counts.get(word.casefold(), 0),
                    "weight": round(float(weight), 6),
                }
                for word, weight in weighted
                if word.casefold() not in STOP_WORDS
                and is_valid_keyword_token(word)
            ][:limit]
        except ImportError:
            counter: Counter[str] = Counter()
            for match in WORD_PATTERN.finditer(joined):
                token = match.group(0).casefold()
                if token in STOP_WORDS or not is_valid_keyword_token(token):
                    continue
                if "\u4e00" <= token[0] <= "\u9fff" and len(token) > 4:
                    for index in range(len(token) - 1):
                        gram = token[index : index + 2]
                        if gram not in STOP_WORDS:
                            counter[gram] += 1
                else:
                    counter[token] += 1
            return [
                {"word": word, "count": count}
                for word, count in counter.most_common(limit)
            ]

    def _format_resource(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        resource_available = bool(item.get("available"))
        supported = bool(item.get("browser_supported"))
        storage_kind = str(item["kind"])
        mime_type = str(item.get("mime_type") or "")
        kind = "image" if storage_kind == "video" and mime_type.startswith("image/") else storage_kind
        rowid = int(item["rowid"])
        available = resource_available and supported
        url = f"/api/resources/{storage_kind}/{rowid}" if available else None
        return {
            "id": item["resource_key"],
            "kind": kind,
            "resource_kind": storage_kind,
            "rowid": rowid,
            "md5": item.get("md5"),
            "file_name": item.get("file_name"),
            "file_size": item.get("file_size"),
            "modify_time": self._iso_time(item.get("modify_time")),
            "available": available,
            "browser_supported": supported,
            "resource_available": resource_available,
            "mime_type": mime_type,
            "url": url,
            "thumbnail_url": url
            if available and supported and str(item.get("mime_type") or "").startswith("image/")
            else None,
        }

    def _media_keyword_sample(self, connection) -> tuple[list[str], dict[str, Any]]:
        texts: list[str] = []
        characters = 0
        # Keep keyword extraction bounded even for multi-million-row archives.
        rows = connection.execute('''
            SELECT category,substr(content,1,4000) content,substr(title,1,4000) title,
                   substr(description,1,4000) description,substr(summary,1,4000) summary,
                   substr(reference_content,1,4000) reference_content
            FROM messages WHERE decoded=1
                AND category NOT IN ('system','image','video','voice','sticker')
            ORDER BY create_time DESC,sort_seq DESC,message_key DESC LIMIT 20000
        ''')
        for row in rows:
            candidates = ([row['content']] if row['category'] == 'text' else
                          [row['title'],row['description'],row['reference_content'],row['summary']])
            if not any(candidates):
                candidates = [row['content']]
            cleaned = list(dict.fromkeys(text for text in
                (normalize_human_text(value) for value in candidates) if len(text) >= 2))
            text = ' '.join(cleaned)[:min(4000, 2000000-characters)]
            if text:
                texts.append(text)
                characters += len(text)
            if characters >= 2000000:
                break
        return texts, {'sampled': True, 'documents': len(texts), 'characters': characters,
                       'message_limit': 20000, 'character_limit': 2000000,
                       'document_character_limit': 4000, 'order': 'newest_first'}

    def media(self, limit: int = 24) -> dict[str, Any]:
        limit = max(1, min(limit, 200))
        with self.cache() as connection:
            metadata = self._metadata(connection)
            category_counts = {
                row["category"]: int(row["count"])
                for row in connection.execute(
                    "SELECT category,count(*) count FROM messages GROUP BY category"
                )
            }
            resource_rows = connection.execute(
                """
                SELECT * FROM resources
                ORDER BY browser_supported DESC,available DESC,modify_time DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            file_rows = connection.execute(
                "SELECT * FROM resources WHERE kind='file' ORDER BY modify_time DESC LIMIT 20"
            ).fetchall()
            link_rows = connection.execute(
                """
                SELECT * FROM messages WHERE category='link'
                ORDER BY create_time DESC LIMIT 20
                """
            ).fetchall()
            human_texts, sample_basis = self._media_keyword_sample(connection)
        keywords = self._extract_keywords(human_texts, 40)
        topic_map = {
            "文本交流": category_counts.get("text", 0),
            "图片影像": category_counts.get("image", 0)
            + category_counts.get("video", 0),
            "文件资料": category_counts.get("file", 0),
            "链接分享": category_counts.get("link", 0),
            "引用讨论": category_counts.get("quote", 0),
        }
        link_items = []
        for row in link_rows:
            message = self._format_message(row)
            link_items.append(
                {
                    "id": message["id"],
                    "kind": "link",
                    "title": message["title"] or message["summary"],
                    "snippet": message["description"] or message["summary"],
                    "url": message["url"],
                    "source": message["session_name"],
                    "create_time": message["create_time"],
                    "available": bool(message["url"]),
                }
            )
        return {
            "counts": {
                "images": category_counts.get("image", 0),
                "videos": category_counts.get("video", 0),
                "files": category_counts.get("file", 0),
                "links": category_counts.get("link", 0),
                "voice": category_counts.get("voice", 0),
                "indexed_resources": int(metadata.get("resource_count", 0)),
                "resource_messages": int(metadata.get("resource_info_count", 0)),
                "resource_details": int(metadata.get("message_resource_detail_count", 0)),
                "linked_resource_details": int(metadata.get("linked_message_resource_count", 0)),
            },
            "items": [self._format_resource(row) for row in resource_rows],
            "keywords": keywords,
            "keyword_basis": {
                **sample_basis,
                "algorithm": "jieba_tfidf_with_token_counts"
                if importlib.util.find_spec("jieba")
                else "unicode_frequency_and_chinese_bigrams",
                "documents": len(human_texts),
                "source_fields": [
                    "clean_text_content",
                    "parsed_title",
                    "parsed_description",
                    "parsed_summary",
                    "parsed_reference_content",
                ],
                "stop_words_version": "2026-08-local-v2",
            },
            "topics": [
                {"name": name, "value": value}
                for name, value in topic_map.items()
                if value
            ],
            "files": [self._format_resource(row) for row in file_rows],
            "links": link_items,
            "ocr": [],
            "ocr_status": {
                "enabled": False,
                "reason": "No local OCR engine result cache is available for this snapshot.",
            },
            "snapshot_time": self.snapshot_time,
            "metric_basis": "Media counts use parsed message types; availability is checked against the original read-only account directory.",
            "data_quality": self._data_quality(metadata),
        }

    @staticmethod
    def _longest_streak(days: Sequence[str]) -> int:
        if not days:
            return 0
        parsed = sorted({date.fromisoformat(value) for value in days})
        longest = current = 1
        for previous, item in zip(parsed, parsed[1:]):
            if item == previous + timedelta(days=1):
                current += 1
                longest = max(longest, current)
            else:
                current = 1
        return longest

    def contributions(self, year: int | None = None, selected_date: str | None = None) -> dict[str, Any]:
        with self.cache() as connection:
            years = [
                int(row[0])
                for row in connection.execute(
                    "SELECT DISTINCT substr(local_date,1,4) year FROM daily ORDER BY year"
                )
            ]
            if not years:
                return {
                    "year": year or datetime.now(self.timezone).year,
                    "available_years": [],
                    "kpis": {"messages": 0, "active_days": 0, "longest_streak": 0, "quoted": 0},
                    "heatmap": [],
                    "selected_day": None,
                    "snapshot_time": self.snapshot_time,
                    "metric_basis": "No messages are present in the snapshot.",
                }
            selected_year = year if year in years else years[-1]
            rows = connection.execute(
                "SELECT * FROM daily WHERE substr(local_date,1,4)=? ORDER BY local_date",
                (str(selected_year),),
            ).fetchall()
            day_values = [row["local_date"] for row in rows]
            chosen = selected_date if selected_date in day_values else day_values[-1]
            chosen_row = next(row for row in rows if row["local_date"] == chosen)
            messages = connection.execute(
                """
                SELECT * FROM messages WHERE local_date=?
                ORDER BY create_time,sort_seq LIMIT 80
                """,
                (chosen,),
            ).fetchall()
            hours = connection.execute(
                """
                SELECT local_hour,count(*) count FROM messages
                WHERE local_date=? GROUP BY local_hour ORDER BY local_hour
                """,
                (chosen,),
            ).fetchall()
            time_bounds = connection.execute(
                "SELECT min(create_time),max(create_time) FROM messages WHERE local_date=?",
                (chosen,),
            ).fetchone()
            outgoing_quotes = connection.execute(
                "SELECT count(*) FROM messages WHERE local_date=? AND is_self=1 AND category='quote'",
                (chosen,),
            ).fetchone()[0]
        kpis = {
            "messages": sum(int(row["message_count"]) for row in rows),
            "active_days": len(rows),
            "longest_streak": self._longest_streak(day_values),
            "quoted": sum(int(row["quoted_count"]) for row in rows),
        }
        peak = max(hours, key=lambda row: row["count"], default=None)
        insight = (
            f"最活跃时段为 {int(peak['local_hour']):02d}:00，记录 {peak['count']} 条消息。"
            if peak
            else "当天没有可用消息。"
        )
        formatted_messages = [self._format_message(row) for row in messages[:12]]
        active_start = self._iso_time(time_bounds[0])
        active_end = self._iso_time(time_bounds[1])
        span_seconds = int(time_bounds[1] - time_bounds[0]) if time_bounds[0] and time_bounds[1] else 0
        return {
            "year": selected_year,
            "available_years": years,
            "kpis": kpis,
            "heatmap": [
                {
                    "date": row["local_date"],
                    "count": row["message_count"],
                    "sent": row["sent_count"],
                    "received": row["received_count"],
                    "quoted": row["quoted_count"],
                    "quality": row["quality_count"],
                    "episodes": row["episode_count"],
                    "turns": row["turn_count"],
                }
                for row in rows
            ],
            "selected_day": {
                "date": chosen,
                "message_count": chosen_row["message_count"],
                "sent": chosen_row["sent_count"],
                "received": chosen_row["received_count"],
                "episodes": chosen_row["episode_count"],
                "turns": chosen_row["turn_count"],
                "quality": chosen_row["quality_count"],
                "quoted": chosen_row["quoted_count"],
                "outgoing_quotes": outgoing_quotes,
                "active_sessions": chosen_row["active_sessions"],
                "active_period": f"{active_start[11:16]} - {active_end[11:16]}" if active_start and active_end else None,
                "duration": f"跨度 {span_seconds // 3600} 小时 {(span_seconds % 3600) // 60} 分钟",
                "hourly": [
                    {"hour": int(row["local_hour"]), "count": row["count"]}
                    for row in hours
                ],
                "summary": formatted_messages,
                "messages": formatted_messages,
                "insight": insight,
                "insights": [insight] if insight else [],
            },
            "snapshot_time": self.snapshot_time,
            "metric_basis": "Episodes split after 30 minutes; turns merge consecutive messages by the same sender; effective contribution score >= 2.",
        }

    def conversations(
        self,
        limit: int = 50,
        offset: int = 0,
        start_date: str | None = None,
        end_date: str | None = None,
        session: str | None = None,
        category: str | None = None,
        query: str | None = None,
        hour: int | None = None,
        message_id: str | None = None,
        sort: str = "desc",
    ) -> dict[str, Any]:
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        where = ["1=1"]
        parameters: list[Any] = []
        if start_date:
            where.append("local_date>=?")
            parameters.append(start_date)
        if end_date:
            where.append("local_date<=?")
            parameters.append(end_date)
        if session:
            where.append("session_username=?")
            parameters.append(session)
        if category:
            where.append("categories LIKE ?")
            parameters.append(f'%"{category}":%')
        if query:
            where.append(
                """
                EXISTS (
                    SELECT 1 FROM messages query_message
                    WHERE query_message.session_username=episodes.session_username
                      AND query_message.create_time BETWEEN episodes.start_time AND episodes.end_time
                      AND (query_message.summary LIKE ? OR query_message.content LIKE ?)
                )
                """
            )
            pattern = f"%{query.strip()}%"
            parameters.extend((pattern, pattern))
        if hour is not None:
            where.append(
                """
                EXISTS (
                    SELECT 1 FROM messages hour_message
                    WHERE hour_message.session_username=episodes.session_username
                      AND hour_message.create_time BETWEEN episodes.start_time AND episodes.end_time
                      AND hour_message.local_hour=?
                )
                """
            )
            parameters.append(hour)
        direction = "ASC" if sort.casefold() == "asc" else "DESC"

        message_where = ["1=1"]
        message_parameters: list[Any] = []
        if start_date:
            message_where.append("local_date>=?")
            message_parameters.append(start_date)
        if end_date:
            message_where.append("local_date<=?")
            message_parameters.append(end_date)
        if session:
            message_where.append("session_username=?")
            message_parameters.append(session)
        if category:
            message_where.append("category=?")
            message_parameters.append(category)
        if query:
            message_where.append("(summary LIKE ? OR content LIKE ?)")
            pattern = f"%{query.strip()}%"
            message_parameters.extend((pattern, pattern))
        if hour is not None:
            message_where.append("local_hour=?")
            message_parameters.append(hour)
        message_clause = " AND ".join(message_where)

        with self.cache() as connection:
            if message_id:
                selected_message = connection.execute(
                    "SELECT session_username,create_time FROM messages WHERE message_key=?",
                    (message_id,),
                ).fetchone()
                if selected_message:
                    where.extend(
                        (
                            "session_username=?",
                            "start_time<=?",
                            "end_time>=?",
                        )
                    )
                    parameters.extend(
                        (
                            selected_message["session_username"],
                            selected_message["create_time"],
                            selected_message["create_time"],
                        )
                    )
            clause = " AND ".join(where)
            total = connection.execute(
                f"SELECT count(*) FROM episodes WHERE {clause}", parameters
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT * FROM episodes WHERE {clause}
                ORDER BY start_time {direction} LIMIT ? OFFSET ?
                """,
                (*parameters, limit, offset),
            ).fetchall()
            hourly_rows = connection.execute(
                f"""
                SELECT local_hour,count(*) count FROM messages
                WHERE {message_clause} GROUP BY local_hour ORDER BY local_hour
                """,
                message_parameters,
            ).fetchall()
            latest_date = connection.execute(
                f"SELECT max(local_date) FROM messages WHERE {message_clause}",
                message_parameters,
            ).fetchone()[0]
            trend_start = (
                (date.fromisoformat(latest_date) - timedelta(days=29)).isoformat()
                if latest_date
                else date.today().isoformat()
            )
            trend_rows = connection.execute(
                f"""
                SELECT local_date,count(*) count FROM messages
                WHERE {message_clause} AND local_date>=?
                GROUP BY local_date ORDER BY local_date
                """,
                (*message_parameters, trend_start),
            ).fetchall()
            episode_trend_rows = connection.execute(
                f"""
                SELECT local_date,count(*) episodes FROM episodes
                WHERE {clause} AND local_date>=?
                GROUP BY local_date ORDER BY local_date
                """,
                (*parameters, trend_start),
            ).fetchall()
            quoted_rows = connection.execute(
                f"""
                SELECT * FROM messages WHERE {message_clause} AND quote_count>0
                ORDER BY quote_count DESC,create_time DESC LIMIT 5
                """,
                message_parameters,
            ).fetchall()
            quoted_total = connection.execute(
                f"SELECT count(*) FROM messages WHERE {message_clause} AND quote_count>0",
                message_parameters,
            ).fetchone()[0]
            episode_totals = connection.execute(
                f"SELECT coalesce(sum(turn_count),0) turns FROM episodes WHERE {clause}",
                parameters,
            ).fetchone()
        episodes_by_date = {row["local_date"]: row["episodes"] for row in episode_trend_rows}
        count_by_date = {row["local_date"]: row["count"] for row in trend_rows}
        trend = []
        if latest_date:
            start = date.fromisoformat(trend_start)
            for index in range(30):
                day = (start + timedelta(days=index)).isoformat()
                trend.append(
                    {
                        "date": day,
                        "count": int(count_by_date.get(day, 0)),
                        "episodes": int(episodes_by_date.get(day, 0)),
                    }
                )
        items = []
        for row in rows:
            categories = _loads(row["categories"], {})
            items.append(
                {
                    "id": row["episode_id"],
                    "date": row["local_date"],
                    "start_time": self._iso_time(row["start_time"]),
                    "end_time": self._iso_time(row["end_time"]),
                    "session_username": row["session_username"],
                    "session_name": row["session_name"],
                    "avatar_url": self._avatar_url(row["session_username"]),
                    "message_count": row["message_count"],
                    "turn_count": row["turn_count"],
                    "summary": row["summary"],
                    "categories": list(categories),
                    "category_counts": categories,
                }
            )
        hourly_counts = {int(row["local_hour"]): int(row["count"]) for row in hourly_rows}
        peak_hour = max(
            range(24),
            key=lambda hour: hourly_counts.get(hour, 0) + hourly_counts.get((hour + 1) % 24, 0),
            default=0,
        )
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "hourly": [
                {"hour": int(row["local_hour"]), "count": row["count"]}
                for row in hourly_rows
            ],
            "trend": trend,
            "top_quoted": [self._format_message(row) for row in quoted_rows],
            "kpis": {
                "sessions": total,
                "peak_period": f"{peak_hour:02d}:00-{(peak_hour + 2) % 24:02d}:00",
                "average_turns": round(episode_totals["turns"] / total, 2) if total else None,
                "quoted_messages": quoted_total,
            },
            "snapshot_time": self.snapshot_time,
            "metric_basis": "Episodes are split when adjacent messages in the same session are more than 30 minutes apart.",
        }

    def episode_messages(self, episode_id: str) -> dict[str, Any] | None:
        with self.cache() as connection:
            episode = connection.execute(
                "SELECT * FROM episodes WHERE episode_id=?", (episode_id,)
            ).fetchone()
            if episode is None:
                return None
            rows = connection.execute(
                """
                SELECT * FROM messages
                WHERE session_username=? AND create_time BETWEEN ? AND ?
                ORDER BY create_time,sort_seq,local_id
                """,
                (
                    episode["session_username"],
                    episode["start_time"],
                    episode["end_time"],
                ),
            ).fetchall()
            resources = self._resource_details_for_messages(
                connection, [row["message_key"] for row in rows]
            )
        resource_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in resources:
            if item.get("message_key"):
                resource_map[item["message_key"]].append(item)
        formatted_messages = []
        for row in rows:
            item = self._format_message(row)
            item["resources"] = resource_map.get(row["message_key"], [])
            formatted_messages.append(item)
        return {
            "episode": {
                "id": episode["episode_id"],
                "session_username": episode["session_username"],
                "session_name": episode["session_name"],
                "start_time": self._iso_time(episode["start_time"]),
                "end_time": self._iso_time(episode["end_time"]),
                "message_count": episode["message_count"],
                "turn_count": episode["turn_count"],
            },
            "messages": formatted_messages,
            "resources": resources,
            "snapshot_time": self.snapshot_time,
        }

    def message(self, message_key: str) -> dict[str, Any] | None:
        with self.cache() as connection:
            row = connection.execute(
                "SELECT * FROM messages WHERE message_key=?", (message_key,)
            ).fetchone()
            if row is None:
                return None
            episode_id = self._message_episode_id(connection, row)
            target = None
            target_episode_id = None
            if row["reference_server_id"]:
                target = connection.execute(
                    "SELECT * FROM messages WHERE server_id=? ORDER BY create_time LIMIT 1",
                    (row["reference_server_id"],),
                ).fetchone()
                if target is not None:
                    target_episode_id = self._message_episode_id(connection, target)
            quoting_rows = []
            if row["server_id"]:
                quoting_rows = connection.execute(
                    """
                    SELECT quote_message.*,
                           (
                               SELECT episode.episode_id FROM episodes episode
                               WHERE episode.session_username=quote_message.session_username
                                 AND quote_message.create_time BETWEEN episode.start_time AND episode.end_time
                               ORDER BY episode.start_time DESC LIMIT 1
                           ) episode_id
                    FROM messages quote_message
                    WHERE quote_message.reference_server_id IS NOT NULL
                      AND CAST(quote_message.reference_server_id AS TEXT)=CAST(? AS TEXT)
                      AND quote_message.message_key<>?
                    ORDER BY quote_message.create_time DESC,quote_message.sort_seq DESC
                    LIMIT 50
                    """,
                    (row["server_id"], message_key),
                ).fetchall()
            resources = self._resource_details_for_messages(connection, [message_key])
        result = self._format_message(row)
        result["episode_id"] = episode_id
        if target is not None:
            referenced_message = self._format_message(target)
            referenced_message["episode_id"] = target_episode_id
            result["referenced_message"] = referenced_message
        else:
            result["referenced_message"] = None
        result["quoting_messages"] = [
            {
                **self._format_message(quote_row),
                "episode_id": quote_row["episode_id"],
                "date": quote_row["local_date"],
            }
            for quote_row in quoting_rows
        ]
        result["quote_chain_limit"] = 50
        result["quote_chain_truncated"] = int(row["quote_count"] or 0) > len(quoting_rows)
        result["resources"] = resources
        return result

    @staticmethod
    def _message_episode_id(
        connection: sqlite3.Connection, row: sqlite3.Row | dict[str, Any]
    ) -> str | None:
        item = dict(row)
        episode = connection.execute(
            """
            SELECT episode_id FROM episodes
            WHERE session_username=? AND ? BETWEEN start_time AND end_time
            ORDER BY start_time DESC LIMIT 1
            """,
            (item.get("session_username"), item.get("create_time")),
        ).fetchone()
        return str(episode["episode_id"]) if episode else None

    def _resource_details_for_messages(
        self, connection: sqlite3.Connection, message_keys: Sequence[str]
    ) -> list[dict[str, Any]]:
        if not message_keys:
            return []
        results: list[dict[str, Any]] = []
        for key_chunk in _chunks(list(message_keys), 500):
            placeholders = ",".join("?" for _ in key_chunk)
            rows = connection.execute(
                f"""
                SELECT mr.*,
                       r.resource_key matched_resource_key,
                       r.kind matched_kind,
                       r.rowid matched_rowid,
                       r.file_name matched_file_name,
                       r.available matched_available,
                       r.browser_supported matched_supported,
                       r.mime_type matched_mime_type
                FROM message_resources mr
                LEFT JOIN resources r ON r.resource_key = (
                    SELECT candidate.resource_key FROM resources candidate
                    WHERE candidate.file_size=mr.size AND mr.size>0
                    ORDER BY candidate.available DESC,candidate.modify_time DESC
                    LIMIT 1
                )
                WHERE mr.message_key IN ({placeholders})
                ORDER BY mr.create_time,mr.resource_id
                """,
                list(key_chunk),
            ).fetchall()
            for row in rows:
                item = dict(row)
                resource_url = None
                if item.get("matched_available") and item.get("matched_supported"):
                    resource_url = (
                        f"/api/resources/{item['matched_kind']}/{item['matched_rowid']}"
                    )
                results.append(
                    {
                        "resource_id": item["resource_id"],
                        "message_key": item["message_key"],
                        "detail_type": item["detail_type"],
                        "size": item["size"],
                        "status": item["status"],
                        "data_index": item["data_index"],
                        "metadata": _loads(item["metadata_text"], []),
                        "matched_resource_id": item.get("matched_resource_key"),
                        "kind": item.get("matched_kind"),
                        "file_name": item.get("matched_file_name"),
                        "resource_available": bool(item.get("matched_available")),
                        "browser_supported": bool(item.get("matched_supported")),
                        "url": resource_url,
                        "mime_type": item.get("matched_mime_type"),
                    }
                )
        return results

    def export_conversations(
        self,
        format_name: str,
        start_date: str | None = None,
        end_date: str | None = None,
        session: str | None = None,
        category: str | None = None,
        query: str | None = None,
        hour: int | None = None,
        order: str = "desc",
    ) -> tuple[bytes, str, str]:
        where = ["1=1"]
        parameters: list[Any] = []
        for column, value in (
            ("local_date>=?", start_date),
            ("local_date<=?", end_date),
            ("session_username=?", session),
            ("category=?", category),
        ):
            if value:
                where.append(column)
                parameters.append(value)
        if query:
            where.append("(summary LIKE ? OR content LIKE ?)")
            pattern = f"%{query.strip()}%"
            parameters.extend((pattern, pattern))
        if hour is not None:
            where.append("local_hour=?")
            parameters.append(hour)
        direction = "DESC" if order.casefold() == "desc" else "ASC"
        with self.cache() as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    f"""
                    SELECT message_key,session_username,session_name,sender_username,
                           sender_name,is_self,create_time,local_date,category,
                           summary,content,decoded,quote_count
                    FROM messages WHERE {' AND '.join(where)}
                    ORDER BY create_time {direction},sort_seq {direction}
                    """,
                    parameters,
                )
            ]
        for row in rows:
            row["create_time_iso"] = self._iso_time(row["create_time"])
            row["is_self"] = bool(row["is_self"])
            row["decoded"] = bool(row["decoded"])
        if format_name == "json":
            data = json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8")
            return data, "application/json; charset=utf-8", "wechat-messages.json"
        if format_name == "html":
            body = "".join(
                "<tr>"
                + "".join(
                    f"<td>{html.escape(str(row.get(key, '')))}</td>"
                    for key in (
                        "create_time_iso",
                        "session_name",
                        "sender_name",
                        "category",
                        "summary",
                    )
                )
                + "</tr>"
                for row in rows
            )
            data = (
                "<!doctype html><meta charset='utf-8'><title>WeChat messages</title>"
                "<table><thead><tr><th>Time</th><th>Session</th><th>Sender</th>"
                "<th>Type</th><th>Summary</th></tr></thead><tbody>"
                + body
                + "</tbody></table>"
            ).encode("utf-8")
            return data, "text/html; charset=utf-8", "wechat-messages.html"
        output = io.StringIO(newline="")
        fields = list(rows[0]) if rows else ["message_key"]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: _csv_cell(value) for key, value in row.items()} for row in rows)
        return (
            output.getvalue().encode("utf-8-sig"),
            "text/csv; charset=utf-8",
            "wechat-messages.csv",
        )

    @staticmethod
    def _match_query(connection: sqlite3.Connection, sql: str, query: str, limit: int) -> list[sqlite3.Row]:
        try:
            return connection.execute(sql, (query, limit)).fetchall()
        except sqlite3.OperationalError:
            quoted = '"' + query.replace('"', '""') + '"'
            return connection.execute(sql, (quoted, limit)).fetchall()

    def _search_message_fts(self, query: str, limit: int) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        with open_fts(
            self.database("message/message_fts.db"),
            self.settings.fts_extension,
            self.settings.weixin_dll,
        ) as connection:
            for shard in range(4):
                table = f"message_fts_v4_{shard}"
                sql = (
                    f'SELECT rowid,acontent,message_local_id,sort_seq,local_type,'
                    f'session_id,sender_id,create_time,rank FROM "{table}" '
                    f'WHERE "{table}" MATCH ? ORDER BY rank LIMIT ?'
                )
                for row in self._match_query(connection, sql, query, limit):
                    item = dict(row)
                    item["shard"] = shard
                    results.append(item)
        results.sort(key=lambda item: float(item.get("rank") or 0.0))
        return results[:limit]

    def _search_contact_fts(self, query: str, limit: int) -> list[dict[str, Any]]:
        with open_fts(
            self.database("contact/contact_fts.db"),
            self.settings.fts_extension,
            self.settings.weixin_dll,
        ) as connection:
            origin_sql = (
                'SELECT rowid,search_key,local_type,rank FROM "contact_fts_v4" '
                'WHERE "contact_fts_v4" MATCH ? ORDER BY rank LIMIT ?'
            )
            results = [dict(row) for row in self._match_query(connection, origin_sql, query, limit)]
            for item in results:
                item["mode"] = "origin"
            pinyin_sql = (
                'SELECT rowid FROM "contact_fts_pinyin_v4" '
                'WHERE "contact_fts_pinyin_v4" MATCH ? LIMIT ?'
            )
            pinyin_rows = self._match_query(connection, pinyin_sql, query, limit)
            rowids = [int(row[0]) for row in pinyin_rows]
            if rowids:
                placeholders = ",".join("?" for _ in rowids)
                content_rows = connection.execute(
                    "SELECT id rowid,c0 search_key,c1 local_type "
                    f"FROM contact_fts_v4_content WHERE id IN ({placeholders})",
                    rowids,
                ).fetchall()
                by_id = {int(row["rowid"]): dict(row) for row in content_rows}
                for rowid in rowids:
                    item = by_id.get(rowid)
                    if item:
                        item["rank"] = None
                        item["mode"] = "pinyin"
                        results.append(item)
        deduped: dict[int, dict[str, Any]] = {}
        for item in results:
            deduped.setdefault(int(item["rowid"]), item)
        return list(deduped.values())[:limit]

    def _search_favorite_fts(self, query: str, limit: int) -> list[dict[str, Any]]:
        with open_fts(
            self.database("favorite/favorite_fts.db"),
            self.settings.fts_extension,
            self.settings.weixin_dll,
        ) as connection:
            sql = (
                'SELECT rowid,content,local_id,update_time,type,rank FROM "fav_fts_v1" '
                'WHERE "fav_fts_v1" MATCH ? ORDER BY rank LIMIT ?'
            )
            return [dict(row) for row in self._match_query(connection, sql, query, limit)]

    def search(self, query: str, scope: str = "all", limit: int = 20) -> dict[str, Any]:
        query = query.strip()
        limit = max(1, min(limit, 200))
        if not query:
            return {
                "query": query,
                "items": [],
                "fts_used": False,
                "errors": [],
                "snapshot_time": self.snapshot_time,
            }
        scopes = {scope.casefold()} if scope.casefold() != "all" else {"messages", "contacts", "favorites"}
        errors: list[str] = []
        fts_used = True
        items: list[dict[str, Any]] = []
        with self.cache() as cache:
            if "messages" in scopes:
                try:
                    fts_rows = self._search_message_fts(query, limit)
                    for hit in fts_rows:
                        row = cache.execute(
                            """
                            SELECT * FROM messages
                            WHERE session_id=? AND local_id=?
                            ORDER BY abs(sort_seq-?) LIMIT 1
                            """,
                            (
                                hit.get("session_id"),
                                hit.get("message_local_id"),
                                hit.get("sort_seq") or 0,
                            ),
                        ).fetchone()
                        if row:
                            formatted = self._format_message(row)
                            items.append(
                                {
                                    "kind": "message",
                                    "id": formatted["id"],
                                    "title": formatted["session_name"],
                                    "snippet": hit.get("acontent") or formatted["summary"],
                                    "create_time": formatted["create_time"],
                                    "session_username": formatted["session_username"],
                                    "session_name": formatted["session_name"],
                                    "avatar_url": self._avatar_url(formatted["session_username"]),
                                    "category": formatted["category"],
                                    "rank": hit.get("rank"),
                                }
                            )
                except (FileNotFoundError, OSError, sqlite3.DatabaseError) as exc:
                    fts_used = False
                    errors.append(f"message_fts:{exc}")
                    pattern = f"%{query}%"
                    rows = cache.execute(
                        """
                        SELECT * FROM messages WHERE content LIKE ? OR summary LIKE ?
                        ORDER BY create_time DESC LIMIT ?
                        """,
                        (pattern, pattern, limit),
                    ).fetchall()
                    for row in rows:
                        formatted = self._format_message(row)
                        items.append(
                            {
                                "kind": "message",
                                "id": formatted["id"],
                                "title": formatted["session_name"],
                                "snippet": formatted["summary"],
                                "create_time": formatted["create_time"],
                                "session_username": formatted["session_username"],
                                "session_name": formatted["session_name"],
                                "avatar_url": self._avatar_url(formatted["session_username"]),
                                "category": formatted["category"],
                                "rank": None,
                            }
                        )

                # Native realtime events are materialized into the analytics cache,
                # not the immutable source FTS database. Always merge cache LIKE
                # matches so a healthy static FTS index cannot hide new messages.
                pattern = f"%{query}%"
                realtime_rows = cache.execute(
                    """
                    SELECT * FROM messages
                    WHERE content LIKE ? OR summary LIKE ?
                    ORDER BY create_time DESC,sort_seq DESC LIMIT ?
                    """,
                    (pattern, pattern, limit),
                ).fetchall()
                for row in realtime_rows:
                    formatted = self._format_message(row)
                    items.append(
                        {
                            "kind": "message",
                            "id": formatted["id"],
                            "title": formatted["session_name"],
                            "snippet": formatted["summary"] or formatted["content"],
                            "create_time": formatted["create_time"],
                            "session_username": formatted["session_username"],
                            "session_name": formatted["session_name"],
                            "avatar_url": self._avatar_url(
                                formatted["session_username"]
                            ),
                            "category": formatted["category"],
                            "rank": None,
                        }
                    )

            if "contacts" in scopes:
                try:
                    contact_hits = self._search_contact_fts(query, limit)
                    for hit in contact_hits:
                        contact = cache.execute(
                            "SELECT * FROM contacts WHERE id=?", (hit["rowid"],)
                        ).fetchone()
                        if contact:
                            items.append(
                                {
                                    "kind": "contact",
                                    "id": contact["username"],
                                    "title": contact["display_name"],
                                    "snippet": str(hit.get("search_key") or "").replace("\b", " ").strip(),
                                    "create_time": None,
                                    "session_username": contact["username"],
                                    "session_name": contact["display_name"],
                                    "avatar_url": self._avatar_url(contact["username"]),
                                    "match_mode": hit.get("mode"),
                                    "rank": hit.get("rank"),
                                }
                            )
                except (FileNotFoundError, OSError, sqlite3.DatabaseError) as exc:
                    fts_used = False
                    errors.append(f"contact_fts:{exc}")
                    pattern = f"%{query}%"
                    rows = cache.execute(
                        """
                        SELECT * FROM contacts
                        WHERE display_name LIKE ? OR username LIKE ? OR alias LIKE ?
                        LIMIT ?
                        """,
                        (pattern, pattern, pattern, limit),
                    ).fetchall()
                    for contact in rows:
                        items.append(
                            {
                                "kind": "contact",
                                "id": contact["username"],
                                "title": contact["display_name"],
                                "snippet": contact["alias"] or contact["username"],
                                "create_time": None,
                                "session_username": contact["username"],
                                "session_name": contact["display_name"],
                                "avatar_url": self._avatar_url(contact["username"]),
                                "match_mode": "like",
                                "rank": None,
                            }
                        )

            if "favorites" in scopes:
                try:
                    for hit in self._search_favorite_fts(query, limit):
                        items.append(
                            {
                                "kind": "favorite",
                                "id": str(hit.get("local_id") or hit.get("rowid")),
                                "title": "收藏",
                                "snippet": str(hit.get("content") or "")[:500],
                                "create_time": self._iso_time(hit.get("update_time")),
                                "session_username": None,
                                "session_name": None,
                                "avatar_url": self._avatar_url(self.account),
                                "category": str(hit.get("type") or "favorite"),
                                "rank": hit.get("rank"),
                            }
                        )
                except (FileNotFoundError, OSError, sqlite3.DatabaseError) as exc:
                    fts_used = False
                    errors.append(f"favorite_fts:{exc}")

        deduped: dict[tuple[str, str], dict[str, Any]] = {}
        for item in items:
            key = (str(item.get("kind") or ""), str(item.get("id") or ""))
            deduped.setdefault(key, item)
        items = list(deduped.values())
        kind_order = {"message": 0, "contact": 1, "favorite": 2}
        items.sort(
            key=lambda item: (
                kind_order.get(item["kind"], 9),
                item.get("rank") is None,
                float(item.get("rank") or 0.0),
            )
        )
        return {
            "query": query,
            "scope": scope,
            "items": items[:limit],
            "fts_used": fts_used,
            "errors": errors,
            "snapshot_time": self.snapshot_time,
            "metric_basis": "Weixin MMFtsTokenizer is loaded per immutable FTS connection; pinyin results resolve rowids through contact_fts_v4_content.",
        }

    def validate_fts(self) -> dict[str, Any]:
        paths = {
            "message": "message/message_fts.db",
            "contact": "contact/contact_fts.db",
            "favorite": "favorite/favorite_fts.db",
        }
        results: dict[str, Any] = {}
        for name, relative in paths.items():
            with open_fts(
                self.database(relative),
                self.settings.fts_extension,
                self.settings.weixin_dll,
            ) as connection:
                results[name] = {
                    "quick_check": [row[0] for row in connection.execute("PRAGMA quick_check")],
                    "integrity_check": [row[0] for row in connection.execute("PRAGMA integrity_check")],
                }
        return results

    def avatar(self, username: str) -> tuple[bytes, str, str]:
        with open_readonly(self.database("head_image/head_image.db")) as source:
            row = source.execute(
                "SELECT md5,image_buffer FROM head_image WHERE username=?", (username,)
            ).fetchone()
        if row and row["image_buffer"]:
            payload = bytes(row["image_buffer"])
            if payload.startswith(b"\xff\xd8\xff"):
                mime = "image/jpeg"
            elif payload.startswith(b"\x89PNG\r\n\x1a\n"):
                mime = "image/png"
            elif payload.startswith((b"GIF87a", b"GIF89a")):
                mime = "image/gif"
            else:
                mime = "application/octet-stream"
            etag = row["md5"] or hashlib.sha256(payload).hexdigest()
            return payload, mime, str(etag)

        with self.cache() as connection:
            contact = connection.execute(
                "SELECT display_name FROM contacts WHERE username=?", (username,)
            ).fetchone()
        name = (contact["display_name"] if contact else username) or "?"
        initials = name[:2]
        digest = hashlib.sha256(username.encode("utf-8")).hexdigest()
        hue = int(digest[:4], 16) % 360
        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">
<rect width="128" height="128" rx="24" fill="hsl({hue} 42% 45%)"/>
<text x="64" y="70" text-anchor="middle" dominant-baseline="middle" fill="white" font-family="Microsoft YaHei UI,Noto Sans SC,sans-serif" font-size="42">{html.escape(initials)}</text>
</svg>"""
        return svg.encode("utf-8"), "image/svg+xml", digest

    def resource(self, kind: str, rowid: int) -> tuple[Path, str, str] | None:
        if kind not in {"image", "video", "file"}:
            return None
        with self.cache() as connection:
            row = connection.execute(
                "SELECT * FROM resources WHERE kind=? AND rowid=?", (kind, rowid)
            ).fetchone()
        if row is None or not row["available"] or not row["file_path"]:
            return None
        path = Path(row["file_path"]).resolve()
        if self.account_root is None or not path.is_relative_to(self.account_root):
            return None
        if not path.is_file():
            return None
        return path, row["mime_type"] or "application/octet-stream", row["file_name"]
