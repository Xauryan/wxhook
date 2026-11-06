"""Regression coverage for retaining PR #1 through snapshot integration."""
import sqlite3
from pathlib import Path

from backend.app.repository import WeChatRepository
from backend.tests.test_setup_state import make_settings, write_snapshot


def test_missing_media_fingerprint_is_stable_until_the_source_appears(tmp_path: Path):
    settings = make_settings(tmp_path)
    write_snapshot(settings)
    media = settings.database_root / "message/media_0.db"
    media.unlink()
    repository = WeChatRepository(settings)

    missing = repository._source_fingerprint()
    assert repository._source_fingerprint() == missing
    assert not media.exists()

    media.write_bytes(b"new local media database")
    assert repository._source_fingerprint() != missing


def test_missing_database_and_table_preserve_available_source_counts(tmp_path: Path):
    repository = WeChatRepository(make_settings(tmp_path))
    contact = repository.database("contact/contact.db")
    contact.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(contact) as db:
        db.executescript("CREATE TABLE contact(id INTEGER); INSERT INTO contact VALUES(1),(2);")

    counts = repository._source_counts()
    assert counts["contact_count"] == "2"
    assert counts["chatroom_count"] == "0"
    assert counts["voice_info_count"] == "0"
    assert len(counts) == 7
    assert not repository.database("message/media_0.db").exists()
