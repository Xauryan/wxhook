import hashlib
import json
import os
from pathlib import Path

from backend.app.config import Settings
from backend.app.repository import CACHE_SOURCE_DATABASES, WeChatRepository


def build_repository(tmp_path: Path, report: dict) -> WeChatRepository:
    panel_root = tmp_path / "panel"
    database_root = tmp_path / "database"
    report_path = tmp_path / "decryption_report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    settings = Settings(
        panel_root=panel_root,
        repository_root=tmp_path,
        database_root=database_root,
        report_path=report_path,
        cache_path=panel_root / ".cache" / "analytics.sqlite",
        fts_extension=tmp_path / "weixin_fts_tokenizer.dll",
        weixin_dll=tmp_path / "Weixin.dll",
    )
    return WeChatRepository(settings)


def test_cache_fingerprint_tracks_source_identity_and_snapshot(tmp_path: Path) -> None:
    database_root = tmp_path / "database"
    report_files = []
    for index, relative in enumerate(CACHE_SOURCE_DATABASES):
        path = database_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = f"database-{index}".encode()
        path.write_bytes(payload)
        report_files.append(
            {"path": relative, "sha256": hashlib.sha256(payload).hexdigest()}
        )
    report = {
        "account": "wxid_test",
        "created_utc": "2026-08-18T00:00:00+08:00",
        "files": report_files,
    }
    repository = build_repository(tmp_path, report)
    original = repository._source_fingerprint()

    source = database_root / CACHE_SOURCE_DATABASES[0]
    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert repository._source_fingerprint() != original

    mtime_fingerprint = repository._source_fingerprint()
    report["files"][0]["sha256"] = "0" * 64
    changed_sha_repository = build_repository(tmp_path, report)
    assert changed_sha_repository._source_fingerprint() != mtime_fingerprint

    sha_fingerprint = changed_sha_repository._source_fingerprint()
    report["created_utc"] = "2026-08-18T00:00:01+08:00"
    changed_snapshot_repository = build_repository(tmp_path, report)
    assert changed_snapshot_repository._source_fingerprint() != sha_fingerprint
