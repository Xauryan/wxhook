from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import time
from urllib.parse import quote, urlencode
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .realtime import normalize_event
from .chat_messages import ChatMessages, present_content
from . import chat_recalls
from .chat_history import ActiveHistory
from .chat_media import ChatMedia, MediaFailure, STATES
from .chat_file_preview import office_preview
from .contacts import Contacts, create_contacts_router
from .favorites import Favorites, create_favorites_router
from .moments import Moments, create_moments_router


class SendText(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account: str = Field(min_length=1, max_length=240)
    username: str = Field(min_length=1, max_length=240)
    text: str = Field(min_length=1, max_length=10000)
    request_id: UUID


class LiveChat:
    """Current-account chat with independently verified history and live events."""

    def __init__(self, path: Path, bridge_url: str, relay_path: Path, roster=None):
        url = httpx.URL(bridge_url)
        if url.scheme != 'http' or url.host not in {'127.0.0.1', 'localhost', '::1'} or url.userinfo:
            raise ValueError('chat bridge must use loopback HTTP')
        self.path, self.bridge_url, self.relay_path = path, bridge_url, relay_path
        self.timezone = ZoneInfo('Asia/Shanghai')
        self.profile_cache: dict[str, Any] = {}
        self.profile_at = 0.0
        self._bridge_client: httpx.AsyncClient | None = None
        self._profile_task: asyncio.Task | None = None
        self.contacts_at: dict[str, float] = {}
        self.roster = roster
        self.contact_tasks: dict[str, asyncio.Task] = {}
        self.contact_errors: dict[str, str] = {}
        self.contact_retry_at: dict[str, float] = {}
        self.history_tasks: dict[str, asyncio.Task] = {}
        self.history_errors: dict[str, str] = {}
        self.history_at: dict[str, float] = {}
        self.recent_tasks: dict[str, asyncio.Task] = {}
        self.recent_at: dict[str, float] = {}
        self.recent_errors: dict[str, str] = {}
        self.recent_verified_at: dict[str, float] = {}
        self.sent_tasks: dict[tuple[str, str], asyncio.Task] = {}
        self.sent_completed: set[tuple[str, str]] = set()
        self.sent_lock = asyncio.Lock()
        self.sent_retry_delay = 2
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS live_messages (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    account TEXT NOT NULL, event_id TEXT NOT NULL, identity_key TEXT,
                    session TEXT NOT NULL, timestamp INTEGER NOT NULL,
                    raw TEXT NOT NULL, message TEXT,
                    UNIQUE(account,event_id), UNIQUE(account,identity_key)
                );
                CREATE INDEX IF NOT EXISTS live_messages_session ON live_messages(account,session,timestamp);
                CREATE TABLE IF NOT EXISTS live_contacts (
                    account TEXT NOT NULL, username TEXT NOT NULL, name TEXT NOT NULL,
                    PRIMARY KEY(account,username)
                );
                CREATE TABLE IF NOT EXISTS live_outbox (
                    account TEXT NOT NULL, request_id TEXT NOT NULL, username TEXT NOT NULL,
                    text TEXT NOT NULL, fingerprint TEXT NOT NULL, status TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
                    PRIMARY KEY(account,request_id)
                );
                CREATE TABLE IF NOT EXISTS live_roster (
                    account TEXT NOT NULL, username TEXT NOT NULL, name TEXT NOT NULL,
                    relation TEXT NOT NULL, PRIMARY KEY(account,username)
                );
                CREATE TABLE IF NOT EXISTS live_names (
                    account TEXT NOT NULL, username TEXT NOT NULL, name TEXT NOT NULL,
                    PRIMARY KEY(account,username)
                );
                CREATE TABLE IF NOT EXISTS live_roster_state (
                    account TEXT PRIMARY KEY, refreshed_at REAL NOT NULL, revision INTEGER NOT NULL
                );
            ''')
        self.messages = ChatMessages(path)
        self.history = ActiveHistory(roster, self.messages) if roster and hasattr(roster, 'repository_root') else None
        self.media = ChatMedia(self.messages, path.parent, bridge_url)
        from .chat_presentation_repair import PresentationRepair
        self.presentation = PresentationRepair(self.messages)
        self.contacts = Contacts(self)
        self.favorites = Favorites(self)
        self.moments = Moments(self)
        # Idempotently migrate retained envelopes without deleting the old ledger.
        with self.db() as db:
            retained = db.execute("SELECT raw FROM live_messages WHERE account!='' ORDER BY sequence").fetchall()
        for row in retained:
            self.ingest(json.loads(row[0]))
        # The old display may have already replaced an original with a recall
        # placeholder. Revisit retained envelopes after notices exist, without
        # rescanning the historical ledger or restoring the chat body.
        with self.db() as db:
            needed = {tuple(row) for row in db.execute('SELECT account,session,server_id FROM chat_recalls WHERE original IS NULL')}
        if needed:
            for row in retained:
                payload = json.loads(row[0])
                flat = self.flatten(payload)
                account = str(flat.get('self_id') or flat.get('selfId') or flat.get('selfWxId') or '')
                event = normalize_event(payload, account, self.timezone)
                message = event['normalized']
                if message and (account, message['session_username'], chat_recalls.identity(message['server_id'])) in needed:
                    with self.messages.db() as db:
                        chat_recalls.capture_original(db, account, self.present(flat, message))

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    async def bridge(self, path: str, payload: dict, timeout: float = 8) -> dict:
        if self._bridge_client is None:
            self._bridge_client = httpx.AsyncClient(base_url=self.bridge_url, trust_env=False, timeout=8)
        response = await self._bridge_client.post(path, json=payload, timeout=timeout)
        response.raise_for_status()
        result = json.loads(response.content.decode('utf-8', errors='replace'))
        if not isinstance(result, dict):
            raise ValueError('invalid bridge response')
        return result

    async def profile(self, force: bool = False) -> dict:
        if not force and self.profile_cache and time.monotonic() - self.profile_at < 3:
            return self.profile_cache
        # Avatar/media/SSE requests share one in-flight verification, not dozens
        # of concurrent native userInfo calls. A completed forced check is never reused.
        if self._profile_task is None or self._profile_task.done():
            self._profile_task = asyncio.create_task(self._read_profile())
        return await asyncio.shield(self._profile_task)

    async def _read_profile(self) -> dict:
        result = await self.bridge('/api/userInfo', {})
        data = result.get('data')
        if result.get('code') != 1 or not isinstance(data, dict) or not data.get('wxid'):
            raise HTTPException(503, '微信尚未登录或桥接未就绪')
        self.profile_cache = {'username': data['wxid'], 'display_name': data.get('nickname') or data['wxid'],
                              'avatar_url': self.avatar_url(data['wxid'], data['wxid'])}
        self.profile_at = time.monotonic()
        return self.profile_cache

    async def status(self) -> dict:
        connected = True
        try:
            profile = await self.profile()
        except (httpx.HTTPError, ValueError, HTTPException):
            connected = False
            profile = self.profile_cache
        relay = {}
        try:
            relay = json.loads(self.relay_path.read_text(encoding='utf-8'))
            relay['fresh'] = time.time() - self.relay_path.stat().st_mtime < 45
        except (OSError, ValueError):
            pass
        account = profile.get('username', '')
        if connected and account:
            self.refresh_contacts(account)
            self.refresh_history(account)
        roster_state = self.roster_state(account)
        with self.db() as db:
            row = db.execute('SELECT coalesce(max(sequence),0),max(timestamp) FROM live_messages WHERE account=?', (account,)).fetchone()
        return {'account': account, 'profile': profile, 'bridge_connected': connected,
                'send_enabled': bool(connected and self.roster and roster_state['contacts_verified'] and not roster_state['contacts_error']),
                **roster_state,
                'receive_connected': bool(connected and relay.get('running') and relay.get('fresh')),
                'newest_sequence': self.messages.revision(account), 'last_message_at': row[1],
                'relay_pending': int(relay.get('pending_disk', 0)), 'relay_dropped': int(relay.get('dropped', 0)),
                'source': 'live_wechat', **self.history_state(account)}

    @staticmethod
    def avatar_url(account, username):
        return f'/api/chat/avatars/{quote(username, safe="")}?{urlencode({"account": account})}'

    def history_state(self, account):
        state = self.messages.state(account)
        task = self.history_tasks.get(account)
        return {'history_syncing': bool(task and not task.done()),
                'history_error': self.history_errors.get(account) or self.recent_errors.get(account),
                'history_updated_at': state.get('verified_at'),
                'history_count': state.get('message_count', 0),
                'history_revision': state.get('fingerprint', ''),
                'history_source': state.get('source'),
                'recent_syncing': bool(self.recent_tasks.get(account) and not self.recent_tasks[account].done()),
                'recent_error': self.recent_errors.get(account),
                'recent_updated_at': self.recent_verified_at.get(account)}

    def refresh_recent(self, account):
        if not self.history or not hasattr(self.history, 'prepare_recent'):
            return
        task = self.recent_tasks.get(account)
        if task and not task.done() or time.monotonic() - self.recent_at.get(account, 0) < 5:
            return
        async def refresh():
            try:
                snapshot = await self.history.prepare_recent(account)
                await self._consume_history_snapshot(account, snapshot,
                    lambda: self.history.apply_recent(account, snapshot))
                self.recent_verified_at[account] = snapshot['verified_at']
                self.recent_errors.pop(account, None)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.recent_errors[account] = '近期聊天消息回读失败，保留已核验记录，请检查微信连接'
                logging.getLogger(__name__).exception('Recent native chat readback failed')
            finally:
                self.recent_at[account] = time.monotonic()
        self.recent_tasks[account] = asyncio.create_task(refresh())

    def refresh_history(self, account):
        self.refresh_recent(account)
        if not self.history:
            return
        task = self.history_tasks.get(account)
        if task and not task.done() or time.monotonic() - self.history_at.get(account, 0) < 20:
            return
        async def refresh():
            try:
                snapshot = await self.history.prepare(account)
                def consume():
                    if hasattr(self.history, 'apply_recent'):
                        self.history.apply_recent(account, snapshot)
                    return self.history.apply(account, snapshot)
                await self._consume_history_snapshot(account, snapshot, consume)
                self.history_errors.pop(account, None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.history_errors[account] = str(exc) if isinstance(exc, RuntimeError) else '当前账号历史校验失败，保留已核验记录，不使用旧账号快照'
            finally:
                self.history_at[account] = time.monotonic()
        self.history_tasks[account] = asyncio.create_task(refresh())

    async def _consume_history_snapshot(self, account, snapshot, consume):
        release = getattr(self.history, 'release', lambda _: None)
        try:
            await self.check_account(account, force=True)
        except BaseException:
            await asyncio.to_thread(release, snapshot)
            raise

        def read_and_release():
            # Cancelling an asyncio waiter does not stop its worker thread.
            # Keep the lease until that reader actually finishes using files.
            try:
                return consume()
            finally:
                release(snapshot)
        return await asyncio.to_thread(read_and_release)

    async def check_account(self, account: str, force: bool = False) -> dict:
        try:
            profile = await self.profile(force)
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(503, '微信连接不可用，请恢复连接后再操作') from exc
        if profile['username'] != account:
            raise HTTPException(409, '微信账号已切换，请刷新后重新选择会话')
        return profile

    async def sync_contacts(self, account: str):
        if self.roster is None:
            raise HTTPException(503,'当前账号通讯录来源未配置')
        if hasattr(self.roster, 'read_async'):
            result = await self.roster.read_async(account)
        else:
            result = await asyncio.to_thread(self.roster.read, account)
        if result.get('account') != account or result.get('source') != 'active_contact_db':
            raise HTTPException(409,'通讯录归属校验失败')
        await self.check_account(account, force=True)
        # History import can hold the SQLite writer lock. Never wait for it on
        # the API event loop or every status/message request stalls with it.
        await asyncio.to_thread(self._replace_roster, account, result)
        self.contacts_at[account] = time.monotonic()
        self.contact_errors.pop(account,None)

    def _replace_roster(self, account, result):
        with self.db() as db:
            db.execute('DELETE FROM live_roster WHERE account=?',(account,))
            db.execute('DELETE FROM live_names WHERE account=?',(account,))
            db.executemany('INSERT INTO live_roster VALUES (?,?,?,?)',[(account,item['username'],item['name'],item['relation']) for item in result['contacts']])
            db.executemany('INSERT INTO live_names VALUES (?,?,?)',[(account,item['username'],item['name']) for item in result['names']])
            if 'directory' in result:
                db.execute('DELETE FROM contact_profiles WHERE account=?', (account,))
                db.executemany('INSERT INTO contact_profiles VALUES (?,?,?)', [(account, item['username'], json.dumps(item, ensure_ascii=False)) for item in result['directory']])
                db.execute('INSERT INTO contact_directory_state VALUES (?,?) ON CONFLICT(account) DO UPDATE SET updated_at=excluded.updated_at', (account, time.time()))
                db.execute("""UPDATE contact_remark_requests SET status='confirmed',detail='已从微信通讯录回读确认备注'
                    WHERE account=? AND status IN ('dispatching','pending','unknown') AND EXISTS (
                    SELECT 1 FROM contact_profiles p WHERE p.account=contact_remark_requests.account
                    AND p.username=contact_remark_requests.username AND json_extract(p.data,'$.remark')=contact_remark_requests.remark)""", (account,))
            db.execute('INSERT INTO live_roster_state VALUES (?,?,1) ON CONFLICT(account) DO UPDATE SET refreshed_at=excluded.refreshed_at, revision=revision+1',(account,time.time()))

    def roster_state(self, account: str) -> dict:
        with self.db() as db:
            state = db.execute('SELECT refreshed_at,revision FROM live_roster_state WHERE account=?',(account,)).fetchone()
            directory_ready = db.execute('SELECT 1 FROM contact_directory_state WHERE account=?', (account,)).fetchone() is not None
        task = self.contact_tasks.get(account)
        return {'contacts_verified':state is not None,'contacts_updated_at':state[0] if state else None,'directory_ready':directory_ready,
                'contacts_revision':state[1] if state else 0,'contacts_syncing':bool(task and not task.done()),
                'contacts_error':self.contact_errors.get(account)}

    def refresh_contacts(self, account: str):
        task = self.contact_tasks.get(account)
        if task and not task.done():
            return
        state = self.roster_state(account)
        if state['directory_ready'] and state['contacts_updated_at'] and time.time() - state['contacts_updated_at'] < 60:
            return
        if time.monotonic() < self.contact_retry_at.get(account,0):
            return
        async def refresh():
            try:
                await self.sync_contacts(account)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.contact_errors[account] = '当前账号通讯录暂未通过校验，未使用内存扫描或旧账号数据兜底'
                self.contact_retry_at[account] = time.monotonic() + 15
        self.contact_tasks[account] = asyncio.create_task(refresh())

    async def close(self):
        tasks = (list(self.contact_tasks.values()) + list(self.history_tasks.values())
                 + list(self.recent_tasks.values())
                 + list(self.sent_tasks.values())
                 + list(self.favorites.tasks.values()) + list(self.moments.tasks.values()))
        if self._profile_task is not None:
            tasks.append(self._profile_task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        self.contact_tasks.clear()
        self.history_tasks.clear()
        self.recent_tasks.clear()
        self.sent_tasks.clear()
        if self._bridge_client is not None:
            await self._bridge_client.aclose()
            self._bridge_client = None

    @staticmethod
    def flatten(payload):
        nested = payload.get('payload')
        return {**(nested if isinstance(nested, dict) else {}), **{k:v for k,v in payload.items() if k != 'payload'}}

    @staticmethod
    def present(flat, message):
        parsed = present_content(str(flat.get('content') or ''), message['local_type'], message['session_username'].endswith('@chatroom'))
        sender = parsed.pop('sender_prefix') or message['sender_username']
        return {**message, **parsed, 'sender_username': sender,
                '_recall_time_unknown': message.get('timestamp_source') == 'observed_at',
                'server_id': str(message['server_id']), 'source': 'realtime', '_live_seen_at': time.time()}

    def ingest(self, payload: dict) -> dict:
        flat = self.flatten(payload)
        account = str(flat.get('self_id') or flat.get('selfId') or flat.get('selfWxId') or '')
        event = normalize_event(payload, account, self.timezone)
        message = event['normalized'] if account else None
        with self.db() as db:
            cursor = db.execute('INSERT OR IGNORE INTO live_messages(account,event_id,identity_key,session,timestamp,raw,message) VALUES (?,?,?,?,?,?,?)',
                (account, event['event_id'], event['identity_key'], event['session_key'] or '',
                 message['create_time'] if message else 0, json.dumps(payload, ensure_ascii=False),
                 json.dumps(message, ensure_ascii=False) if message else None))
            duplicate = cursor.rowcount == 0
        if message:
            value = self.present(flat, message)
            with self.messages.db() as db:
                raw = json.dumps(payload,ensure_ascii=False,sort_keys=True)
                digest = hashlib.sha256(raw.encode()).hexdigest()
                fresh = db.execute('INSERT OR IGNORE INTO chat_envelopes VALUES (?,?,?,?)', (account,event['event_id'],digest,raw)).rowcount
                if fresh:
                    self.messages.upsert(db, account, value, event['event_id'])
                else:
                    self.messages.repair_sender(db, account, value, event['event_id'])
                    notice = db.execute('SELECT message_id FROM chat_aliases WHERE account=? AND session=? AND alias=?',
                        (account, message['session_username'], 'event:' + event['event_id'])).fetchone()
                    chat_recalls.collect(db, account, value, notice[0] if notice else None)
        observer = getattr(self, 'notification_observer', None)
        if message and fresh and not duplicate and observer:
            try:
                observer(account, event['event_id'], value)
            except Exception:
                # Notification storage must never prevent durable message ACKs.
                logging.getLogger(__name__).warning('local notification observer failed')
        return {'event_id': event['event_id'], 'duplicate': duplicate, 'status': 'applied' if message else 'unparsed', 'account': account}

    @staticmethod
    def lookup_names(db, account, usernames):
        # Group members can number tens of thousands. Resolve only the names
        # required by the current list or message page, with bounded parameters.
        usernames = list(dict.fromkeys(name for name in usernames if isinstance(name,str) and name))
        result = {}
        for start in range(0, len(usernames), 400):
            batch = usernames[start:start+400]
            markers = ','.join('?' for _ in batch)
            result.update(db.execute(f'SELECT username,name FROM live_names WHERE account=? AND username IN ({markers})', (account,*batch)))
        return result

    def sessions(self, account: str, q: str, scope: str, offset: int, limit: int) -> dict:
        historical = self.messages.state(account).get('sessions', {})
        with self.db() as db:
            db.execute('BEGIN')
            contacts = dict(db.execute('SELECT username,name FROM live_roster WHERE account=?', (account,)))
            rows = db.execute('SELECT session,message_count count,last_timestamp last_active,latest_id FROM chat_session_stats WHERE account=?', (account,)).fetchall()
            stats = {r['session']: r for r in rows}
            usernames = contacts.keys() | stats.keys() | historical.keys()
            names = self.lookup_names(db, account, usernames - contacts.keys())
            items = []
            for username in usernames:
                group = username.endswith('@chatroom')
                name = contacts.get(username, names.get(username, username))
                if (scope == 'groups' and not group) or (scope == 'contacts' and group):
                    continue
                if q.casefold() not in f'{name} {username}'.casefold():
                    continue
                row = stats.get(username)
                timestamp = row['last_active'] if row else historical.get(username,{}).get('last_active')
                items.append({'username':username, 'display_name':name, 'is_group':group,
                              'avatar_url':self.avatar_url(account, username), 'can_send': username in contacts,
                              'message_count':row['count'] if row else 0,
                              'last_active':datetime.fromtimestamp(timestamp,self.timezone).isoformat() if timestamp is not None else None})
            items.sort(key=lambda item: (item['last_active'] or '',item['display_name']), reverse=True)
            total = len(items)
            selected = items[offset:offset+limit]
            ids = [stats[item['username']]['latest_id'] for item in selected if item['username'] in stats]
            bodies = {}
            if ids:
                # SQLite can prefer the account index and scan millions of
                # rows for IN queries. NOT INDEXED retains primary-key seeks
                # while the account predicate still enforces isolation.
                # Pagination precedes body decoding; at most one body per
                # displayed session, rather than one per historical session.
                for start in range(0,len(ids),400):
                    batch = ids[start:start+400]
                    markers = ','.join('?' for _ in batch)
                    bodies.update((row['id'],json.loads(row['data'])) for row in db.execute(
                        f'SELECT id,data FROM chat_messages NOT INDEXED WHERE account=? AND id IN ({markers})', (account,*batch)))
            for item in selected:
                row = stats.get(item['username'])
                message = bodies.get(row['latest_id'],{}) if row else {}
                item['summary'] = message.get('summary') or ('暂无消息，可直接发起聊天' if item['username'] in contacts else '微信会话暂无可读取的本地正文')
        return {'items':selected, 'total':total, 'account':account, **self.roster_state(account)}

    def detail(self, account: str, username: str, limit: int = 60, before: str | None = None, around: str | None = None, floor: int | None = None, around_id: int | None = None) -> dict:
        started = time.perf_counter()
        page = self.messages.page(account, username, limit, before, around, around_id)
        if self.presentation.repair(account, page['messages']):
            page = self.messages.page(account, username, limit, before, around, around_id)
        with self.db() as db:
            contact = db.execute('SELECT name FROM live_roster WHERE account=? AND username=?',(account,username)).fetchone()
            names = self.lookup_names(db, account, [username, *self.message_authors(page['messages'])])
        if not contact and not page['total'] and username not in self.messages.state(account).get('sessions',{}):
            raise HTTPException(404,'当前账号不存在这个会话')
        return {**page, 'session':{'username':username,'display_name':contact[0] if contact else names.get(username, username),
                'is_group':username.endswith('@chatroom'),'message_count':page['total'],
                'avatar_url':self.avatar_url(account, username),'can_send':bool(contact)},
                'messages':self.format_messages(account, page['messages'], names), 'account':account,
                **self.history_state(account), 'boundary_cursor':self.messages.boundary(account,username,floor) if floor else page['next_cursor'],
                'elapsed_ms':round((time.perf_counter()-started)*1000,2)}

    @staticmethod
    def message_authors(messages):
        for message in messages:
            yield message.get('sender_username')
            reference = message.get('reference')
            if isinstance(reference,dict):
                yield reference.get('sender')

    def format_messages(self, account, messages, names=None):
        if not messages:
            return []
        if names is None:
            with self.db() as db:
                names = self.lookup_names(db, account, self.message_authors(messages))
        results = []
        for message in messages:
            value = {k:v for k,v in message.items() if not k.startswith('_') and k not in {'media_hints','source_shard'}}
            value.update(is_self=bool(value['is_self']),
                         sender_name=names.get(value['sender_username'],value['sender_username']) or '未知发送人',
                         avatar_url=self.avatar_url(account,value['sender_username']),
                         create_time=datetime.fromtimestamp(value['create_time'],self.timezone).isoformat())
            if value.get('reference'):
                reference = value['reference']
                reference['display_name'] = reference.get('display_name') or names.get(reference.get('sender'), '引用消息')
            results.append(value)
        return results

    def changes(self, account, username, after, floor=None):
        started = time.perf_counter()
        delta = self.messages.changes(account, username, after)
        if self.presentation.repair(account, delta['messages']):
            delta = self.messages.changes(account, username, after)
        with self.db() as db:
            contact = db.execute('SELECT name FROM live_roster WHERE account=? AND username=?', (account, username)).fetchone()
            name = db.execute('SELECT name FROM live_names WHERE account=? AND username=?', (account, username)).fetchone()
            total, _ = self.messages.session_totals(db, account, username)
        return {**delta, 'account': account,
                'messages': self.format_messages(account, delta['messages']),
                'session': {'username': username, 'display_name': contact[0] if contact else name[0] if name else username,
                            'is_group': username.endswith('@chatroom'), 'message_count': total,
                            'avatar_url': self.avatar_url(account, username), 'can_send': bool(contact)},
                **({'boundary_cursor': self.messages.boundary(account, username, floor)} if floor else {}),
                'elapsed_ms': round((time.perf_counter() - started) * 1000, 2)}

    def receipt(self, account: str, request_id: str) -> dict:
        with self.db() as db:
            row = db.execute('SELECT request_id,username,status,detail,text,created_at FROM live_outbox WHERE account=? AND request_id=?',(account,request_id)).fetchone()
        if not row:
            raise HTTPException(404,'尚未找到发送请求，请勿在结果未知时重复发送')
        if row['status'] == 'accepted':
            self.refresh_sent(account, request_id, row['username'], row['created_at'], row['text'])
        return {key: row[key] for key in ('request_id', 'username', 'status', 'detail')}

    def refresh_sent(self, account, request_id, username, created_at, text):
        key = (account, request_id)
        if not self.history or key in self.sent_tasks or key in self.sent_completed:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def refresh():
            try:
                # Serialize readbacks; they never resubmit the send request.
                async with self.sent_lock:
                    for attempt in range(3):
                        prepare = getattr(self.history, 'prepare_recent', self.history.prepare)
                        snapshot = await prepare(account)
                        matched = await self._consume_history_snapshot(account, snapshot,
                            lambda: self.history.apply_sent(account, snapshot, username, created_at, text))
                        if matched:
                            self.sent_completed.add(key)
                            return
                        if attempt < 2:
                            await asyncio.sleep(self.sent_retry_delay)
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.getLogger(__name__).exception('Readback of accepted chat request failed')
            finally:
                self.sent_tasks.pop(key, None)
        self.sent_tasks[key] = loop.create_task(refresh())

    def _outbox_fingerprint(self, account, request_id):
        with self.db() as db:
            row = db.execute('SELECT fingerprint FROM live_outbox WHERE account=? AND request_id=?',
                             (account, request_id)).fetchone()
            return row[0] if row else None

    def _reserve_send(self, payload, request_id, fingerprint):
        # The worker may wait for SQLite; it must not block the API event loop.
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            target = db.execute('SELECT 1 FROM live_roster WHERE account=? AND username=?',
                                (payload.account, payload.username)).fetchone()
            if not target:
                raise HTTPException(409, '收件人不在当前账号已核验的好友或群聊中')
            created_at = time.time()
            inserted = db.execute('INSERT OR IGNORE INTO live_outbox(account,request_id,username,text,fingerprint,status,created_at) VALUES (?,?,?,?,?,?,?)',
                                  (payload.account, request_id, payload.username, payload.text, fingerprint, 'dispatching', created_at)).rowcount
            if not inserted:
                row = db.execute('SELECT fingerprint FROM live_outbox WHERE account=? AND request_id=?',
                                 (payload.account, request_id)).fetchone()
                if row['fingerprint'] != fingerprint:
                    raise HTTPException(409, '同一发送请求不能更改内容')
            return bool(inserted), created_at

    def _save_send_result(self, account, request_id, status, detail):
        with self.db() as db:
            db.execute('UPDATE live_outbox SET status=?,detail=? WHERE account=? AND request_id=?',
                       (status, detail, account, request_id))

    async def send(self, payload: SendText) -> dict:
        if not payload.text.strip() or '\x00' in payload.text:
            raise HTTPException(422,'消息不能为空或包含空字符')
        fingerprint = hashlib.sha256(json.dumps([payload.username,payload.text]).encode()).hexdigest()
        request_id = str(payload.request_id)
        try:
            old = await asyncio.to_thread(self._outbox_fingerprint, payload.account, request_id)
        except sqlite3.DatabaseError as exc:
            raise HTTPException(503, '本地消息库暂不可用，请先在微信核对发送结果') from exc
        if old:
            if old != fingerprint:
                raise HTTPException(409,'同一发送请求不能更改收件人或内容')
            return self.receipt(payload.account, request_id)
        await self.check_account(payload.account, force=True)
        if not self.roster:
            raise HTTPException(503,'当前账号通讯录未核验，禁止发送')
        try:
            if hasattr(self.roster, 'verify_async'):
                await self.roster.verify_async(payload.account)
            else:
                await asyncio.to_thread(self.roster.verify,payload.account,True)
        except Exception as exc:
            raise HTTPException(409,'当前微信进程与通讯录账号不一致，禁止发送') from exc
        try:
            inserted, created_at = await asyncio.to_thread(self._reserve_send, payload, request_id, fingerprint)
        except sqlite3.DatabaseError as exc:
            raise HTTPException(503, '本地消息库繁忙，本次请求未提交微信；请先核对结果后再手动发送') from exc
        if not inserted:
            return self.receipt(payload.account, request_id)
        status, detail = 'unknown', '发送结果未知，请先在微信核对；系统不会自动重发'
        try:
            result = await self.bridge('/api/sendTextMsg', {'wxid':payload.username,'msg':payload.text}, 35)
            if result.get('code') == 1:
                status, detail = 'accepted', '微信已受理请求，实际消息以实时回显为准'
            else:
                status, detail = 'failed', '微信拒绝了发送请求'
        except (httpx.HTTPError, ValueError):
            pass
        try:
            await asyncio.to_thread(self._save_send_result, payload.account, request_id, status, detail)
        except sqlite3.DatabaseError:
            logging.getLogger(__name__).exception('Chat send receipt could not be saved for request %s', request_id)
            if status == 'accepted':
                self.refresh_sent(payload.account, request_id, payload.username, created_at, payload.text)
            return {'request_id': request_id, 'username': payload.username, 'status': 'unknown',
                    'detail': '微信调用已发生，但本地回执保存失败；请先在微信核对，系统不会自动重发'}
        return self.receipt(payload.account,request_id)


def create_live_router(service: LiveChat) -> APIRouter:
    from .relationship_analysis import create_relationship_router
    router = APIRouter(prefix='/api/chat')
    router.include_router(create_contacts_router(service))
    router.include_router(create_favorites_router(service))
    router.include_router(create_moments_router(service))
    router.include_router(create_relationship_router(service))

    @router.get('/status')
    async def status():
        return await service.status()

    @router.get('/sessions')
    async def sessions(account: str, q: str = Query('',max_length=200), scope: str = Query('all',pattern='^(all|groups|contacts)$'), offset: int = Query(0,ge=0), limit: int = Query(80,ge=1,le=500)):
        await service.check_account(account)
        service.refresh_contacts(account)
        service.refresh_history(account)
        return await asyncio.to_thread(service.sessions,account,q,scope,offset,limit)

    @router.get('/recalls')
    async def recalls(account: str, q: str = Query('', max_length=200), before: int | None = Query(None, ge=1), limit: int = Query(50, ge=1, le=100)):
        await service.check_account(account)
        def read():
            with service.db() as db:
                return chat_recalls.page(db, account, q, before, limit)
        result = await asyncio.to_thread(read)
        await service.check_account(account)
        return result

    @router.get('/sessions/{username}')
    async def session(username: str, account: str, limit: int = Query(60,ge=1,le=100), before: str | None = Query(None,max_length=2000), around: str | None = Query(None,pattern=r'^\d{1,24}$'), floor: int | None = Query(None,ge=1), around_id: int | None = Query(None,ge=1)):
        await service.check_account(account)
        service.refresh_history(account)
        result = await asyncio.to_thread(service.detail,account,username,limit,before,around,floor,around_id)
        await service.check_account(account)
        return result

    @router.get('/sessions/{username}/changes')
    async def changes(username: str, account: str, after: int = Query(0,ge=0), floor: int | None = Query(None,ge=1)):
        await service.check_account(account)
        result = await asyncio.to_thread(service.changes,account,username,after,floor)
        await service.check_account(account)
        return result

    @router.get('/messages/{ident}/media')
    async def media_info(ident: int, account: str, refresh: bool = False, record: str | None = Query(None,pattern=r'^\d{1,2}(?:\.\d{1,2}){0,2}$')):
        await service.check_account(account, force=True)
        result = await asyncio.to_thread(service.media.describe,account,ident,refresh,record)
        await service.check_account(account, force=True)
        return result

    @router.get('/media/{ident}/{index}/preview')
    async def file_preview(ident: int, index: int, account: str, asset: str = Query(...,pattern=r'^[a-f0-9]{24}$'), record: str | None = Query(None,pattern=r'^\d{1,2}(?:\.\d{1,2}){0,2}$')):
        await service.check_account(account, force=True)
        try:
            path, _ = await asyncio.to_thread(service.media.resolve, account, ident, index, asset, record)
            if service.media.specs(account, ident, record=record)[index]['kind'] != 'file':
                raise MediaFailure('unsupported')
            result = await asyncio.to_thread(office_preview, path)
        except MediaFailure as exc:
            raise HTTPException(415, STATES[exc.state]) from None
        await service.check_account(account, force=True)
        return result

    @router.get('/media/{ident}/{index}')
    async def media_file(ident: int, index: int, account: str, asset: str = Query(...,pattern=r'^[a-f0-9]{24}$'), download: bool = False, record: str | None = Query(None,pattern=r'^\d{1,2}(?:\.\d{1,2}){0,2}$')):
        await service.check_account(account, force=True)
        try:
            path, mime = await asyncio.to_thread(service.media.resolve,account,ident,index,asset,record)
        except MediaFailure as exc:
            raise HTTPException(415 if exc.state == 'unsupported' else 404, STATES[exc.state]) from None
        await service.check_account(account, force=True)
        spec = service.media.specs(account,ident,record=record)[index]
        filename = (spec.get('name') or 'attachment').replace('\\','/').split('/')[-1]
        extension = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/gif': '.gif',
                     'image/webp': '.webp', 'video/mp4': '.mp4', 'audio/wav': '.wav'}.get(mime)
        if spec['kind'] != 'file' and extension:
            filename = Path(filename).stem + extension
        headers = {'Cache-Control':'private, no-cache', 'X-Content-Type-Options':'nosniff',
                   'Content-Security-Policy':"default-src 'none'; sandbox"}
        attachment = download or spec['kind'] == 'file' and mime == 'application/octet-stream'
        return FileResponse(path,media_type=mime,filename=filename,
                            content_disposition_type='attachment' if attachment else 'inline',headers=headers)

    @router.get('/avatars/{username}')
    async def avatar(username: str, account: str):
        await service.check_account(account)
        payload, mime = await asyncio.to_thread(service.media.avatar,account,username)
        return Response(payload,media_type=mime,headers={'Cache-Control':'private, max-age=60','X-Content-Type-Options':'nosniff'})

    @router.post('/send')
    async def send(payload: SendText, request: Request):
        origin = request.headers.get('origin')
        if request.client and request.client.host not in {'127.0.0.1','::1','testclient'}:
            raise HTTPException(403,'发送仅允许本机访问')
        if origin and origin.rstrip('/') not in {'http://127.0.0.1:5174','http://localhost:5174','http://127.0.0.1:5173','http://localhost:5173','http://127.0.0.1:4173','http://localhost:4173'}:
            raise HTTPException(403,'拒绝外部页面发起发送')
        return await service.send(payload)

    @router.get('/outbox/{request_id}')
    async def receipt(request_id: UUID, account: str):
        await service.check_account(account)
        return service.receipt(account,str(request_id))

    @router.get('/events')
    async def events(request: Request, account: str, after: int = Query(0,ge=0), once: bool = False):
        await service.check_account(account)
        try:
            after = max(after,int(request.headers.get('last-event-id','0')))
        except ValueError:
            pass
        async def stream():
            cursor = after
            heartbeat = 0.0
            contacts_revision = -1
            history_revision = None
            recall_revision = -1
            checked_account = 0.0
            yield 'retry: 2000\n\n'
            while not await request.is_disconnected():
                if time.monotonic() - checked_account > 3:
                    try:
                        await service.check_account(account, force=True)
                    except HTTPException:
                        yield 'event: chat.account_changed\ndata: {}\n\n'
                        return
                    checked_account = time.monotonic()
                    service.refresh_history(account)
                newest = service.messages.revision(account)
                if newest != cursor:
                    update = await asyncio.to_thread(service.messages.changed_sessions, account, cursor)
                    cursor = update['cursor']
                    yield f'id: {cursor}\nevent: chat.updated\ndata: {json.dumps({"account":account,**update})}\n\n'
                revision = service.roster_state(account)['contacts_revision']
                with service.db() as db:
                    recalls = chat_recalls.revision(db, account)
                if recalls != recall_revision:
                    recall_revision = recalls
                    yield f'event: chat.recalls\ndata: {json.dumps({"account": account, "revision": recalls})}\n\n'
                if revision != contacts_revision:
                    contacts_revision = revision
                    yield f'event: chat.contacts\ndata: {json.dumps({"account":account,"revision":revision})}\n\n'
                history = service.history_state(account)
                marker = json.dumps(history,sort_keys=True)
                if marker != history_revision:
                    history_revision = marker
                    yield f'event: chat.history\ndata: {json.dumps({"account":account,**history})}\n\n'
                if time.monotonic() - heartbeat > 15:
                    yield ': heartbeat\n\n'
                    heartbeat = time.monotonic()
                if once:
                    break
                await asyncio.sleep(.5)
        return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

    return router
