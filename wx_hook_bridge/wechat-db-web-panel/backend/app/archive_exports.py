"""Durable local export jobs; output is staged and published only after scope validation."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import sqlite3
from typing import Literal
from uuid import uuid4

import psutil
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .analysis_snapshots import atomic_json, job_lock, read_json, TERMINAL
from .snapshot_cache import plain_path


def local_write(request):
    origin = request.headers.get('origin', '').rstrip('/')
    allowed = {f'http://{host}:{port}' for host in ('localhost', '127.0.0.1') for port in (5173, 5174, 4173, 5175, 8776, 8765)}
    if origin and origin not in allowed:
        raise HTTPException(403, '仅允许本机应用修改设置。')


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account: str = Field(min_length=1, max_length=128)
    scope: str = Field(min_length=1, max_length=64)
    request_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    source: Literal['messages', 'contacts'] = 'messages'
    format: Literal['json', 'csv', 'html', 'txt', 'markdown', 'xlsx', 'pgsql', 'chatlab', 'chatlab-jsonl'] = 'json'
    sessions: list[str] = Field(default_factory=list, max_length=50)
    all_sessions: bool = False
    start_date: str = ''
    end_date: str = ''
    direction: Literal['all', 'sent', 'received'] = 'all'
    contact_kind: Literal['all', 'friends', 'groups', 'official', 'history'] = 'all'
    use_local_edits: bool = False

    @model_validator(mode='after')
    def selection(self):
        from datetime import date
        for value in (self.start_date, self.end_date):
            if value:
                try:
                    if date.fromisoformat(value).isoformat() != value:
                        raise ValueError()
                except ValueError:
                    raise ValueError('日期必须为 YYYY-MM-DD') from None
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError('结束日期不能早于开始日期')
        if any(not s or len(s) > 256 for s in self.sessions):
            raise ValueError('会话标识无效')
        if self.source == 'messages' and not (self.sessions or self.all_sessions):
            raise ValueError('请选择会话或明确选择全部会话')
        if self.source == 'contacts' and self.format in {'chatlab', 'chatlab-jsonl'}:
            raise ValueError('联系人不支持 ChatLab 格式')
        return self


class ExportAutomation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    enabled: bool
    interval_minutes: Literal[60, 360, 1440] = 1440
    template: ExportRequest


class ArchiveExports:
    def __init__(self, insights, live=None):
        self.insights, self.live = insights, live
        self.root = insights.repository.settings.cache_path.parent / 'archive-exports'
        self.task = None
        self.children = []

    def directory(self, token):
        if not re.fullmatch(r'[a-f0-9]{32}', token):
            raise HTTPException(404, '任务不存在。')
        path = self.root / token
        if not self.root.exists() or not plain_path(self.root) or not path.is_dir() or not plain_path(path):
            raise HTTPException(404, '任务不存在。')
        return path

    def state(self, token):
        directory = self.directory(token)
        value = read_json(directory / 'state.json')
        if not value:
            raise HTTPException(404, '任务不存在。')
        if value['status'] == 'running' and time.time() - value['updated_at'] > 30:
            alive = False
            try:
                process = psutil.Process(value.get('pid', -1))
                alive = abs(process.create_time() - value.get('process_started', 0)) < 2
            except (psutil.Error, ValueError):
                pass
            if not alive:
                value.update(status='interrupted', message='后台进程已停止，请重新创建任务。', finished_at=time.time())
                atomic_json(directory / 'state.json', value)
        return value

    def jobs(self, account, scope):
        self.insights.check(account, scope)
        if not self.root.exists():
            return {'jobs': []}
        result = []
        for path in self.root.iterdir():
            if re.fullmatch(r'[a-f0-9]{32}', path.name):
                state = self.state(path.name)
                if state['account'] == account and state['scope'] == scope:
                    result.append(state)
        return {'jobs': sorted(result, key=lambda s: s['started_at'], reverse=True)[:30]}

    def create(self, payload, trigger='manual', expected_revision=None):
        identity = self.insights.check(payload.account, payload.scope)
        with job_lock(self.root):
            if expected_revision is not None:
                config = self.config()
                if not config['enabled'] or config['revision'] != expected_revision:
                    return None
            token = payload.request_id
            if (self.root / token).exists():
                state = self.state(token)
                original = read_json(self.directory(token) / 'request.json')['payload']
                if original != payload.model_dump():
                    raise HTTPException(409, '请求标识已用于其他导出。')
                return state
            for path in self.root.iterdir():
                if re.fullmatch(r'[a-f0-9]{32}', path.name) and self.state(path.name)['status'] not in TERMINAL:
                    raise HTTPException(409, '已有导出正在执行，请等待或取消。')
            cache = self.insights.repository.ensure_cache()
            self.insights.check(payload.account, payload.scope)
            directory = self.root / token
            directory.mkdir()
            settings = {key: str(value) if isinstance(value, Path) else value
                        for key, value in asdict(self.insights.repository.settings).items()}
            atomic_json(directory / 'request.json', {'payload': payload.model_dump(), 'settings': settings,
                'cache': str(cache), 'identity': identity})
            now = time.time()
            state = {'id': token, 'account': payload.account, 'scope': payload.scope, 'format': payload.format,
                     'source': payload.source, 'trigger': trigger, 'status': 'running', 'rows': 0,
                     'started_at': now, 'updated_at': now, 'message': '正在启动本机导出任务。'}
            atomic_json(directory / 'state.json', state)
            try:
                self.children = [child for child in self.children if child.poll() is None]
                self.children.append(subprocess.Popen([sys.executable, '-m', 'backend.app.archive_export_worker', '--launch', str(directory)],
                    cwd=str(self.insights.repository.settings.panel_root), stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=0x08000000 if os.name == 'nt' else 0))
            except OSError:
                state.update(status='failed', message='无法启动导出任务。', finished_at=time.time())
                atomic_json(directory / 'state.json', state)
                raise HTTPException(503, state['message']) from None
        return state

    def cancel(self, token, account, scope):
        self.insights.check(account, scope)
        state = self.state(token)
        if (state['account'], state['scope']) != (account, scope):
            raise HTTPException(404, '任务不存在。')
        if state['status'] not in TERMINAL:
            atomic_json(self.directory(token) / 'cancel.json', {'at': time.time()})
        return {'cancel_requested': True}

    def download(self, token, account, scope):
        self.insights.check(account, scope)
        state = self.state(token)
        if (state['account'], state['scope']) != (account, scope) or state['status'] != 'succeeded':
            raise HTTPException(409, '导出尚未完成或快照已变化。')
        path = self.directory(token) / 'archive.zip'
        if not path.is_file() or not plain_path(path):
            raise HTTPException(404, '导出文件不存在。')
        return path

    def config(self):
        return read_json(self.root / 'automation.json', {'enabled': False, 'revision': 0, 'interval_minutes': 1440, 'template': None})

    async def configure(self, value):
        self.insights.check(value.template.account, value.template.scope)
        if value.enabled:
            try:
                profile = await self.live.profile(force=True) if self.live else {}
            except (httpx.HTTPError, ValueError):
                raise HTTPException(503, '本机微信桥接暂不可用，请稍后重试。') from None
            if profile.get('username') != value.template.account:
                raise HTTPException(409, '请先连接自动导出绑定的微信账号。')
        with job_lock(self.root):
            current = self.config()
            if current['revision'] != value.revision:
                raise HTTPException(409, '设置已变化，请重新读取。')
            result = value.model_dump()
            result.update(revision=value.revision + 1, last_attempt_at=time.time())
            atomic_json(self.root / 'automation.json', result)
        return result

    async def tick(self):
        config = self.config()
        if not config['enabled'] or time.time()-config.get('last_attempt_at', 0) < config['interval_minutes']*60:
            return
        template = config['template']
        profile = await self.live.profile(force=True) if self.live else {}
        if profile.get('username') != template['account']:
            return
        identity = await asyncio.to_thread(self.insights.identity)
        if identity['account'] != template['account']:
            return
        # Scheduled rules follow newer snapshots of the explicitly bound account.
        request = ExportRequest(**{**template, 'scope': identity['scope'], 'request_id': uuid4().hex})
        with job_lock(self.root):
            latest = self.config()
            if latest['revision'] != config['revision'] or not latest['enabled']:
                return
            latest['last_attempt_at'] = time.time()
            atomic_json(self.root / 'automation.json', latest)
        await asyncio.to_thread(self.create, request, 'scheduled', config['revision'])

    async def start(self):
        async def loop():
            while True:
                try:
                    await self.tick()
                except (HTTPException, OSError, ValueError, httpx.HTTPError, sqlite3.DatabaseError):
                    pass
                await asyncio.sleep(30)
        self.task = asyncio.create_task(loop())

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        for child in self.children:
            if child.poll() is not None:
                child.wait()


def create_exports_router(service):
    router = APIRouter(prefix='/api/archive/exports', tags=['local archive'])

    @router.get('')
    def jobs(account: str, scope: str):
        return service.jobs(account, scope)

    @router.post('')
    def create(payload: ExportRequest, request: Request):
        local_write(request)
        return service.create(payload)

    @router.get('/automation')
    def config(account: str, scope: str):
        service.insights.check(account, scope)
        value = service.config()
        if value.get('template') and value['template']['account'] != account:
            return {**value, 'template': None, 'enabled': False}
        return value

    @router.post('/automation')
    async def configure(payload: ExportAutomation, request: Request):
        local_write(request)
        return await service.configure(payload)

    @router.post('/{token}/cancel')
    def cancel(token: str, account: str, scope: str, request: Request):
        local_write(request)
        return service.cancel(token, account, scope)

    @router.get('/{token}/download')
    def download(token: str, account: str, scope: str):
        return FileResponse(service.download(token, account, scope), media_type='application/zip', filename=f'wechat-{token[:8]}.zip')

    return router
