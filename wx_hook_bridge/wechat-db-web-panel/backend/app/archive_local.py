"""Account-bound local copies and opt-in notification inbox. Originals are read-only."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import sqlite3
import time
from typing import Literal

import httpx

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .archive_exports import local_write


def content_hash(content):
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


class LocalEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account: str
    scope: str
    message_key: str = Field(min_length=1, max_length=256)
    original_hash: str
    revision: int = Field(ge=0)
    text: str | None = Field(default=None, max_length=100000)
    note: str = Field(default='', max_length=2000)


class NotificationConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=0)
    enabled: bool = False
    mode: Literal['all', 'allowlist', 'blocklist'] = 'all'
    sessions: list[str] = Field(default_factory=list, max_length=200)
    preview: bool = False
    quiet_start: int = Field(default=23, ge=0, le=23)
    quiet_end: int = Field(default=8, ge=0, le=23)
    quiet_enabled: bool = False


class ArchiveLocal:
    def __init__(self, insights, live=None):
        self.insights, self.live = insights, live
        self.path = insights.repository.settings.cache_path.parent / 'archive-local.sqlite'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS local_edits(account,scope,message_key,original_hash,text,note,revision INTEGER,
                    PRIMARY KEY(account,scope,message_key));
                CREATE TABLE IF NOT EXISTS notification_config(account PRIMARY KEY,data);
                CREATE TABLE IF NOT EXISTS notification_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    account,event_id,session,title,body,created_at,UNIQUE(account,event_id));
                CREATE INDEX IF NOT EXISTS notification_account ON notification_events(account,sequence);
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.row_factory = sqlite3.Row
            with db:
                yield db
        finally:
            db.close()

    def preview(self, account, scope, session):
        self.insights.check(account, scope)
        with self.insights.repository.cache() as db:
            rows = [dict(r) for r in db.execute('SELECT message_key,content,summary,sender_name,local_date FROM messages WHERE session_username=? ORDER BY create_time DESC,sort_seq DESC LIMIT 20', (session,))]
        with self.db() as db:
            for row in rows:
                row['original_hash'] = content_hash(row['content'])
                saved = db.execute('SELECT text,note,revision FROM local_edits WHERE account=? AND scope=? AND message_key=? AND original_hash=?',
                    (account, scope, row['message_key'], row['original_hash'])).fetchone()
                row['edit'] = dict(saved) if saved else {'text': None, 'note': '', 'revision': 0}
        self.insights.check(account, scope)
        return {'messages': rows}

    def edit(self, value):
        self.insights.check(value.account, value.scope)
        with self.insights.repository.cache() as db:
            source = db.execute('SELECT content FROM messages WHERE message_key=?', (value.message_key,)).fetchone()
        if not source or content_hash(source[0]) != value.original_hash:
            raise HTTPException(409, '原文已变化，请重新读取。')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT revision FROM local_edits WHERE account=? AND scope=? AND message_key=?', (value.account,value.scope,value.message_key)).fetchone()
            if (current[0] if current else 0) != value.revision:
                raise HTTPException(409, '本地副本已变化，请重新读取。')
            db.execute('INSERT OR REPLACE INTO local_edits VALUES (?,?,?,?,?,?,?)',
                       (value.account,value.scope,value.message_key,value.original_hash,value.text,value.note,value.revision+1))
        return {'revision': value.revision+1}

    def config(self, account):
        with self.db() as db:
            row = db.execute('SELECT data FROM notification_config WHERE account=?', (account,)).fetchone()
        return json.loads(row[0]) if row else NotificationConfig(account=account, revision=0).model_dump()

    def enabled(self):
        # The global browser bridge can check opt-in without waking the native bridge.
        with self.db() as db:
            return any(json.loads(row[0]).get('enabled') is True
                       for row in db.execute('SELECT data FROM notification_config'))

    async def verify(self, account):
        try:
            profile = await self.live.profile(force=True) if self.live else {}
        except (httpx.HTTPError, ValueError):
            raise HTTPException(503, '本机微信桥接暂不可用，请稍后重试。') from None
        if profile.get('username') != account:
            raise HTTPException(409, '当前微信账号未连接或已变化。')

    async def configure(self, value):
        await self.verify(value.account)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM notification_config WHERE account=?', (value.account,)).fetchone()
            current = json.loads(row[0]) if row else {'revision': 0}
            if current['revision'] != value.revision:
                raise HTTPException(409, '提醒设置已变化，请重新读取。')
            result = {**value.model_dump(), 'revision': value.revision+1}
            db.execute('INSERT OR REPLACE INTO notification_config VALUES (?,?)', (value.account,json.dumps(result)))
        return result

    def observe(self, account, event_id, message):
        # Called only for fresh realtime envelopes, after chat commit. Never on history imports.
        if message.get('is_self') or abs(time.time()-float(message.get('create_time') or 0)) > 120 or message.get('category') in {'system','recall'}:
            return
        config = self.config(account)
        if not config['enabled']:
            return
        session = message['session_username']
        listed = session in config['sessions']
        if config['mode'] == 'allowlist' and not listed or config['mode'] == 'blocklist' and listed:
            return
        hour = time.localtime().tm_hour
        start, end = config['quiet_start'], config['quiet_end']
        quiet = start <= hour < end if start <= end else hour >= start or hour < end
        if config['quiet_enabled'] and quiet:
            return
        title = str(message.get('session_name') or session)[:100]
        body = str(message.get('summary') or message.get('content') or '收到新消息')[:160] if config['preview'] else '收到一条新消息'
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO notification_events(account,event_id,session,title,body,created_at) VALUES (?,?,?,?,?,?)',
                       (account,event_id,session,title,body,time.time()))
            db.execute('DELETE FROM notification_events WHERE sequence NOT IN (SELECT sequence FROM notification_events ORDER BY sequence DESC LIMIT 1000)')

    async def poll(self, account, after):
        await self.verify(account)
        config = self.config(account)
        with self.db() as db:
            newest = db.execute('SELECT COALESCE(MAX(sequence),0) FROM notification_events WHERE account=?', (account,)).fetchone()[0]
            rows = [dict(r) for r in db.execute('SELECT sequence,session,title,body FROM notification_events WHERE account=? AND sequence>? AND created_at>? ORDER BY sequence LIMIT 50',
                    (account,after,time.time()-120))] if config['enabled'] and after is not None else []
        rows = [r for r in rows if config['mode'] == 'all' or
                config['mode'] == 'allowlist' and r['session'] in config['sessions'] or
                config['mode'] == 'blocklist' and r['session'] not in config['sessions']]
        if not config['preview']:
            for row in rows:
                row['body'] = '收到一条新消息'
        return {'config': config, 'events': rows, 'cursor': rows[-1]['sequence'] if rows else newest}


def create_local_router(service):
    router = APIRouter(prefix='/api/archive', tags=['local archive'])

    @router.get('/preview')
    def preview(account: str, scope: str, session: str):
        return service.preview(account, scope, session)

    @router.post('/edit')
    def edit(value: LocalEdit, request: Request):
        local_write(request)
        return service.edit(value)

    @router.get('/notifications/enabled')
    def enabled():
        return {'enabled': service.enabled()}

    @router.get('/notifications')
    async def notifications(account: str, after: int | None = None):
        return await service.poll(account, after)

    @router.post('/notifications')
    async def configure(value: NotificationConfig, request: Request):
        local_write(request)
        return await service.configure(value)

    return router
