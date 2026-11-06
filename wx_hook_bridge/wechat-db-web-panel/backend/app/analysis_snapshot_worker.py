"""Read-only native capture and atomic publication, isolated from live chat."""
from __future__ import annotations

from dataclasses import replace
from contextlib import closing
import ctypes
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time

import psutil

from .active_roster import ActiveRosterError, ActiveWechatRoster
from .analysis_snapshots import TERMINAL, atomic_json, job_lock, read_json
from .config import Settings
from .repository import CACHE_SOURCE_DATABASES, WeChatRepository
from .snapshot_cache import plain_path
from .sns_sync import SnsSnapshotSyncError


class SnapshotFailure(RuntimeError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


class SnapshotCancelled(Exception):
    pass


def file_version(path):
    from ctypes import wintypes
    version = ctypes.WinDLL('version', use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p]
    version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p]
    size = version.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return ''
    buffer = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(str(path), 0, size, buffer):
        return ''
    pointer, length = ctypes.c_void_p(), wintypes.UINT()
    if not version.VerQueryValueW(buffer, '\\', ctypes.byref(pointer), ctypes.byref(length)):
        return ''
    fields = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD))
    return '.'.join(str(value) for value in (fields[2] >> 16, fields[2] & 65535, fields[3] >> 16, fields[3] & 65535))


def inventory(root):
    """Refuse reparse points before traversing; never accept API-supplied source paths."""
    files = []
    for directory, names, filenames in os.walk(root, followlinks=False):
        directory = Path(directory)
        if not plain_path(directory) or any(not plain_path(directory / name) for name in names):
            raise SnapshotFailure('unsafe_source', '数据库目录包含链接，已停止读取。')
        for name in filenames:
            path = directory / name
            if path.suffix.casefold() in {'.db', '.kvdb'}:
                if not plain_path(path):
                    raise SnapshotFailure('unsafe_source', '数据库文件是链接，已停止读取。')
                files.append(path)
    relative = {p.relative_to(root).as_posix() for p in files}
    if not set(CACHE_SOURCE_DATABASES).issubset(relative):
        raise SnapshotFailure('missing_databases', '当前账号数据库不完整，请等待微信加载完成后重试。')
    return sorted(files, key=lambda p: p.relative_to(root).as_posix())


class SnapshotWorker:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.root = self.directory.parent
        self.request = read_json(self.directory / 'request.json')
        values = self.request['settings']
        for name in ('panel_root', 'repository_root', 'database_root', 'report_path', 'cache_path', 'fts_extension', 'weixin_dll'):
            values[name] = Path(values[name])
        self.settings = Settings(**values)
        self.account = self.request['account']
        self.roster = ActiveWechatRoster(self.settings.friend_automation_bridge_url,
            self.settings.repository_root, Path(self.request['key_manifest']), self.settings.cache_path.parent)
        self.loader = self.roster.loader
        self.state = read_json(self.directory / 'state.json')
        self._last_write = 0.0
        self.binding = None
        self._last_binding = 0.0

    def check(self, binding=False):
        if (self.directory / 'cancel.json').exists():
            raise SnapshotCancelled()
        if binding or time.monotonic() - self._last_binding > 30:
            try:
                current = self.roster.verify(self.account, force=True)
            except (ActiveRosterError, psutil.Error):
                raise SnapshotFailure('account_changed', '微信账号、进程或桥接连接已变化，任务已停止；上一份快照仍保留。') from None
            self._last_binding = time.monotonic()
            if self.binding and current != self.binding:
                raise SnapshotFailure('account_changed', '微信账号或进程已切换，任务已停止；上一份快照仍保留。')

    def progress(self, force=False, **values):
        self.check()
        changed = any(key in {'stage', 'message', 'file'} and self.state.get(key) != value
                      for key, value in values.items())
        self.state.update(values, updated_at=time.time())
        if force or changed or time.monotonic() - self._last_write >= 1:
            atomic_json(self.directory / 'state.json', self.state)
            self._last_write = time.monotonic()

    def copy_source(self, source, private):
        for attempt in range(3):
            self.check()
            before = self.loader._source_fingerprint(source)
            for suffix in ('', '-wal'):
                original, target = Path(str(source) + suffix), Path(str(private) + suffix)
                target.unlink(missing_ok=True)
                if original.exists():
                    if not plain_path(original):
                        raise SnapshotFailure('unsafe_source', '数据库或 WAL 文件是链接，已停止读取。')
                    with original.open('rb') as src, target.open('wb') as dst:
                        while chunk := src.read(4 * 1024 * 1024):
                            self.check()
                            dst.write(chunk)
            if before == self.loader._source_fingerprint(source):
                return
            time.sleep(.5)
        raise SnapshotFailure('source_busy', '数据库持续写入，未获得一致副本，请稍后重试。')

    def run(self):
        if os.name != 'nt':
            raise SnapshotFailure('unsupported_platform', '一键解密目前支持 Windows 微信 4.1.9.23。')
        self.binding = self.roster.verify(self.account, force=True)
        if file_version(psutil.Process(self.binding['pid']).exe()) != '4.1.9.23':
            raise SnapshotFailure('unsupported_version', '当前微信版本尚未适配一键解密，支持版本为 4.1.9.23。')
        source_root = self.binding['source'].parents[1]
        files = inventory(source_root)
        total = sum(p.stat().st_size + (Path(str(p) + '-wal').stat().st_size if Path(str(p) + '-wal').exists() else 0) for p in files)
        output = self.settings.database_root.with_name(f'{self.settings.database_root.name}.{self.state["id"]}')
        output.parent.mkdir(parents=True, exist_ok=True)
        # Capture + decrypted copy + analytics index; retain the previously published snapshot.
        requirements = {}
        for location, size in ((output.parent, total * 2), (self.root, total * 2)):
            anchor = str(location.resolve().anchor).casefold()
            requirements[anchor] = requirements.get(anchor, 0) + size
        if any(shutil.disk_usage(anchor).free < size + 1024 ** 3 for anchor, size in requirements.items()):
            raise SnapshotFailure('insufficient_space', '可用空间不足，需预留数据库大小约四倍及 1 GB 空间。')
        self.progress(force=True, total_files=len(files), total_bytes=total, stage='keys', message='正在校验本机密钥。')
        tool = self.loader._load_decrypt_tool()
        manifest_path = self.roster.key_manifest
        manifest = None
        try:
            manifest = tool.load_manifest(manifest_path)
            # All salts must be covered; fall back to extracting only from the bound process.
            for path in files:
                with path.open('rb') as stream:
                    tool.choose_key(manifest, stream.read(int(manifest['sqlcipher']['page_size'])))
        except (OSError, ValueError, KeyError, tool.DecryptError):
            self.progress(force=True, message='正在从已核验的微信进程读取数据库密钥。')
            previous_manifest = manifest
            manifest = tool.build_key_manifest(self.binding['pid'], chunk_size=8 * 1024 * 1024, private_only=True)
            if previous_manifest and previous_manifest.get('sqlcipher') == manifest.get('sqlcipher'):
                keys = {(item['salt_hex'], item['key_hex']): item
                        for item in previous_manifest['keys'] + manifest['keys']}
                manifest['keys'] = list(keys.values())
            self.check(binding=True)
            manifest_path = self.directory / 'keys.dpapi'
            tool.write_protected_manifest(manifest_path, manifest)
        output.mkdir()
        private = self.directory / 'source.db'
        results = []
        failures = []
        verification_kwargs = {'fts_extension': self.settings.fts_extension,
                               'weixin_dll': self.settings.weixin_dll, 'allow_unsupported_tokenizer': True}
        completed = 0
        try:
            for index, source in enumerate(files):
                relative = source.relative_to(source_root)
                try:
                    with source.open('rb') as stream:
                        tool.choose_key(manifest, stream.read(int(manifest['sqlcipher']['page_size'])))
                except tool.DecryptError:
                    if relative.as_posix() in CACHE_SOURCE_DATABASES or relative.name.startswith('message_') and relative.stem[8:].isdigit():
                        raise SnapshotFailure('required_key_missing', f'必需数据库 {relative.as_posix()} 的密钥不可用，未切换快照。') from None
                    failures.append({'input': str(source), 'error': 'missing validated key for optional database'})
                    self.progress(force=True, completed_files=index + 1, unresolved_count=len(failures),
                                  unresolved_files=[Path(item['input']).relative_to(source_root).as_posix() for item in failures])
                    continue
                self.progress(force=True, stage='copying', file=relative.as_posix(), file_bytes=0,
                              file_total_bytes=source.stat().st_size, message='正在复制数据库及已提交的 WAL。')
                self.copy_source(source, private)
                self.progress(force=True, stage='decrypting', message='正在解密并校验数据库。')
                try:
                    result = tool.decrypt_database(private, output / relative, manifest, force=False, ignore_wal=True,
                        **verification_kwargs,
                        progress_callback=lambda done, size: self.progress(file_bytes=done, file_total_bytes=size))
                    self.loader._validate_wal_status(result['wal'])
                    if result['wal'].get('commit_frames'):
                        self.loader._apply_encrypted_wal(private, output / relative, manifest, result['wal'], tool,
                                                         verification_kwargs=verification_kwargs)
                        result.update(tool.verify_plain_database(output / relative, **verification_kwargs))
                except (tool.DecryptError, sqlite3.DatabaseError, SnsSnapshotSyncError):
                    if relative.as_posix() in CACHE_SOURCE_DATABASES or relative.name.startswith('message_') and relative.stem[8:].isdigit():
                        raise SnapshotFailure('required_database_failed', f'必需数据库 {relative.as_posix()} 未通过解密或完整性校验，未切换快照。') from None
                    (output / relative).unlink(missing_ok=True)
                    failures.append({'input': str(source), 'error': 'optional database failed cryptographic or integrity validation'})
                    self.progress(force=True, completed_files=index + 1, unresolved_count=len(failures),
                                  unresolved_files=[Path(item['input']).relative_to(source_root).as_posix() for item in failures])
                    continue
                result.update(input=str(source), bytes=(output / relative).stat().st_size)
                result['pages'] = result['bytes'] // int(manifest['sqlcipher']['page_size'])
                result['wal']['applied_committed_frames'] = int(result['wal'].get('last_commit_frame') or 0)
                results.append(result)
                completed += source.stat().st_size + int(result['wal'].get('size') or 0)
                self.progress(force=True, completed_files=index + 1, completed_bytes=completed)
                for suffix in ('', '-wal', '-shm'):
                    Path(str(private) + suffix).unlink(missing_ok=True)
            self.progress(force=True, stage='validating', file='', message='正在生成完整性报告。')
            report = tool.build_decryption_report(account=self.account, source_root=source_root,
                key_manifest=manifest_path, output_root=output, results=results, failures=failures,
                reserve_size=int(manifest['sqlcipher']['reserve_size']))
            warnings = [item['path'] for item in report['files'] if item['quick_check'] != 'ok' or item['integrity_check'] != 'ok']
            self.progress(force=True, validation_warning_count=len(warnings), validation_warning_files=warnings)
            candidate_report = self.directory / 'report.json'
            atomic_json(candidate_report, report)
            self.progress(force=True, stage='indexing', message='正在建立统计索引，首次全量处理可能需要较长时间。')
            candidate = WeChatRepository(replace(self.settings, report_path=candidate_report))
            if not candidate.configured:
                raise SnapshotFailure('validation_failed', '解密报告或必需数据库未通过校验，未切换快照。')
            candidate.build_progress = lambda **values: self.progress(**values)
            cache = candidate.ensure_cache()
            with closing(sqlite3.connect(cache.as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
                if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise SnapshotFailure('validation_failed', '统计索引完整性校验失败，未切换快照。')
                count = int(db.execute("SELECT value FROM meta WHERE key='message_count'").fetchone()[0])
            self.progress(force=True, stage='publishing', message='校验通过，正在切换到新快照。', message_count=count)
            # Cancellation and publication share one cross-process gate.
            with job_lock(self.root):
                self.check(binding=True)
                previous = self.settings.report_path
                if previous.exists():
                    shutil.copyfile(previous, self.directory / 'previous-report.json')
                atomic_json(previous, report)
                self.state.update(status='succeeded', stage='complete', finished_at=time.time(),
                                  updated_at=time.time(), unresolved_count=len(failures),
                                  message=(f'分析快照已生成，{len(failures)} 个附属库未解密，详见下方列表。' if failures else '快照已生成并通过统计校验。'))
                atomic_json(self.directory / 'state.json', self.state)
        finally:
            for suffix in ('', '-wal', '-shm'):
                Path(str(private) + suffix).unlink(missing_ok=True)


def main():
    worker = SnapshotWorker(sys.argv[1])
    atomic_json(worker.directory / 'process.json', {'pid': os.getpid(), 'created_at': psutil.Process().create_time()})
    try:
        worker.run()
    except BaseException as exc:
        # Do not serialize exception text: a native/decrypt exception can contain local secrets.
        if worker.state.get('status') in TERMINAL:
            return
        cancelled = isinstance(exc, SnapshotCancelled) or (worker.directory / 'cancel.json').exists()
        worker.state.update(status='cancelled' if cancelled else 'failed',
            failed_stage=worker.state.get('stage'), stage='cancelled' if cancelled else 'failed',
            finished_at=time.time(), updated_at=time.time(),
            error_type=type(exc).__name__,
            error_code='cancelled' if cancelled else getattr(exc, 'code', 'snapshot_failed'),
            message='任务已取消，上一份快照仍保留。' if cancelled else getattr(exc, 'message', '快照生成失败，上一份快照仍保留。请检查微信登录状态后重试。'))
        atomic_json(worker.directory / 'state.json', worker.state)


if __name__ == '__main__':
    if sys.argv[1] == '--launch':
        # A short-lived launcher leaves the worker outside the API supervisor's child tree.
        subprocess.Popen([sys.executable, '-m', 'backend.app.analysis_snapshot_worker', sys.argv[2]],
            cwd=str(Path(__file__).resolve().parents[2]), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=0x08000000 if os.name == 'nt' else 0)
    else:
        main()
