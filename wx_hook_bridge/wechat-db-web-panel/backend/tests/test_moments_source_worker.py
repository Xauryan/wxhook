from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app import moments_source
from backend.app.moments_process_files import ProcessFilesChanged, WindowsProcessFiles
from backend.app.moments_source import MomentsRoster, MomentsSourceError, prepare_moments, prepare_worker
from backend.app.sns_sync import SnsSnapshotSync


@pytest.fixture
def source_reader(tmp_path):
    root = tmp_path / 'self_1234'
    contact = root / 'db_storage/contact/contact.db'
    contact.parent.mkdir(parents=True)
    contact.write_bytes(b'fixture')
    source = root / 'db_storage/sns/sns.db'
    source.parent.mkdir(parents=True)
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE snapshot (id INTEGER)')
    binding = {'account': 'self', 'pid': 1, 'created_at': 1, 'source': contact}
    reader = MomentsRoster('http://127.0.0.1:19088', tmp_path, tmp_path / 'manifest.json', tmp_path / 'cache')
    reader.loader = SnsSnapshotSync(Path('unused'), tmp_path, tmp_path / 'unused')
    reader.verify = lambda account, force=False: dict(binding)
    return reader, source, binding


@pytest.mark.parametrize('changed', [
    {'pid': 2}, {'created_at': 2}, {'account': 'other'}, {'source': Path('other/contact.db')},
])
def test_snapshot_rejects_switch_after_source_read(source_reader, changed):
    reader, _, binding = source_reader
    calls = []

    def verify(account, force=False):
        assert force
        calls.append(account)
        return binding if len(calls) == 1 else {**binding, **changed}

    reader.verify = verify
    with pytest.raises(MomentsSourceError) as failure:
        prepare_moments(reader, 'self')
    assert failure.value.code == 'source_changed'
    assert failure.value.stage == 'verify_after'
    assert len(calls) == 2


def test_cached_snapshot_still_revalidates_both_bindings(source_reader):
    reader, _, binding = source_reader
    first = prepare_moments(reader, 'self')
    calls = []
    reader.verify = lambda account, force=False: calls.append(force) or dict(binding)
    second = prepare_moments(reader, 'self')
    assert first['database'] == second['database']
    assert calls == [True, True]


def test_snapshot_process_death_is_typed_and_does_not_expose_source(source_reader):
    reader, _, _ = source_reader

    def fail(*args, **kwargs):
        raise PermissionError('secret path and key')

    reader.verify = fail
    with pytest.raises(MomentsSourceError) as failure:
        prepare_moments(reader, 'self')
    assert failure.value.code == 'active_account_unverified'
    assert failure.value.stage == 'verify_before'
    assert failure.value.exception_type == 'PermissionError'
    assert 'secret' not in str(failure.value)


def symlink_or_skip(link, target):
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip('Filesystem does not permit symlink fixtures')


def test_snapshot_rejects_wal_outside_active_account(source_reader, tmp_path):
    reader, source, _ = source_reader
    foreign = tmp_path / 'foreign.sqlite'
    writer = sqlite3.connect(foreign)
    try:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('CREATE TABLE foreign_data (id INTEGER)')
        writer.commit()
        symlink_or_skip(Path(str(source) + '-wal'), Path(str(foreign) + '-wal'))
        with pytest.raises(MomentsSourceError) as failure:
            prepare_moments(reader, 'self')
        assert failure.value.code == 'source_changed'
        assert not list(reader.cache_root.rglob('*.sqlite'))
    finally:
        writer.close()


def test_snapshot_rejects_cache_subdirectory_link_to_source(source_reader):
    reader, source, _ = source_reader
    cache = reader.cache_root / 'moments_sources'
    cache.mkdir(parents=True)
    link = cache / hashlib.sha256(b'self').hexdigest()[:24]
    try:
        link.symlink_to(source.parents[2], target_is_directory=True)
    except OSError:
        pytest.skip('Filesystem does not permit symlink fixtures')
    before = {path.relative_to(source.parents[2]) for path in source.parents[2].rglob('*')}
    with pytest.raises(MomentsSourceError) as failure:
        prepare_moments(reader, 'self')
    assert failure.value.code == 'cache_invalid'
    after = {path.relative_to(source.parents[2]) for path in source.parents[2].rglob('*')}
    assert before == after


def test_snapshot_rechecks_wal_containment_after_materialization(source_reader, tmp_path):
    reader, source, _ = source_reader
    foreign = tmp_path / 'foreign-wal'
    foreign.write_bytes(b'foreign')
    original = reader.loader._backup_plain_database

    def changed(source_copy, target):
        original(source_copy, target)
        symlink_or_skip(Path(str(source) + '-wal'), foreign)

    reader.loader._backup_plain_database = changed
    with pytest.raises(MomentsSourceError) as failure:
        prepare_moments(reader, 'self')
    assert failure.value.code == 'source_changed'
    assert not list(reader.cache_root.rglob('*.sqlite'))


def native_reader(monkeypatch, source_reader, paths=None, created=None, owners=None):
    reader, _, binding = source_reader
    del reader.verify
    monkeypatch.setattr(moments_source.sys, 'platform', 'win32')
    process = SimpleNamespace(pid=1, name=lambda: 'Weixin.exe', create_time=created or (lambda: 1))
    monkeypatch.setattr(moments_source.psutil, 'Process', lambda pid: process)
    connection = SimpleNamespace(pid=1, status='LISTEN', laddr=SimpleNamespace(port=19088, ip='127.0.0.1'))
    monkeypatch.setattr(moments_source.psutil, 'net_connections', lambda kind: owners or [connection])
    reader._process_files = SimpleNamespace(paths=lambda pid: paths if paths is not None else {str(binding['source'])})
    reader._binding = {'account': 'stale', 'pid': 100}
    reader._binding_at = float('inf')
    return reader


def test_native_verifier_ignores_cached_binding_and_uses_actual_files(monkeypatch, source_reader):
    reader = native_reader(monkeypatch, source_reader)
    assert reader.verify('self') == source_reader[2]
    with pytest.raises(moments_source.ActiveRosterError):
        reader.verify('other')


@pytest.mark.parametrize('count', [0, 2])
def test_native_verifier_rejects_missing_or_multiple_contact_handles(monkeypatch, source_reader, count):
    source = source_reader[2]['source']
    paths = [str(source), str(source.parents[3] / 'other/db_storage/contact/contact.db')][:count]
    reader = native_reader(monkeypatch, source_reader, paths=paths)
    with pytest.raises(moments_source.ActiveRosterError):
        reader.verify('self')


def test_native_verifier_rejects_restarted_process(monkeypatch, source_reader):
    created = iter([1, 2])
    reader = native_reader(monkeypatch, source_reader, created=lambda: next(created))
    with pytest.raises(moments_source.ActiveRosterError):
        reader.verify('self')


def test_native_verifier_rejects_current_account_alias_to_foreign_file(monkeypatch, source_reader, tmp_path):
    source = source_reader[2]['source']
    foreign = tmp_path / 'foreign_1234/db_storage/contact/contact.db'
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(b'foreign')
    source.unlink()
    symlink_or_skip(source, foreign)
    reader = native_reader(monkeypatch, source_reader)
    with pytest.raises(moments_source.ActiveRosterError):
        reader.verify('self')


def test_native_verifier_rejects_ambiguous_listener(monkeypatch, source_reader):
    owners = [SimpleNamespace(pid=pid, status='LISTEN', laddr=SimpleNamespace(port=19088, ip='127.0.0.1'))
              for pid in (1, 2)]
    reader = native_reader(monkeypatch, source_reader, owners=owners)
    with pytest.raises(moments_source.ActiveRosterError):
        reader.verify('self')


def test_native_verifier_rejects_port_handoff_while_old_process_lives(monkeypatch, source_reader):
    reader = native_reader(monkeypatch, source_reader)
    owners = iter([{1}, {2}])
    reader._bridge_owners = lambda: next(owners)
    with pytest.raises(moments_source.ActiveRosterError):
        reader.verify('self')


class FakeProcess:
    def __init__(self, response, returncode=0, exception=None):
        self.response = response
        self.returncode = None
        self.final_code = returncode
        self.exception = exception
        self.killed = False
        self.waited = False

    async def communicate(self):
        if self.exception:
            raise self.exception
        self.returncode = self.final_code
        return self.response, b'secret stderr not returned'

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        self.waited = True
        return self.returncode


def use_process(monkeypatch, process):
    async def start(*args, **kwargs):
        return process
    monkeypatch.setattr(moments_source.asyncio, 'create_subprocess_exec', start)


def test_worker_accepts_only_current_account_snapshot(monkeypatch, source_reader):
    result = {'account': 'self', 'source': 'active_sns_db'}
    use_process(monkeypatch, FakeProcess(json.dumps(result).encode()))
    assert asyncio.run(prepare_worker(source_reader[0], 'self')) == result


@pytest.mark.parametrize('response', [
    b'', b'not json', b'[]', b'x' * 16385,
    b'{"account":"other","source":"active_sns_db"}',
    b'{"account":"self","source":"legacy_sns_snapshot"}',
], ids=['empty', 'malformed', 'not-object', 'oversize', 'other-account', 'unverified-source'])
def test_worker_rejects_invalid_output(monkeypatch, source_reader, response):
    use_process(monkeypatch, FakeProcess(response))
    with pytest.raises(MomentsSourceError) as failure:
        asyncio.run(prepare_worker(source_reader[0], 'self'))
    assert failure.value.code == 'worker_failed'


def test_worker_preserves_only_allowlisted_diagnostics(monkeypatch, source_reader):
    error = {'code': 'active_account_unverified', 'stage': 'verify_after', 'exception_type': 'AccessDenied'}
    use_process(monkeypatch, FakeProcess(json.dumps({'error': error}).encode(), returncode=1))
    with pytest.raises(MomentsSourceError) as failure:
        asyncio.run(prepare_worker(source_reader[0], 'self'))
    assert failure.value.code == error['code']
    assert failure.value.stage == error['stage']
    assert failure.value.exception_type == error['exception_type']
    assert 'secret' not in str(failure.value)
    unexpected = MomentsSourceError('secret', stage='private path', exception_type='key material')
    assert (unexpected.code, unexpected.stage, unexpected.exception_type) == ('worker_failed', 'worker', 'UnknownError')


def test_worker_kills_and_reaps_on_timeout(monkeypatch, source_reader):
    process = FakeProcess(b'', exception=TimeoutError())
    use_process(monkeypatch, process)
    with pytest.raises(MomentsSourceError) as failure:
        asyncio.run(prepare_worker(source_reader[0], 'self'))
    assert failure.value.code == 'worker_timeout'
    assert process.killed and process.waited


def test_worker_kills_and_reaps_on_cancellation(monkeypatch, source_reader):
    process = FakeProcess(b'', exception=asyncio.CancelledError())
    use_process(monkeypatch, process)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(prepare_worker(source_reader[0], 'self'))
    assert process.killed and process.waited


def test_worker_start_failure_is_sanitized(monkeypatch, source_reader):
    async def start(*args, **kwargs):
        raise PermissionError('secret executable path')
    monkeypatch.setattr(moments_source.asyncio, 'create_subprocess_exec', start)
    with pytest.raises(MomentsSourceError) as failure:
        asyncio.run(prepare_worker(source_reader[0], 'self'))
    assert failure.value.code == 'worker_failed'
    assert 'secret' not in str(failure.value)


def test_native_handle_scan_retries_closed_handles_and_closes_process():
    scanner = object.__new__(WindowsProcessFiles)
    closed = []
    scanner.kernel = SimpleNamespace(OpenProcess=lambda *args: 42, CloseHandle=closed.append)
    calls = []

    def scan(process):
        calls.append(process)
        if len(calls) < 3:
            raise ProcessFilesChanged()
        return {'contact.db'}

    scanner._paths = scan
    assert scanner.paths(1) == {'contact.db'}
    assert calls == [42, 42, 42] and closed == [42]


def test_native_handle_scan_rejects_unstable_handles_with_bounded_retries():
    scanner = object.__new__(WindowsProcessFiles)
    calls, closed = [], []
    scanner.kernel = SimpleNamespace(OpenProcess=lambda *args: 42, CloseHandle=closed.append)

    def scan(process):
        calls.append(process)
        raise ProcessFilesChanged()

    scanner._paths = scan
    with pytest.raises(ProcessFilesChanged):
        scanner.paths(1)
    assert len(calls) == 3 and closed == [42]
