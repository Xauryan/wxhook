import json
import sqlite3
import time

from backend.app.config import Settings
from backend.app.repository import CACHE_SCHEMA_VERSION, CACHE_SOURCE_DATABASES, WeChatRepository


def make_repository(tmp_path, monkeypatch):
    root = tmp_path / 'panel'
    database_root = tmp_path / 'all'
    for name in CACHE_SOURCE_DATABASES:
        source = database_root / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b'isolated source fingerprint')
    report = tmp_path / 'report.json'
    report.write_text(json.dumps({'account': 'wxid_self', 'created_utc': '2026-10-14T00:00:00Z'}), encoding='utf-8')
    config = Settings(panel_root=root, repository_root=tmp_path, database_root=database_root,
                      report_path=report, cache_path=root / '.cache/analytics.sqlite',
                      fts_extension=tmp_path / 'unused.dll', weixin_dll=tmp_path / 'Weixin.dll')
    repository = WeChatRepository(config)

    def build_cache(path, fingerprint):
        connection = sqlite3.connect(path)
        try:
            repository._create_cache_schema(connection)
            connection.executemany('INSERT INTO meta VALUES (?,?)', [
                ('source_fingerprint', fingerprint), ('schema_version', str(CACHE_SCHEMA_VERSION)),
            ])
            connection.commit()
        finally:
            connection.close()
    monkeypatch.setattr(repository, '_build_cache', build_cache)
    return repository


def test_realtime_writes_follow_a_published_cache_and_keep_an_existing_reader_valid(tmp_path, monkeypatch):
    repository = make_repository(tmp_path, monkeypatch)
    original = repository.ensure_cache()
    with repository.cache() as reader:
        assert reader.execute('SELECT count(*) FROM messages').fetchone()[0] == 0
        published = repository.ensure_cache(force=True)
        assert published != original
        result = repository.ingest_realtime({
            'event_id': 'isolated-active-cache-test', 'event_type': 'message.added',
            'source': 'wx_hook_bridge', 'schema_version': 1,
            'msgId': '99000001', 'localId': '90001', 'type': '1', 'subType': '0',
            'timestamp_unix': int(time.time()), 'direction': 'incoming', 'isIncoming': True,
            'peerWxId': 'wxid_peer', 'senderWxId': 'wxid_peer', 'selfWxId': 'wxid_self',
            'rawFrom': 'wxid_peer', 'rawTo': 'wxid_self', 'content': 'isolated compatibility check',
        })
        assert result['status'] == 'applied'
        assert repository.health()['message_count'] == 1
        assert reader.execute('SELECT count(*) FROM messages').fetchone()[0] == 0
    with sqlite3.connect(original) as connection:
        assert connection.execute('SELECT count(*) FROM messages').fetchone()[0] == 0


def test_snapshot_reload_rebinds_the_realtime_account_without_deleting_previous_events(tmp_path, monkeypatch):
    repository = make_repository(tmp_path, monkeypatch)
    original = repository.realtime
    repository.settings.report_path.write_text(json.dumps({
        'account': 'wxid_other_account', 'created_utc': '2026-10-15T00:00:00Z',
    }), encoding='utf-8')
    assert repository.reload_configuration_if_available()
    assert repository.realtime.account == 'wxid_other_account'
    assert repository.realtime.scope_key != original.scope_key
    assert repository.realtime.path == original.path
    assert original.path.is_file()
