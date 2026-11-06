import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import main
from backend.app.config import Settings
from backend.app.repository import (
    CACHE_SCHEMA_VERSION,
    CACHE_SOURCE_DATABASES,
    WeChatRepository,
)


def make_settings(tmp_path: Path) -> Settings:
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


def write_snapshot(settings: Settings) -> None:
    for relative in CACHE_SOURCE_DATABASES:
        path = settings.database_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test database")
    settings.report_path.parent.mkdir(parents=True, exist_ok=True)
    settings.report_path.write_text(
        json.dumps(
            {
                "account": "wxid_runtime_setup",
                "created_utc": "2026-08-20T00:00:00+08:00",
                "files": [],
            }
        ),
        encoding="utf-8",
    )


def install_minimal_cache_builder(repository: WeChatRepository, monkeypatch) -> list[str]:
    build_fingerprints: list[str] = []

    def build_cache(path: Path, fingerprint: str) -> None:
        build_fingerprints.append(fingerprint)
        connection = sqlite3.connect(path)
        try:
            connection.executescript(
                """
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE contacts (
                    username TEXT PRIMARY KEY,
                    display_name TEXT,
                    nick_name TEXT,
                    remark TEXT,
                    alias TEXT,
                    big_head_url TEXT,
                    small_head_url TEXT
                );
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY,
                    local_date TEXT,
                    create_time INTEGER
                );
                """
            )
            connection.executemany(
                "INSERT INTO meta(key,value) VALUES(?,?)",
                (
                    ("source_fingerprint", fingerprint),
                    ("schema_version", str(CACHE_SCHEMA_VERSION)),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    monkeypatch.setattr(repository, "_build_cache", build_cache)
    return build_fingerprints


def test_api_starts_without_a_decrypted_snapshot(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    repository = WeChatRepository(settings)
    monkeypatch.setattr(main, "repository", repository)

    with TestClient(main.app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "setup_required"
        assert health.json()["configured"] is False

        profile = client.get("/api/profile")
        assert profile.status_code == 503
        assert profile.json()["status"] == "setup_required"


def test_api_starts_with_an_invalid_decryption_report(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    settings.report_path.parent.mkdir(parents=True, exist_ok=True)
    settings.report_path.write_text("{truncated", encoding="utf-8")
    repository = WeChatRepository(settings)
    monkeypatch.setattr(main, "repository", repository)

    with TestClient(main.app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "setup_required"
        assert "Unable to read decryption report" in health.json()["detail"]


def test_api_starts_when_report_source_root_has_the_wrong_type(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    settings.report_path.parent.mkdir(parents=True, exist_ok=True)
    settings.report_path.write_text(
        json.dumps({"account": "wxid_invalid", "source_root": ["not", "a", "path"]}),
        encoding="utf-8",
    )
    repository = WeChatRepository(settings)
    monkeypatch.setattr(main, "repository", repository)

    with TestClient(main.app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "setup_required"
        assert "source_root must be a path string" in health.json()["detail"]


def test_api_rejects_malformed_report_file_entries(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    write_snapshot(settings)
    settings.report_path.write_text(
        json.dumps({"account": "wxid_invalid", "files": ["not-an-object"]}),
        encoding="utf-8",
    )
    repository = WeChatRepository(settings)
    monkeypatch.setattr(main, "repository", repository)

    with TestClient(main.app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "setup_required"
        assert "files[0] must be an object" in health.json()["detail"]


def test_corrupt_snapshot_keeps_health_endpoint_available(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    write_snapshot(settings)
    repository = WeChatRepository(settings)
    monkeypatch.setattr(main, "repository", repository)

    with TestClient(main.app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "setup_required"
        assert health.json()["configured"] is False
        assert "Unable to load decrypted snapshot" in health.json()["detail"]


def test_health_loads_snapshot_created_after_startup(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    repository = WeChatRepository(settings)
    build_fingerprints = install_minimal_cache_builder(repository, monkeypatch)
    load_calls = 0
    original_load_report = repository._load_report

    def load_report() -> dict:
        nonlocal load_calls
        load_calls += 1
        return original_load_report()

    monkeypatch.setattr(repository, "_load_report", load_report)
    monkeypatch.setattr(main, "repository", repository)

    with TestClient(main.app) as client:
        assert client.get("/api/health").json()["status"] == "setup_required"

        write_snapshot(settings)

        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert health.json()["configured"] is True
        assert repository.account == "wxid_runtime_setup"

        assert client.get("/api/health").json()["status"] == "ok"
        assert client.get("/api/profile").status_code == 200

    assert load_calls == 1
    assert len(build_fingerprints) == 1


def test_concurrent_health_reload_builds_cache_once(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    repository = WeChatRepository(settings)
    build_fingerprints = install_minimal_cache_builder(repository, monkeypatch)
    write_snapshot(settings)

    def reload_and_check_health(_: int) -> dict:
        repository.reload_configuration_if_available()
        return repository.health()

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(reload_and_check_health, range(16)))

    assert all(result["status"] == "ok" for result in results)
    assert repository.account == "wxid_runtime_setup"
    assert len(build_fingerprints) == 1


def test_cache_reload_keeps_an_existing_reader_valid(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    repository = WeChatRepository(settings)
    build_fingerprints = install_minimal_cache_builder(repository, monkeypatch)
    write_snapshot(settings)
    repository.reload_configuration_if_available()

    with repository.cache() as reader:
        first_path = repository.active_cache_path
        assert reader.execute("SELECT count(*) FROM contacts").fetchone()[0] == 0

        settings.report_path.write_text(
            json.dumps(
                {
                    "account": "wxid_runtime_setup",
                    "created_utc": "2026-08-22T00:00:00+08:00",
                    "files": [],
                }
            ),
            encoding="utf-8",
        )
        repository.reload_configuration_if_available()
        repository.ensure_cache()

        assert repository.active_cache_path != first_path
        assert reader.execute("SELECT count(*) FROM contacts").fetchone()[0] == 0

    assert len(build_fingerprints) == 2


def test_health_reloads_a_new_snapshot_after_initial_configuration(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    repository = WeChatRepository(settings)
    build_fingerprints = install_minimal_cache_builder(repository, monkeypatch)
    write_snapshot(settings)
    load_calls = 0
    original_load_report = repository._load_report

    def load_report() -> dict:
        nonlocal load_calls
        load_calls += 1
        return original_load_report()

    monkeypatch.setattr(repository, "_load_report", load_report)
    monkeypatch.setattr(main, "repository", repository)

    with TestClient(main.app) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        assert repository.account == "wxid_runtime_setup"
        assert client.get("/api/health").json()["status"] == "ok"
        assert load_calls == 1

        settings.report_path.write_text(
            json.dumps(
                {
                    "account": "wxid_reloaded_snapshot",
                    "created_utc": "2026-08-21T00:00:00+08:00",
                    "files": [],
                }
            ),
            encoding="utf-8",
        )

        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert repository.account == "wxid_reloaded_snapshot"
        assert repository.snapshot_time == "2026-08-21T00:00:00+08:00"

    assert load_calls == 2
    assert len(build_fingerprints) == 2
