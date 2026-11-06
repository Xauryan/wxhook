"""Verified live-account snapshots. Source databases and their WAL stay read-only."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import time
from uuid import uuid4
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

from .active_roster import ActiveWechatRoster, ActiveRosterError
from .chat_messages import ChatMessages, dumps, present_content, server_identity
from .parsing import decode_wcdb_value, extract_protobuf_strings
from .snapshot_cache import SnapshotCache

import psutil

HISTORY_SCHEMA = 6
HISTORY_BATCH_SIZE = 50


@contextmanager
def readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON')
        yield db
    finally:
        db.close()


class ActiveHistory:
    def __init__(self, roster, store: ChatMessages):
        self.roster, self.store = roster, store
        self.snapshot_lock = asyncio.Lock()

    async def prepare(self, account):
        async with self.snapshot_lock:
            return await self._prepare(account)

    async def prepare_recent(self, account):
        async with self.snapshot_lock:
            return await self._prepare(account, recent=True)

    async def _prepare(self, account, recent=False):
        token = uuid4().hex
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'backend.app.chat_history', account,
            self.roster.bridge_url, str(self.roster.repository_root),
            str(self.roster.key_manifest.resolve()), str(self.roster.cache_root.resolve()),
            '--lease-token', token, '--owner-pid', str(os.getpid()),
            '--owner-started', str(psutil.Process().create_time()),
            *(['--recent'] if recent else []),
            cwd=str(Path(__file__).resolve().parents[2]),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=0x08000000 if sys.platform == 'win32' else 0,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 180)
            if process.returncode:
                raise ActiveRosterError(stderr.decode('utf-8', 'replace')[:300])
            return json.loads(stdout)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            await asyncio.to_thread(SnapshotCache(self.roster.cache_root).release, account, token)
            raise

    def release(self, snapshot):
        token = snapshot.get('_cache_lease')
        if token:
            SnapshotCache(self.roster.cache_root).release(snapshot['account'], token)

    def _write_batch(self, account, batch):
        # Decode and locate resources before taking the shared cache write lock.
        with self.store.db() as target:
            for message in batch:
                self.store.upsert(target, account, message)

    def apply_recent(self, account, snapshot, limit=60, session_limit=80):
        """Import native recent rows without waiting for the archive or media index."""
        if snapshot['account'] != account:
            raise ActiveRosterError('历史来源账号不一致')
        sources = snapshot['databases']
        session_path = sources.get('session/session.db')
        sessions = None
        if session_path:
            with readonly(session_path) as db:
                sessions = [row[0] for row in db.execute(
                    'SELECT username FROM SessionTable WHERE last_timestamp>=? ORDER BY last_timestamp DESC LIMIT ?',
                    (int(snapshot['read_started_at']) - 86400, session_limit))]
        read_count = own_count = 0
        batch = []
        for relative, path in sources.items():
            if not re.fullmatch(r'message/(?:biz_)?message_\d+\.db', relative):
                continue
            with readonly(path) as db:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")}
                selected = sessions
                if selected is None:
                    selected = [row[0] for row in db.execute('SELECT user_name FROM Name2Id LIMIT ?', (session_limit,))]
                for session in selected:
                    table = 'Msg_' + hashlib.md5(session.encode()).hexdigest()
                    if table not in tables:
                        continue
                    # Native local_id is an INTEGER PRIMARY KEY. Read its tail
                    # before decoding, instead of sorting the entire conversation.
                    rows = db.execute(f'''SELECT local_id,server_id,local_type,sort_seq,real_sender_id,create_time,
                        CAST(message_content AS BLOB) body,CAST(compress_content AS BLOB) compressed
                        FROM "{table}" ORDER BY local_id DESC LIMIT ?''', (limit,)).fetchall()
                    authors = list({row['real_sender_id'] for row in rows})
                    names = {}
                    if authors:
                        markers = ','.join('?' for _ in authors)
                        names = dict(db.execute(f'SELECT rowid,user_name FROM Name2Id WHERE rowid IN ({markers})', authors))
                    for row in reversed(rows):
                        decoded = decode_wcdb_value(row['body'] or row['compressed'])
                        parsed = present_content(decoded.text, int(row['local_type'] or 0), session.endswith('@chatroom'))
                        sender = parsed.pop('sender_prefix') or names.get(row['real_sender_id'], '')
                        message = {**parsed, 'session_username':session, 'sender_username':sender,
                                   'is_self':sender == account, 'server_id':server_identity(row['server_id']),
                                   'local_id':row['local_id'], 'create_time':row['create_time'],
                                   'sort_seq':row['sort_seq'], 'source':'history', 'source_shard':relative,
                                   '_history_read_at':snapshot['read_started_at'],
                                   '_recent_read_at':snapshot['read_started_at'], 'decode_error':decoded.error}
                        batch.append(message)
                        read_count += 1
                        own_count += sender == account
                        if len(batch) >= HISTORY_BATCH_SIZE:
                            self._write_batch(account, batch)
                            batch.clear()
        if batch:
            self._write_batch(account, batch)
        # A bounded tail is never a complete archive verification.
        return {'read_count':read_count, 'own_count':own_count, 'verified_at':snapshot['verified_at']}

    def apply_sent(self, account, snapshot, session, created_at, text):
        """Read actual outgoing text without waiting for the full history import."""
        if snapshot['account'] != account:
            raise ActiveRosterError('历史来源账号不一致')
        table = 'Msg_' + hashlib.md5(session.encode()).hexdigest()
        matched = False
        batch = []
        for relative, path in snapshot['databases'].items():
            if not re.fullmatch(r'message/(?:biz_)?message_\d+\.db', relative):
                continue
            with readonly(path) as db:
                if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                    continue
                names = {row[0]: row[1] for row in db.execute('SELECT rowid,user_name FROM Name2Id')}
                authors = [ident for ident, name in names.items() if name == account]
                if not authors:
                    continue
                markers = ','.join('?' for _ in authors)
                # Native timestamps may precede request recording by a few
                # seconds. IDs and authors still come only from the source.
                rows = db.execute(f'''SELECT local_id,server_id,local_type,sort_seq,real_sender_id,create_time,
                    CAST(message_content AS BLOB) body,CAST(compress_content AS BLOB) compressed
                    FROM "{table}" WHERE real_sender_id IN ({markers}) AND local_type=1 AND create_time>=?
                    ORDER BY create_time DESC,sort_seq DESC,local_id DESC LIMIT 200''',
                    (*authors, max(0, int(created_at) - 5)))
                for row in rows:
                    decoded = decode_wcdb_value(row['body'] or row['compressed'])
                    parsed = present_content(decoded.text, int(row['local_type'] or 0), session.endswith('@chatroom'))
                    sender = parsed.pop('sender_prefix') or names.get(row['real_sender_id'], '')
                    if sender != account or parsed.get('category') != 'text' or decoded.error:
                        continue
                    server = server_identity(row['server_id'])
                    if server == '0' and not row['local_id']:
                        continue
                    message = {**parsed, 'session_username': session, 'sender_username': sender,
                               'is_self': True, 'server_id': server, 'local_id': row['local_id'],
                               'create_time': row['create_time'], 'sort_seq': row['sort_seq'],
                               'source': 'history', 'source_shard': relative,
                               '_history_read_at': snapshot.get('read_started_at', 0), 'decode_error': None}
                    batch.append(message)
                    if len(batch) >= HISTORY_BATCH_SIZE:
                        self._write_batch(account, batch)
                        batch.clear()
                    matched = matched or (server != '0' and parsed.get('content') == text.strip())
        if batch:
            self._write_batch(account, batch)
        # This is a partial read. Do not publish complete history metadata or
        # turn an accepted request into a delivery acknowledgement.
        return matched

    def apply(self, account, snapshot):
        if snapshot['account'] != account:
            raise ActiveRosterError('历史来源账号不一致')
        if snapshot.get('partial'):
            raise ActiveRosterError('近期消息快照不能用作完整历史校验')
        old = self.store.state(account)
        # A fresh process binding is still verified on every prepare, even when
        # no file changed. No old-account snapshot is used as a fallback.
        if old.get('fingerprint') == snapshot['fingerprint'] and old.get('schema') == HISTORY_SCHEMA and time.time() - old.get('imported_at', 0) < 120:
            old['verified_at'] = snapshot['verified_at']
            with self.store.db() as db:
                db.execute('UPDATE chat_history_state SET data=? WHERE account=?', (dumps(old), account))
            return old
        started = time.perf_counter()
        sources = snapshot['databases']
        root = Path(snapshot['root'])
        resources = HistoryResources(root, sources)
        sessions = {}
        if sources.get('session/session.db'):
            with readonly(sources['session/session.db']) as db:
                for row in db.execute('SELECT username,last_timestamp,summary FROM SessionTable'):
                    sessions[row['username']] = {'last_active': row['last_timestamp'], 'summary': decode_wcdb_value(row['summary']).text}
        count = 0
        categories = defaultdict(int)
        batch = []
        for relative, path in sources.items():
            if not re.fullmatch(r'message/(?:biz_)?message_\d+\.db', relative):
                continue
            with readonly(path) as db:
                names = {r[0]: r[1] for r in db.execute('SELECT rowid,user_name FROM Name2Id')}
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")}
                for session in names.values():
                    table = 'Msg_' + hashlib.md5(session.encode()).hexdigest()
                    if table not in tables:
                        continue
                    for row in db.execute(f'SELECT local_id,server_id,local_type,sort_seq,real_sender_id,create_time,CAST(message_content AS BLOB) body,CAST(compress_content AS BLOB) compressed FROM "{table}" ORDER BY create_time,sort_seq,local_id'):
                        decoded = decode_wcdb_value(row['body'] or row['compressed'])
                        parsed = present_content(decoded.text, int(row['local_type'] or 0), session.endswith('@chatroom'))
                        sender = parsed.pop('sender_prefix') or names.get(row['real_sender_id'], '')
                        message = {**parsed, 'session_username': session, 'sender_username': sender,
                                   'is_self': sender == account, 'server_id': server_identity(row['server_id']),
                                   'local_id': row['local_id'], 'create_time': row['create_time'],
                                   'sort_seq': row['sort_seq'], 'source': 'history',
                                   '_history_read_at':snapshot.get('read_started_at', 0),
                                   'decode_error': decoded.error, 'source_shard': relative}
                        message['_media'] = resources.for_message(message)
                        batch.append(message)
                        count += 1
                        categories[message['category']] += 1
                        if len(batch) >= HISTORY_BATCH_SIZE:
                            self._write_batch(account, batch)
                            batch.clear()
                            # Give foreground writers a chance between batches.
                            time.sleep(.01)
        if batch:
            self._write_batch(account, batch)
        with self.store.db() as target:
            snapshot.update(schema=HISTORY_SCHEMA, imported_at=time.time(), message_count=count, categories=dict(categories),
                            sessions=sessions, import_ms=round((time.perf_counter() - started) * 1000, 2))
            published = {key: value for key, value in snapshot.items() if key != '_cache_lease'}
            target.execute('INSERT INTO chat_history_state VALUES (?,?) ON CONFLICT(account) DO UPDATE SET data=excluded.data', (account, dumps(published)))
        return snapshot


def prepare_snapshot(roster, account, *, recent=False, token=None, owner_pid=None, owner_started=None):
    before = roster.verify(account, force=True)
    root = before['source'].parents[2].resolve()
    cache = SnapshotCache(roster.cache_root)
    if cache.root.is_relative_to(root):
        raise ActiveRosterError('历史缓存不能位于微信源目录')
    with cache.preparing(account, token=token, owner_pid=owner_pid, owner_started=owner_started) as token:
        result = _prepare_snapshot(roster, account, before, recent=recent)
        cache.publish_locked(account, token, result['databases'])
        result['_cache_lease'] = token
        return result


def _prepare_snapshot(roster, account, before, *, recent=False):
    started = time.perf_counter()
    read_started_at = time.time()
    root = before['source'].parents[2].resolve()
    destination = roster.cache_root / 'chat_sources' / hashlib.sha256(account.encode()).hexdigest()[:24]
    if destination.resolve().is_relative_to(root):
        raise ActiveRosterError('历史缓存不能位于微信源目录')
    destination.mkdir(parents=True, exist_ok=True)
    sources = sorted((root / 'db_storage' / 'message').glob('*message_[0-9]*.db'))
    if not recent:
        sources += sorted((root / 'db_storage' / 'message').glob('media_[0-9]*.db'))
    extra_sources = ('session/session.db',) if recent else ('session/session.db', 'message/message_resource.db', 'hardlink/hardlink.db', 'head_image/head_image.db', 'emoticon/emoticon.db', 'general/general.db')
    for relative in extra_sources:
        candidate = root / 'db_storage' / relative
        if candidate.is_file():
            sources.append(candidate)
    if not any(re.fullmatch(r'message_\d+\.db', p.name) for p in sources):
        raise ActiveRosterError('当前账号消息库不存在')
    databases, fingerprints = {}, {}
    tool = roster.loader._load_decrypt_tool()
    manifest = tool.load_manifest(roster.key_manifest)
    for source in sources:
        if not source.resolve().is_relative_to(root):
            raise ActiveRosterError('源库路径越界')
        relative = source.relative_to(root / 'db_storage').as_posix()
        for attempt in range(3):
            fingerprint = roster.loader._source_fingerprint(source)
            stamp = hashlib.sha256(dumps([relative, fingerprint]).encode()).hexdigest()[:24]
            output = destination / f'{source.stem}-{stamp}.sqlite'
            if output.is_file():
                break
            temporary = destination / f'{source.stem}-{stamp}-{os.getpid()}.partial'
            try:
                plain_source = roster.loader._is_plain_sqlite(source)
                if plain_source:
                    # SQLite may create/update -shm even with mode=ro. Perform
                    # recovery/backup only on private copies, never source files.
                    copied = temporary.with_suffix('.source')
                    try:
                        shutil.copyfile(source, copied)
                        wal = Path(str(source) + '-wal')
                        if wal.is_file():
                            shutil.copyfile(wal, Path(str(copied) + '-wal'))
                        roster.loader._backup_plain_database(copied, temporary)
                    finally:
                        for suffix in ('', '-wal', '-shm'):
                            Path(str(copied) + suffix).unlink(missing_ok=True)
                else:
                    # Capture encrypted DB/WAL while the source is stable, then
                    # decrypt private copies. New messages must not invalidate
                    # a coherent copy during a multi-second HMAC/decrypt pass.
                    copied = temporary.with_suffix('.encrypted')
                    try:
                        shutil.copyfile(source, copied)
                        wal = Path(str(source) + '-wal')
                        if wal.is_file():
                            shutil.copyfile(wal, Path(str(copied) + '-wal'))
                        if fingerprint != roster.loader._source_fingerprint(source):
                            raise ActiveRosterError('复制期间源库或 WAL 变化，重试一致性快照')
                        result = tool.decrypt_database(copied, temporary, manifest, force=True, ignore_wal=True)
                        roster.loader._validate_wal_status(result['wal'])
                        if result['wal'].get('commit_frames'):
                            roster.loader._apply_encrypted_wal(copied, temporary, manifest, result['wal'], tool)
                    finally:
                        for suffix in ('', '-wal', '-shm'):
                            Path(str(copied) + suffix).unlink(missing_ok=True)
                if plain_source and fingerprint != roster.loader._source_fingerprint(source):
                    raise ActiveRosterError('读取期间源库或 WAL 变化，重试一致性快照')
                with readonly(temporary) as db:
                    if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                        raise ActiveRosterError('历史库完整性校验失败')
                os.replace(temporary, output)
                break
            except Exception:
                temporary.unlink(missing_ok=True)
                if attempt == 2:
                    raise
                time.sleep(.1)
        databases[relative] = str(output)
        fingerprints[relative] = fingerprint
    if roster.verify(account, force=True) != before:
        raise ActiveRosterError('读取历史期间账号或微信进程已切换')
    return {'account': account, 'root': str(root), 'databases': databases,
            'fingerprint': hashlib.sha256(dumps(fingerprints).encode()).hexdigest(),
            'verified_at': time.time(), 'snapshot_ms': round((time.perf_counter() - started) * 1000, 2),
            'read_started_at':read_started_at,
            'source': 'active_wechat_databases', 'pid': before['pid'], 'partial':recent}


class HistoryResources:
    def __init__(self, root, sources):
        self.root, self.sources = root, sources
        self.files = defaultdict(list)
        self.hardlinks = defaultdict(list)
        self.links = defaultdict(list)
        self.voices = {}
        for name in ('msg', 'resource', 'cache', 'business/emoticon'):
            base = root / name
            if not base.is_dir() or not base.resolve().is_relative_to(root):
                continue
            for directory, dirs, _ in os.walk(base, followlinks=False):
                dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink() and (Path(directory) / d).resolve().is_relative_to(root)]
                if not Path(directory).resolve().is_relative_to(root):
                    dirs[:] = []
                    continue
                # DirEntry caches Windows attributes from directory enumeration;
                # resolving every media file otherwise opens it repeatedly.
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                            continue
                        if os.name == 'nt' and entry.stat(follow_symlinks=False).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                            continue
                        self.files[entry.name.casefold()].append(Path(entry.path))
        if sources.get('hardlink/hardlink.db'):
            with readonly(sources['hardlink/hardlink.db']) as db:
                for kind in ('image', 'video', 'file'):
                    for row in db.execute(f'SELECT md5,file_name,file_size,type FROM {kind}_hardlink_info_v4'):
                        item = dict(row)
                        item['kind'] = kind
                        for key in {str(row['md5']).lower(), Path(row['file_name']).stem.lower().removesuffix('_t').removesuffix('_h')}:
                            self.hardlinks[key].append(item)
        if sources.get('message/message_resource.db'):
            with readonly(sources['message/message_resource.db']) as db:
                for row in db.execute('''SELECT n.user_name,i.message_svr_id,i.message_local_id,i.message_create_time,
                    i.packed_info parent,d.packed_info detail,d.type,d.status,d.size
                    FROM MessageResourceInfo i JOIN ChatName2Id n ON n.rowid=i.chat_id
                    LEFT JOIN MessageResourceDetail d ON d.message_id=i.message_id'''):
                    item = dict(row)
                    item['hints'] = extract_protobuf_strings(row['parent']) + extract_protobuf_strings(row['detail'])
                    self.links[(row['user_name'], server_identity(row['message_svr_id']))].append(item)
        for relative, path in sources.items():
            if re.fullmatch(r'message/media_\d+\.db', relative):
                with readonly(path) as db:
                    for row in db.execute('SELECT v.rowid,n.user_name,v.svr_id,v.local_id,length(v.voice_data) size FROM VoiceInfo v JOIN Name2Id n ON n.rowid=v.chat_name_id'):
                        if row['size']:
                            self.voices[(row['user_name'], server_identity(row['svr_id']))] = {'kind': 'voice', 'database': path, 'rowid': row['rowid'], 'size': row['size'], 'state': 'local'}

    def for_message(self, message):
        category = message['category']
        if category not in {'image', 'sticker', 'voice', 'video', 'file'}:
            return []
        session, server = message['session_username'], str(message['server_id'])
        if category == 'voice':
            return [self.voices.get((session, server), {'kind': category, 'state': 'not_downloaded'})]
        hints = set(message.get('media_hints') or [])
        links = self.links.get((session, server), []) if server != '0' else []
        for item in links:
            for text in item['hints']:
                hints.update(re.findall(r'(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])', text.lower()))
        candidates = []
        seen = set()
        for hint in hints:
            for record in self.hardlinks.get(hint, []):
                if record['kind'] != ('image' if category == 'sticker' else category):
                    continue
                matches = self.files.get(record['file_name'].casefold(), [])
                if len(matches) > 1:
                    scoped = [p for p in matches if hashlib.md5(session.encode()).hexdigest() in str(p)]
                    matches = scoped or [p for p in matches if p.stat().st_size == record['file_size']]
                if len(matches) != 1:
                    continue
                path = matches[0]
                if path in seen:
                    continue
                seen.add(path)
                actual_kind = 'image' if category == 'video' and path.suffix.lower() in {'.jpg','.png','.webp'} else category
                variant = 'thumbnail' if actual_kind == 'image' and (category == 'video' or '_t.' in path.name or record['type'] == 1) else 'original' if '_h.' in path.name or category in {'video','file'} else 'preview'
                candidates.append({'kind': actual_kind, 'path': path.relative_to(self.root).as_posix(), 'size': path.stat().st_size,
                                   'name': message.get('file_name') or path.name, 'variant': variant, 'state': 'local'})
                if category == 'image':
                    # Some databases index only the normal file, although WeChat
                    # has already materialized its exact _t / _h sibling.
                    stem = path.stem.removesuffix('_t').removesuffix('_h')
                    for suffix, variant in (('_t','thumbnail'),('_h','original')):
                        sibling = path.with_name(stem + suffix + path.suffix)
                        if sibling.is_file() and sibling.resolve().is_relative_to(self.root) and sibling not in seen:
                            seen.add(sibling)
                            candidates.append({'kind':category,'path':sibling.relative_to(self.root).as_posix(),
                                               'size':sibling.stat().st_size,'name':sibling.name,'variant':variant,'state':'local'})
            if category == 'sticker':
                for suffix in ('', '.gif', '.png', '.webp', '.dat'):
                    for path in self.files.get(hint + suffix, []):
                        if path not in seen:
                            seen.add(path)
                            candidates.append({'kind': category, 'path': path.relative_to(self.root).as_posix(), 'name': path.name,
                                               'aes_key':message.get('_media_key',''),'md5':hint,'state': 'local', 'variant': 'original'})
        if not candidates:
            expected_local = any(item['status'] == 1 for item in links)
            candidates.append({'kind': category, 'name': message.get('file_name'), 'size': message.get('file_size'),
                               'state': 'missing' if expected_local else 'not_downloaded'})
        return sorted(candidates, key=lambda item: (item.get('variant',''), item.get('path',''), item.get('database','')))


if __name__ == '__main__':
    try:
        import argparse
        parser = argparse.ArgumentParser()
        for name in ('account', 'bridge', 'repository', 'manifest', 'cache'):
            parser.add_argument(name)
        parser.add_argument('--recent', action='store_true')
        parser.add_argument('--lease-token')
        parser.add_argument('--owner-pid', type=int)
        parser.add_argument('--owner-started', type=float)
        args = parser.parse_args()
        reader = ActiveWechatRoster(args.bridge, Path(args.repository), Path(args.manifest), Path(args.cache))
        result = prepare_snapshot(reader, args.account, recent=args.recent, token=args.lease_token,
                                  owner_pid=args.owner_pid, owner_started=args.owner_started)
        sys.stdout.buffer.write(dumps(result).encode('utf-8'))
    except Exception as exc:
        # Do not return source paths, key material or message bodies to the UI.
        sys.stderr.buffer.write(('当前账号历史读取失败 (' + type(exc).__name__ + ')，源库未修改；请检查本机密钥及 WAL 一致性。').encode('utf-8'))
        sys.exit(1)
