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
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any


SQLITE_HEADER = b"SQLite format 3\0"
REQUIRED_TIMELINE_COLUMNS = {"tid", "user_name", "content", "pack_info_buf"}


class SnsSnapshotSyncError(RuntimeError):
    """Raised when the live Moments database cannot be refreshed safely."""


class SnsSnapshotSync:
    """Materialize the live encrypted SNS database as an atomic SQLite snapshot."""

    def __init__(
        self,
        report_path: Path,
        repository_root: Path,
        snapshot_path: Path,
        *,
        lock: threading.RLock | None = None,
    ) -> None:
        self.report_path = report_path.resolve()
        self.repository_root = repository_root.resolve()
        self.snapshot_path = snapshot_path.resolve()
        self._lock = lock or threading.RLock()
        self._fingerprint: tuple[Any, ...] | None = None
        self._source_path: Path | None = None
        self._last_attempt: str | None = None
        self._last_success: str | None = None
        self._last_error: str | None = None
        self._last_changed = False
        self._refresh_count = 0
        self._generation = 0
        self._last_wal: dict[str, Any] = {}
        self._decrypt_tool: ModuleType | None = None

    @staticmethod
    def _stat_fingerprint(path: Path) -> tuple[bool, int, int]:
        try:
            value = path.stat()
        except OSError:
            return False, 0, 0
        return True, int(value.st_size), int(value.st_mtime_ns)

    def _configuration(self) -> tuple[Path, Path | None]:
        try:
            with self.report_path.open("r", encoding="utf-8") as stream:
                report = json.load(stream)
        except (OSError, json.JSONDecodeError) as exc:
            raise SnsSnapshotSyncError(
                f"cannot read decryption report {self.report_path}: {exc}"
            ) from exc
        source_root = str(report.get("source_root") or "").strip()
        if not source_root:
            raise SnsSnapshotSyncError("decryption report is missing source_root")
        source_root_path = Path(source_root).expanduser()
        if not source_root_path.is_absolute():
            source_root_path = self.report_path.parent / source_root_path

        key_manifest = str(report.get("key_manifest") or "").strip()
        manifest_path: Path | None = None
        if key_manifest:
            manifest_path = Path(key_manifest).expanduser()
            if not manifest_path.is_absolute():
                manifest_path = self.report_path.parent / manifest_path
            manifest_path = manifest_path.resolve()
        return (source_root_path / "sns" / "sns.db").resolve(), manifest_path

    def _source_fingerprint(self, source: Path) -> tuple[Any, ...]:
        return (
            str(source),
            self._stat_fingerprint(source),
            self._stat_fingerprint(Path(str(source) + "-wal")),
        )

    def _load_decrypt_tool(self) -> ModuleType:
        if self._decrypt_tool is not None:
            return self._decrypt_tool
        tool_path = self.repository_root / "python" / "tools" / "wechat_db_decrypt.py"
        module_name = "_wechat_db_decrypt_sns_runtime"
        module = sys.modules.get(module_name)
        if module is None:
            spec = importlib.util.spec_from_file_location(module_name, tool_path)
            if spec is None or spec.loader is None:
                raise SnsSnapshotSyncError(f"cannot load decrypt tool {tool_path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
            except BaseException as exc:
                sys.modules.pop(module_name, None)
                raise SnsSnapshotSyncError(
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
        verification_kwargs: dict[str, Any] | None = None,
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
            raise SnsSnapshotSyncError("encrypted SNS WAL changed before apply")

        params = manifest["sqlcipher"]
        page_size = int(params["page_size"])
        frame_size = 24 + page_size
        last_commit = int(wal_status.get("last_commit_frame") or 0)
        database_size = int(wal_status.get("last_commit_database_size") or 0)
        if last_commit <= 0 or database_size <= 0:
            return
        if len(data) < 32 + last_commit * frame_size:
            raise SnsSnapshotSyncError("encrypted SNS WAL is shorter than its commit")

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
                    raise SnsSnapshotSyncError(
                        f"encrypted SNS WAL contains invalid page {page_number}"
                    )
                plain = tool.decrypt_page(
                    frame[24:], page_number, key, hmac_key, params
                )
                stream.seek((page_number - 1) * page_size)
                stream.write(plain)
            stream.truncate(database_size * page_size)
            stream.flush()
            os.fsync(stream.fileno())

        current = tool.inspect_wal(source, page_size)
        if tool.wal_snapshot_id(current) != tool.wal_snapshot_id(wal_status):
            raise SnsSnapshotSyncError("encrypted SNS WAL changed during apply")
        tool.verify_plain_database(output, **(verification_kwargs or {}))

    @staticmethod
    def _validate_database(path: Path) -> None:
        try:
            uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
            connection = sqlite3.connect(uri, uri=True, timeout=5.0)
            try:
                columns = {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(SnsTimeLine)")
                }
                quick_check = connection.execute("PRAGMA quick_check").fetchone()
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise SnsSnapshotSyncError(
                f"cannot read refreshed SNS snapshot {path}: {exc}"
            ) from exc
        missing = sorted(REQUIRED_TIMELINE_COLUMNS - columns)
        if missing:
            raise SnsSnapshotSyncError(
                "SnsTimeLine is missing required columns: " + ", ".join(missing)
            )
        if not quick_check or str(quick_check[0]) != "ok":
            raise SnsSnapshotSyncError(f"SNS snapshot quick_check failed: {path}")

    @staticmethod
    def _validate_wal_status(wal_status: dict[str, Any]) -> None:
        if (
            wal_status.get("exists")
            and int(wal_status.get("size") or 0) > 0
            and not wal_status.get("header_valid")
        ):
            reason = str(wal_status.get("stop_reason") or "unknown")
            raise SnsSnapshotSyncError(
                f"encrypted SNS WAL is not safely readable: {reason}"
            )

    def refresh(self, *, force: bool = False) -> dict[str, Any]:
        with self._lock:
            self._last_attempt = datetime.now(UTC).isoformat()
            self._last_changed = False
            try:
                source, manifest_path = self._configuration()
                if not source.is_file():
                    raise SnsSnapshotSyncError(f"SNS source database is missing: {source}")
                fingerprint = self._source_fingerprint(source)
                if (
                    not force
                    and fingerprint == self._fingerprint
                    and self.snapshot_path.is_file()
                ):
                    self._source_path = source
                    self._last_error = None
                    return self.status()

                self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.snapshot_path.with_name(
                    f".{self.snapshot_path.name}.{os.getpid()}.{time.time_ns()}.refresh"
                )
                try:
                    if source == self.snapshot_path:
                        self._validate_database(source)
                        wal_status: dict[str, Any] = {}
                    elif self._is_plain_sqlite(source):
                        self._backup_plain_database(source, temporary)
                        self._validate_database(temporary)
                        wal_status = {}
                    else:
                        if manifest_path is None or not manifest_path.is_file():
                            raise SnsSnapshotSyncError(
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
                        wal_status = dict(result.get("wal") or {})
                        self._validate_wal_status(wal_status)
                        if int(wal_status.get("commit_frames") or 0) > 0:
                            self._apply_encrypted_wal(
                                source, temporary, manifest, wal_status, tool
                            )
                        self._validate_database(temporary)

                    if source != self.snapshot_path:
                        for suffix in ("-wal", "-shm"):
                            Path(str(self.snapshot_path) + suffix).unlink(missing_ok=True)
                        replace_deadline = time.monotonic() + 3.0
                        while True:
                            try:
                                os.replace(temporary, self.snapshot_path)
                                break
                            except PermissionError:
                                if time.monotonic() >= replace_deadline:
                                    raise
                                time.sleep(0.05)
                finally:
                    temporary.unlink(missing_ok=True)
                    for suffix in ("-wal", "-shm"):
                        Path(str(temporary) + suffix).unlink(missing_ok=True)

                self._source_path = source
                # Remember the input version used to build this snapshot. If
                # Weixin changes the source after the build, the next poll must
                # observe that newer fingerprint instead of treating it as done.
                self._fingerprint = fingerprint
                self._last_success = datetime.now(UTC).isoformat()
                self._last_error = None
                self._last_changed = True
                self._last_wal = wal_status
                self._refresh_count += 1
                self._generation += 1
            except Exception as exc:
                self._last_error = str(exc)
                if isinstance(exc, SnsSnapshotSyncError):
                    raise
                raise SnsSnapshotSyncError(
                    f"cannot refresh SNS snapshot from {self._source_path or self.snapshot_path}: {exc}"
                ) from exc
            return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            configuration_error: str | None = None
            source = self._source_path
            try:
                configured_source, _ = self._configuration()
                source = configured_source
            except SnsSnapshotSyncError as exc:
                configuration_error = str(exc)
            source_stat = self._stat_fingerprint(source) if source else (False, 0, 0)
            wal_stat = (
                self._stat_fingerprint(Path(str(source) + "-wal"))
                if source
                else (False, 0, 0)
            )
            snapshot_stat = self._stat_fingerprint(self.snapshot_path)
            return {
                "configured": configuration_error is None,
                "configuration_error": configuration_error,
                "source_available": bool(source_stat[0]),
                "source_path": str(source) if source else None,
                "snapshot_path": str(self.snapshot_path),
                "source_size": source_stat[1],
                "source_mtime_ns": source_stat[2] or None,
                "source_wal_size": wal_stat[1],
                "source_wal_mtime_ns": wal_stat[2] or None,
                "snapshot_size": snapshot_stat[1],
                "snapshot_mtime_ns": snapshot_stat[2] or None,
                "last_attempt": self._last_attempt,
                "last_success": self._last_success,
                "last_error": self._last_error,
                "changed": self._last_changed,
                "refresh_count": self._refresh_count,
                "generation": self._generation,
                "wal": {
                    key: self._last_wal.get(key)
                    for key in (
                        "exists",
                        "size",
                        "header_valid",
                        "valid_frames",
                        "commit_frames",
                        "stop_reason",
                    )
                    if key in self._last_wal
                },
            }


__all__ = ["SnsSnapshotSync", "SnsSnapshotSyncError"]
