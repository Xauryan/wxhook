from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from fastapi import HTTPException


class ReplyStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'replies.sqlite'
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS reply_rules (
                    account TEXT NOT NULL,room TEXT NOT NULL,name TEXT NOT NULL,
                    revision INTEGER NOT NULL,config TEXT NOT NULL,enabled_after_seq INTEGER NOT NULL,
                    updated_at REAL NOT NULL,PRIMARY KEY(account,room));
                CREATE TABLE IF NOT EXISTS reply_batches (
                    id TEXT PRIMARY KEY,request_key TEXT UNIQUE NOT NULL,fingerprint TEXT NOT NULL,
                    account TEXT NOT NULL,room TEXT NOT NULL,name TEXT NOT NULL,
                    status TEXT NOT NULL,total INTEGER NOT NULL,created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS reply_runs (
                    id TEXT PRIMARY KEY,request_key TEXT UNIQUE NOT NULL,batch_id TEXT,
                    origin TEXT NOT NULL,lane TEXT NOT NULL,account TEXT NOT NULL,room TEXT NOT NULL,
                    message_id INTEGER NOT NULL,trigger_mode TEXT NOT NULL,search_mode TEXT NOT NULL,
                    rule_revision INTEGER NOT NULL,model_revision INTEGER NOT NULL,
                    config TEXT NOT NULL,context TEXT NOT NULL,context_hash TEXT NOT NULL,
                    trigger_at REAL NOT NULL,status TEXT NOT NULL,result TEXT,
                    delivery_status TEXT NOT NULL DEFAULT '',delivery_detail TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,finished_at REAL);
                CREATE INDEX IF NOT EXISTS reply_runs_queue ON reply_runs(status,origin,trigger_at);
                CREATE INDEX IF NOT EXISTS reply_runs_batch ON reply_runs(batch_id,trigger_at);
            ''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(reply_runs)')}
            if 'profile_revisions' not in columns:
                db.execute("ALTER TABLE reply_runs ADD COLUMN profile_revisions TEXT NOT NULL DEFAULT '{}'")

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
    def rule(row):
        return {**dict(row), 'config': json.loads(row['config'])} if row else None

    def rules(self, account):
        with self.db() as db:
            return [self.rule(row) for row in db.execute('SELECT * FROM reply_rules WHERE account=? ORDER BY name', (account,))]

    def get_rule(self, account, room):
        with self.db() as db:
            return self.rule(db.execute('SELECT * FROM reply_rules WHERE account=? AND room=?', (account, room)).fetchone())

    def save_rule(self, edit, name, sequence):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM reply_rules WHERE account=? AND room=?', (edit.account, edit.room)).fetchone()
            if edit.revision != (old['revision'] if old else 0):
                raise HTTPException(409, '群聊问答配置已变化，请刷新后保存')
            db.execute('''INSERT INTO reply_rules VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(account,room) DO UPDATE SET name=excluded.name,revision=excluded.revision,
                config=excluded.config,enabled_after_seq=excluded.enabled_after_seq,updated_at=excluded.updated_at''',
                (edit.account, edit.room, name, edit.revision + 1, edit.config.model_dump_json(), sequence, time.time()))
        return self.get_rule(edit.account, edit.room)

    def enqueue(self, db, *, key, rule, context, context_hash, model_revision, origin, batch_id=None, profile_revisions=None):
        current = context['current']
        from .models import ReplyConfig
        from .prompts import PROMPTS
        config = ReplyConfig.model_validate(rule['config']).model_dump()
        for name, default in PROMPTS.items():
            config[name] = config[name] or default
        mode = config['search_mode']
        trigger = 'ask' if current['text'].strip().startswith('/ask') else 'original'
        lane = f'{batch_id}:{mode}:{trigger}' if batch_id else f'live:{rule["account"]}:{rule["room"]}'
        from datetime import datetime
        values = {'id': uuid4().hex, 'request_key': key, 'batch_id': batch_id, 'origin': origin,
                  'lane': lane, 'account': rule['account'], 'room': rule['room'],
                  'message_id': current['message_id'], 'trigger_mode': trigger, 'search_mode': mode,
                  'rule_revision': rule['revision'], 'model_revision': model_revision,
                  'config': json.dumps(config, ensure_ascii=False),
                  'context': json.dumps(context, ensure_ascii=False), 'context_hash': context_hash,
                  'trigger_at': datetime.fromisoformat(context['at']).timestamp(), 'created_at': time.time(),
                  'profile_revisions': json.dumps(profile_revisions or {})}
        db.execute('''INSERT OR IGNORE INTO reply_runs (
            id,request_key,batch_id,origin,lane,account,room,message_id,trigger_mode,search_mode,
            rule_revision,model_revision,config,context,context_hash,trigger_at,status,created_at,profile_revisions)
            VALUES (:id,:request_key,:batch_id,:origin,:lane,:account,:room,:message_id,:trigger_mode,:search_mode,
            :rule_revision,:model_revision,:config,:context,:context_hash,:trigger_at,'queued',:created_at,:profile_revisions)''', values)
        return db.execute('SELECT id FROM reply_runs WHERE request_key=?', (key,)).fetchone()[0]

    def create_batch(self, request_key, fingerprint, rule, cases):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT * FROM reply_batches WHERE request_key=?', (request_key,)).fetchone()
            if previous:
                if previous['fingerprint'] != fingerprint:
                    raise HTTPException(409, '同一个回放请求不能修改输入或检索方案')
                return previous['id']
            ident = uuid4().hex
            db.execute('INSERT INTO reply_batches VALUES (?,?,?,?,?,?,?,?,?)',
                       (ident, request_key, fingerprint, rule['account'], rule['room'], rule['name'],
                        'running', len(cases), time.time()))
            for case in cases:
                self.enqueue(db, key=f'{ident}:{case["rule"]["config"]["search_mode"]}:{case["context_hash"]}',
                             origin='replay', batch_id=ident, **case)
        return ident

    def claim(self):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('''SELECT r.* FROM reply_runs r WHERE r.status='queued'
                AND NOT EXISTS (SELECT 1 FROM reply_runs busy WHERE busy.lane=r.lane AND busy.status='running')
                ORDER BY (r.origin='live') DESC,r.trigger_at,r.created_at LIMIT 1''').fetchone()
            if row:
                db.execute("UPDATE reply_runs SET status='running' WHERE id=?", (row['id'],))
        return dict(row) if row else None

    def last_reply(self, run):
        with self.db() as db:
            row = db.execute('''SELECT max(trigger_at) FROM reply_runs WHERE lane=? AND trigger_at<?
                AND status='completed' AND json_extract(result,'$.should_reply')=1
                AND (origin='replay' OR delivery_status IN ('accepted','unknown'))''',
                (run['lane'], run['trigger_at'])).fetchone()
        return row[0] or 0

    def save_result(self, ident, result):
        with self.db() as db:
            db.execute('UPDATE reply_runs SET result=? WHERE id=?', (json.dumps(result, ensure_ascii=False), ident))

    def complete(self, ident):
        with self.db() as db:
            db.execute("UPDATE reply_runs SET status='completed',finished_at=? WHERE id=?", (time.time(), ident))
            db.execute('''UPDATE reply_batches SET status='completed' WHERE status='running'
                AND NOT EXISTS (SELECT 1 FROM reply_runs WHERE batch_id=reply_batches.id AND status IN ('queued','running'))''')

    def reserve_delivery(self, ident):
        with self.db() as db:
            return db.execute("UPDATE reply_runs SET delivery_status='sending' WHERE id=? AND origin='live' AND delivery_status=''", (ident,)).rowcount == 1

    def delivery(self, ident, status, detail):
        with self.db() as db:
            db.execute('UPDATE reply_runs SET delivery_status=?,delivery_detail=? WHERE id=?', (status, detail, ident))

    def recover(self):
        with self.db() as db:
            db.execute("UPDATE reply_runs SET delivery_status='unknown',delivery_detail='发送期间服务中断，请核对微信群',status='completed' WHERE delivery_status='sending'")
            db.execute("UPDATE reply_runs SET status='queued' WHERE status='running'")

    def run(self, ident):
        with self.db() as db:
            row = db.execute('SELECT * FROM reply_runs WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise HTTPException(404, '问答记录不存在')
        value = dict(row)
        for name in ('config', 'context', 'result', 'profile_revisions'):
            value[name] = json.loads(value[name]) if value[name] else None
        return value

    def batches(self, account):
        with self.db() as db:
            return [dict(row) for row in db.execute('''SELECT b.*,
                (SELECT count(*) FROM reply_runs r WHERE r.batch_id=b.id AND r.status IN ('completed','cancelled')) done,
                (SELECT count(*) FROM reply_runs r WHERE r.batch_id=b.id AND json_extract(r.result,'$.should_reply')=1) replies
                FROM reply_batches b WHERE account=? ORDER BY created_at DESC LIMIT 20''', (account,))]

    def batch(self, ident):
        with self.db() as db:
            row = db.execute('SELECT * FROM reply_batches WHERE id=?', (ident,)).fetchone()
            if row is None:
                raise HTTPException(404, '回放任务不存在')
            runs = [dict(item) for item in db.execute('''SELECT id,message_id,trigger_mode,search_mode,status,
                context_hash,trigger_at,delivery_status,json_extract(context,'$.current.text') input,
                json_extract(result,'$.should_reply') should_reply,json_extract(result,'$.answer') answer,
                json_extract(result,'$.reason') reason,json_extract(result,'$.detail') detail,
                json_extract(result,'$.elapsed_ms') elapsed_ms,json_extract(result,'$.confidence') confidence
                FROM reply_runs WHERE batch_id=? ORDER BY trigger_at,message_id,trigger_mode,search_mode''', (ident,))]
        return {**dict(row), 'runs': runs}

    def recent(self, account):
        with self.db() as db:
            return [dict(row) for row in db.execute('''SELECT id,room,message_id,status,delivery_status,
                json_extract(context,'$.current.text') input,json_extract(result,'$.answer') answer,
                json_extract(result,'$.reason') reason,created_at FROM reply_runs
                WHERE account=? AND origin='live' ORDER BY created_at DESC LIMIT 30''', (account,))]

    def cancel(self, ident):
        with self.db() as db:
            db.execute("UPDATE reply_batches SET status='cancelled' WHERE id=?", (ident,))
            db.execute("UPDATE reply_runs SET status='cancelled' WHERE batch_id=? AND status='queued'", (ident,))
