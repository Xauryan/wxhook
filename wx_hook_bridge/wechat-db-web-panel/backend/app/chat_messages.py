"""Lossless chat presentation and a source-independent, account-scoped ledger."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException

from .parsing import GROUP_PREFIX, _parse_xml, parse_message_content, split_local_type
from . import chat_recalls


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def integer(value, default=0):
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def server_identity(value):
    number = integer(value)
    return str(number + (1 << 64) if number < 0 else number)


def web_url(value):
    value = (value or '').strip()
    try:
        url = urlsplit(value)
        if url.scheme.lower() in {'http', 'https'} and url.hostname and not url.username and not url.password:
            return value
    except ValueError:
        pass
    return ''


def public_articles(root):
    readers = root.xpath('self::mmreader | ./mmreader | ./appmsg/mmreader')
    if not readers:
        return None
    articles, seen = [], set()
    for reader in readers:
        for category in reader.findall('./category'):
            source = txt_from(category, './name')
            top_cover = web_url(txt_from(category, './topnew/cover'))
            for node in category:
                if node.tag not in {'item', 'newitem', 'topnew'}:
                    continue
                title = txt_from(node, './title').strip()
                if not title:
                    continue
                url = next((u for tag in ('url', 'longurl', 'shorturl')
                            if (u := web_url(txt_from(node, './' + tag)))), '')
                identity = (title, url)
                if identity in seen:
                    continue
                seen.add(identity)
                articles.append({'title': title, 'url': url,
                                 'cover_url': (top_cover if not articles else '') or web_url(txt_from(node, './cover')),
                                 'description': txt_from(node, './digest') or txt_from(node, './summary'),
                                 'source': txt_from(node, './sources/source/name') or source})
                if len(articles) >= 100:
                    return articles
    return articles


def present_content(text: str, local_type: int, group: bool) -> dict:
    from .message_presentation import present_content as present
    return present(text, local_type, group)


def txt_from(node, path):
    found = node.find(path)
    return ''.join(found.itertext()) if found is not None else ''


class ChatMessages:
    def __init__(self, path: Path):
        self.path = path
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,
                    session TEXT NOT NULL, timestamp INTEGER NOT NULL, data TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS chat_page ON chat_messages(account,session,timestamp,id);
                CREATE INDEX IF NOT EXISTS chat_local_identity ON chat_messages(
                    account,session,json_extract(data,'$.local_id'),
                    json_extract(data,'$.create_time'),json_extract(data,'$.sender_username'));
                CREATE TABLE IF NOT EXISTS chat_aliases (
                    account TEXT NOT NULL, session TEXT NOT NULL, alias TEXT NOT NULL, message_id INTEGER NOT NULL,
                    PRIMARY KEY(account,session,alias)
                );
                CREATE TABLE IF NOT EXISTS chat_changes (
                    revision INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,
                    session TEXT NOT NULL, message_id INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS chat_delta ON chat_changes(account,session,revision);
                CREATE INDEX IF NOT EXISTS chat_account_revision ON chat_changes(account,revision);
                CREATE TABLE IF NOT EXISTS chat_history_state (account TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS chat_envelopes (
                    account TEXT NOT NULL, event_id TEXT NOT NULL, digest TEXT NOT NULL,
                    raw TEXT NOT NULL, PRIMARY KEY(account,event_id,digest)
                );
            ''')
            chat_recalls.initialize(db)

        self._initialize_session_stats()

    def _initialize_session_stats(self):
        # Backfill once from the cache, then maintain counts/latest IDs in the
        # same transaction as each message. A refresh must not scan all history.
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_session_stats'").fetchone()
            db.execute('''CREATE TABLE IF NOT EXISTS chat_session_stats (
                account TEXT NOT NULL, session TEXT NOT NULL,
                message_count INTEGER NOT NULL CHECK(message_count >= 0),
                last_timestamp INTEGER NOT NULL, latest_id INTEGER NOT NULL,
                max_id INTEGER NOT NULL, PRIMARY KEY(account,session))''')
            if not existing:
                db.execute('''INSERT INTO chat_session_stats
                    SELECT account,session,count(*),max(timestamp),0,max(id)
                    FROM chat_messages GROUP BY account,session''')
                db.execute('''UPDATE chat_session_stats SET latest_id=(
                    SELECT id FROM chat_messages m WHERE m.account=chat_session_stats.account
                    AND m.session=chat_session_stats.session ORDER BY timestamp DESC,id DESC LIMIT 1)''')
            db.execute('''CREATE TRIGGER IF NOT EXISTS chat_stats_insert AFTER INSERT ON chat_messages BEGIN
                INSERT INTO chat_session_stats VALUES (NEW.account,NEW.session,1,NEW.timestamp,NEW.id,NEW.id)
                ON CONFLICT(account,session) DO UPDATE SET
                    message_count=message_count+1,
                    latest_id=CASE WHEN NEW.timestamp>last_timestamp OR
                        (NEW.timestamp=last_timestamp AND NEW.id>latest_id) THEN NEW.id ELSE latest_id END,
                    last_timestamp=max(last_timestamp,NEW.timestamp), max_id=max(max_id,NEW.id);
                END''')
            db.execute('''CREATE TRIGGER IF NOT EXISTS chat_stats_delete AFTER DELETE ON chat_messages BEGIN
                UPDATE chat_session_stats SET message_count=message_count-1,
                    latest_id=CASE WHEN latest_id=OLD.id THEN coalesce((SELECT id FROM chat_messages
                        WHERE account=OLD.account AND session=OLD.session ORDER BY timestamp DESC,id DESC LIMIT 1),0) ELSE latest_id END,
                    last_timestamp=CASE WHEN latest_id=OLD.id THEN coalesce((SELECT timestamp FROM chat_messages
                        WHERE account=OLD.account AND session=OLD.session ORDER BY timestamp DESC,id DESC LIMIT 1),0) ELSE last_timestamp END,
                    max_id=CASE WHEN max_id=OLD.id THEN coalesce((SELECT max(id) FROM chat_messages
                        WHERE account=OLD.account AND session=OLD.session),0) ELSE max_id END
                    WHERE account=OLD.account AND session=OLD.session;
                DELETE FROM chat_session_stats WHERE account=OLD.account AND session=OLD.session AND message_count=0;
                END''')
            db.execute('''CREATE TRIGGER IF NOT EXISTS chat_stats_move AFTER UPDATE OF account,session,timestamp ON chat_messages
                WHEN OLD.account<>NEW.account OR OLD.session<>NEW.session OR OLD.timestamp<>NEW.timestamp BEGIN
                DELETE FROM chat_session_stats WHERE (account=OLD.account AND session=OLD.session) OR (account=NEW.account AND session=NEW.session);
                INSERT INTO chat_session_stats SELECT m.account,m.session,count(*),max(m.timestamp),
                    (SELECT id FROM chat_messages n WHERE n.account=m.account AND n.session=m.session ORDER BY timestamp DESC,id DESC LIMIT 1),max(m.id)
                    FROM chat_messages m WHERE (m.account=OLD.account AND m.session=OLD.session) OR (m.account=NEW.account AND m.session=NEW.session)
                    GROUP BY m.account,m.session;
                END''')

    @staticmethod
    def session_totals(db, account, session):
        row = db.execute('SELECT message_count,max_id FROM chat_session_stats WHERE account=? AND session=?', (account,session)).fetchone()
        return (row[0], row[1]) if row else (0, 0)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def upsert(self, db, account: str, message: dict, event_id: str = '') -> tuple[int, bool]:
        session = message['session_username']
        server = server_identity(message.get('server_id'))
        local = int(message.get('local_id') or 0)
        # Never match content/time alone. A positive server id or a scoped local
        # identity is required, and two positive server ids must not be coalesced.
        aliases = [f'server:{server}'] if server != '0' else []
        if local > 0:
            aliases.append(f"local:{local}:{message['create_time']}:{message.get('sender_username', '')}")
        if event_id:
            aliases.append('event:' + event_id)
        if not aliases:
            raise ValueError('message identity unavailable')
        candidates = []
        for alias in aliases:
            row = db.execute('SELECT m.* FROM chat_aliases a JOIN chat_messages m ON m.id=a.message_id WHERE a.account=? AND a.session=? AND a.alias=?', (account, session, alias)).fetchone()
            if row:
                if row['id'] in [item['id'] for item in candidates]:
                    continue
                old = json.loads(row['data'])
                old_server = str(old.get('server_id') or '0')
                if server != '0' and old_server != '0' and old_server != server:
                    continue
                if alias.startswith('local:'):
                    if old.get('source_shard') and message.get('source_shard') and old['source_shard'] != message['source_shard']:
                        continue
                    # Multiple shards may reuse a local id in the same second.
                    # An unscoped Hook identity is then ambiguous, not a match.
                    matches = db.execute("SELECT count(*) FROM chat_messages WHERE account=? AND session=? AND json_extract(data,'$.local_id')=? AND json_extract(data,'$.create_time')=? AND json_extract(data,'$.sender_username')=?", (account,session,local,message['create_time'],message.get('sender_username',''))).fetchone()[0]
                    if matches > 1:
                        continue
                if row['id'] not in [item['id'] for item in candidates]:
                    candidates.append(row)
        candidates.sort(key=lambda row: row['id'])
        previous = candidates[0] if candidates else None
        if message.get('recalled_server_id'):
            # Some historical updates replace the original row in place. Save
            # evidence before writing that replacement, not only afterwards.
            chat_recalls.collect(db, account, message, previous['id'] if previous else None)
        merged = dict(json.loads(previous['data'])) if previous else {}
        # A historical read may race a newer Hook update. Its resource metadata
        # can enrich that update, but must not restore a recalled/edited body.
        stale_history = message.get('source') == 'history' and (
            (merged.get('source') == 'realtime' and message.get('_history_read_at', 0) <= merged.get('_live_seen_at', 0)) or
            message.get('_history_read_at', 0) < merged.get('_recent_read_at', 0))
        if stale_history:
            for key in ('_media', 'decode_error', 'sort_seq'):
                if key in message:
                    merged[key] = message[key]
        else:
            if message.get('_presentation_version'):
                from .chat_presentation_repair import FIELDS
                if message['_presentation_version'] >= merged.get('_presentation_version', 0):
                    if merged.get('category') != message.get('category'):
                        merged.pop('_media', None)
                    for key in FIELDS - message.keys():
                        merged.pop(key, None)
                    merged.update(message)
                else:
                    merged.update({k:v for k,v in message.items() if k not in FIELDS})
            elif merged.get('_presentation_version') and not message.get('_recalled'):
                from .chat_presentation_repair import FIELDS
                merged.update({k:v for k,v in message.items() if k not in FIELDS})
            else:
                merged.update(message)
        recall = chat_recalls.capture_original(db, account, message)
        if recall:
            merged.update(category='system', content=recall['notice'], summary=recall['notice'], _recalled=True)
        merged.pop('_history_read_at', None)
        if previous and json.loads(previous['data']).get('_recalled') and not message.get('_recalled'):
            old = json.loads(previous['data'])
            merged.update(category='system', content=old['content'], summary=old['content'], _recalled=True)
        if server != '0':
            merged['server_id'] = server
        elif previous:
            merged['server_id'] = json.loads(previous['data']).get('server_id', '0')
        if previous and '_recent_read_at' in merged:
            old_data = json.loads(previous['data'])
            # Identical tail reads retain their original freshness marker and
            # do not issue a revision for a snapshot timestamp alone.
            comparable = {k:v for k,v in merged.items() if k != '_recent_read_at'}
            if comparable == {k:v for k,v in old_data.items() if k != '_recent_read_at'}:
                if '_recent_read_at' in old_data:
                    merged['_recent_read_at'] = old_data['_recent_read_at']
                else:
                    merged.pop('_recent_read_at', None)
        encoded = dumps(merged)
        if previous:
            ident = previous['id']
            changed = previous['data'] != encoded
            if changed:
                db.execute('UPDATE chat_messages SET data=? WHERE id=?', (encoded, ident))
        else:
            ident = db.execute('INSERT INTO chat_messages(account,session,timestamp,data) VALUES (?,?,?,?)', (account, session, message['create_time'], encoded)).lastrowid
            changed = True
        for other in candidates[1:]:
            db.execute('UPDATE chat_aliases SET message_id=? WHERE message_id=?', (ident, other['id']))
            db.execute('DELETE FROM chat_messages WHERE id=?', (other['id'],))
            db.execute('INSERT INTO chat_changes(account,session,message_id,deleted) VALUES (?,?,?,1)', (account, session, other['id']))
        for alias in aliases:
            db.execute('INSERT OR IGNORE INTO chat_aliases VALUES (?,?,?,?)', (account, session, alias, ident))
        if changed:
            revision = db.execute('INSERT INTO chat_changes(account,session,message_id) VALUES (?,?,?)', (account, session, ident)).lastrowid
            db.execute('UPDATE chat_messages SET revision=? WHERE id=?', (revision, ident))
        recalled = message.get('recalled_server_id')
        if recalled:
            chat_recalls.collect(db, account, message, ident)
            original = db.execute('SELECT m.* FROM chat_messages m JOIN chat_aliases a ON a.message_id=m.id WHERE a.account=? AND a.session=? AND a.alias=?', (account, session, 'server:' + server_identity(recalled))).fetchone()
            if original and original['id'] != ident:
                body = json.loads(original['data'])
                if not body.get('_recalled'):
                    body.update(category='system', content=message['content'] or '消息已撤回', summary=message['content'] or '消息已撤回', _recalled=True)
                    rev = db.execute('INSERT INTO chat_changes(account,session,message_id) VALUES (?,?,?)', (account, session, original['id'])).lastrowid
                    db.execute('UPDATE chat_messages SET data=?,revision=? WHERE id=?', (dumps(body),rev,original['id']))
        return ident, changed

    def revision(self, account):
        with self.db() as db:
            return db.execute('SELECT coalesce(max(revision),0) FROM chat_changes WHERE account=?', (account,)).fetchone()[0]

    def repair_sender(self, db, account, message, event_id):
        """Correct old envelope attribution without replaying its body over edits."""
        sender = message.get('sender_username')
        if not sender or sender.endswith('@chatroom'):
            return False
        row = db.execute('''SELECT m.* FROM chat_aliases a JOIN chat_messages m ON m.id=a.message_id
            WHERE a.account=? AND a.session=? AND a.alias=?''',
            (account, message['session_username'], 'event:' + event_id)).fetchone()
        if not row:
            return False
        body = json.loads(row['data'])
        if body.get('sender_username') == sender and bool(body.get('is_self')) == bool(message.get('is_self')):
            return False
        body.update(sender_username=sender, is_self=message['is_self'])
        revision = db.execute('INSERT INTO chat_changes(account,session,message_id) VALUES (?,?,?)',
            (account, message['session_username'], row['id'])).lastrowid
        db.execute('UPDATE chat_messages SET data=?,revision=? WHERE id=?', (dumps(body), revision, row['id']))
        return True

    @staticmethod
    def cursor(account, session, timestamp, ident, ceiling):
        return base64.urlsafe_b64encode(dumps([1, account, session, timestamp, ident, ceiling]).encode()).decode().rstrip('=')

    @staticmethod
    def decode_cursor(cursor, account, session):
        try:
            values = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
            if len(values) != 6 or values[:3] != [1, account, session] or any(type(v) is not int or v < 0 for v in values[3:]):
                raise ValueError()
            return values[3:]
        except (ValueError, TypeError, KeyError):
            raise HTTPException(422, '历史游标无效或不属于当前账号和会话') from None

    def page(self, account, session, limit=60, before=None, around=None, around_id=None):
        with self.db() as db:
            db.execute('BEGIN')
            revision = db.execute('SELECT coalesce(max(revision),0) FROM chat_changes WHERE account=?', (account,)).fetchone()[0]
            count, ceiling = self.session_totals(db, account, session)
            params = [account, session]
            condition = ''
            if before:
                timestamp, ident, ceiling = self.decode_cursor(before, account, session)
                condition = ' AND (timestamp,id)<(?,?) AND id<=?'
                params.extend([timestamp, ident, ceiling])
            if around or around_id:
                target = (db.execute('SELECT m.* FROM chat_messages m JOIN chat_aliases a ON a.message_id=m.id WHERE a.account=? AND a.session=? AND a.alias=?', (account, session, 'server:' + around)).fetchone()
                          if around else db.execute('SELECT * FROM chat_messages WHERE account=? AND session=? AND id=?', (account, session, around_id)).fetchone())
                if not target:
                    raise HTTPException(404, '原消息尚未在当前账号历史中找到')
                newer = db.execute('SELECT timestamp,id FROM chat_messages WHERE account=? AND session=? AND (timestamp,id)>=(?,?) ORDER BY timestamp,id LIMIT ?', (account, session, target['timestamp'], target['id'], max(1, limit // 2))).fetchall()
                edge = newer[-1]
                condition = ' AND (timestamp,id)<=(?,?)'
                params.extend([edge['timestamp'], edge['id']])
            rows = db.execute('SELECT * FROM chat_messages WHERE account=? AND session=?' + condition + ' ORDER BY timestamp DESC,id DESC LIMIT ?', (*params, limit + 1)).fetchall()
            has_more = len(rows) > limit
            rows = rows[:limit]
            result = [self.unpack(row) for row in reversed(rows)]
            cursor = self.cursor(account, session, rows[-1]['timestamp'], rows[-1]['id'], ceiling) if rows and has_more else None
            return {'messages': result, 'next_cursor': cursor, 'has_more': has_more, 'revision': revision, 'total': count}

    @staticmethod
    def unpack(row):
        return {**json.loads(row['data']), 'id': str(row['id']), 'order_time': row['timestamp'], 'revision': row['revision']}

    def changes(self, account, session, after, limit=200):
        with self.db() as db:
            db.execute('BEGIN')
            ceiling = db.execute('SELECT coalesce(max(revision),0) FROM chat_changes WHERE account=?', (account,)).fetchone()[0]
            rows = db.execute('SELECT * FROM chat_changes WHERE account=? AND session=? AND revision>? ORDER BY revision LIMIT ?', (account, session, after, limit + 1)).fetchall()
            more = len(rows) > limit
            rows = rows[:limit]
            latest = {r['message_id']: r for r in rows}
            messages, deleted = [], []
            for ident, change in latest.items():
                row = db.execute('SELECT * FROM chat_messages WHERE id=? AND account=? AND session=?', (ident, account, session)).fetchone()
                if row:
                    messages.append(self.unpack(row))
                else:
                    deleted.append(str(ident))
            return {'messages': messages, 'deleted': deleted,
                    'revision': rows[-1]['revision'] if more else max(after, ceiling), 'has_more': more}

    def changed_sessions(self, account, after, limit=200):
        with self.db() as db:
            db.execute('BEGIN')
            revision = db.execute('SELECT coalesce(max(revision),0) FROM chat_changes WHERE account=?', (account,)).fetchone()[0]
            rows = db.execute('SELECT DISTINCT session FROM chat_changes WHERE account=? AND revision>? LIMIT ?', (account, after, limit + 1)).fetchall()
            return {'cursor': revision, 'sessions': [row[0] for row in rows] if len(rows) <= limit else None}

    def state(self, account):
        with self.db() as db:
            row = db.execute('SELECT data FROM chat_history_state WHERE account=?', (account,)).fetchone()
        return json.loads(row[0]) if row else {}

    def boundary(self, account, session, ident):
        with self.db() as db:
            row = db.execute('SELECT timestamp,id FROM chat_messages WHERE account=? AND session=? AND id=?', (account,session,ident)).fetchone()
            if not row:
                return None
            older = db.execute('SELECT 1 FROM chat_messages WHERE account=? AND session=? AND (timestamp,id)<(?,?) LIMIT 1', (account,session,row['timestamp'],row['id'])).fetchone()
            _, ceiling = self.session_totals(db, account, session)
            return self.cursor(account,session,row['timestamp'],row['id'],ceiling) if older else None
