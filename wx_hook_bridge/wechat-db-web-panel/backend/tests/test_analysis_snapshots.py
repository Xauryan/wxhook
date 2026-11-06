import asyncio
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import psutil
import pytest

from backend.app.analysis_snapshots import AnalysisSnapshots, AutoSnapshot, atomic_json, create_snapshot_router, job_lock
from backend.app import analysis_snapshot_worker as worker_module
from backend.app.analysis_snapshot_worker import SnapshotCancelled, SnapshotFailure, SnapshotWorker, inventory
from backend.app.config import Settings
from backend.app.repository import CACHE_SCHEMA_VERSION, CACHE_SOURCE_DATABASES, WeChatRepository


def settings_at(tmp_path):
    return Settings(panel_root=tmp_path / 'panel', repository_root=tmp_path,
        database_root=tmp_path / 'snapshots/all', report_path=tmp_path / 'snapshots/report.json',
        cache_path=tmp_path / 'panel/.cache/analytics.sqlite', fts_extension=tmp_path / 'unused.dll',
        weixin_dll=tmp_path / 'Weixin.dll')


class Live:
    def __init__(self, settings):
        self.account = 'wxid_test'
        self.roster = SimpleNamespace(key_manifest=settings.report_path.parent / 'keys.json', verify_async=self.verify)

    async def profile(self, force=False):
        return {'username': self.account}

    async def verify(self, account):
        assert account == self.account
        return {'account': account}


def service_at(tmp_path):
    settings = settings_at(tmp_path)
    return AnalysisSnapshots(settings, Live(settings))


def test_publication_gate_rejects_overlapping_writers(tmp_path):
    with job_lock(tmp_path):
        with pytest.raises(Exception) as raised:
            with job_lock(tmp_path):
                pytest.fail('overlapping writer acquired the publication gate')
        assert raised.value.status_code == 409
    with job_lock(tmp_path):
        pass


def install_spawn(monkeypatch):
    calls = []

    def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        atomic_json(Path(args[0][-1]) / 'process.json', {'pid': os.getpid(), 'created_at': psutil.Process().create_time()})
        return SimpleNamespace(pid=os.getpid(), poll=lambda: None)
    monkeypatch.setattr('backend.app.analysis_snapshots.subprocess.Popen', spawn)
    return calls


def test_duplicate_start_and_account_change_are_fenced(tmp_path, monkeypatch):
    service = service_at(tmp_path)
    calls = install_spawn(monkeypatch)
    async def run():
        first = await service.create('wxid_test')
        second = await service.create('wxid_test')
        assert first['job']['id'] == second['job']['id']
        service.live.account = 'wxid_other'
        with pytest.raises(Exception) as raised:
            await service.create('wxid_test')
        assert raised.value.status_code == 409
    asyncio.run(run())
    assert len(calls) == 1
    assert calls[0][1]['stdout'] < 0
    restored = AnalysisSnapshots(service.settings, service.live)
    assert restored.current()['status'] == 'running'
    assert restored.current()['account'] == 'wxid_test'


def test_dead_worker_recovery_and_cancel_are_persistent(tmp_path, monkeypatch):
    service = service_at(tmp_path)
    install_spawn(monkeypatch)
    state = asyncio.run(service.create('wxid_test'))['job']
    token = state['id']
    asyncio.run(service.cancel(token))
    assert (service.root / token / 'cancel.json').exists()
    state['started_at'] -= 40
    atomic_json(service.root / token / 'state.json', state)
    atomic_json(service.root / token / 'process.json', {'pid': os.getpid(), 'created_at': 0})
    assert service.current()['status'] == 'interrupted'
    assert AnalysisSnapshots(service.settings, service.live).current()['status'] == 'interrupted'
    # Recovery under the same cross-process gate must not deadlock the next start.
    assert asyncio.run(service.create('wxid_test'))['job']['id'] != token


def test_recovery_recognizes_a_report_published_before_worker_exit(tmp_path, monkeypatch):
    service = service_at(tmp_path)
    install_spawn(monkeypatch)
    state = asyncio.run(service.create('wxid_test'))['job']
    state['started_at'] -= 40
    atomic_json(service.root / state['id'] / 'state.json', state)
    atomic_json(service.root / state['id'] / 'process.json', {'pid': os.getpid(), 'created_at': 0})
    atomic_json(service.settings.report_path, {'account': 'wxid_test',
        'output_root': str(service.settings.database_root.with_name('all.' + state['id']).resolve())})
    assert service.current()['status'] == 'succeeded'


def test_automation_revision_binding_and_retry_interval(tmp_path, monkeypatch):
    service = service_at(tmp_path)
    calls = install_spawn(monkeypatch)
    assert service.config()['enabled'] is False
    async def run():
        payload = AutoSnapshot(account='wxid_test', enabled=True, revision=0, interval_minutes=60)
        await service.configure(payload)
        with pytest.raises(Exception) as raised:
            await service.configure(payload)
        assert raised.value.status_code == 409
        service.live.account = 'wxid_other'
        service._capability_at = 0
        await service.tick()
        assert not calls
        service.live.account = 'wxid_test'
        service._capability_at = 0
        await service.tick()
        assert len(calls) == 1
        state = service.current()
        state.update(status='failed')
        atomic_json(service.root / state['id'] / 'state.json', state)
        await service.tick()
        assert len(calls) == 1
    asyncio.run(run())


def test_local_router_rejects_external_origin_and_arbitrary_paths(tmp_path, monkeypatch):
    service = service_at(tmp_path)
    install_spawn(monkeypatch)
    app = FastAPI()
    app.include_router(create_snapshot_router(service))
    with TestClient(app) as client:
        assert client.post('/api/analysis-snapshots', json={'account': 'wxid_test'},
                           headers={'Origin': 'https://evil.example'}).status_code == 403
        assert client.post('/api/analysis-snapshots', json={'account': 'wxid_test', 'source': 'C:/'}).status_code == 422
        assert client.get('/api/analysis-snapshots').json()['capability']['available']
        response = client.post('/api/analysis-snapshots', json={'account': 'wxid_test'})
        assert response.status_code == 200
        public = client.get('/api/analysis-snapshots').json()['job']
        assert 'pid' not in public and 'key_manifest' not in public


def prepared_worker(tmp_path, monkeypatch):
    service = service_at(tmp_path)
    install_spawn(monkeypatch)
    job = asyncio.run(service.create('wxid_test'))['job']
    worker = SnapshotWorker(service.root / job['id'])
    source_root = tmp_path / 'wxid_test_1234/db_storage'
    for relative in CACHE_SOURCE_DATABASES:
        path = source_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE test(value)')
            db.execute('INSERT INTO test VALUES (1)')
    binding = {'account': 'wxid_test', 'pid': os.getpid(), 'created_at': psutil.Process().create_time(),
               'source': source_root / 'contact/contact.db'}
    monkeypatch.setattr(worker.roster, 'verify', lambda *args, **kwargs: binding)
    monkeypatch.setattr(worker_module, 'file_version', lambda path: '4.1.9.23')
    # Use real SQLite validation and report builder; isolate native keys/decryption.
    from backend.app.sns_sync import SnsSnapshotSync
    actual_tool = SnsSnapshotSync(Path('unused'), Path(__file__).resolve().parents[3], tmp_path / 'unused')._load_decrypt_tool()
    manifest = {'schema_version': 1, 'sqlcipher': {'page_size': 4096, 'reserve_size': 0},
                'keys': [{'salt_hex': '00', 'key_hex': '00'}]}
    monkeypatch.setattr(actual_tool, 'load_manifest', lambda path: manifest)
    monkeypatch.setattr(actual_tool, 'choose_key', lambda *args: {})
    def decrypt(source, output, manifest, **kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, output)
        return {'output': str(output), 'bytes': output.stat().st_size, 'pages': output.stat().st_size // 4096,
                'wal': {'exists': False}, **actual_tool.verify_plain_database(output)}
    monkeypatch.setattr(actual_tool, 'decrypt_database', decrypt)
    monkeypatch.setattr(worker.loader, '_load_decrypt_tool', lambda: actual_tool)
    def cache(repository, path, fingerprint):
        repository._build_progress(message_count=123)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE meta(key PRIMARY KEY,value)')
            db.executemany('INSERT INTO meta VALUES (?,?)', [('source_fingerprint', fingerprint),
                ('schema_version', str(CACHE_SCHEMA_VERSION)), ('message_count', '123')])
    monkeypatch.setattr(WeChatRepository, '_build_cache', cache)
    return worker


@pytest.mark.skipif(os.name != 'nt', reason='Windows native job')
def test_worker_publishes_only_after_a_verified_index(tmp_path, monkeypatch):
    worker = prepared_worker(tmp_path, monkeypatch)
    worker.run()
    report = json.loads(worker.settings.report_path.read_text(encoding='utf-8'))
    assert report['decrypted_database_count'] == len(CACHE_SOURCE_DATABASES)
    assert report['unresolved'] == []
    assert worker.state['status'] == 'succeeded'
    restored = WeChatRepository(worker.settings)
    # The API reuses the ready cache without rebuilding in its process.
    monkeypatch.setattr(WeChatRepository, '_build_cache', lambda *args: pytest.fail('cache should already be ready'))
    assert restored.ensure_cache().is_file()


@pytest.mark.skipif(os.name != 'nt', reason='Windows native job')
def test_optional_missing_key_is_reported_without_hiding_it(tmp_path, monkeypatch):
    worker = prepared_worker(tmp_path, monkeypatch)
    tool = worker.loader._load_decrypt_tool()
    source = worker.roster.verify(worker.account)['source'].parents[1] / 'optional/unused.db'
    source.parent.mkdir()
    source.write_bytes(b'missing key' + bytes(4085))
    manifest = tool.load_manifest(worker.roster.key_manifest)
    monkeypatch.setattr(tool, 'build_key_manifest', lambda *a, **kw: manifest)
    def choose(manifest, page):
        if page.startswith(b'missing key'):
            raise tool.DecryptError('missing key')
        return {}
    monkeypatch.setattr(tool, 'choose_key', choose)
    worker.run()
    report = json.loads(worker.settings.report_path.read_text(encoding='utf-8'))
    assert report['decrypted_database_count'] == len(CACHE_SOURCE_DATABASES)
    assert report['unresolved'][0]['path'] == 'optional/unused.db'
    assert worker.state['unresolved_count'] == 1
    assert worker.state['unresolved_files'] == ['optional/unused.db']


@pytest.mark.skipif(os.name != 'nt', reason='Windows native job')
@pytest.mark.parametrize('failure', ['index', 'cancel', 'account'])
def test_failed_cancelled_or_switched_account_preserves_previous_report(tmp_path, monkeypatch, failure):
    worker = prepared_worker(tmp_path, monkeypatch)
    worker.settings.report_path.parent.mkdir(parents=True, exist_ok=True)
    worker.settings.report_path.write_bytes(b'previous report must survive')
    def cache(repository, path, fingerprint):
        if failure == 'cancel':
            atomic_json(worker.directory / 'cancel.json', {})
            repository._build_progress()
        elif failure == 'account':
            monkeypatch.setattr(worker.roster, 'verify', lambda *a, **kw: {'account': 'other'})
            worker.check(binding=True)
        raise RuntimeError('index failed')
    monkeypatch.setattr(WeChatRepository, '_build_cache', cache)
    with pytest.raises((SnapshotCancelled, SnapshotFailure, RuntimeError)):
        worker.run()
    assert worker.settings.report_path.read_bytes() == b'previous report must survive'


def test_all_message_shards_are_merged_sorted_and_deduplicated(tmp_path):
    settings = settings_at(tmp_path)
    repository = WeChatRepository(settings)
    table = 'Msg_' + hashlib.md5(b'peer').hexdigest()
    for shard, rows in [(0, [(1, 100, 20, 'first'), (2, 0, 30, 'local-a')]),
                        (1, [(9, 100, 20, 'duplicate'), (2, 0, 10, 'local-b'), (3, 101, 40, 'last')])]:
        path = settings.database_root / f'message/message_{shard}.db'
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.executescript(f'''CREATE TABLE Name2Id(user_name,is_session);
                INSERT INTO Name2Id VALUES ('peer',1);
                CREATE TABLE {table}(local_id,server_id,local_type,sort_seq,real_sender_id,create_time,
                    source,message_content,compress_content,packed_info_data);''')
            db.executemany(f'INSERT INTO {table} VALUES (?,?,1,0,1,?,NULL,?,NULL,NULL)', rows)
    with sqlite3.connect(':memory:') as db:
        db.row_factory = sqlite3.Row
        repository._create_cache_schema(db)
        result = repository._scan_messages(db, {})
        assert result['message_count'] == 4
        assert result['message_table_count'] == 2
        assert [row['time'] for row in result['session_messages']['peer']] == [10, 20, 30, 40]
        assert db.execute('SELECT count(*) FROM messages').fetchone()[0] == 4
        resource = settings.database_root / 'message/message_resource.db'
        with closing(sqlite3.connect(resource)) as source, source:
            source.executescript('''CREATE TABLE MessageResourceInfo(message_id,chat_id,message_local_id,
                message_svr_id,message_create_time,packed_info);
                INSERT INTO MessageResourceInfo VALUES (1,1,2,0,20,NULL);
                CREATE TABLE MessageResourceDetail(resource_id,message_id,type,size,status,data_index,create_time,packed_info);
                INSERT INTO MessageResourceDetail VALUES (1,1,3,10,0,'fixture',20,NULL);''')
        assert repository._build_message_resources(db) == (1, 0)
        assert db.execute('SELECT message_key FROM message_resources').fetchone()[0] is None


def test_inventory_rejects_missing_required_sources(tmp_path):
    with pytest.raises(SnapshotFailure) as raised:
        inventory(tmp_path)
    assert raised.value.code == 'missing_databases'


def test_media_scan_only_reads_metadata_for_referenced_files(tmp_path, monkeypatch):
    repository = WeChatRepository(settings_at(tmp_path))
    repository.account_root = tmp_path / 'account'
    media = repository.account_root / 'msg'
    media.mkdir(parents=True)
    (media / 'used.jpg').write_bytes(b'fixture')
    (media / 'irrelevant.bin').write_bytes(b'unrelated')
    original = Path.stat
    def stat(path, *args, **kwargs):
        if path.name == 'irrelevant.bin':
            pytest.fail('unreferenced media metadata should not be read')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'stat', stat)
    result = repository._scan_source_files({'used.jpg'})
    assert set(result) == {'used.jpg'}
    assert result['used.jpg'][0][1] == 7
