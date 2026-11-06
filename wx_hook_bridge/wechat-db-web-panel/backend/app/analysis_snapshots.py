"""Persistent, account-bound local analytics snapshot jobs."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager, nullcontext
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Literal
from uuid import uuid4

import psutil
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .snapshot_cache import plain_path

TERMINAL = {'succeeded', 'failed', 'cancelled', 'interrupted'}
PUBLIC_FIELDS = {
    'id', 'account', 'trigger', 'status', 'stage', 'file', 'completed_files',
    'total_files', 'completed_bytes', 'total_bytes', 'file_bytes', 'file_total_bytes',
    'message_count', 'started_at', 'updated_at', 'finished_at', 'error_code', 'message',
    'unresolved_count', 'unresolved_files',
    'validation_warning_count', 'validation_warning_files',
    'resource_files',
}


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
    try:
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def job_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    if not plain_path(root):
        raise RuntimeError('snapshot job directory must not be a link')
    path = root / '.lock'
    if path.exists() and not plain_path(path):
        raise RuntimeError('invalid snapshot job lock')
    with path.open('a+b') as handle:
        if not handle.tell():
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise HTTPException(409, '任务状态正在切换，请稍后重试。') from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


class StartSnapshot(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account: str = Field(min_length=1, max_length=128)


class AutoSnapshot(StartSnapshot):
    enabled: bool
    interval_minutes: Literal[60, 360, 1440] = 360
    revision: int = Field(ge=0)


class AnalysisSnapshots:
    def __init__(self, settings, live):
        self.settings = settings
        self.live = live
        self.root = settings.cache_path.parent / 'analysis-snapshots'
        self._task = None
        self._capability = None
        self._capability_at = 0.0
        self._capability_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._children = []

    def config(self):
        return read_json(self.root / 'automation.json', {
            'enabled': False, 'account': '', 'interval_minutes': 360, 'revision': 0,
            'last_attempt_at': 0,
        })

    def current(self, locked=False):
        pointer = read_json(self.root / 'current.json')
        if not pointer:
            return None
        token = str(pointer.get('id', ''))
        if len(token) != 32 or any(c not in '0123456789abcdef' for c in token):
            raise RuntimeError('invalid snapshot job id')
        directory = self.root / token
        state = read_json(directory / 'state.json')
        if not state or state['status'] in TERMINAL:
            return state
        identity = read_json(directory / 'process.json', {})
        alive = False
        try:
            alive = psutil.Process(identity['pid']).create_time() == identity['created_at']
        except (KeyError, psutil.Error):
            pass
        if not alive and time.time() - state['started_at'] > 30:
            # Serialize recovery with final publication/cancellation. Re-read after locking.
            with nullcontext() if locked else job_lock(self.root):
                state = read_json(directory / 'state.json')
                if state['status'] not in TERMINAL:
                    report = read_json(self.settings.report_path, {})
                    published = (report.get('account') == state['account'] and
                        report.get('output_root') == str(self.settings.database_root.with_name(
                            f'{self.settings.database_root.name}.{token}').resolve()))
                    if published:
                        state.update(status='succeeded', stage='complete', finished_at=time.time(),
                                     message='快照已发布，任务状态已恢复。')
                    else:
                        state.update(status='interrupted', stage='interrupted', finished_at=time.time(),
                                     error_code='worker_stopped', message='后台任务已中断，可重新生成；上一份快照仍保留。')
                    atomic_json(directory / 'state.json', state)
        return state

    async def capability(self, force=False):
        async with self._capability_lock:
            if not force and self._capability and time.monotonic() - self._capability_at < 10:
                return self._capability
            try:
                profile = await self.live.profile(force=force)
                account = str(profile.get('username') or '')
                if not account or self.live.roster is None:
                    raise RuntimeError('unavailable')
                # Verify handles in the existing isolated reader, not the API's GIL.
                await self.live.roster.verify_async(account)
                result = {'available': True, 'account': account, 'reason': ''}
            except Exception:
                result = {'available': False, 'account': '', 'reason': '请登录本机微信，并确认聊天桥接已连接。'}
            self._capability, self._capability_at = result, time.monotonic()
            return result

    async def status(self):
        job = await asyncio.to_thread(self.current)
        self._children = [p for p in self._children if p.poll() is None]
        return {'capability': await self.capability(), 'automation': self.config(),
                'job': {key: value for key, value in job.items() if key in PUBLIC_FIELDS} if job else None}

    async def create(self, account, trigger='manual'):
        async with self._start_lock:
            capability = await self.capability(force=True)
            if not capability['available'] or capability['account'] != account:
                raise HTTPException(409, '微信账号已变化或未连接，请刷新后重试。')
            with job_lock(self.root):
                current = self.current(locked=True)
                if current and current['status'] not in TERMINAL:
                    if current['account'] != account:
                        raise HTTPException(409, '另一账号的快照任务正在执行。')
                    return {'job': {k: v for k, v in current.items() if k in PUBLIC_FIELDS}}
                token = uuid4().hex
                directory = self.root / token
                directory.mkdir()
                settings = {key: str(value) if isinstance(value, Path) else value
                            for key, value in asdict(self.settings).items()}
                atomic_json(directory / 'request.json', {'settings': settings, 'account': account,
                            'key_manifest': str(self.live.roster.key_manifest)})
                now = time.time()
                state = {'id': token, 'account': account, 'trigger': trigger, 'status': 'running',
                         'stage': 'preflight', 'started_at': now, 'updated_at': now,
                         'message': '正在核验账号、存储空间和数据库。', 'completed_files': 0,
                         'total_files': 0, 'completed_bytes': 0, 'total_bytes': 0}
                atomic_json(directory / 'state.json', state)
                atomic_json(self.root / 'current.json', {'id': token})
                try:
                    process = subprocess.Popen(
                        [sys.executable, '-m', 'backend.app.analysis_snapshot_worker', '--launch', str(directory)],
                        cwd=str(self.settings.panel_root), stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=0x08000000 if os.name == 'nt' else 0,
                    )
                    self._children.append(process)
                except Exception:
                    state.update(status='failed', stage='failed', finished_at=time.time(),
                                 error_code='launch_failed', message='无法启动后台任务，请重试。')
                    atomic_json(directory / 'state.json', state)
                    raise HTTPException(503, state['message']) from None
                config = self.config()
                config['last_attempt_at'] = now
                atomic_json(self.root / 'automation.json', config)
            return {'job': state}

    async def cancel(self, token):
        with job_lock(self.root):
            state = self.current(locked=True)
            if not state or state['id'] != token:
                raise HTTPException(409, '任务已变化，请刷新后重试。')
            if state['status'] not in TERMINAL:
                atomic_json(self.root / token / 'cancel.json', {'requested_at': time.time()})
        return {'cancel_requested': True}

    async def configure(self, payload):
        if payload.enabled:
            capability = await self.capability(force=True)
            if not capability['available'] or capability['account'] != payload.account:
                raise HTTPException(409, '请先连接配置所绑定的微信账号。')
        with job_lock(self.root):
            config = self.config()
            if config['revision'] != payload.revision:
                raise HTTPException(409, '自动更新配置已变化，请刷新后重试。')
            config.update(enabled=payload.enabled, account=payload.account,
                          interval_minutes=payload.interval_minutes, revision=config['revision'] + 1)
            atomic_json(self.root / 'automation.json', config)
        return config

    async def tick(self):
        config = self.config()
        if not config['enabled'] or time.time() - config['last_attempt_at'] < config['interval_minutes'] * 60:
            return
        current = await asyncio.to_thread(self.current)
        if current and current['status'] not in TERMINAL:
            return
        capability = await self.capability()
        if capability['available'] and capability['account'] == config['account']:
            await self.create(config['account'], 'automatic')

    async def start(self):
        async def loop():
            while True:
                try:
                    await self.tick()
                except Exception:
                    # A disconnected bridge is retried at the next tick; jobs record their own failures.
                    pass
                await asyncio.sleep(30)
        self._task = asyncio.create_task(loop())

    async def close(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # The isolated worker and its persisted progress survive API restarts.


def create_snapshot_router(service):
    router = APIRouter(prefix='/api/analysis-snapshots', tags=['analysis snapshots'])

    def local(request):
        allowed = {f'http://{host}:{port}' for host in ('localhost', '127.0.0.1') for port in (5173, 5174, 4173, 8765)}
        if not request.client or request.client.host not in {'127.0.0.1', '::1', 'testclient'}:
            raise HTTPException(403, '快照操作仅允许本机访问。')
        if request.headers.get('origin', '').rstrip('/') not in allowed | {''}:
            raise HTTPException(403, '拒绝外部页面操作本地快照。')

    @router.get('')
    async def status(request: Request):
        local(request)
        return await service.status()

    @router.post('')
    async def create(payload: StartSnapshot, request: Request):
        local(request)
        return await service.create(payload.account)

    @router.post('/{token}/cancel')
    async def cancel(token: str, request: Request):
        local(request)
        return await service.cancel(token)

    @router.post('/automation')
    async def configure(payload: AutoSnapshot, request: Request):
        local(request)
        return await service.configure(payload)

    return router
