"""Read-only current-account Moments feed; no CDN fetching or social writes."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlencode

import zstandard
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from .chat_history import readonly
from .chat_media import MediaFailure, STATES, safe_path
from .chat_messages import dumps
from .moments_source import MomentsSourceError, prepare_worker
from .moments_media_index import scan_media
from .parsing import ZSTD_MAGIC, decode_wcdb_value
from .sns_backup import SnsBackupError, parse_timeline_xml
from .sns_media import find_sns_cache_file

SCHEMA = 2
MAX_RECORD_BYTES = 4 * 1024 * 1024
WRITE_DETAIL = '当前仅浏览本机已缓存的朋友圈；发布、点赞、评论和消息通知请在微信中操作。'
logger = logging.getLogger(__name__)


def location_text(value):
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ''
    city = value.get('city') or ''
    place = next((value.get(key) for key in ('name', 'poiName', 'poiAddressName', 'address', 'poiAddress') if value.get(key)), '')
    parts = [str(part) for part in (city, place) if part]
    return ' · '.join(dict.fromkeys(parts))


def safe_timestamp(value):
    return value if isinstance(value, int) and 0 <= value <= 253402300799 else 0


def bounded_content(value):
    if isinstance(value, (str, bytes, memoryview)) and len(value) > MAX_RECORD_BYTES:
        raise ValueError('Moments record exceeds size limit')
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytes) and value.startswith(ZSTD_MAGIC):
        try:
            parameters = zstandard.get_frame_parameters(value)
            if parameters.content_size not in (zstandard.CONTENTSIZE_UNKNOWN, zstandard.CONTENTSIZE_ERROR) and parameters.content_size > MAX_RECORD_BYTES:
                raise ValueError('Moments frame exceeds size limit')
            if parameters.window_size > MAX_RECORD_BYTES:
                raise ValueError('Moments compression window exceeds size limit')
            # max_output_size alone does not bound frames with declared sizes.
            with zstandard.ZstdDecompressor(max_window_size=MAX_RECORD_BYTES).stream_reader(value, read_across_frames=False) as stream:
                value = stream.read(MAX_RECORD_BYTES + 1)
        except zstandard.ZstdError as exc:
            raise ValueError('Invalid compressed Moments record') from exc
        if len(value) > MAX_RECORD_BYTES:
            raise ValueError('Moments decoded record exceeds size limit')
        if value.startswith(ZSTD_MAGIC):
            raise ValueError('Nested compressed Moments record')
    return decode_wcdb_value(value).text


def parse_post(row, account):
    text = bounded_content(row['content'])
    if len(text) > MAX_RECORD_BYTES or re.search(r'<!\s*(?:DOCTYPE|ENTITY)', text, re.I):
        raise ValueError('Unsupported Moments XML')
    if '<TimelineObject' not in text and '<SnsDataItem' not in text:
        raise ValueError('Unsupported Moments record')
    parsed = parse_timeline_xml(text, row_tid=row['tid'], row_username=row['user_name'])
    ident = parsed['id']
    if not re.fullmatch(r'[0-9]{1,20}', ident) or int(ident) >= 1 << 64:
        raise ValueError('Unsupported Moments identifier')
    fields = ('id', 'username', 'nickname', 'content', 'create_time', 'location', 'content_type', 'private')
    result = {key: parsed[key] for key in fields}
    result.update(is_owner=parsed['username'] == account, interactions_limited=True,
                  location=location_text(parsed['location']), create_time=safe_timestamp(parsed['create_time']))
    for kind in ('likes', 'comments'):
        result[kind] = [{key: entry[key] for key in ('id', 'username', 'nickname', 'content', 'create_time', 'ref_comment_id', 'ref_comment_64id')}
                        for entry in parsed[kind] if not entry.get('deleted')]
        for entry in result[kind]:
            entry['create_time'] = safe_timestamp(entry['create_time'])
    result['like_count'], result['comment_count'] = len(result['likes']), len(result['comments'])
    result['_media'] = [{key: entry[key] for key in ('id', 'type', 'width', 'height', 'duration')}
                        for entry in parsed['media'] if re.fullmatch(r'[0-9]{1,20}', entry['id'])]
    result['revision'] = hashlib.sha256(dumps(result).encode()).hexdigest()[:24]
    return result


class Moments:
    def __init__(self, service):
        self.service = service
        self.tasks, self.errors, self.last_attempt = {}, {}, {}
        self.error_stages, self.error_codes, self.sync_timings = {}, {}, {}
        self.media_lock = threading.Lock()
        with service.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS moments_items(account TEXT NOT NULL,id TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(account,id));
                CREATE TABLE IF NOT EXISTS moments_state(account TEXT PRIMARY KEY,data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS moments_page ON moments_items(
                    account,json_extract(data,'$.create_time') DESC,length(id) DESC,id DESC);
                CREATE INDEX IF NOT EXISTS moments_self_page ON moments_items(
                    account,json_extract(data,'$.is_owner'),json_extract(data,'$.create_time') DESC,length(id) DESC,id DESC);
                CREATE TABLE IF NOT EXISTS moments_media_index(
                    account TEXT NOT NULL,id TEXT NOT NULL,revision TEXT NOT NULL,root TEXT NOT NULL,
                    data TEXT NOT NULL,PRIMARY KEY(account,id));
            ''')

    def state(self, account):
        with self.service.db() as db:
            row = db.execute('SELECT data FROM moments_state WHERE account=?', (account,)).fetchone()
        return json.loads(row[0]) if row else {}

    async def prepare(self, account):
        reader = self.service.roster
        if not reader or not hasattr(reader, 'repository_root'):
            raise RuntimeError('当前账号朋友圈来源未配置')
        return await prepare_worker(reader, account)

    def ensure(self, account, force=False):
        task = self.tasks.get(account)
        if task and not task.done():
            return task
        if not force and time.monotonic() - self.last_attempt.get(account, 0) < 20:
            return None

        async def sync():
            started = time.monotonic()
            stage = 'prepare'
            try:
                snapshot = await self.prepare(account)
                prepared = time.monotonic()
                stage = 'account_check'
                await self.service.check_account(account, force=True)
                checked = time.monotonic()
                stage = 'apply'
                await asyncio.to_thread(self.apply, account, snapshot)
                finished = time.monotonic()
                self.errors.pop(account, None)
                self.error_stages.pop(account, None)
                self.error_codes.pop(account, None)
                self.sync_timings[account] = {
                    'prepare_ms': round((prepared - started) * 1000, 2),
                    'account_check_ms': round((checked - prepared) * 1000, 2),
                    'apply_ms': round((finished - checked) * 1000, 2),
                    'total_ms': round((finished - started) * 1000, 2),
                }
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                source_error = isinstance(exc, MomentsSourceError)
                self.errors[account] = str(exc) if source_error else '本次朋友圈更新失败，请重试；未使用其他账号数据。'
                self.error_stages[account] = exc.stage if source_error else stage
                self.error_codes[account] = exc.code if source_error else 'sync_failed'
                logger.warning('Moments sync failed: stage=%s code=%s type=%s',
                               self.error_stages[account], self.error_codes[account],
                               exc.exception_type if source_error else type(exc).__name__)
            finally:
                self.last_attempt[account] = time.monotonic()
        task = self.tasks[account] = asyncio.create_task(sync())
        return task

    def apply(self, account, snapshot):
        if snapshot.get('account') != account or snapshot.get('source') != 'active_sns_db':
            raise ValueError('Moments snapshot ownership mismatch')
        previous = self.state(account)
        if (previous.get('schema') == SCHEMA and previous.get('fingerprint') == snapshot.get('fingerprint')
                and previous.get('root') == snapshot.get('root')):
            with self.service.db() as db:
                items = [json.loads(row[0]) for row in db.execute('SELECT data FROM moments_items WHERE account=?', (account,))]
            self.index_media(account, items, Path(snapshot['root']))
            with self.service.db() as db:
                db.execute('UPDATE moments_state SET data=? WHERE account=?', (dumps({**previous, **snapshot}), account))
            return
        items, malformed = [], 0
        with readonly(snapshot['database']) as db:
            columns = {row[1] for row in db.execute('PRAGMA table_info(SnsTimeLine)')}
            if not {'tid', 'user_name', 'content'} <= columns:
                raise ValueError('Unsupported Moments schema')
            total = db.execute('SELECT count(*) FROM SnsTimeLine').fetchone()[0]
            for row in db.execute('''SELECT tid,user_name,
                CASE WHEN length(CAST(content AS BLOB)) <= ? THEN content ELSE NULL END AS content
                FROM SnsTimeLine ORDER BY (tid < 0) DESC,tid DESC LIMIT 20000''', (MAX_RECORD_BYTES,)):
                try:
                    items.append(parse_post(row, account))
                except (SnsBackupError, ValueError, TypeError, AttributeError, KeyError):
                    malformed += 1
        state = {**snapshot, 'schema': SCHEMA, 'ready': True, 'count': len(items),
                 'limited_count': total - len(items), 'parse_error_count': malformed}
        state['revision'] = hashlib.sha256(dumps([SCHEMA, snapshot['fingerprint']]).encode()).hexdigest()
        media = self.scan_media(items, Path(snapshot['root']))
        with self.service.db() as db:
            db.execute('DELETE FROM moments_items WHERE account=?', (account,))
            db.executemany('INSERT INTO moments_items VALUES (?,?,?)', [(account, item['id'], dumps(item)) for item in items])
            db.execute('DELETE FROM moments_media_index WHERE account=?', (account,))
            db.executemany('INSERT INTO moments_media_index VALUES (?,?,?,?,?)',
                           [(account, item['id'], item['revision'], snapshot['root'], dumps(media[item['id']])) for item in items])
            db.execute('INSERT INTO moments_state VALUES (?,?) ON CONFLICT(account) DO UPDATE SET data=excluded.data', (account, dumps(state)))

    def scan_media(self, items, root):
        return scan_media(items, root)

    def index_media(self, account, items, root, reuse=False):
        # Availability hints are account/revision/root-bound; file serving still
        # resolves and validates every source independently of this index.
        with self.media_lock:
            indexed = {}
            if reuse:
                with self.service.db() as db:
                    for item in items:
                        row = db.execute('SELECT data FROM moments_media_index WHERE account=? AND id=? AND revision=? AND root=?',
                                         (account, item['id'], item['revision'], str(root))).fetchone()
                        if row:
                            indexed[item['id']] = json.loads(row[0])
            missing = [item for item in items if item['id'] not in indexed]
            indexed.update(self.scan_media(missing, root))
            with self.service.db() as db:
                db.executemany('''INSERT INTO moments_media_index VALUES (?,?,?,?,?)
                    ON CONFLICT(account,id) DO UPDATE SET revision=excluded.revision,root=excluded.root,data=excluded.data''',
                    [(account, item['id'], item['revision'], str(root), dumps(indexed[item['id']])) for item in missing])
        return indexed

    def raw(self, account, ident):
        with self.service.db() as db:
            row = db.execute('SELECT data FROM moments_items WHERE account=? AND id=?', (account, ident)).fetchone()
        if not row:
            raise HTTPException(404, '当前账号没有此朋友圈记录，或记录已更新')
        return json.loads(row[0])

    def candidates(self, root, ident, media, variant, first_only=False):
        kind = 'video' if media['type'] == 6 else 'image'
        if kind == 'video' and variant == 'source':
            orders = [(3, 'video', None)]
        elif kind == 'video':
            orders = [(3, 'video', ('.jpg', '.jpeg', '.png', '.webp')), (1, 'image', None), (6, 'image', None), (2, 'image', None)]
        else:
            orders = [(entry, 'image', None) for entry in ((6, 1, 2, 3) if variant == 'preview' else (2, 3, 6, 1))]
        result = []
        for version, cache_kind, extensions in orders:
            source = find_sns_cache_file(root, ident, media['id'], version, kind=cache_kind, extensions=extensions)
            if source is None:
                continue
            try:
                relative = source.relative_to(root).as_posix()
                checked = safe_path(root, relative)
                if checked not in result:
                    result.append(checked)
                    if first_only:
                        return result
            except (ValueError, MediaFailure):
                continue
        return result

    def asset_url(self, account, item, index, variant):
        return f'/api/chat/moments/{quote(item["id"], safe="")}/media/{index}?' + urlencode(
            {'account': account, 'asset': item['revision'], 'variant': variant})

    def present(self, account, item, names, availability):
        value = {key: val for key, val in item.items() if not key.startswith('_') and key != 'nickname'}
        value['location'] = location_text(item.get('location'))
        value['create_time'] = safe_timestamp(item.get('create_time'))
        value['display_name'] = names.get(item['username']) or item['nickname'] or item['username'] or '微信用户'
        value['avatar_url'] = self.service.avatar_url(account, item['username'])
        for kind in ('comments', 'likes'):
            value[kind] = [{**{key: val for key, val in entry.items() if key != 'nickname'},
                            'display_name': names.get(entry['username']) or entry['nickname'] or entry['username'] or '微信用户',
                            'create_time': safe_timestamp(entry.get('create_time'))}
                           for entry in item[kind]]
        value['media'] = []
        for index, media in enumerate(item['_media']):
            preview = bool(availability[index].get('preview')) if index < len(availability) else False
            source = bool(availability[index].get('source')) if index < len(availability) else False
            status = 'local' if preview or source else 'not_downloaded'
            value['media'].append({'id': media['id'], 'kind': 'video' if media['type'] == 6 else 'image',
                                   'width': media['width'], 'height': media['height'], 'duration': media['duration'],
                                   'state': status, 'detail': STATES[status],
                                   'thumbnail_url': self.asset_url(account, item, index, 'preview') if preview else None,
                                   'url': self.asset_url(account, item, index, 'source') if source else None})
        return value

    def list(self, account, q='', scope='all', cursor='', limit=20):
        with self.service.db() as db:
            db.execute('BEGIN')
            state_row = db.execute('SELECT data FROM moments_state WHERE account=?', (account,)).fetchone()
            state = json.loads(state_row[0]) if state_row else {}
            binding = hashlib.sha256(dumps([account, q, scope, state.get('revision')]).encode()).hexdigest()
            before = None
            if cursor:
                try:
                    payload = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
                    if payload['binding'] != binding:
                        raise HTTPException(409, '朋友圈缓存或筛选条件已更新，请从最新动态重新加载')
                    before = (int(payload['time']), str(int(payload['id'])))
                except HTTPException:
                    raise
                except (ValueError, TypeError, KeyError, UnicodeDecodeError):
                    raise HTTPException(400, '朋友圈分页游标无效') from None
            where, parameters = 'i.account=?', [account]
            if scope == 'self':
                where += " AND json_extract(i.data,'$.is_owner')=1"
            join = ''
            if q:
                db.create_function('casefold', 1, lambda value: str(value or '').casefold(), deterministic=True)
                join = " LEFT JOIN live_names n ON n.account=i.account AND n.username=json_extract(i.data,'$.username')"
                where += """ AND instr(casefold(json_extract(i.data,'$.content') || ' ' || coalesce(n.name,'') ||
                    ' ' || json_extract(i.data,'$.nickname') || ' ' || json_extract(i.data,'$.username')),?)>0"""
                parameters.append(q.casefold())
            total = db.execute(f'SELECT count(*) FROM moments_items i{join} WHERE {where}', parameters).fetchone()[0]
            if before:
                where += " AND (json_extract(i.data,'$.create_time'),length(i.id),i.id)<(?,?,?)"
                parameters.extend((before[0], len(before[1]), before[1]))
            # Text length then text value preserve uint64 ordering without SQLite's
            # signed integer overflow; the same expressions cover the keyset index.
            page = db.execute(f'''SELECT i.data,m.data AS availability FROM moments_items i{join}
                LEFT JOIN moments_media_index m ON m.account=i.account AND m.id=i.id
                    AND m.revision=json_extract(i.data,'$.revision') AND m.root=?
                WHERE {where} ORDER BY json_extract(i.data,'$.create_time') DESC,length(i.id) DESC,i.id DESC LIMIT ?''',
                [state.get('root', ''), *parameters, limit + 1]).fetchall()
            selected = [json.loads(row['data']) for row in page[:limit]]
            availability = {item['id']: json.loads(row['availability']) for item, row in zip(selected, page)
                            if row['availability'] is not None}
            users = list({entry['username'] for item in selected for entry in [item, *item['likes'], *item['comments']]})
            names = {}
            for start in range(0, len(users), 500):
                batch = users[start:start + 500]
                names.update(db.execute(f"SELECT username,name FROM live_names WHERE account=? AND username IN ({','.join('?' for _ in batch)})",
                                        [account, *batch]))
        missing = [item for item in selected if item['id'] not in availability]
        if missing and state.get('root'):
            availability.update(self.index_media(account, missing, Path(state['root']), reuse=True))
        more = len(page) > limit
        next_cursor = None
        if more and selected:
            last = selected[-1]
            next_cursor = base64.urlsafe_b64encode(dumps({'binding': binding, 'time': last['create_time'], 'id': last['id']}).encode()).decode().rstrip('=')
        task = self.tasks.get(account)
        return {'account': account, 'ready': bool(state.get('ready')), 'syncing': bool(task and not task.done()),
                'error': self.errors.get(account), 'updated_at': state.get('verified_at'), 'revision': state.get('revision'),
                'error_stage': self.error_stages.get(account), 'error_code': self.error_codes.get(account),
                'sync_timings': self.sync_timings.get(account),
                'source': 'active_sns_db', 'total': total, 'items': [self.present(account, item, names, availability.get(item['id'], [])) for item in selected],
                'next_cursor': next_cursor, 'has_more': more, 'limited_count': state.get('limited_count', 0),
                'interactions_limited': True, 'write_supported': False, 'write_detail': WRITE_DETAIL,
                'source_detail': '仅展示当前账号本地已缓存动态及互动，不代表完整朋友圈；刷新不会联网补拉。'}

    def resolve(self, account, ident, index, asset, variant='source'):
        item = self.raw(account, ident)
        if item['revision'] != asset:
            raise HTTPException(409, '朋友圈附件已更新，请刷新后重新打开')
        if index < 0 or index >= len(item['_media']):
            raise HTTPException(404, '朋友圈附件不存在')
        media = item['_media'][index]
        state = self.state(account)
        root = Path(state['root']).resolve()
        candidates = self.candidates(root, ident, media, variant)
        if not candidates:
            self.index_media(account, [item], root)
            raise MediaFailure('not_downloaded')
        error = 'unavailable'
        for source in candidates:
            try:
                kind = 'video' if media['type'] == 6 and variant == 'source' else 'image'
                return self.service.media._resolve_source(account, source, {'kind': kind, 'path': source.relative_to(root).as_posix()})
            except MediaFailure as exc:
                error = exc.state
                if error == 'decode_limited':
                    break
        raise MediaFailure(error)


def create_moments_router(service):
    router = APIRouter(prefix='/moments')
    moments = service.moments

    @router.get('')
    async def listing(account: str, q: str = Query('', max_length=200), scope: str = Query('all', pattern='^(all|self)$'),
                      cursor: str = Query('', max_length=1024), limit: int = Query(20, ge=1, le=60)):
        await service.check_account(account, force=True)
        moments.ensure(account)
        result = await asyncio.to_thread(moments.list, account, q, scope, cursor, limit)
        await service.check_account(account, force=True)
        return result

    @router.post('/refresh')
    async def refresh(account: str):
        await service.check_account(account, force=True)
        moments.ensure(account, force=True)
        result = await asyncio.to_thread(moments.list, account)
        await service.check_account(account, force=True)
        return result

    @router.get('/{ident}/media/{index}')
    async def media_file(ident: str, index: int, account: str, asset: str = Query(..., pattern='^[a-f0-9]{24}$'),
                         variant: str = Query('source', pattern='^(preview|source)$')):
        await service.check_account(account, force=True)
        try:
            path, mime = await asyncio.to_thread(moments.resolve, account, ident, index, asset, variant)
        except MediaFailure as exc:
            raise HTTPException(404, STATES[exc.state]) from None
        await service.check_account(account, force=True)
        return FileResponse(path, media_type=mime, headers={'Cache-Control': 'private, no-store',
                            'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "default-src 'none'; sandbox"})
    return router
