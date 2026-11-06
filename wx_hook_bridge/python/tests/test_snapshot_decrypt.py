import hashlib
import json
import os
import sqlite3
from argparse import Namespace
from pathlib import Path
from typing import Any

import pytest

from tools import wechat_db_decrypt as decryptor
from tools.wechat_db_decrypt import (
    DecryptError,
    build_decryption_report,
    infer_account,
    validate_decrypt_paths,
)


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI')
def test_unattended_manifest_is_sealed_and_readable_by_current_user(tmp_path):
    path = tmp_path / 'keys.dpapi'
    manifest = {'schema_version': 1, 'keys': [{'key_hex': '12' * 32}], 'sqlcipher': {}}
    decryptor.write_protected_manifest(path, manifest)
    assert path.read_bytes().startswith(b'WXDPAPI1\n')
    assert b'1212121212121212' not in path.read_bytes()
    assert decryptor.load_manifest(path) == manifest


@pytest.mark.parametrize('message,allowed', [('SQL logic error', True), ('database disk image is malformed', False)])
def test_native_tokenizer_limit_is_explicit_and_does_not_mask_corruption(tmp_path, monkeypatch, message, allowed):
    path = tmp_path / 'sample.db'
    connection = sqlite3.connect(path)
    connection.execute('CREATE TABLE sample(value)')
    connection.commit()

    class Connection:
        def execute(self, sql):
            if 'MMFtsTokenizer' in sql:
                return type('Cursor', (), {'fetchone': lambda self: (1,)})()
            if sql in ('PRAGMA quick_check', 'PRAGMA integrity_check'):
                raise sqlite3.OperationalError(message)
            return connection.execute(sql)

        def close(self):
            pass

    monkeypatch.setattr(decryptor.sqlite3, 'connect', lambda *args, **kwargs: Connection())
    try:
        if allowed:
            result = decryptor.verify_plain_database(path, fts_extension=tmp_path / 'missing.dll', allow_unsupported_tokenizer=True)
            assert result['quick_check'].startswith('skipped_unsupported_tokenizer:')
            assert result['integrity_check'] != 'ok'
        else:
            with pytest.raises(sqlite3.DatabaseError, match='malformed'):
                decryptor.verify_plain_database(path, fts_extension=tmp_path / 'missing.dll', allow_unsupported_tokenizer=True)
        with pytest.raises(sqlite3.DatabaseError):
            decryptor.verify_plain_database(path, fts_extension=tmp_path / 'missing.dll')
    finally:
        connection.close()


def _directory_decrypt_args(
    source: Path, output_dir: Path, report: Path, keys: Path
) -> Namespace:
    return Namespace(
        source=source,
        keys=keys,
        output=None,
        output_dir=output_dir,
        account="wxid_test",
        report=report,
        no_report=False,
        force=False,
        ignore_wal=False,
        fts_extension=None,
        weixin_dll=None,
    )


def _fake_decryption_result(source: Path, output: Path) -> dict[str, Any]:
    payload = b"SQLite format 3\0" + bytes(4080)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(payload)
    return {
        "input": str(source.resolve()),
        "output": str(output.resolve()),
        "bytes": len(payload),
        "pages": 1,
        "quick_check": "ok",
        "integrity_check": "ok",
        "page_count": 1,
        "page_size": 4096,
        "freelist_count": 0,
        "schema_objects": 0,
        "schema_preview": [],
        "wal": {"exists": False},
    }


def _write_test_manifest(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sqlcipher": {"reserve_size": 80},
                "keys": [{"key_hex": "00" * 32}],
            }
        ),
        encoding="utf-8",
    )


def _write_database_tree(source: Path) -> tuple[Path, Path]:
    first = source / "contact" / "contact.db"
    second = source / "message" / "nested" / "message.kvdb"
    first.parent.mkdir(parents=True, exist_ok=True)
    second.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(b"encrypted-contact")
    second.write_bytes(b"encrypted-message")
    return first, second


def test_infer_account_from_weixin_data_directory(tmp_path: Path) -> None:
    source = tmp_path / "wxid_example1234_abcd" / "db_storage"
    source.mkdir(parents=True)
    assert infer_account(source) == "wxid_example1234"


def test_build_decryption_report_uses_relative_paths(tmp_path: Path) -> None:
    source_root = tmp_path / "encrypted"
    output_root = tmp_path / "decrypted"
    output = output_root / "contact" / "contact.db"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"SQLite format 3\0" + bytes(4080))

    report = build_decryption_report(
        account="wxid_test",
        source_root=source_root,
        key_manifest=tmp_path / "keys.json",
        output_root=output_root,
        results=[
            {
                "output": str(output),
                "bytes": output.stat().st_size,
                "pages": 1,
                "quick_check": "ok",
                "integrity_check": "ok",
                "page_count": 1,
                "page_size": 4096,
                "freelist_count": 0,
                "schema_objects": 0,
                "schema_preview": [],
                "wal": {"exists": False},
            }
        ],
        failures=[],
        reserve_size=80,
    )

    assert report["account"] == "wxid_test"
    assert report["decrypted_database_count"] == 1
    assert report["files"][0]["path"] == "contact/contact.db"
    assert report["files"][0]["sha256"] == hashlib.sha256(
        output.read_bytes()
    ).hexdigest()


def test_rejects_plaintext_and_report_paths_inside_source_tree(tmp_path: Path) -> None:
    source = tmp_path / "encrypted"
    encrypted = source / "message" / "message.db"
    encrypted.parent.mkdir(parents=True)
    encrypted.write_bytes(b"encrypted")
    keys = tmp_path / "keys.json"

    with pytest.raises(DecryptError, match="outside the encrypted source tree"):
        validate_decrypt_paths(
            source=source,
            key_manifest=keys,
            jobs=[(encrypted, source / "plain" / "message.db")],
            report=tmp_path / "decryption_report.json",
        )

    with pytest.raises(DecryptError, match="report path must be outside"):
        validate_decrypt_paths(
            source=source,
            key_manifest=keys,
            jobs=[(encrypted, tmp_path / "plain" / "message.db")],
            report=source / "decryption_report.json",
        )

    single_source = tmp_path / "single" / "message.db"
    single_source.parent.mkdir()
    with pytest.raises(DecryptError, match="output path must be outside"):
        validate_decrypt_paths(
            source=single_source,
            key_manifest=keys,
            jobs=[(single_source, single_source.parent / "plain.db")],
            report=tmp_path / "single-report.json",
        )


def test_rejects_report_collisions_with_inputs_outputs_and_keys(tmp_path: Path) -> None:
    source = tmp_path / "encrypted.db"
    output = tmp_path / "plain.db"
    keys = tmp_path / "keys.json"

    for report in (source, output, keys):
        with pytest.raises(DecryptError, match="report path collides"):
            validate_decrypt_paths(
                source=source,
                key_manifest=keys,
                jobs=[(source, output)],
                report=report,
            )


def test_directory_failure_preserves_previous_snapshot_and_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "wxid_test_abcd" / "db_storage"
    _write_database_tree(source)
    output_dir = tmp_path / "snapshots" / "all"
    previous = tmp_path / "snapshots" / "all.previous"
    previous.mkdir(parents=True)
    (previous / "sentinel.txt").write_text("old snapshot", encoding="utf-8")
    report = tmp_path / "decryption_report.json"
    old_report = {"account": "wxid_test", "output_root": str(previous)}
    report.write_text(json.dumps(old_report), encoding="utf-8")
    keys = tmp_path / "keys.json"
    _write_test_manifest(keys)

    def fake_decrypt(
        input_path: Path,
        output_path: Path,
        manifest: dict[str, Any],
        force: bool,
        ignore_wal: bool,
        fts_extension: Path | None = None,
        weixin_dll: Path | None = None,
    ) -> dict[str, Any]:
        if input_path.name == "message.kvdb":
            raise DecryptError("synthetic database failure")
        return _fake_decryption_result(input_path, output_path)

    monkeypatch.setattr(decryptor, "decrypt_database", fake_decrypt)
    args = _directory_decrypt_args(source, output_dir, report, keys)

    assert decryptor.command_decrypt(args) == 1
    assert (previous / "sentinel.txt").read_text(encoding="utf-8") == "old snapshot"
    assert report.read_text(encoding="utf-8") == json.dumps(old_report)
    assert not output_dir.exists()
    assert not list(output_dir.parent.glob(".all.*.staging"))
    assert not [path for path in output_dir.parent.glob("all.*") if path != previous]


def test_successful_directory_run_publishes_versioned_snapshot_and_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "wxid_test_abcd" / "db_storage"
    _write_database_tree(source)
    output_dir = tmp_path / "snapshots" / "all"
    previous = tmp_path / "snapshots" / "all.previous"
    previous.mkdir(parents=True)
    (previous / "sentinel.txt").write_text("old snapshot", encoding="utf-8")
    report = tmp_path / "decryption_report.json"
    keys = tmp_path / "keys.json"
    _write_test_manifest(keys)

    def fake_decrypt(
        input_path: Path,
        output_path: Path,
        manifest: dict[str, Any],
        force: bool,
        ignore_wal: bool,
        fts_extension: Path | None = None,
        weixin_dll: Path | None = None,
    ) -> dict[str, Any]:
        return _fake_decryption_result(input_path, output_path)

    monkeypatch.setattr(decryptor, "decrypt_database", fake_decrypt)
    args = _directory_decrypt_args(source, output_dir, report, keys)

    assert decryptor.command_decrypt(args) == 0
    output = json.loads(capsys.readouterr().out)
    published_root = Path(json.loads(report.read_text(encoding="utf-8"))["output_root"])
    published_report = json.loads(report.read_text(encoding="utf-8"))

    assert published_root.is_dir()
    assert published_root.name.startswith("all.")
    assert published_root != output_dir
    assert published_report["output_root"] == str(published_root)
    assert sorted(item["path"] for item in published_report["files"]) == [
        "contact/contact.db",
        "message/nested/message.kvdb",
    ]
    assert all((published_root / item["path"]).is_file() for item in published_report["files"])
    assert (previous / "sentinel.txt").read_text(encoding="utf-8") == "old snapshot"
    assert all(Path(item["output"]).is_relative_to(published_root) for item in output["decrypted"])


def test_replace_failure_preserves_existing_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging = tmp_path / ".all.staging"
    staging.mkdir()
    (staging / "new.txt").write_text("new", encoding="utf-8")
    published = tmp_path / "all"
    published.mkdir()
    sentinel = published / "old.txt"
    sentinel.write_text("old", encoding="utf-8")
    original_replace = decryptor.os.replace

    def fail_first_replace(source: Path, destination: Path) -> None:
        if Path(source) == published:
            raise PermissionError("synthetic active-reader failure")
        original_replace(source, destination)

    monkeypatch.setattr(decryptor.os, "replace", fail_first_replace)

    with pytest.raises(PermissionError, match="active-reader"):
        decryptor.publish_directory_snapshot(
            staging,
            published,
            None,
            None,
            replace_existing=True,
        )

    assert sentinel.read_text(encoding="utf-8") == "old"
    assert (staging / "new.txt").read_text(encoding="utf-8") == "new"
    assert not list(tmp_path.glob(".all.*.backup"))


def test_report_failure_removes_unpublished_version_and_keeps_old_report(
    tmp_path: Path,
) -> None:
    staging = tmp_path / ".all.snapshot.staging"
    staging.mkdir()
    (staging / "new.txt").write_text("new", encoding="utf-8")
    published = tmp_path / "all.snapshot"
    report = tmp_path / "decryption_report.json"
    report.write_text('{"active":"old"}', encoding="utf-8")

    def fail_report() -> dict[str, Any]:
        raise OSError("synthetic report failure")

    with pytest.raises(OSError, match="report failure"):
        decryptor.publish_directory_snapshot(
            staging,
            published,
            report,
            fail_report,
        )

    assert not published.exists()
    assert report.read_text(encoding="utf-8") == '{"active":"old"}'


@pytest.mark.parametrize("use_environment", [True, False])
def test_verify_plain_database_selects_environment_or_default_weixin_dll(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    use_environment: bool,
) -> None:
    database = tmp_path / "message_fts.db"
    database.write_bytes(bytes(4096))
    extension = tmp_path / "weixin_fts_tokenizer.dll"
    extension.write_bytes(b"extension")
    environment_dll = tmp_path / "configured" / "Weixin.dll"
    environment_dll.parent.mkdir()
    environment_dll.write_bytes(b"configured")
    default_dll = tmp_path / "default" / "Weixin.dll"
    default_dll.parent.mkdir()
    default_dll.write_bytes(b"default")
    monkeypatch.setattr(decryptor, "DEFAULT_WEIXIN_DLL", default_dll)
    if use_environment:
        monkeypatch.setenv("WEIXIN_DLL_PATH", str(environment_dll))
        expected_dll = environment_dll
    else:
        monkeypatch.delenv("WEIXIN_DLL_PATH", raising=False)
        expected_dll = default_dll

    class FakeConnection:
        def __init__(self) -> None:
            self.extension_states: list[bool] = []
            self.loaded_extensions: list[str] = []

        def execute(self, sql: str) -> Any:
            if "LIKE '%MMFtsTokenizer%'" in sql:
                return FakeCursor([(1,)])
            if sql in {"PRAGMA quick_check", "PRAGMA integrity_check"}:
                return FakeCursor([("ok",)])
            if sql == "PRAGMA page_count":
                return FakeCursor([(1,)])
            if sql == "PRAGMA page_size":
                return FakeCursor([(4096,)])
            if sql == "PRAGMA freelist_count":
                return FakeCursor([(0,)])
            if sql == "SELECT count(*) FROM sqlite_master":
                return FakeCursor([(1,)])
            if sql.startswith("SELECT type, name FROM sqlite_master"):
                return FakeCursor([("table", "message_fts_v4_0")])
            raise AssertionError(f"unexpected SQL: {sql}")

        def enable_load_extension(self, enabled: bool) -> None:
            self.extension_states.append(enabled)

        def load_extension(self, path: str) -> None:
            self.loaded_extensions.append(path)

        def close(self) -> None:
            pass

    class FakeCursor:
        def __init__(self, rows: list[tuple[Any, ...]]) -> None:
            self.rows = rows

        def fetchone(self) -> tuple[Any, ...] | None:
            return self.rows[0] if self.rows else None

        def fetchall(self) -> list[tuple[Any, ...]]:
            return self.rows

        def __iter__(self):
            return iter(self.rows)

    connection = FakeConnection()
    monkeypatch.setattr(decryptor.sqlite3, "connect", lambda *args, **kwargs: connection)

    result = decryptor.verify_plain_database(database, fts_extension=extension)

    assert result["fts_extension_loaded"] is True
    assert connection.loaded_extensions == [str(extension.resolve())]
    assert connection.extension_states == [True, False]
    assert Path(decryptor.os.environ["WEIXIN_DLL_PATH"]) == expected_dll.resolve()
