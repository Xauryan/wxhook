"""Account-scoped, opt-in integrations over the existing live event ledger."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .friend_automation import _AutomationLeaderLock, event_friend_request
from .integration_data import HongbaoControl, normalize_hongbao_delay
from .live_chat import SendText

logger = logging.getLogger(__name__)


class FriendConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    enabled: bool = False
    reply_enabled: bool = False
    reply_text: str = Field(default='', max_length=2000)

    @model_validator(mode='after')
    def validate_reply(self):
        if '\x00' in self.reply_text or (self.reply_enabled and not self.reply_text.strip()):
            raise ValueError('开启自动回复时请填写有效的回复内容')
        return self


class GroupConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    enabled: bool = False
    keyword: str = Field(default='进群', min_length=1, max_length=100)
    room: str = Field(default='', max_length=240)

    @model_validator(mode='after')
    def validate_rule(self):
        self.keyword = self.keyword.strip()
        if not self.keyword or '\x00' in self.keyword:
            raise ValueError('请填写有效的私聊关键词')
        if self.room and (not self.room.endswith('@chatroom') or any(c.isspace() for c in self.room)):
            raise ValueError('请选择当前账号的群聊')
        if self.enabled and not self.room:
            raise ValueError('开启自动拉人前请选择目标群聊')
        return self


class ConfigEdit(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    account: str = Field(min_length=1, max_length=240)
    revision: int = Field(ge=0)
    config: dict


class HongbaoEdit(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    account: str = Field(min_length=1, max_length=240)
    enabled: bool
    expected_enabled: bool


class HongbaoDelayEdit(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    account: str = Field(min_length=1, max_length=240)
    delay_ms: str = Field(min_length=1, max_length=32)
    expected_delay_ms: str = Field(max_length=64)

    @field_validator('delay_ms')
    @classmethod
    def validate_delay(cls, value: str) -> str:
        return normalize_hongbao_delay(value)


class Integrations:
    def __init__(self, live, path: Path, available: bool = True, hongbao=None):
        self.live, self.path, self.available = live, path, available
        self.hongbao = hongbao or HongbaoControl(live.relay_path.parent)
        self._lock = asyncio.Lock()
        self._leader = _AutomationLeaderLock(path.with_suffix('.lock'))
        self._task = None
        self._cursor = 0
        self._boot_at = time.time()
        self.last_error = None
        self.groups_cache = {}
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS integration_config (
                    account TEXT NOT NULL, feature TEXT NOT NULL, config TEXT NOT NULL,
                    revision INTEGER NOT NULL, after_sequence INTEGER NOT NULL, updated_at REAL NOT NULL,
                    PRIMARY KEY(account,feature));
                CREATE TABLE IF NOT EXISTS integration_actions (
                    account TEXT NOT NULL, action_id TEXT NOT NULL, feature TEXT NOT NULL,
                    peer TEXT NOT NULL, room TEXT NOT NULL DEFAULT '', state TEXT NOT NULL,
                    detail TEXT NOT NULL, meta TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL,
                    PRIMARY KEY(account,action_id));
                CREATE INDEX IF NOT EXISTS integration_actions_recent ON integration_actions(account,created_at);
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def config(self, account, feature):
        with self.db() as db:
            row = db.execute('SELECT * FROM integration_config WHERE account=? AND feature=?', (account, feature)).fetchone()
        model = FriendConfig if feature == 'friends' else GroupConfig
        return {'config': model.model_validate_json(row['config']).model_dump() if row else model().model_dump(),
                'revision': row['revision'] if row else 0, 'after_sequence': row['after_sequence'] if row else 0,
                'updated_at': row['updated_at'] if row else 0}

    def latest_sequence(self):
        with self.live.db() as db:
            return db.execute('SELECT COALESCE(MAX(sequence),0) FROM live_messages').fetchone()[0]

    async def verify(self, account):
        await self.live.check_account(account, force=True)
        if not self.live.roster:
            raise HTTPException(503, '当前账号来源未配置')
        try:
            binding = await self.live.roster.verify_async(account)
        except Exception as exc:
            raise HTTPException(409, '当前微信进程与账号未通过核验') from exc
        await self.live.check_account(account, force=True)
        return binding

    async def directory(self, account):
        await self.live.check_account(account, force=True)
        if not self.live.roster:
            raise HTTPException(503, '当前账号通讯录不可用')
        snapshot = await self.live.roster.read_async(account)
        if snapshot.get('account') != account or snapshot.get('source') != 'active_contact_db' or 'automation' not in snapshot:
            raise HTTPException(409, '自动化通讯录归属或权限数据未通过核验')
        await self.live.check_account(account, force=True)
        await asyncio.to_thread(self.live._replace_roster, account, snapshot)
        self.groups_cache[account] = (time.time(), snapshot['automation'])
        return snapshot['automation']

    async def groups(self, account, refresh=False):
        await self.live.check_account(account, force=True)
        cached = self.groups_cache.get(account)
        if refresh or not cached or time.time() - cached[0] > 30:
            await self.directory(account)
        checked, directory = self.groups_cache[account]
        return {'account': account, 'checked_at': checked,
                'items': [{k: v for k, v in group.items() if k not in {'members', 'members_known'}}
                          for group in directory['groups']]}

    async def status(self):
        profile = await self.live.profile(force=True)
        account = profile['username']
        binding = await self.verify(account)
        native = await asyncio.to_thread(self.hongbao.status, binding)
        native_friends = await self.native_friends()
        with self.db() as db:
            actions = [dict(row) for row in db.execute('''SELECT action_id,feature,peer,room,state,detail,created_at
                    FROM integration_actions WHERE account=? ORDER BY created_at DESC,rowid DESC LIMIT 20''', (account,))]
        await self.live.check_account(account, force=True)
        return {'account': account, 'profile': profile,
                'friends': self.config(account, 'friends'), 'groups': self.config(account, 'groups'),
                'hongbao': native, 'native_friends': native_friends, 'actions': actions,
                'runtime': {'running': bool(self._task and not self._task.done()), 'available': self.available,
                            'error': self.last_error if self.available else '旧业务模式运行中，未启动新的应用自动化，避免双重执行'}}

    async def native_friends(self, disable=False):
        try:
            response = await self.live.bridge('/api/autoAcceptFriend', {'enable': False} if disable else {})
            data = response.get('data')
            if isinstance(data, str):
                data = json.loads(data)
            if response.get('code') == 1 and isinstance(data, dict) and type(data.get('enabled')) is bool:
                return {'available': True, 'enabled': data['enabled']}
        except (httpx.HTTPError, ValueError):
            pass
        return {'available': False, 'enabled': None}

    async def save(self, feature, payload: ConfigEdit):
        from pydantic import ValidationError
        model = FriendConfig if feature == 'friends' else GroupConfig
        try:
            config = model.model_validate(payload.config).model_dump()
        except ValidationError as exc:
            raise HTTPException(422, str(exc.errors()[0]['msg'])) from exc
        async with self._lock:
            if not self.available or not self._task or self._task.done():
                raise HTTPException(503, '应用自动化未运行，未保存开关')
            await self.verify(payload.account)
            if self.config(payload.account, feature)['revision'] != payload.revision:
                raise HTTPException(409, '配置已在其他页面更改，请重新读取后再保存')
            if feature == 'groups' and config['enabled']:
                directory = await self.directory(payload.account)
                room = next((g for g in directory['groups'] if g['username'] == config['room']), None)
                if not room or not room['can_manage']:
                    raise HTTPException(403, '目标群未核实管理权限，不能启用自动拉人')
            cutoff = await asyncio.to_thread(self.latest_sequence)
            await self.live.check_account(payload.account, force=True)
            if feature == 'friends':
                # Explicit saving transfers ownership from the process-wide native
                # switch. Reading the page or starting the service never does so.
                native = await self.native_friends(disable=True)
                if not native['available'] or native['enabled']:
                    raise HTTPException(503, '旧原生好友自动模式未确认关闭，未接管；请重新读取状态')
                await self.live.check_account(payload.account, force=True)
            with self.db() as db:
                db.execute('''INSERT INTO integration_config VALUES (?,?,?,?,?,?)
                    ON CONFLICT(account,feature) DO UPDATE SET config=excluded.config,revision=excluded.revision,
                    after_sequence=excluded.after_sequence,updated_at=excluded.updated_at''',
                           (payload.account, feature, json.dumps(config, ensure_ascii=False), payload.revision + 1, cutoff, time.time()))
            return {'account': payload.account, **self.config(payload.account, feature)}

    async def set_hongbao(self, payload: HongbaoEdit):
        async with self._lock:
            binding = await self.verify(payload.account)
            try:
                result = await asyncio.to_thread(self.hongbao.set_enabled, binding, payload.enabled, payload.expected_enabled)
            except (ValueError, OSError) as exc:
                raise HTTPException(409, '红包状态变化或控制文件写入失败，请刷新核对') from exc
            await self.live.check_account(payload.account, force=True)
            return result

    async def set_hongbao_delay(self, payload: HongbaoDelayEdit):
        async with self._lock:
            binding = await self.verify(payload.account)
            try:
                result = await asyncio.to_thread(self.hongbao.set_delay, binding, payload.delay_ms, payload.expected_delay_ms)
            except (ValueError, OSError) as exc:
                raise HTTPException(409, '红包延迟配置变化或写入失败，请刷新核对') from exc
            await self.live.check_account(payload.account, force=True)
            return result

    async def start(self):
        if not self.available or not self._leader.acquire():
            self.last_error = '未取得自动化执行锁；已有其他进程运行'
            return
        self._boot_at = time.time()
        self._cursor = await asyncio.to_thread(self.latest_sequence)
        with self.db() as db:
            db.execute("UPDATE integration_actions SET state='unknown',detail='服务重启前执行结果未确认，不会自动重试' WHERE state='dispatching'")
            db.execute("UPDATE integration_actions SET state='skipped',detail='服务重启，未补发历史自动回复' WHERE state='pending'")
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        self._leader.release()

    async def _run(self):
        while True:
            try:
                await self.run_once()
                self.last_error = None
            except Exception as exc:
                self.last_error = '当前自动化处理失败，已暂停本轮执行；请检查微信连接与账号'
                logger.warning('integration worker paused: %s', type(exc).__name__)
            await asyncio.sleep(1)

    def record(self, account, key, feature, peer, state, detail, room='', meta=None):
        with self.db() as db:
            return bool(db.execute('INSERT OR IGNORE INTO integration_actions VALUES (?,?,?,?,?,?,?,?,?)',
                                  (account, key, feature, peer, room, state, detail,
                                   json.dumps(meta or {}), time.time())).rowcount)

    def finish(self, account, key, state, detail):
        with self.db() as db:
            db.execute('UPDATE integration_actions SET state=?,detail=? WHERE account=? AND action_id=?',
                       (state, detail, account, key))

    async def run_once(self):
        with self.live.db() as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM live_messages WHERE sequence>? ORDER BY sequence LIMIT 50', (self._cursor,))]
        for row in rows:
            # Never replay failed or stale events after a disconnect or restart.
            self._cursor = row['sequence']
            async with self._lock:
                try:
                    await self.process_event(row)
                except Exception:
                    self.record(row['account'], 'error:' + row['event_id'], 'runtime', '', 'blocked',
                                '本事件处理异常或账号核验失败，已停止；不回放或自动重试')
                    raise
        async with self._lock:
            await self.send_pending_replies()

    async def process_event(self, row):
        if not row['account'] or not row['message']:
            return
        message = json.loads(row['message'])
        if message['is_self'] or message['session_username'].endswith('@chatroom'):
            return
        raw = json.loads(row['raw'])
        flat = {**(raw.get('payload') if isinstance(raw.get('payload'), dict) else {}),
                **{k: v for k, v in raw.items() if k != 'payload'}}
        incoming = (flat.get('direction') == 'incoming' or flat.get('is_incoming') in (True, 1)
                    or flat.get('isIncoming') in (True, 1) or flat.get('is_recv_msg') in (True, 1)
                    or (flat.get('raw_to') == row['account'] and flat.get('raw_from') != row['account']))
        if not incoming:
            return
        feature = 'friends' if message['base_type'] == 37 else 'groups' if message['base_type'] == 1 else ''
        if not feature:
            return
        saved = self.config(row['account'], feature)
        config = saved['config']
        if (not config['enabled'] or row['sequence'] <= saved['after_sequence']
                or message['create_time'] < max(self._boot_at, saved['updated_at']) - 1
                or not -30 <= time.time() - message['create_time'] <= 120):
            return
        if feature == 'groups' and str(flat.get('content', '')).strip() != config['keyword']:
            return
        await self.verify(row['account'])
        if feature == 'friends':
            request = event_friend_request({'payload': {**flat, 'message_type': 37, 'direction': 'incoming'}})
            if not request or not request['ticket'] or request['scene'] is None:
                self.record(row['account'], 'friend:' + row['event_id'], feature, '', 'skipped', '好友申请凭据不完整，未执行')
                return
            await self.accept_friend(row['account'], request, saved)
        else:
            await self.invite(row['account'], row['event_id'], message['sender_username'], config)

    async def dispatch(self, account, key, path, payload):
        try:
            await self.live.check_account(account, force=True)
        except HTTPException:
            self.finish(account, key, 'blocked', '提交前账号校验失败，未执行')
            return 'blocked'
        state, detail = 'unknown', '请求结果未知，不会自动重试，请在微信中核对'
        try:
            response = await self.live.bridge(path, payload, timeout=35)
            data = response.get('data')
            if isinstance(data, str):
                data = json.loads(data)
            if response.get('code') == 1 and isinstance(data, dict) and data.get('ok') is True:
                state, detail = 'accepted', '微信已受理，最终结果以微信和通讯录为准'
            elif response.get('code') != 1 or (isinstance(data, dict) and data.get('ok') is False):
                state, detail = 'failed', '微信拒绝了请求，不自动重试'
        except (httpx.HTTPError, ValueError):
            pass
        self.finish(account, key, state, detail)
        return state

    async def accept_friend(self, account, request, saved):
        digest = hashlib.sha256((request['encrypt_username'] + '\0' + request['ticket']).encode()).hexdigest()
        key = 'friend:' + digest
        native = await self.native_friends()
        if not native['available'] or native['enabled']:
            self.record(account, key, 'friends', request['candidate_username'], 'blocked',
                        '原生自动通过未关闭或状态未知，避免两套逻辑重复执行；请重新保存配置接管')
            return
        directory = await self.directory(account)
        if any(f['username'] == request['candidate_username'] or f['encrypt_username'] == request['encrypt_username']
               for f in directory['friends']):
            self.record(account, key, 'friends', request['candidate_username'], 'skipped', '已是好友，不重复通过或发送欢迎语')
            return
        if not self.record(account, key, 'friends', request['candidate_username'], 'dispatching', '正在提交好友申请'):
            return
        state = await self.dispatch(account, key, '/api/acceptFriend',
                                    {'encryptUsername': request['encrypt_username'], 'ticket': request['ticket'],
                                     'scene': request['scene'], 'friendFlag': request['friend_flag'], 'timeout': 15000})
        if state in {'accepted', 'unknown'} and saved['config']['reply_enabled']:
            self.record(account, 'reply:' + digest, 'reply', request['candidate_username'], 'pending', '等待通讯录确认好友关系后回复',
                        meta={'revision': saved['revision'], 'encrypt_username': request['encrypt_username']})

    async def send_pending_replies(self):
        with self.db() as db:
            pending = [dict(row) for row in db.execute("SELECT * FROM integration_actions WHERE state='pending' AND feature='reply' ORDER BY created_at LIMIT 5")]
        for row in pending:
            account, key = row['account'], row['action_id']
            meta = json.loads(row['meta'])
            saved = self.config(account, 'friends')
            config = saved['config']
            if not config['enabled'] or not config['reply_enabled'] or saved['revision'] != meta['revision']:
                self.finish(account, key, 'skipped', '配置已关闭或修改，取消待发送回复')
                continue
            if time.time() - row['created_at'] > 180:
                self.finish(account, key, 'skipped', '好友关系未及时确认，未发送自动回复')
                continue
            try:
                directory = await self.directory(account)
            except HTTPException as exc:
                if exc.status_code == 409:
                    self.finish(account, key, 'skipped', '账号归属变化，取消自动回复')
                    continue
                raise
            matches = [f for f in directory['friends'] if f['username'] == row['peer'] or
                       (f['encrypt_username'] and f['encrypt_username'] == meta['encrypt_username'])]
            if len(matches) != 1:
                continue
            self.finish(account, key, 'dispatching', '好友关系已确认，正在提交自动回复')
            try:
                receipt = await self.live.send(SendText(account=account, username=matches[0]['username'],
                                                       text=config['reply_text'], request_id=uuid5(NAMESPACE_URL, account + ':' + key)))
            except Exception:
                self.finish(account, key, 'unknown', '自动回复未取得确定回执，不会自动重发')
                raise
            self.finish(account, key, receipt['status'], receipt['detail'])

    async def invite(self, account, event_id, peer, config):
        key, room = 'group:' + event_id, config['room']
        directory = await self.directory(account)
        group = next((g for g in directory['groups'] if g['username'] == room), None)
        if not group or not group['can_manage'] or not group['members_known']:
            self.record(account, key, 'groups', peer, 'blocked', '群管理权限或成员名单未通过实时核验，未执行', room)
            return
        if not any(f['username'] == peer for f in directory['friends']):
            self.record(account, key, 'groups', peer, 'blocked', '仅处理当前已核验好友的私聊关键词', room)
            return
        if peer in group['members']:
            self.record(account, key, 'groups', peer, 'skipped', '好友已在目标群内，无需重复邀请', room)
            return
        with self.db() as db:
            recent = db.execute("""SELECT 1 FROM integration_actions WHERE account=? AND feature='groups' AND peer=? AND room=?
                AND state IN ('dispatching','accepted','unknown') AND created_at>?""", (account, peer, room, time.time() - 300)).fetchone()
        if recent:
            self.record(account, key, 'groups', peer, 'skipped', '5 分钟内已提交过邀请，不重复执行', room)
            return
        if not self.record(account, key, 'groups', peer, 'dispatching', '管理权限已核验，正在提交入群请求', room):
            return
        path = '/api/addMemberToChatRoom' if len(group['members']) < 40 else '/api/InviteMemberToChatRoom'
        await self.dispatch(account, key, path, {'chatRoomId': room, 'memberIds': peer, 'timeout': 30000})


def create_integrations_router(service: Integrations):
    router = APIRouter(prefix='/api/integrations')

    @router.get('')
    async def status():
        return await service.status()

    @router.get('/groups')
    async def groups(account: str, refresh: bool = False):
        return await service.groups(account, refresh)

    def local_write(request):
        origin = request.headers.get('origin', '').rstrip('/')
        allowed = {f'http://{host}:{port}' for host in ('localhost', '127.0.0.1') for port in (5174, 5173, 4173)}
        if not request.client or request.client.host not in {'127.0.0.1', '::1', 'testclient'}:
            raise HTTPException(403, '应用配置仅允许本机访问')
        if origin and origin not in allowed:
            raise HTTPException(403, '拒绝外部页面修改应用配置')

    @router.post('/hongbao')
    async def hongbao(payload: HongbaoEdit, request: Request):
        local_write(request)
        return await service.set_hongbao(payload)

    @router.post('/hongbao/delay')
    async def hongbao_delay(payload: HongbaoDelayEdit, request: Request):
        local_write(request)
        return await service.set_hongbao_delay(payload)

    @router.post('/{feature}')
    async def save(feature: Literal['friends', 'groups'], payload: ConfigEdit, request: Request):
        local_write(request)
        return await service.save(feature, payload)

    return router
