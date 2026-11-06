"""Account-bound, read-only Moments snapshots from the bridge-owning process."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import psutil

from .active_roster import ActiveRosterError, ActiveWechatRoster


ERROR_DETAILS = {
    'active_account_unverified': '无法验证当前微信进程和账号，请稍后重试',
    'source_missing': '当前账号本地朋友圈库尚未就绪，请先在微信中打开朋友圈',
    'source_changed': '读取朋友圈时微信账号、进程或源库已变化，请重试',
    'source_permission_denied': '当前朋友圈源文件无法读取，请检查本地访问权限',
    'source_read_failed': '当前账号朋友圈源库读取失败，请稍后重试',
    'snapshot_integrity_failed': '朋友圈快照完整性校验失败',
    'cache_invalid': '朋友圈缓存目录配置无效',
    'worker_timeout': '朋友圈源库读取超时，请重试',
    'worker_failed': '朋友圈读取进程未正常完成，请重试',
}


class MomentsSourceError(ActiveRosterError):
    def __init__(self, code, *, stage='snapshot', exception_type=''):
        self.code = code if isinstance(code, str) and code in ERROR_DETAILS else 'worker_failed'
        self.stage = stage if isinstance(stage, str) and stage in {
            'verify_before', 'verify_after', 'snapshot', 'worker',
        } else 'worker'
        self.exception_type = exception_type if isinstance(exception_type, str) and exception_type in {
            'ActiveRosterError', 'PermissionError', 'OSError', 'FileNotFoundError',
            'SnsSnapshotSyncError', 'ValueError', 'RuntimeError', 'OperationalError',
            'AccessDenied', 'NoSuchProcess', 'TimeoutError', 'MomentsSourceError',
        } else 'UnknownError'
        super().__init__(ERROR_DETAILS[self.code])


class MomentsRoster(ActiveWechatRoster):
    """Verify all current DB handles on each read, without cached ownership."""

    def _bridge_owners(self):
        return {connection.pid for connection in psutil.net_connections(kind='tcp')
                if connection.status == psutil.CONN_LISTEN and connection.pid
                and connection.laddr.port == self.port
                and connection.laddr.ip in {'127.0.0.1', '::1'}}

    def verify(self, account, force=False):
        if sys.platform != 'win32':
            return super().verify(account, force=True)
        from .moments_process_files import WindowsProcessFiles

        owners = self._bridge_owners()
        if len(owners) != 1:
            raise ActiveRosterError('Bridge process ownership is not unique')
        process = psutil.Process(owners.pop())
        created_at = process.create_time()
        if not re.fullmatch(r'(?:wechat\d*|weixin\d*|weixi\d+)\.exe', process.name().casefold()):
            raise ActiveRosterError('Bridge listener is not WeChat')
        if not hasattr(self, '_process_files'):
            self._process_files = WindowsProcessFiles()
        paths = {Path(path) for path in self._process_files.paths(process.pid)
                 if path.replace('\\', '/').casefold().endswith('/db_storage/contact/contact.db')}
        if len(paths) != 1:
            raise ActiveRosterError('Active contact database is not unique')
        source = paths.pop().resolve(strict=True)
        folder = source.parents[2].name
        if not account or (folder != account and not folder.startswith(account + '_')):
            raise ActiveRosterError('Active account does not match contact database')
        if (psutil.Process(process.pid).create_time() != created_at
                or self._bridge_owners() != {process.pid}):
            raise ActiveRosterError('Bridge process restarted during verification')
        return {'account': account, 'pid': process.pid, 'created_at': created_at, 'source': source}


def verified_binding(reader, account, stage):
    try:
        return reader.verify(account, force=True)
    except Exception as exc:
        raise MomentsSourceError('active_account_unverified', stage=stage,
                                 exception_type=type(exc).__name__) from exc


def verify_source_paths(source, root):
    if not source.is_file() or not source.resolve().is_relative_to(root):
        raise MomentsSourceError('source_missing')
    wal = Path(str(source) + '-wal')
    if ((wal.exists() or wal.is_symlink())
            and (not wal.is_file() or not wal.resolve().is_relative_to(root))):
        raise MomentsSourceError('source_changed')
    return wal


def prepare_moments(reader, account):
    before = verified_binding(reader, account, 'verify_before')
    root = before['source'].parents[2].resolve()
    source = root / 'db_storage/sns/sns.db'
    verify_source_paths(source, root)
    cache = (reader.cache_root.resolve() / 'moments_sources'
             / hashlib.sha256(account.encode()).hexdigest()[:24]).resolve()
    if cache.is_relative_to(root):
        raise MomentsSourceError('cache_invalid')
    cache.mkdir(parents=True, exist_ok=True)
    if cache.resolve() != cache:
        raise MomentsSourceError('cache_invalid')
    for attempt in range(3):
        wal = verify_source_paths(source, root)
        fingerprint = reader.loader._source_fingerprint(source)
        stamp = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
        output = cache / (stamp + '.sqlite')
        if not output.is_file():
            with tempfile.TemporaryDirectory(prefix='snapshot-', dir=cache) as temporary:
                target = Path(temporary) / 'sns.sqlite'
                if reader.loader._is_plain_sqlite(source):
                    # Recovery may write SHM files, so SQLite sees private copies only.
                    copied = Path(temporary) / 'source.sqlite'
                    shutil.copyfile(source, copied)
                    if wal.is_file():
                        shutil.copyfile(wal, Path(str(copied) + '-wal'))
                    reader.loader._backup_plain_database(copied, target)
                else:
                    tool = reader.loader._load_decrypt_tool()
                    manifest = tool.load_manifest(reader.key_manifest)
                    result = tool.decrypt_database(source, target, manifest, force=False, ignore_wal=True)
                    reader.loader._validate_wal_status(result['wal'])
                    if result['wal'].get('commit_frames'):
                        reader.loader._apply_encrypted_wal(source, target, manifest, result['wal'], tool)
                if fingerprint != reader.loader._source_fingerprint(source):
                    continue
                verify_source_paths(source, root)
                db = sqlite3.connect(target.as_uri() + '?mode=ro&immutable=1', uri=True)
                try:
                    if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                        raise MomentsSourceError('snapshot_integrity_failed')
                finally:
                    db.close()
                os.replace(target, output)
        if fingerprint != reader.loader._source_fingerprint(source):
            continue
        verify_source_paths(source, root)
        if verified_binding(reader, account, 'verify_after') != before:
            raise MomentsSourceError('source_changed', stage='verify_after')
        verify_source_paths(source, root)
        if fingerprint != reader.loader._source_fingerprint(source):
            continue
        return {'account': account, 'root': str(root), 'database': str(output),
                'fingerprint': stamp, 'verified_at': time.time(), 'source': 'active_sns_db'}
    raise MomentsSourceError('source_changed')


async def prepare_worker(reader, account):
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'backend.app.moments_source', account, reader.bridge_url,
            str(reader.repository_root), str(reader.key_manifest.resolve()), str(reader.cache_root.resolve()),
            cwd=str(Path(__file__).resolve().parents[2]), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=0x08000000 if sys.platform == 'win32' else 0)
    except OSError as exc:
        raise MomentsSourceError('worker_failed', stage='worker', exception_type=type(exc).__name__) from exc
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=15)
    except TimeoutError as exc:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise MomentsSourceError('worker_timeout', stage='worker', exception_type='TimeoutError') from exc
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if len(stdout) > 16384:
        raise MomentsSourceError('worker_failed', stage='worker')
    try:
        result = json.loads(stdout)
    except (ValueError, UnicodeError) as exc:
        raise MomentsSourceError('worker_failed', stage='worker') from exc
    if not isinstance(result, dict):
        raise MomentsSourceError('worker_failed', stage='worker')
    if process.returncode:
        error = result.get('error')
        if isinstance(error, dict):
            raise MomentsSourceError(error.get('code'), stage=error.get('stage'),
                                     exception_type=error.get('exception_type'))
        raise MomentsSourceError('worker_failed', stage='worker')
    if result.get('account') != account or result.get('source') != 'active_sns_db':
        raise MomentsSourceError('worker_failed', stage='worker')
    return result


if __name__ == '__main__':
    try:
        account, bridge, repository, manifest, cache = sys.argv[1:]
        reader = MomentsRoster(bridge, Path(repository), Path(manifest), Path(cache))
        sys.stdout.buffer.write(json.dumps(prepare_moments(reader, account), ensure_ascii=False).encode('utf-8'))
    except Exception as exc:
        if not isinstance(exc, MomentsSourceError):
            exc = MomentsSourceError('source_permission_denied' if isinstance(exc, PermissionError)
                                     else 'source_read_failed', exception_type=type(exc).__name__)
        sys.stdout.buffer.write(json.dumps({'error': {
            'code': exc.code, 'stage': exc.stage, 'exception_type': exc.exception_type,
        }}).encode('utf-8'))
        sys.exit(1)
