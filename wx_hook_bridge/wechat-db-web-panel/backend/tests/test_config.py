from pathlib import Path

import pytest

from backend.app.config import Settings


def make_settings(panel_root: Path, cache_path: Path, database_root: Path) -> Settings:
    return Settings(
        panel_root=panel_root,
        repository_root=panel_root.parent,
        database_root=database_root,
        report_path=panel_root.parent / "decryption_report.json",
        cache_path=cache_path,
        fts_extension=panel_root.parent / "weixin_fts_tokenizer.dll",
        weixin_dll=panel_root.parent / "Weixin.dll",
    )


def test_settings_accept_project_local_cache(tmp_path: Path) -> None:
    panel_root = tmp_path / "panel"
    cache_path = panel_root / ".cache" / "analytics.sqlite"
    settings = make_settings(panel_root, cache_path, tmp_path / "database")
    assert settings.cache_path == cache_path
    assert settings.friend_automation_group_welcome == "请先查看群公告"


@pytest.mark.parametrize("location", ["outside", "source"])
def test_settings_reject_cache_outside_project_cache(
    tmp_path: Path, location: str
) -> None:
    panel_root = tmp_path / "panel"
    database_root = tmp_path / "database"
    cache_path = (
        tmp_path / "analytics.sqlite"
        if location == "outside"
        else database_root / "analytics.sqlite"
    )
    with pytest.raises(ValueError, match="analytics cache"):
        make_settings(panel_root, cache_path, database_root)


def test_settings_reject_empty_group_welcome(tmp_path: Path) -> None:
    panel_root = tmp_path / "panel"
    with pytest.raises(ValueError, match="group welcome"):
        Settings(
            panel_root=panel_root,
            repository_root=tmp_path,
            database_root=tmp_path / "database",
            report_path=tmp_path / "decryption_report.json",
            cache_path=panel_root / ".cache" / "analytics.sqlite",
            fts_extension=tmp_path / "weixin_fts_tokenizer.dll",
            weixin_dll=tmp_path / "Weixin.dll",
            friend_automation_group_welcome="   ",
        )
