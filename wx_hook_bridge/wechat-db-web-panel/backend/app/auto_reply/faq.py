"""Verified answers kept per group, so a repeated question is answered without a new search."""
from contextlib import contextmanager
import json
import re
import sqlite3
import time
from uuid import uuid4

import jieba

from ..retrieval.service import STOP_WORDS

MATCH_THRESHOLD = .85
# Time-sensitive facts (events, prices, schedules) go stale quickly.
TIME_SENSITIVE_TTL = 86400


def terms(question):
    text = re.sub(r'[\W_]+', ' ', question.lower())
    return {word.strip() for word in jieba.lcut(text) if word.strip() and word.strip() not in STOP_WORDS}


def similarity(left, right):
    if not left or not right:
        return 0
    return len(left & right) / len(left | right)


class FAQStore:
    def __init__(self, path):
        self.path = path
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS reply_faq (
                id TEXT PRIMARY KEY, account TEXT NOT NULL, room TEXT NOT NULL, question TEXT NOT NULL,
                terms TEXT NOT NULL, domain TEXT NOT NULL, time_sensitive INTEGER NOT NULL,
                answer TEXT NOT NULL, sources TEXT NOT NULL, run_id TEXT NOT NULL,
                created_at REAL NOT NULL, hits INTEGER NOT NULL DEFAULT 0, last_hit_at REAL)''')
            db.execute('CREATE INDEX IF NOT EXISTS reply_faq_room ON reply_faq(account,room,created_at)')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def public(row):
        return {'id': row['id'], 'question': row['question'], 'answer': row['answer'],
                'sources': json.loads(row['sources']), 'domain': row['domain'],
                'time_sensitive': bool(row['time_sensitive']), 'created_at': row['created_at'],
                'hits': row['hits'], 'last_hit_at': row['last_hit_at']}

    def find(self, account, room, question, domain, time_sensitive, ttl_days, now=None):
        now = now or time.time()
        wanted = terms(question)
        best, best_score = None, 0
        with self.db() as db:
            rows = db.execute('SELECT * FROM reply_faq WHERE account=? AND room=? AND domain=? AND created_at>? '
                              'ORDER BY created_at DESC LIMIT 500', (account, room, domain, now - ttl_days * 86400)).fetchall()
            for row in rows:
                if (row['time_sensitive'] or time_sensitive) and row['created_at'] < now - TIME_SENSITIVE_TTL:
                    continue
                score = 1 if row['question'].strip() == question.strip() else similarity(wanted, set(json.loads(row['terms'])))
                if score > best_score:
                    best, best_score = row, score
            if best is None or best_score < MATCH_THRESHOLD:
                return None
            db.execute('UPDATE reply_faq SET hits=hits+1,last_hit_at=? WHERE id=?', (now, best['id']))
        return self.public(best)

    def add(self, account, room, run_id, plan, body, sources):
        with self.db() as db:
            if db.execute('SELECT 1 FROM reply_faq WHERE run_id=?', (run_id,)).fetchone():
                return
            db.execute('INSERT INTO reply_faq(id,account,room,question,terms,domain,time_sensitive,answer,sources,run_id,created_at) '
                       'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                       (uuid4().hex, account, room, plan['question'], json.dumps(sorted(terms(plan['question'])), ensure_ascii=False),
                        plan['domain'], int(plan.get('time_sensitive', False)), body,
                        json.dumps(sources, ensure_ascii=False), run_id, time.time()))

    def list(self, account, room):
        with self.db() as db:
            return [self.public(row) for row in db.execute(
                'SELECT * FROM reply_faq WHERE account=? AND room=? ORDER BY created_at DESC LIMIT 200', (account, room))]

    def delete(self, account, ident):
        with self.db() as db:
            db.execute('DELETE FROM reply_faq WHERE account=? AND id=?', (account, ident))
        return {'ok': True}
