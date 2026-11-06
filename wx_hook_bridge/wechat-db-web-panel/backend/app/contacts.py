from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from .chat_history import readonly
from .parsing import _parse_xml


class RemarkEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account: str = Field(min_length=1, max_length=240)
    remark: str = Field(max_length=64, pattern=r'^[^\x00-\x1f]*$')
    expected_remark: str = Field(max_length=2000)
    request_id: UUID


class Contacts:
    def __init__(self, service):
        self.service = service
        self.write_lock = asyncio.Lock()
        with service.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS contact_profiles(account TEXT NOT NULL, username TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(account,username));
                CREATE TABLE IF NOT EXISTS contact_directory_state(account TEXT PRIMARY KEY, updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS contact_remark_requests(account TEXT NOT NULL, request_id TEXT NOT NULL, username TEXT NOT NULL, fingerprint TEXT NOT NULL, remark TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(account,request_id));
            ''')

    def list(self, account, q='', offset=0, limit=100):
        with self.service.db() as db:
            state = db.execute('SELECT updated_at FROM contact_directory_state WHERE account=?', (account,)).fetchone()
            items = [json.loads(r[0]) for r in db.execute('SELECT data FROM contact_profiles WHERE account=?', (account,))]
            eligible = {r[0] for r in db.execute("SELECT username FROM live_roster WHERE account=? AND relation='friend'", (account,))}
        count = len(items)
        items = [item for item in items if q.casefold() in item.get('search_text', '')]
        items.sort(key=lambda item: (item.get('initial') == '#', item.get('initial', '#'), item.get('sort_key', ''), item['username']))
        return {'account': account, 'ready': state is not None, 'updated_at': state[0] if state else None,
                'directory_count': count if state else None, 'total': len(items),
                'items': [self.present(account, item, item['username'] in eligible) for item in items[offset:offset + limit]],
                **self.service.roster_state(account)}

    def present(self, account, item, eligible):
        result = {k: v for k, v in item.items() if k not in {'search_text', 'sort_key'}}
        result.update(avatar_url=self.service.avatar_url(account, item['username']), can_send=eligible,
                      can_edit_remark=eligible and not item.get('is_self') and not item.get('is_builtin'), source='active_contact_db')
        return result

    def detail(self, account, username):
        with self.service.db() as db:
            row = db.execute('SELECT data FROM contact_profiles WHERE account=? AND username=?', (account, username)).fetchone()
            eligible = db.execute("SELECT 1 FROM live_roster WHERE account=? AND username=? AND relation='friend'", (account, username)).fetchone()
        if not row:
            raise HTTPException(404, '当前账号通讯录中没有此联系人')
        return self.present(account, json.loads(row[0]), bool(eligible))

    def new_friends(self, account):
        state = self.service.messages.state(account)
        path = state.get('databases', {}).get('general/general.db')
        result = {'account': account, 'ready': False, 'items': [], 'detail': '当前账号本地申请记录尚未就绪', 'updated_at': state.get('verified_at')}
        if not path:
            return result
        try:
            with readonly(path) as db:
                if not db.execute("SELECT 1 FROM sqlite_master WHERE name='FMessageTable'").fetchone():
                    return result
                rows = db.execute('SELECT rowid,user_name_,content_,timestamp_ FROM FMessageTable WHERE type_=37 AND is_sender_=0 ORDER BY timestamp_ DESC,rowid DESC LIMIT 100').fetchall()
            with self.service.db() as db:
                contacts = {r[0]: json.loads(r[1]) for r in db.execute('SELECT username,data FROM contact_profiles WHERE account=?', (account,))}
            items = []
            for row in rows:
                body = row['content_'] or ''
                if isinstance(body, bytes):
                    body = body.decode('utf-8', errors='replace')
                root = _parse_xml(body)
                username = row['user_name_'] or ''
                known = contacts.get(username)
                name = known['display_name'] if known else (root.get('fromnickname') or root.get('nickname') or '微信用户') if root is not None else '微信用户'
                content = (root.get('content') or root.get('sign') or '好友申请') if root is not None else body if '<' not in body else '好友申请'
                items.append({'id': str(row['rowid']), 'username': username if known else '', 'display_name': name,
                              'content': content[:500], 'timestamp': row['timestamp_'], 'in_contacts': bool(known),
                              'avatar_url': self.service.avatar_url(account, username) if known else None})
            result.update(ready=True, items=items, detail='最近 100 条本地申请记录；是否已接受以当前通讯录为准，暂不在此接受申请。')
        except (sqlite3.DatabaseError, OSError):
            result['detail'] = '当前账号申请记录暂不可读取，请稍后刷新'
        return result

    async def refresh(self, account):
        task = self.service.contact_tasks.get(account)
        if task and not task.done():
            await asyncio.shield(task)
        await self.service.sync_contacts(account)

    def receipt(self, account, request):
        with self.service.db() as db:
            row = db.execute('SELECT request_id,status,detail FROM contact_remark_requests WHERE account=? AND request_id=?', (account, request)).fetchone()
        return dict(row) if row else None

    def record_status(self, account, request, status, detail):
        with self.service.db() as db:
            db.execute('UPDATE contact_remark_requests SET status=?,detail=? WHERE account=? AND request_id=?', (status, detail, account, request))
        return self.receipt(account, request)

    async def edit(self, username, payload):
        account, request = payload.account, str(payload.request_id)
        fingerprint = hashlib.sha256(json.dumps([username, payload.remark, payload.expected_remark], ensure_ascii=False).encode()).hexdigest()
        async with self.write_lock:
            await self.service.check_account(account, force=True)
            with self.service.db() as db:
                previous = db.execute('SELECT * FROM contact_remark_requests WHERE account=? AND request_id=?', (account, request)).fetchone()
            if previous:
                if previous['fingerprint'] != fingerprint:
                    raise HTTPException(409, '该请求编号已用于不同的备注修改')
                if previous['status'] in {'confirmed', 'failed'}:
                    return self.receipt(account, request)
                try:
                    await self.refresh(account)
                except Exception:
                    await self.service.check_account(account, force=True)
                return self.receipt(account, request)
            await self.refresh(account)
            current = self.detail(account, username)
            if not current['can_edit_remark']:
                raise HTTPException(403, '仅可修改当前账号已核验好友的备注')
            if current['remark'] != payload.expected_remark:
                raise HTTPException(409, '备注已在微信或其他页面更改，请刷新资料后重新编辑')
            with self.service.db() as db:
                if db.execute("SELECT 1 FROM contact_remark_requests WHERE account=? AND username=? AND status IN ('dispatching','pending','unknown')", (account, username)).fetchone():
                    raise HTTPException(409, '该联系人的上一笔备注修改仍待确认，请先刷新通讯录')
                db.execute('INSERT INTO contact_remark_requests VALUES (?,?,?,?,?,?,?,?)', (account, request, username, fingerprint, payload.remark, 'dispatching', '修改结果待确认', time.time()))
            if current['remark'] == payload.remark:
                return self.record_status(account, request, 'confirmed', '备注与微信通讯录一致')
            status, detail = 'pending', '微信已受理，备注尚未从通讯录回读确认；请重新核验，不要重复提交。'
            try:
                await self.service.check_account(account, force=True)
                response = await self.service.bridge('/api/modifyRemark', {'wxid': username, 'remark': payload.remark}, timeout=20)
                if response.get('code') != 1:
                    status, detail = 'failed', '微信未确认受理本次备注修改'
            except (httpx.HTTPError, ValueError, HTTPException):
                status, detail = 'unknown', '请求结果不确定，请核验微信通讯录；此请求不会再次派发。'
            self.record_status(account, request, status, detail)
            await self.service.check_account(account, force=True)
            try:
                await self.refresh(account)
            except Exception:
                await self.service.check_account(account, force=True)
            return self.receipt(account, request)


def create_contacts_router(service):
    router = APIRouter(prefix='/contacts')
    contacts = service.contacts

    @router.get('')
    async def listing(account: str, q: str = Query('', max_length=200), offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200)):
        await service.check_account(account, force=True)
        service.refresh_contacts(account)
        result = await asyncio.to_thread(contacts.list, account, q, offset, limit)
        await service.check_account(account, force=True)
        return result

    @router.post('/refresh')
    async def refresh(account: str):
        await service.check_account(account, force=True)
        await contacts.refresh(account)
        return await asyncio.to_thread(contacts.list, account)

    @router.get('/new-friends')
    async def new_friends(account: str):
        await service.check_account(account, force=True)
        service.refresh_history(account)
        result = await asyncio.to_thread(contacts.new_friends, account)
        await service.check_account(account, force=True)
        return result

    @router.get('/{username}')
    async def detail(username: str, account: str):
        await service.check_account(account, force=True)
        result = await asyncio.to_thread(contacts.detail, account, username)
        await service.check_account(account, force=True)
        return result

    @router.post('/{username}/remark')
    async def remark(username: str, payload: RemarkEdit, request: Request):
        origin = request.headers.get('origin', '').rstrip('/')
        if request.client and request.client.host not in {'127.0.0.1', '::1', 'testclient'}:
            raise HTTPException(403, '备注修改仅允许本机访问')
        if origin and origin not in {'http://127.0.0.1:5174', 'http://localhost:5174', 'http://127.0.0.1:5173', 'http://localhost:5173', 'http://127.0.0.1:4173', 'http://localhost:4173'}:
            raise HTTPException(403, '拒绝外部页面修改通讯录')
        return await contacts.edit(username, payload)

    return router
