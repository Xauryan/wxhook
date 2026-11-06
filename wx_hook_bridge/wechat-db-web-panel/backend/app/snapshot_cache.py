"""Bounded native chat snapshots, with process leases for concurrent readers."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from uuid import uuid4

import psutil


SNAPSHOT_NAME = re.compile(
    r'((?:biz_)?message_\d+|media_\d+|session|message_resource|hardlink|head_image|emoticon|general)'
    r'-[0-9a-f]{24}\.sqlite\Z')
TOKEN = re.compile(r'[0-9a-f]{32}\Z')
ACCOUNT_DIRECTORY = re.compile(r'[0-9a-f]{24}\Z')


def plain_path(path: Path) -> bool:
    """Never traverse a symlink or Windows junction during cache maintenance."""
    info = path.lstat()
    return not (stat.S_ISLNK(info.st_mode) or
                getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


class SnapshotCache:
    def __init__(self, cache_root: Path, history_path: Path | None = None):
        original = Path(cache_root).absolute()
        if original.exists() and not plain_path(original):
            raise ValueError('snapshot cache root must not be a link')
        self.base = original.resolve()
        self.root = self.base / 'chat_sources'
        self.history_path = history_path or self.base / 'live_chat.sqlite'

    def _directory(self, directory: Path):
        directory.mkdir(exist_ok=True)
        if not plain_path(directory) or directory.resolve() != directory:
            raise ValueError('snapshot cache directory must not be a link')

    @contextmanager
    def locked(self):
        self.base.mkdir(parents=True, exist_ok=True)
        self._directory(self.root)
        lock = self.root / '.retention.lock'
        if lock.exists() and not plain_path(lock):
            raise ValueError('invalid snapshot cache lock')
        with lock.open('a+b') as handle:
            if not handle.tell():
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                deadline = time.monotonic() + 180
                while True:
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError('snapshot cache is busy')
                        time.sleep(.05)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def account_directory(self, account):
        return self.root / hashlib.sha256(account.encode()).hexdigest()[:24]

    def _lease_path(self, account, token):
        if not TOKEN.fullmatch(token):
            raise ValueError('invalid snapshot lease')
        directory = self.account_directory(account)
        if directory.exists() and not plain_path(directory):
            raise ValueError('invalid snapshot account directory')
        return directory / '.leases' / (token + '.json')

    @staticmethod
    def _write_lease(path, lease):
        temporary = path.with_suffix('.tmp')
        try:
            temporary.write_text(json.dumps(lease), encoding='utf-8')
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @contextmanager
    def preparing(self, account, *, token=None, owner_pid=None, owner_started=None):
        """Serialize publishing and GC; the lease survives the snapshot subprocess."""
        token = token or uuid4().hex
        owner_pid = owner_pid or os.getpid()
        owner_started = owner_started or psutil.Process(owner_pid).create_time()
        with self.locked():
            directory = self.account_directory(account)
            self._directory(directory)
            self._directory(directory / '.leases')
            path = self._lease_path(account, token)
            if path.exists():
                raise ValueError('snapshot lease already exists')
            lease = {'pid': owner_pid, 'started': owner_started, 'databases': []}
            self._write_lease(path, lease)
            try:
                self.try_prune_locked()
                yield token
            except BaseException:
                path.unlink(missing_ok=True)
                raise

    def publish_locked(self, account, token, databases):
        path = self._lease_path(account, token)
        lease = json.loads(path.read_text(encoding='utf-8'))
        lease['databases'] = list(databases.values())
        self._write_lease(path, lease)
        self.try_prune_locked()

    def release(self, account, token):
        with self.locked():
            path = self._lease_path(account, token)
            if path.exists():
                if not plain_path(path) or not plain_path(path.parent):
                    raise ValueError('invalid snapshot lease path')
                path.unlink()
            self.try_prune_locked()

    def _published_references(self):
        if not self.history_path.exists():
            return set()
        # Include WAL commits. A locked/corrupt/unreadable manifest aborts GC.
        with sqlite3.connect(self.history_path.resolve().as_uri() + '?mode=ro',
                             uri=True, timeout=2) as db:
            db.execute('PRAGMA query_only=ON')
            rows = db.execute('SELECT data FROM chat_history_state').fetchall()
        references = set()
        for row in rows:
            state = json.loads(row[0])
            if not isinstance(state, dict) or not isinstance(state.get('databases', {}), dict):
                raise ValueError('invalid published snapshot manifest')
            for path in state.get('databases', {}).values():
                if not isinstance(path, str) or not path:
                    raise ValueError('invalid published snapshot reference')
                references.add(str(Path(path).resolve()))
        return references

    @staticmethod
    def _owner_alive(lease):
        try:
            return abs(psutil.Process(int(lease['pid'])).create_time() - float(lease['started'])) < .01
        except psutil.NoSuchProcess:
            return False
        except psutil.AccessDenied:
            return True

    def prune_locked(self, *, apply=True, keep=2):
        if keep < 1:
            raise ValueError('at least one snapshot version must be retained')
        protected = self._published_references()
        candidates, dead_leases = [], []
        for directory in self.root.iterdir():
            if not ACCOUNT_DIRECTORY.fullmatch(directory.name) or not directory.is_dir():
                continue
            if not plain_path(directory):
                raise ValueError('invalid snapshot account directory')
            leases = directory / '.leases'
            if leases.exists():
                if not plain_path(leases):
                    raise ValueError('invalid snapshot lease directory')
                for path in leases.glob('*.json'):
                    if not plain_path(path):
                        raise ValueError('invalid snapshot lease file')
                    lease = json.loads(path.read_text(encoding='utf-8'))
                    if (not isinstance(lease, dict) or not isinstance(lease.get('databases'), list) or
                            not all(isinstance(p, str) and p for p in lease['databases'])):
                        raise ValueError('invalid snapshot reader manifest')
                    if self._owner_alive(lease):
                        protected.update(str(Path(p).resolve()) for p in lease['databases'])
                    else:
                        dead_leases.append(path)
            groups = {}
            for path in directory.iterdir():
                match = SNAPSHOT_NAME.fullmatch(path.name)
                if match and path.is_file() and plain_path(path):
                    groups.setdefault(match[1], []).append(path)
            for files in groups.values():
                ordered = sorted(files, key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)
                candidates.extend(path for path in ordered[keep:]
                                  if str(path.resolve()) not in protected)
        report = {'apply': apply, 'files': 0, 'bytes': 0, 'paths': [], 'busy': []}
        for path in candidates:
            # Only direct, recognized files inside a verified account cache.
            if path.parent.parent != self.root or not plain_path(path.parent) or not plain_path(path):
                raise ValueError('snapshot cleanup path escaped managed cache')
            size = path.stat().st_size
            if apply:
                try:
                    path.unlink()
                except PermissionError:
                    report['busy'].append(str(path))
                    continue
            report['files'] += 1
            report['bytes'] += size
            report['paths'].append(str(path))
        if apply:
            for path in dead_leases:
                path.unlink(missing_ok=True)
        return report

    def try_prune_locked(self):
        try:
            return self.prune_locked()
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
            logging.getLogger(__name__).exception('Snapshot retention skipped; references could not be verified')

    def prune(self, *, apply=False):
        with self.locked():
            return self.prune_locked(apply=apply)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-root', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(SnapshotCache(args.cache_root).prune(apply=args.apply), ensure_ascii=False))
