from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sqlite3
import struct
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any


SQLITE_HEADER = b"SQLite format 3\0"
REQUIRED_COLUMNS = {
    "user_name_",
    "type_",
    "timestamp_",
    "encrypt_user_name_",
    "content_",
    "is_sender_",
    "ticket_",
    "scene_",
}


class FMessageSourceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FMessageRequest:
    rowid: int
    username: str
    message_type: int
    timestamp: int
    encrypt_username: str
    content: str
    is_sender: int
    ticket: str
    scene: int

    def realtime_event(self, account: str) -> dict[str, Any]:
        identity = "\0".join(
            (self.encrypt_username, str(self.timestamp), self.ticket)
        )
        event_id = "fmessage-" + hashlib.sha256(
            identity.encode("utf-8")
        ).hexdigest()
        peer_id = self.username or self.encrypt_username
        return {
            "event_id": event_id,
            "event_type": "friend.request",
            "source": "fmessage_table",
            "schema_version": 1,
            "message_type": self.message_type,
            "timestamp_unix": self.timestamp,
            "direction": "incoming",
            "is_incoming": True,
            "is_outgoing": False,
            "peer_id": peer_id,
            "sender_id": peer_id,
            "raw_from": peer_id,
            "raw_to": account,
            "content": self.content,
            "encrypt_username": self.encrypt_username,
            "candidate_username": self.username,
            "ticket": self.ticket,
            "scene": self.scene,
            "friend_flag": 0,
            "accept_required": True,
            "fmessage_rowid": self.rowid,
        }


@dataclass(frozen=True, slots=True)
class FMessageBatch:
    requests: tuple[FMessageRequest, ...]
    high_watermark: int


class FMessageSource:
    def __init__(
        self,
        report_path: Path,
        repository_root: Path,
        snapshot_path: Path,
    ) -> None:
        self.report_path = report_path.resolve()
        self.repository_root = repository_root.resolve()
        self.snapshot_path = snapshot_path.resolve()
        self._lock = threading.RLock()
        self._fingerprint: tuple[Any, ...] | None = None
        self._source_path: Path | None = None
        self._last_refresh_ms: int | None = None
        self._refresh_count = 0
        self._last_error: str | None = None
        self._decrypt_tool: ModuleType | None = None

    @staticmethod
    def _stat_fingerprint(path: Path) -> tuple[bool, int, int]:
        try:
            value = path.stat()
        except FileNotFoundError:
            return False, 0, 0
        return True, int(value.st_size), int(value.st_mtime_ns)

    def _configuration(self) -> tuple[Path, Path]:
        try:
            with self.report_path.open("r", encoding="utf-8") as stream:
                report = json.load(stream)
        except (OSError, json.JSONDecodeError) as exc:
            raise FMessageSourceError(
                f"cannot read decryption report {self.report_path}: {exc}"
            ) from exc
        source_root = str(report.get("source_root") or "").strip()
        key_manifest = str(report.get("key_manifest") or "").strip()
        if not source_root or not key_manifest:
            raise FMessageSourceError(
                "decryption report is missing source_root or key_manifest"
            )
        source = Path(source_root).expanduser()
        if not source.is_absolute():
            source = self.report_path.parent / source
        manifest = Path(key_manifest).expanduser()
        if not manifest.is_absolute():
            manifest = self.report_path.parent / manifest
        return (source / "general" / "general.db").resolve(), manifest.resolve()

    def _load_decrypt_tool(self) -> ModuleType:
        if self._decrypt_tool is not None:
            return self._decrypt_tool
        tool_path = self.repository_root / "python" / "tools" / "wechat_db_decrypt.py"
        module_name = "_wechat_db_decrypt_runtime"
        module = sys.modules.get(module_name)
        if module is None:
            spec = importlib.util.spec_from_file_location(module_name, tool_path)
            if spec is None or spec.loader is None:
                raise FMessageSourceError(f"cannot load decrypt tool {tool_path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
            except BaseException as exc:
                sys.modules.pop(module_name, None)
                raise FMessageSourceError(
                    f"cannot import decrypt tool {tool_path}: {exc}"
                ) from exc
        self._decrypt_tool = module
        return module

    @staticmethod
    def _is_plain_sqlite(path: Path) -> bool:
        try:
            with path.open("rb") as stream:
                return stream.read(len(SQLITE_HEADER)) == SQLITE_HEADER
        except OSError:
            return False

    def _refresh_locked(self) -> None:
        source, manifest_path = self._configuration()
        if not source.is_file():
            raise FMessageSourceError(f"FMessage source database is missing: {source}")
        fingerprint = (
            str(source),
            self._stat_fingerprint(source),
            self._stat_fingerprint(Path(str(source) + "-wal")),
        )
        if fingerprint == self._fingerprint and self.snapshot_path.is_file():
            return

        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.snapshot_path.with_name(
            f".{self.snapshot_path.name}.{os.getpid()}.{time.time_ns()}.refresh"
        )
        try:
            if self._is_plain_sqlite(source):
                self._backup_plain_database(source, temporary)
            else:
                if not manifest_path.is_file():
                    raise FMessageSourceError(
                        f"SQLCipher key manifest is missing: {manifest_path}"
                    )
                tool = self._load_decrypt_tool()
                manifest = tool.load_manifest(manifest_path)
                result = tool.decrypt_database(
                    source,
                    temporary,
                    manifest,
                    force=True,
                    ignore_wal=True,
                )
                if int(result.get("wal", {}).get("commit_frames") or 0) > 0:
                    self._apply_encrypted_wal(
                        source,
                        temporary,
                        manifest,
                        result["wal"],
                        tool,
                    )
            self._validate_database(temporary)
            os.replace(temporary, self.snapshot_path)
        except Exception as exc:
            raise FMessageSourceError(
                f"cannot refresh FMessage snapshot from {source}: {exc}"
            ) from exc
        finally:
            temporary.unlink(missing_ok=True)
            for suffix in ("-wal", "-shm"):
                Path(str(temporary) + suffix).unlink(missing_ok=True)

        self._source_path = source
        self._fingerprint = fingerprint
        self._last_refresh_ms = time.time_ns() // 1_000_000
        self._refresh_count += 1

    @staticmethod
    def _backup_plain_database(source: Path, output: Path) -> None:
        source_uri = source.as_uri() + "?mode=ro"
        source_connection = sqlite3.connect(source_uri, uri=True, timeout=5.0)
        output_connection = sqlite3.connect(output, timeout=5.0)
        try:
            with output_connection:
                source_connection.backup(output_connection)
        finally:
            output_connection.close()
            source_connection.close()

    @staticmethod
    def _apply_encrypted_wal(
        source: Path,
        output: Path,
        manifest: dict[str, Any],
        wal_status: dict[str, Any],
        tool: ModuleType,
    ) -> None:
        wal_path = Path(str(source) + "-wal")
        data = wal_path.read_bytes()
        stat = wal_path.stat()
        expected_size = int(wal_status.get("size") or 0)
        expected_mtime = int(wal_status.get("mtime_ns") or 0)
        expected_hash = str(wal_status.get("sha256") or "")
        if (
            len(data) != expected_size
            or int(stat.st_mtime_ns) != expected_mtime
            or hashlib.sha256(data).hexdigest() != expected_hash
        ):
            raise FMessageSourceError("encrypted WAL changed before it could be applied")

        params = manifest["sqlcipher"]
        page_size = int(params["page_size"])
        frame_size = 24 + page_size
        last_commit = int(wal_status.get("last_commit_frame") or 0)
        database_size = int(wal_status.get("last_commit_database_size") or 0)
        if last_commit <= 0 or database_size <= 0:
            return
        if len(data) < 32 + last_commit * frame_size:
            raise FMessageSourceError("encrypted WAL is shorter than its committed frame set")

        with source.open("rb") as stream:
            selected = tool.choose_key(manifest, stream.read(page_size))
        key = bytes.fromhex(selected["key_hex"])
        hmac_key = bytes.fromhex(selected["hmac_key_hex"])
        with output.open("r+b") as stream:
            for frame_index in range(last_commit):
                offset = 32 + frame_index * frame_size
                frame = data[offset : offset + frame_size]
                page_number = struct.unpack_from(">I", frame, 0)[0]
                if page_number <= 0 or page_number > database_size:
                    raise FMessageSourceError(
                        f"encrypted WAL contains invalid page {page_number}"
                    )
                plain = tool.decrypt_page(
                    frame[24:],
                    page_number,
                    key,
                    hmac_key,
                    params,
                )
                stream.seek((page_number - 1) * page_size)
                stream.write(plain)
            stream.truncate(database_size * page_size)
            stream.flush()
            os.fsync(stream.fileno())

        current = tool.inspect_wal(source, page_size)
        if tool.wal_snapshot_id(current) != tool.wal_snapshot_id(wal_status):
            raise FMessageSourceError("encrypted WAL changed while it was applied")
        tool.verify_plain_database(output)

    def _validate_snapshot(self) -> None:
        self._validate_database(self.snapshot_path)

    @staticmethod
    def _validate_database(path: Path) -> None:
        try:
            uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
            connection = sqlite3.connect(uri, uri=True, timeout=5.0)
            try:
                columns = {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(FMessageTable)")
                }
                quick_check = connection.execute("PRAGMA quick_check").fetchone()
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise FMessageSourceError(
                f"cannot read FMessage snapshot {path}: {exc}"
            ) from exc
        missing = sorted(REQUIRED_COLUMNS - columns)
        if missing:
            raise FMessageSourceError(
                "FMessageTable is missing required columns: " + ", ".join(missing)
            )
        if not quick_check or str(quick_check[0]) != "ok":
            raise FMessageSourceError(f"FMessage snapshot quick_check failed: {path}")

    def _connect(self) -> sqlite3.Connection:
        uri = self.snapshot_path.as_uri() + "?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True, timeout=5.0)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _run(self, operation: Any) -> Any:
        with self._lock:
            try:
                self._refresh_locked()
                result = operation()
            except Exception as exc:
                self._last_error = str(exc)
                raise
            self._last_error = None
            return result

    def latest_rowid(self) -> int:
        def query() -> int:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT max(rowid) FROM FMessageTable"
                ).fetchone()
            return int((row or [0])[0] or 0)

        return int(self._run(query))

    def scan(self, after_rowid: int, limit: int = 256) -> FMessageBatch:
        after_rowid = max(0, int(after_rowid))
        limit = max(1, min(int(limit), 2048))

        def query() -> FMessageBatch:
            with self._connection() as connection:
                rows = connection.execute(
                    """
                    SELECT rowid,user_name_,type_,timestamp_,encrypt_user_name_,
                           content_,is_sender_,ticket_,scene_
                    FROM FMessageTable
                    WHERE rowid>?
                    ORDER BY rowid
                    LIMIT ?
                    """,
                    (after_rowid, limit),
                ).fetchall()
            requests: list[FMessageRequest] = []
            for row in rows:
                message_type = int(row["type_"] or 0)
                is_sender = int(row["is_sender_"] or 0)
                encrypt_username = str(row["encrypt_user_name_"] or "").strip()
                ticket = str(row["ticket_"] or "").strip()
                scene = int(row["scene_"] if row["scene_"] is not None else -1)
                if (
                    message_type != 37
                    or is_sender != 0
                    or not encrypt_username
                    or not ticket
                    or scene < 0
                ):
                    continue
                requests.append(
                    FMessageRequest(
                        rowid=int(row["rowid"]),
                        username=str(row["user_name_"] or "").strip(),
                        message_type=message_type,
                        timestamp=int(row["timestamp_"] or 0),
                        encrypt_username=encrypt_username,
                        content=str(row["content_"] or ""),
                        is_sender=is_sender,
                        ticket=ticket,
                        scene=scene,
                    )
                )
            high_watermark = int(rows[-1]["rowid"]) if rows else after_rowid
            return FMessageBatch(tuple(requests), high_watermark)

        return self._run(query)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "available": self.snapshot_path.is_file(),
                "source": str(self._source_path) if self._source_path else None,
                "snapshot": str(self.snapshot_path),
                "last_refresh_ms": self._last_refresh_ms,
                "refresh_count": self._refresh_count,
                "last_error": self._last_error,
            }
