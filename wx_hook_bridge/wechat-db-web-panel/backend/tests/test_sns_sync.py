from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from backend.app.sns_sync import SnsSnapshotSync, SnsSnapshotSyncError


def _create_sns_database(path: Path, rows: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE SnsTimeLine(
                tid INTEGER PRIMARY KEY,
                user_name TEXT,
                content TEXT,
                pack_info_buf BLOB
            )
            """
        )
        connection.executemany(
            "INSERT INTO SnsTimeLine VALUES(?,?,?,?)",
            [(index, f"wxid_{index}", f"<post>{index}</post>", b"") for index in range(rows)],
        )


def test_plain_snapshot_refreshes_atomically_only_when_source_changes(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "account" / "db_storage"
    source = source_root / "sns" / "sns.db"
    snapshot = tmp_path / "runtime" / "sns" / "sns.db"
    report = tmp_path / "report.json"
    _create_sns_database(source, 1)
    report.write_text(json.dumps({"source_root": str(source_root)}), encoding="utf-8")
    sync = SnsSnapshotSync(report, tmp_path, snapshot)

    first = sync.refresh()

    assert first["changed"] is True
    assert first["generation"] == 1
    connection = sqlite3.connect(snapshot)
    try:
        assert connection.execute("SELECT count(*) FROM SnsTimeLine").fetchone() == (1,)
    finally:
        connection.close()

    unchanged = sync.refresh()
    assert unchanged["changed"] is False
    assert unchanged["generation"] == 1

    with sqlite3.connect(source) as connection:
        connection.execute(
            "INSERT INTO SnsTimeLine VALUES(?,?,?,?)",
            (2, "wxid_2", "<post>2</post>", b""),
        )

    updated = sync.refresh()

    assert updated["changed"] is True
    assert updated["generation"] == 2
    assert updated["last_error"] is None
    connection = sqlite3.connect(snapshot)
    try:
        assert connection.execute("SELECT count(*) FROM SnsTimeLine").fetchone() == (2,)
    finally:
        connection.close()


def test_source_change_during_publish_is_refreshed_on_next_poll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "account" / "db_storage"
    source = source_root / "sns" / "sns.db"
    snapshot = tmp_path / "runtime" / "sns" / "sns.db"
    report = tmp_path / "report.json"
    _create_sns_database(source, 1)
    report.write_text(json.dumps({"source_root": str(source_root)}), encoding="utf-8")
    sync = SnsSnapshotSync(report, tmp_path, snapshot)
    original = SnsSnapshotSync._backup_plain_database
    mutated = False

    def backup_then_mutate(source_path: Path, output_path: Path) -> None:
        nonlocal mutated
        original(source_path, output_path)
        if mutated:
            return
        mutated = True
        with sqlite3.connect(source_path) as connection:
            connection.execute(
                "INSERT INTO SnsTimeLine VALUES(?,?,?,?)",
                (2, "wxid_2", "<post>2</post>", b""),
            )

    monkeypatch.setattr(
        SnsSnapshotSync,
        "_backup_plain_database",
        staticmethod(backup_then_mutate),
    )

    first = sync.refresh()
    second = sync.refresh()

    assert first["generation"] == 1
    assert second["changed"] is True
    assert second["generation"] == 2
    with sqlite3.connect(snapshot) as connection:
        assert connection.execute("SELECT count(*) FROM SnsTimeLine").fetchone() == (2,)


def test_unverifiable_nonempty_wal_is_rejected() -> None:
    with pytest.raises(SnsSnapshotSyncError, match="changed_during_read"):
        SnsSnapshotSync._validate_wal_status(
            {
                "exists": True,
                "size": 4096,
                "header_valid": False,
                "stop_reason": "changed_during_read",
            }
        )

    SnsSnapshotSync._validate_wal_status(
        {
            "exists": True,
            "size": 4096,
            "header_valid": True,
            "commit_frames": 0,
            "stop_reason": "salt_mismatch",
        }
    )
