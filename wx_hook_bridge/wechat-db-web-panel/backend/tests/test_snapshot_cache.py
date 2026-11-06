import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import psutil
import pytest

from backend.app.chat_history import prepare_snapshot, readonly
from backend.app.live_chat import LiveChat
from backend.app.snapshot_cache import SnapshotCache
from backend.app.sns_sync import SnsSnapshotSync


def version(cache, number, stem='message_0', account='self'):
    directory = cache.account_directory(account)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'{stem}-{number:024x}.sqlite'
    path.write_bytes(f'snapshot {number}'.encode())
    os.utime(path, (number, number))
    return path


def publish(cache, databases):
    with sqlite3.connect(cache.history_path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS chat_history_state(account PRIMARY KEY, data)')
        db.execute('INSERT OR REPLACE INTO chat_history_state VALUES (?,?)',
                   ('self', json.dumps({'databases': databases})))


def test_cleanup_retains_latest_versions_published_media_and_active_readers(tmp_path):
    cache = SnapshotCache(tmp_path / 'cache')
    published = version(cache, 1)
    publish(cache, {'message/message_0.db': str(published)})
    reading = version(cache, 2)
    untouched = published.parent / 'manual.sqlite'
    untouched.write_bytes(b'user-owned')
    # A different account has its own retention window.
    other = [version(cache, i, account='other') for i in range(1, 4)]
    with cache.preparing('self') as lease:
        cache.publish_locked('self', lease, {'message/message_0.db': str(reading)})
    obsolete, previous, latest = [version(cache, i) for i in range(3, 6)]
    report = cache.prune(apply=True)
    assert all(p.exists() for p in (published, reading, previous, latest, untouched, *other[1:]))
    assert not obsolete.exists() and not other[0].exists()
    assert cache.prune()['files'] == 0
    cache.release('self', lease)
    assert not reading.exists()
    assert published.exists() and latest.exists() and untouched.read_bytes() == b'user-owned'


def test_stale_lease_cannot_pin_files_after_pid_reuse(tmp_path):
    cache = SnapshotCache(tmp_path / 'cache')
    old = version(cache, 1)
    with cache.preparing('self') as token:
        cache.publish_locked('self', token, {'message/message_0.db': str(old)})
    path = cache._lease_path('self', token)
    lease = json.loads(path.read_text(encoding='utf-8'))
    lease['started'] -= 100
    path.write_text(json.dumps(lease), encoding='utf-8')
    version(cache, 2)
    version(cache, 3)
    cache.prune(apply=True)
    assert not old.exists()
    assert not cache._lease_path('self', token).exists()


@pytest.mark.parametrize('broken', ['history', 'lease', 'history_shape', 'lease_shape'])
def test_unreadable_references_abort_cleanup_without_deleting_files(tmp_path, broken):
    cache = SnapshotCache(tmp_path / 'cache')
    files = [version(cache, i) for i in range(1, 5)]
    if broken == 'history':
        cache.history_path.write_bytes(b'not a database')
    elif broken == 'history_shape':
        publish(cache, {})
        with sqlite3.connect(cache.history_path) as db:
            db.execute('UPDATE chat_history_state SET data=?', ('{"databases":[]}',))
    else:
        leases = files[0].parent / '.leases'
        leases.mkdir()
        content = '{"databases":"not a path list"}' if broken == 'lease_shape' else '{'
        (leases / ('a' * 32 + '.json')).write_text(content, encoding='utf-8')
    with pytest.raises((ValueError, sqlite3.Error)):
        cache.prune(apply=True)
    assert all(p.exists() for p in files)


def test_dry_run_reports_bytes_without_removing_files(tmp_path):
    cache = SnapshotCache(tmp_path / 'cache')
    files = [version(cache, i) for i in range(1, 7)]
    report = cache.prune()
    assert report['files'] == 4
    assert report['bytes'] == sum(p.stat().st_size for p in files[:4])
    assert all(p.exists() for p in files)
    assert cache.prune(apply=True)['bytes'] == report['bytes']
    assert [p.exists() for p in files] == [False] * 4 + [True] * 2


def test_junction_to_non_cache_files_is_never_followed(tmp_path):
    cache = SnapshotCache(tmp_path / 'cache')
    outside = tmp_path / 'outside'
    outside.mkdir()
    victim = outside / ('message_0-' + '0' * 24 + '.sqlite')
    victim.write_bytes(b'original database')
    cache.root.mkdir(parents=True)
    link = cache.account_directory('self')
    if os.name == 'nt':
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)],
                       check=True, capture_output=True)
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(ValueError):
            cache.prune(apply=True)
        assert victim.read_bytes() == b'original database'
    finally:
        # Remove only the link; do not recurse into its target.
        if os.name == 'nt':
            os.rmdir(link)
        else:
            link.unlink()


def test_repeated_native_updates_stay_bounded_and_leave_source_unchanged(tmp_path):
    root = tmp_path / 'self_123'
    source = root / 'db_storage/message/message_0.db'
    source.parent.mkdir(parents=True)
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE evidence(value)')
    contact = root / 'db_storage/contact/contact.db'
    contact.parent.mkdir()
    contact.touch()
    cache = SnapshotCache(tmp_path / 'cache')
    loader = SnsSnapshotSync(Path('unused'), tmp_path, cache.base / 'unused')
    loader._load_decrypt_tool = lambda: SimpleNamespace(load_manifest=lambda _: {})
    binding = {'source': contact, 'account': 'self', 'pid': 1}
    roster = SimpleNamespace(verify=lambda *a, **k: binding, cache_root=cache.base,
                             loader=loader, key_manifest=Path('unused'))
    for number in range(7):
        with sqlite3.connect(source) as db:
            db.execute('INSERT INTO evidence VALUES (?)', (number,))
        os.utime(source, ns=(time.time_ns(), time.time_ns() + number))
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        snapshot = prepare_snapshot(roster, 'self', recent=True)
        with readonly(snapshot['databases']['message/message_0.db']) as db:
            assert db.execute('SELECT count(*) FROM evidence').fetchone()[0] == number + 1
        cache.release('self', snapshot['_cache_lease'])
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before
        assert len(list(cache.root.rglob('*.sqlite'))) <= 2
    assert not list(cache.root.rglob('*.json'))


def test_cache_lock_serializes_cleanup_across_processes(tmp_path):
    cache = SnapshotCache(tmp_path / 'cache')
    old = version(cache, 1)
    version(cache, 2)
    version(cache, 3)
    ready = tmp_path / 'ready'
    script = ('from pathlib import Path; from backend.app.snapshot_cache import SnapshotCache; '
              'import sys; Path(sys.argv[2]).touch(); '
              'SnapshotCache(Path(sys.argv[1])).prune(apply=True)')
    process = None
    try:
        with cache.locked():
            process = subprocess.Popen([sys.executable, '-c', script, str(cache.base), str(ready)],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            deadline = time.monotonic() + 10
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            assert ready.exists()
            assert process.poll() is None and old.exists()
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr.decode()
        assert not old.exists()
    finally:
        if process and process.poll() is None:
            process.kill()
            process.communicate(timeout=10)


def test_cancelled_async_read_keeps_lease_until_its_thread_finishes(tmp_path):
    cache = SnapshotCache(tmp_path / 'cache')
    old = version(cache, 1)
    with cache.preparing('self') as token:
        cache.publish_locked('self', token, {'message/message_0.db': str(old)})
    snapshot = {'account': 'self', '_cache_lease': token}
    started, finish, released = threading.Event(), threading.Event(), threading.Event()

    def release(_):
        cache.release('self', token)
        released.set()

    async def verified(*args, **kwargs):
        return {'username': 'self'}

    service = SimpleNamespace(history=SimpleNamespace(release=release), check_account=verified)

    def consume():
        started.set()
        assert finish.wait(10)
        assert old.read_bytes() == b'snapshot 1'

    async def scenario():
        task = asyncio.create_task(LiveChat._consume_history_snapshot(service, 'self', snapshot, consume))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            version(cache, 2)
            version(cache, 3)
            await asyncio.to_thread(cache.prune, apply=True)
            assert old.exists() and not released.is_set()
        finally:
            finish.set()
        assert await asyncio.to_thread(released.wait, 5)
        assert not old.exists()
    asyncio.run(scenario())


def test_account_check_failure_releases_snapshot_without_reading(tmp_path):
    released = []
    async def switched(*args, **kwargs):
        raise RuntimeError('switched account')
    service = SimpleNamespace(history=SimpleNamespace(release=released.append), check_account=switched)
    snapshot = {'account': 'self'}
    with pytest.raises(RuntimeError, match='switched account'):
        asyncio.run(LiveChat._consume_history_snapshot(service, 'self', snapshot,
                                                     lambda: pytest.fail('must not read')))
    assert released == [snapshot]
