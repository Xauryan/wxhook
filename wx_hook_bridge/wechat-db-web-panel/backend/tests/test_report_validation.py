import json
from pathlib import Path

import pytest

from backend.app import main
from backend.app.config import Settings
from backend.app.repository import WeChatRepository


def _settings(tmp_path: Path) -> Settings:
    panel_root = tmp_path / "panel"
    return Settings(
        panel_root=panel_root,
        repository_root=tmp_path,
        database_root=tmp_path / "data" / "db_decrypt" / "all",
        report_path=tmp_path / "data" / "db_decrypt" / "decryption_report.json",
        cache_path=panel_root / ".cache" / "analytics.sqlite",
        fts_extension=tmp_path / "dist" / "weixin_fts_tokenizer.dll",
        weixin_dll=tmp_path / "data" / "Weixin.dll",
    )


def _versioned_report(tmp_path: Path) -> dict:
    return {
        "schema_version": 1,
        "created_utc": "2026-08-20T00:00:00+00:00",
        "account": "wxid_test",
        "source_root": str((tmp_path / "wxid_test_abcd" / "db_storage").resolve()),
        "key_manifest": str((tmp_path / "keys.json").resolve()),
        "output_root": str(
            (tmp_path / "data" / "db_decrypt" / ("all." + "a" * 32)).resolve()
        ),
        "decrypted_database_count": 0,
        "total_bytes": 0,
        "unresolved": [],
        "files": [],
    }


def test_legacy_report_normalizes_string_unresolved_entries() -> None:
    report = WeChatRepository._validated_report(
        {
            "account": "wxid_legacy",
            "unresolved": ["message\\message_1.db"],
            "files": [],
        }
    )

    assert report["unresolved"] == [
        {
            "path": "message/message_1.db",
            "error": "legacy unresolved entry",
        }
    ]


def test_versioned_report_rejects_legacy_unresolved_strings(tmp_path: Path) -> None:
    report = _versioned_report(tmp_path)
    report["unresolved"] = ["message/message_1.db"]

    with pytest.raises(RuntimeError, match=r"unresolved\[0\] must be an object"):
        WeChatRepository._validated_report(report)


def test_versioned_report_rejects_unknown_fields(tmp_path: Path) -> None:
    report = _versioned_report(tmp_path)
    report["unexpected"] = True

    with pytest.raises(RuntimeError, match="unknown field: unexpected"):
        WeChatRepository._validated_report(report)


def test_diagnostics_return_the_active_versioned_database_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    report = _versioned_report(tmp_path)
    settings.report_path.parent.mkdir(parents=True)
    settings.report_path.write_text(json.dumps(report), encoding="utf-8")
    repository = WeChatRepository(settings)
    monkeypatch.setattr(main, "repository", repository)

    assert repository.database_root == Path(report["output_root"])
    assert repository.health()["database_root"] == report["output_root"]
    assert main.local_settings()["database_root"] == report["output_root"]
