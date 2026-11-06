from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from .chat_file_preview import office_preview
from .chat_history import readonly
from .chat_media import MediaFailure, STATES, safe_path
from .chat_messages import dumps, integer
from .favorite_data import FavoriteFiles, parse_favorite
from .favorite_source import prepare_worker
from .sns_media import SIG_V1, SIG_V2


def file_digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'md5').hexdigest()


class Favorites:
    def __init__(self, service):
        self.service = service
        self.tasks, self.errors, self.last_attempt = {}, {}, {}
        self.verified_files = {}
        with service.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS favorite_items(account TEXT NOT NULL,id INTEGER NOT NULL,data TEXT NOT NULL,PRIMARY KEY(account,id));
                CREATE TABLE IF NOT EXISTS favorite_state(account TEXT PRIMARY KEY,data TEXT NOT NULL);
            ''')

    def state(self, account):
        with self.service.db() as db:
            row = db.execute('SELECT data FROM favorite_state WHERE account=?', (account,)).fetchone()
        return json.loads(row[0]) if row else {}

    async def prepare(self, account):
        reader = self.service.roster
        if not reader or not hasattr(reader, 'repository_root'):
            raise RuntimeError('收藏来源未配置')
        return await prepare_worker(reader, account)

    def ensure(self, account, force=False):
        task = self.tasks.get(account)
        if task and not task.done():
            return task
        if not force and time.monotonic() - self.last_attempt.get(account, 0) < 15:
            return None
        async def sync():
            try:
                snapshot = await self.prepare(account)
                await self.service.check_account(account, force=True)
                await asyncio.to_thread(self.apply, account, snapshot)
                self.errors.pop(account, None)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.errors[account] = '当前账号收藏读取失败，请刷新重试；未使用其他账号或聊天记录代替。'
            finally:
                self.last_attempt[account] = time.monotonic()
        task = self.tasks[account] = asyncio.create_task(sync())
        return task

    def apply(self, account, snapshot):
        if snapshot.get('account') != account or snapshot.get('source') != 'active_favorite_db':
            raise ValueError('Favorite snapshot ownership mismatch')
        files = FavoriteFiles(Path(snapshot['root']))
        with readonly(snapshot['database']) as db:
            columns = {row[1] for row in db.execute('PRAGMA table_info(fav_db_item)')}
            if not {'local_id', 'type', 'flag', 'content', 'update_time'} <= columns:
                raise ValueError('Unsupported favorite schema')
            total = db.execute('SELECT count(*) FROM fav_db_item').fetchone()[0]
            rows = [dict(row) for row in db.execute('SELECT * FROM fav_db_item ORDER BY update_time DESC,local_id DESC LIMIT 20000')]
            items = [parse_favorite(row, files, account) for row in rows if integer(row['flag']) == 0]
            by_local = {item['id']: item for item in items}
            by_server = {str(row.get('server_id')): str(row['local_id']) for row in rows if row.get('server_id') and str(row['local_id']) in by_local}
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            tags, binding_limited = [], 0
            tags_ready = {'fav_tag_db_item', 'fav_bind_tag_db_item'} <= tables
            if tags_ready:
                tags = [{'id': str(row['local_id']), 'name': row['name'], 'count': 0} for row in db.execute('SELECT local_id,name FROM fav_tag_db_item ORDER BY seq,local_id') if row['name']]
                known_tags = {tag['id']: tag for tag in tags}
                for row in db.execute('SELECT * FROM fav_bind_tag_db_item'):
                    if integer(row['op_code']) != 0:
                        binding_limited += 1
                        continue
                    ident = str(row['fav_local_id']) if row['fav_local_id'] else by_server.get(str(row['fav_server_id']))
                    item, tag = by_local.get(ident), known_tags.get(str(row['tag_local_id']))
                    if item and tag and tag['id'] not in [entry['id'] for entry in item['tags']]:
                        item['tags'].append({'id': tag['id'], 'name': tag['name']})
                        tag['count'] += 1
        for item in items:
            item['revision'] = hashlib.sha256(dumps(item).encode()).hexdigest()[:20]
        state = {**snapshot, 'ready': True, 'count': len(items), 'tags': tags, 'tags_ready': tags_ready,
                 'binding_limited': binding_limited, 'limited_count': total - len(items),
                 'asset_index_limited': files.limited,
                 'recorded_attachment_bytes': sum(spec.get('size') or 0 for item in items for spec in item['_media'] if spec['variant'] == 'original')}
        with self.service.db() as db:
            db.execute('DELETE FROM favorite_items WHERE account=?', (account,))
            db.executemany('INSERT INTO favorite_items VALUES (?,?,?)', [(account, int(item['id']), dumps(item)) for item in items])
            db.execute('INSERT INTO favorite_state VALUES (?,?) ON CONFLICT(account) DO UPDATE SET data=excluded.data', (account, dumps(state)))

    def raw(self, account, ident):
        with self.service.db() as db:
            row = db.execute('SELECT data FROM favorite_items WHERE account=? AND id=?', (account, ident)).fetchone()
        if not row:
            raise HTTPException(404, '当前账号没有此收藏，或收藏已更新')
        return json.loads(row[0])

    def asset_url(self, account, ident, index, spec):
        token = self.service.media.spec_key(spec)
        return f'/api/chat/favorites/{ident}/media/{index}?' + urlencode({'account': account, 'asset': token})

    def present(self, account, item, names, summary=False):
        value = {k: v for k, v in item.items() if not k.startswith('_') and k not in {'source_name_hint', 'source_username'}}
        value['source_name'] = names.get(item.get('source_username')) or item.get('source_name_hint') or '来源未知'
        available = [(index, spec) for index, spec in enumerate(item['_media']) if spec['kind'] == 'image' and spec['state'] == 'local']
        available.sort(key=lambda pair: (pair[1]['group'], pair[1]['variant'] != 'thumbnail'))
        value['thumbnail_url'] = self.asset_url(account, item['id'], *available[0]) if available else None
        value['thumbnail_fallback_url'] = self.asset_url(account, item['id'], *available[1]) if len(available) > 1 and available[1][1]['group'] == available[0][1]['group'] else None
        if summary:
            value['content'] = value['content'][:360]
        return value

    def list(self, account, q='', category='all', tag='', offset=0, limit=60):
        state = self.state(account)
        with self.service.db() as db:
            rows = [json.loads(row[0]) for row in db.execute('SELECT data FROM favorite_items WHERE account=?', (account,))]
            names = dict(db.execute('SELECT username,name FROM live_names WHERE account=?', (account,)))
        items = [self.present(account, item, names, summary=True) for item in rows]
        counts = {'all': len(items), 'media': sum(item['category'] in {'image', 'video'} for item in items)}
        selected = []
        for item, raw in zip(items, rows):
            if category == 'media' and item['category'] not in {'image', 'video'}:
                continue
            if tag and tag not in [entry['id'] for entry in item['tags']]:
                continue
            haystack = ' '.join([raw.get('title', ''), raw.get('content', ''), raw.get('description', ''), item['source_name'], raw.get('url', '')] + [entry['name'] for entry in raw.get('attachments', [])] + [entry['name'] for entry in item['tags']])
            if q.casefold() not in haystack.casefold():
                continue
            selected.append(item)
        selected.sort(key=lambda item: (item['updated_at'], int(item['id'])), reverse=True)
        task = self.tasks.get(account)
        return {'account': account, 'ready': bool(state.get('ready')), 'syncing': bool(task and not task.done()),
                'error': self.errors.get(account), 'updated_at': state.get('verified_at'), 'revision': state.get('fingerprint'),
                'total': len(selected), 'counts': counts, 'items': selected[offset:offset + limit],
                'tags': state.get('tags', []), 'tags_ready': state.get('tags_ready', False),
                'limited_count': state.get('limited_count', 0), 'binding_limited': state.get('binding_limited', 0),
                'recorded_attachment_bytes': state.get('recorded_attachment_bytes'), 'cloud_remaining_bytes': None,
                'write_supported': False, 'write_detail': '当前微信桥接尚未接入收藏写入，新建笔记及标签修改请在微信中完成。'}

    def detail(self, account, ident):
        item = self.raw(account, ident)
        with self.service.db() as db:
            names = dict(db.execute('SELECT username,name FROM live_names WHERE account=?', (account,)))
        return self.present(account, item, names)

    def spec(self, account, ident, index, token=None):
        specs = self.raw(account, ident)['_media']
        if index < 0 or index >= len(specs):
            raise HTTPException(404, '收藏附件不存在')
        spec = specs[index]
        if token is not None and token != self.service.media.spec_key(spec):
            raise HTTPException(409, '收藏资源已更新，请刷新后重新打开')
        return spec

    def resolve(self, account, ident, index, token=None):
        spec = self.spec(account, ident, index, token)
        if spec['state'] != 'local':
            raise MediaFailure(spec['state'])
        state = self.state(account)
        root = Path(state['root'])
        candidates = spec.get('candidates', [])
        if not candidates or len(candidates) > 32 or not spec.get('expected_md5'):
            raise MediaFailure('unavailable')
        error = 'missing'
        for relative in candidates:
            if not relative.startswith(('business/favorite/data/', 'business/favorite/mid/')):
                raise MediaFailure('unavailable')
            try:
                source = safe_path(root, relative)
                allowed = (root / 'business/favorite' / ('mid' if spec['variant'] == 'thumbnail' else 'data')).resolve()
                if not source.is_relative_to(allowed) or not allowed.is_relative_to(root.resolve()):
                    raise MediaFailure('unavailable')
                stat = source.stat()
                identity = (account, str(source), stat.st_size, stat.st_mtime_ns, spec['expected_md5'])
                verified = self.verified_files.get(identity)
                if verified and verified[0].is_file():
                    return verified
                with source.open('rb') as stream:
                    encrypted = stream.read(6) in {SIG_V1, SIG_V2}
                if not encrypted and file_digest(source) != spec['expected_md5']:
                    error = 'integrity_failed'
                    continue
                converted = self.service.media._resolve_source(account, source, {
                    'kind': spec['kind'], 'path': relative, 'name': spec['name'] if spec['kind'] == 'file' else source.name})
                if encrypted and file_digest(converted[0]) != spec['expected_md5']:
                    error = 'integrity_failed'
                    continue
                self.verified_files[identity] = converted
                return converted
            except MediaFailure as exc:
                error = exc.state
                if error == 'decode_limited':
                    break
            except OSError:
                error = 'missing'
        raise MediaFailure(error)

    def media(self, account, ident, group=0):
        started = time.perf_counter()
        specs = [(i, s) for i, s in enumerate(self.raw(account, ident)['_media']) if s['group'] == group]
        if not specs:
            return {'items': [], 'preferred': 0, 'elapsed_ms': 0}
        kind = next((s['kind'] for _, s in specs if s['variant'] == 'original'), 'image')
        order = sorted(range(len(specs)), key=lambda i: (specs[i][1]['kind'] != kind, specs[i][1]['variant'] != 'thumbnail'))
        preferred, checked = order[0], {}
        for position in order:
            index, spec = specs[position]
            if spec['state'] != 'local' or spec['kind'] != kind:
                continue
            try:
                path, mime = self.resolve(account, ident, index)
                checked[position] = ('available', mime, path.stat().st_size)
                preferred = position
                break
            except MediaFailure as exc:
                checked[position] = (exc.state, None, spec.get('size'))
        items = []
        for position, (index, spec) in enumerate(specs):
            status, mime, size = checked.get(position, (spec['state'], None, spec.get('size')))
            items.append({'index': index, 'kind': spec['kind'], 'variant': spec['variant'], 'name': spec['name'],
                          'size': size, 'state': status, 'detail': STATES[status], 'mime': mime,
                          'url': self.asset_url(account, ident, index, spec) if status in {'available', 'local'} else None})
        return {'items': items, 'preferred': preferred, 'elapsed_ms': round((time.perf_counter() - started) * 1000, 2)}


def create_favorites_router(service):
    router = APIRouter(prefix='/favorites')
    favorites = service.favorites

    @router.get('')
    async def listing(account: str, q: str = Query('', max_length=200), category: str = Query('all', pattern='^(all|media)$'), tag: str = Query('', max_length=100), offset: int = Query(0, ge=0), limit: int = Query(60, ge=1, le=100)):
        await service.check_account(account, force=True)
        favorites.ensure(account)
        result = await asyncio.to_thread(favorites.list, account, q, category, tag, offset, limit)
        await service.check_account(account, force=True)
        return result

    @router.post('/refresh')
    async def refresh(account: str):
        await service.check_account(account, force=True)
        await asyncio.shield(favorites.ensure(account, force=True))
        await service.check_account(account, force=True)
        return await asyncio.to_thread(favorites.list, account)

    @router.get('/{ident}')
    async def detail(ident: int, account: str):
        await service.check_account(account, force=True)
        result = await asyncio.to_thread(favorites.detail, account, ident)
        await service.check_account(account, force=True)
        return result

    @router.get('/{ident}/media')
    async def media(ident: int, account: str, group: int = Query(0, ge=0, le=99)):
        await service.check_account(account, force=True)
        result = await asyncio.to_thread(favorites.media, account, ident, group)
        await service.check_account(account, force=True)
        return result

    @router.get('/{ident}/media/{index}/preview')
    async def preview(ident: int, index: int, account: str, asset: str = Query(..., pattern='^[a-f0-9]{24}$')):
        await service.check_account(account, force=True)
        try:
            spec = favorites.spec(account, ident, index, asset)
            if spec['kind'] != 'file':
                raise MediaFailure('unsupported')
            path, _ = await asyncio.to_thread(favorites.resolve, account, ident, index, asset)
            result = await asyncio.to_thread(office_preview, path, spec['name'])
        except MediaFailure as exc:
            raise HTTPException(415, STATES[exc.state]) from None
        await service.check_account(account, force=True)
        return JSONResponse(result, headers={'Cache-Control': 'private, no-store'})

    @router.get('/{ident}/media/{index}')
    async def asset_file(ident: int, index: int, account: str, asset: str = Query(..., pattern='^[a-f0-9]{24}$'), download: bool = False):
        await service.check_account(account, force=True)
        try:
            path, mime = await asyncio.to_thread(favorites.resolve, account, ident, index, asset)
        except MediaFailure as exc:
            raise HTTPException(404, STATES[exc.state]) from None
        await service.check_account(account, force=True)
        spec = favorites.spec(account, ident, index, asset)
        name = (spec['name'] or 'favorite').replace('\\', '/').split('/')[-1]
        ext = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/gif': '.gif', 'image/webp': '.webp', 'video/mp4': '.mp4', 'audio/wav': '.wav'}.get(mime)
        if spec['kind'] != 'file' and ext:
            name = Path(name).stem + ext
        return FileResponse(path, media_type=mime, filename=name,
                            content_disposition_type='attachment' if download or mime == 'application/octet-stream' else 'inline',
                            headers={'Cache-Control': 'private, no-cache', 'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "default-src 'none'; sandbox"})
    return router
