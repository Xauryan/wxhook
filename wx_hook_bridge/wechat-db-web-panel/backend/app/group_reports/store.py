import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException


class ReportStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'reports.sqlite'
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS report_rules (
                    account TEXT NOT NULL, room TEXT NOT NULL, name TEXT NOT NULL,
                    revision INTEGER NOT NULL, config TEXT NOT NULL, next_run REAL NOT NULL,
                    updated_at REAL NOT NULL, PRIMARY KEY(account,room));
                CREATE TABLE IF NOT EXISTS report_jobs (
                    id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE,
                    account TEXT NOT NULL, room TEXT NOT NULL, name TEXT NOT NULL,
                    start REAL NOT NULL, end REAL NOT NULL, trigger TEXT NOT NULL,
                    rule_revision INTEGER NOT NULL, config TEXT NOT NULL,
                    auto_send INTEGER NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS report_deliveries (
                    job_id TEXT NOT NULL, part INTEGER NOT NULL, status TEXT NOT NULL,
                    detail TEXT NOT NULL, PRIMARY KEY(job_id,part));
                CREATE INDEX IF NOT EXISTS report_jobs_account ON report_jobs(account,created_at);
            ''')

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
            return [self.rule(row) for row in db.execute('SELECT * FROM report_rules WHERE account=? ORDER BY name', (account,))]

    def get_rule(self, account, room):
        with self.db() as db:
            return self.rule(db.execute('SELECT * FROM report_rules WHERE account=? AND room=?', (account, room)).fetchone())

    def save_rule(self, edit, name, scheduled_at):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT revision FROM report_rules WHERE account=? AND room=?', (edit.account, edit.room)).fetchone()
            if edit.revision != (previous[0] if previous else 0):
                raise HTTPException(409, '群日报配置已变化，请刷新后保存')
            db.execute('INSERT INTO report_rules VALUES (?,?,?,?,?,?,?) ON CONFLICT(account,room) DO UPDATE SET name=excluded.name,revision=excluded.revision,config=excluded.config,next_run=excluded.next_run,updated_at=excluded.updated_at',
                       (edit.account, edit.room, name, edit.revision + 1, edit.config.model_dump_json(), scheduled_at, time.time()))
        return self.get_rule(edit.account, edit.room)

    def enqueue(self, rule, start, end, trigger, request_key, send, *, next_at=None):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT * FROM report_jobs WHERE request_key=?', (request_key,)).fetchone()
            if previous:
                if (previous['account'], previous['room'], previous['start'], previous['end'], bool(previous['auto_send'])) != (rule['account'], rule['room'], start, end, send):
                    raise HTTPException(409, '同一生成请求不能修改群聊、时间或发送选项')
                return dict(previous)
            ident, now = uuid4().hex, time.time()
            db.execute('INSERT INTO report_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (ident, request_key, rule['account'], rule['room'], rule['name'], start, end, trigger,
                        rule['revision'], json.dumps(rule['config'], ensure_ascii=False), int(send),
                        'queued', '等待生成', now, now))
            if next_at is not None:
                db.execute('UPDATE report_rules SET next_run=? WHERE account=? AND room=? AND revision=?',
                           (next_at, rule['account'], rule['room'], rule['revision']))
            return dict(db.execute('SELECT * FROM report_jobs WHERE id=?', (ident,)).fetchone())

    def jobs(self, account):
        with self.db() as db:
            return [dict(row) for row in db.execute('SELECT id,account,room,name,start,end,trigger,auto_send,status,detail,created_at,updated_at FROM report_jobs WHERE account=? ORDER BY created_at DESC LIMIT 50', (account,))]

    def job(self, ident):
        with self.db() as db:
            row = db.execute('SELECT * FROM report_jobs WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise HTTPException(404, '报告不存在')
        return dict(row)

    def update(self, ident, status, detail):
        with self.db() as db:
            db.execute('UPDATE report_jobs SET status=?,detail=?,updated_at=? WHERE id=?', (status, detail, time.time(), ident))

    def recover(self):
        with self.db() as db:
            db.execute("UPDATE report_jobs SET status='queued',detail='服务恢复，等待重新生成' WHERE status IN ('preparing','analyzing','rendering')")
            db.execute("UPDATE report_jobs SET status='unknown',detail='发送期间服务中断，请先在微信核对' WHERE status='sending'")
            db.execute("UPDATE report_deliveries SET status='unknown',detail='发送期间服务中断' WHERE status='sending'")

    def reserve_delivery(self, job_id, part):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status FROM report_deliveries WHERE job_id=? AND part=?', (job_id, part)).fetchone()
            if row:
                return row[0]
            db.execute('INSERT INTO report_deliveries VALUES (?,?,?,?)', (job_id, part, 'sending', '提交微信中'))
            return 'new'

    def delivery_result(self, job_id, part, status, detail):
        with self.db() as db:
            db.execute('UPDATE report_deliveries SET status=?,detail=? WHERE job_id=? AND part=?', (status, detail, job_id, part))
