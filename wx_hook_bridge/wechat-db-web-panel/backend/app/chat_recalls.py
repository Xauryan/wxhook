"""Account-scoped recall evidence, independent of the mutable chat display."""
from __future__ import annotations

import json
import time


def initialize(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS chat_recalls (
            id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,
            session TEXT NOT NULL, server_id TEXT NOT NULL,
            recalled_at INTEGER, observed_at REAL NOT NULL,
            notice TEXT NOT NULL, notice_message_id INTEGER,
            original TEXT, revision INTEGER NOT NULL DEFAULT 0,
            UNIQUE(account,session,server_id)
        );
        CREATE INDEX IF NOT EXISTS chat_recalls_page ON chat_recalls(account,id);
        CREATE TABLE IF NOT EXISTS chat_recall_changes (
            revision INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS chat_recall_revision ON chat_recall_changes(account,revision);
    ''')


def identity(value):
    try:
        number = int(value or 0)
    except (ValueError, TypeError):
        return ''
    return str(number + (1 << 64) if number < 0 else number) if number else ''


def changed(db, account, ident):
    revision = db.execute('INSERT INTO chat_recall_changes(account) VALUES (?)', (account,)).lastrowid
    db.execute('UPDATE chat_recalls SET revision=? WHERE id=?', (revision, ident))


def capture_original(db, account, message):
    """Capture once, including originals delivered after the recall notice."""
    if message.get('_recalled') or message.get('recalled_server_id') or message.get('category') == 'system':
        return None
    server = identity(message.get('server_id'))
    if not server:
        return None
    row = db.execute('SELECT * FROM chat_recalls WHERE account=? AND session=? AND server_id=?',
                     (account, message['session_username'], server)).fetchone()
    if row and row['original'] is None:
        # Internal media/decryption hints and arrival times are not part of the
        # retained presentation. Raw envelopes remain in their existing ledger.
        snapshot = {k: v for k, v in message.items() if not k.startswith('_') and k not in {'media_hints', 'source_shard'}}
        db.execute('UPDATE chat_recalls SET original=? WHERE id=?',
                   (json.dumps(snapshot, ensure_ascii=False), row['id']))
        changed(db, account, row['id'])
    return row


def collect(db, account, message, notice_message_id=None):
    server = identity(message.get('recalled_server_id'))
    if not server:
        return capture_original(db, account, message)
    session = message['session_username']
    recalled_at = message.get('_recall_time') or (None if message.get('_recall_time_unknown') else message.get('create_time')) or None
    notice = message.get('content') or '消息已撤回'
    inserted = db.execute('''INSERT OR IGNORE INTO chat_recalls
        (account,session,server_id,recalled_at,observed_at,notice,notice_message_id)
        VALUES (?,?,?,?,?,?,?)''',
        (account, session, server, recalled_at, time.time(), notice, notice_message_id)).rowcount
    row = db.execute('SELECT * FROM chat_recalls WHERE account=? AND session=? AND server_id=?',
                     (account, session, server)).fetchone()
    # A second hook may supply the timestamp that the first hook lacked.
    updated = False
    if row['recalled_at'] is None and recalled_at:
        db.execute('UPDATE chat_recalls SET recalled_at=? WHERE id=?', (recalled_at, row['id']))
        updated = True
    if row['notice_message_id'] is None and notice_message_id:
        db.execute('UPDATE chat_recalls SET notice_message_id=? WHERE id=?', (notice_message_id, row['id']))
        updated = True
    if inserted or updated:
        changed(db, account, row['id'])
    original = db.execute('''SELECT m.data FROM chat_aliases a JOIN chat_messages m ON m.id=a.message_id
        WHERE a.account=? AND a.session=? AND a.alias=?''', (account, session, 'server:' + server)).fetchone()
    if original:
        capture_original(db, account, json.loads(original[0]))
    return row


def revision(db, account):
    return db.execute('SELECT coalesce(max(revision),0) FROM chat_recall_changes WHERE account=?', (account,)).fetchone()[0]


def page(db, account, q='', before=None, limit=50):
    joins = ''' FROM chat_recalls r
        LEFT JOIN live_roster c ON c.account=r.account AND c.username=r.session
        LEFT JOIN live_names n ON n.account=r.account AND n.username=r.session
        LEFT JOIN live_names s ON s.account=r.account AND s.username=json_extract(r.original,'$.sender_username')'''
    where = ' WHERE r.account=?'
    params = [account]
    if q:
        where += """ AND instr(lower(coalesce(c.name,n.name,'')||' '||r.session||' '||coalesce(s.name,'')||' '||
            coalesce(json_extract(r.original,'$.sender_username'),'')||' '||
            coalesce(json_extract(r.original,'$.content'),'')||' '||
            coalesce(json_extract(r.original,'$.title'),'')||' '||
            coalesce(json_extract(r.original,'$.summary'),'')||' '||
            coalesce(json_extract(r.original,'$.file_name'),'')||' '||r.server_id||' '||r.notice),lower(?))>0"""
        params.append(q)
    total = db.execute('SELECT count(*)' + joins + where, params).fetchone()[0]
    if before:
        where += ' AND r.id<?'
        params.append(before)
    rows = db.execute('SELECT r.*,n.name session_name,s.name sender_name' + joins + where + ' ORDER BY r.id DESC LIMIT ?',
                      (*params, limit + 1)).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    items = []
    for row in rows:
        original = json.loads(row['original']) if row['original'] else None
        target = db.execute('''SELECT m.id FROM chat_aliases a JOIN chat_messages m ON m.id=a.message_id
            WHERE a.account=? AND a.session=? AND a.alias=?''', (account, row['session'], 'server:' + row['server_id'])).fetchone()
        contact = db.execute('SELECT name FROM live_roster WHERE account=? AND username=?', (account, row['session'])).fetchone()
        # An uncaptured original can still be located if history contains its
        # recalled placeholder. Otherwise locate the exact notice, not latest.
        notice = db.execute('SELECT id FROM chat_messages WHERE account=? AND session=? AND id=?',
                            (account, row['session'], row['notice_message_id'])).fetchone()
        items.append({'id': str(row['id']), 'server_id': row['server_id'],
            'recalled_at': row['recalled_at'], 'observed_at': row['observed_at'],
            'notice': row['notice'], 'original': original,
            'sender_name': row['sender_name'] or (original or {}).get('sender_username') or '未知发送人',
            'session': {'username': row['session'], 'display_name': contact[0] if contact else row['session_name'] or row['session'],
                        'is_group': row['session'].endswith('@chatroom'), 'message_count': 0, 'can_send': bool(contact)},
            'jump_server_id': row['server_id'] if target else None,
            'jump_message_id': str(notice[0]) if notice and not target else None})
    return {'account': account, 'items': items, 'total': total,
            'next_cursor': str(rows[-1]['id']) if more else None, 'revision': revision(db, account)}
