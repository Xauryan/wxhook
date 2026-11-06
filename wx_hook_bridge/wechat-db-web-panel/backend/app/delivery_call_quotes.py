"""Resumable text -> native quoted statistics image, with no resend on uncertainty."""
import asyncio
import hashlib
import json
from datetime import timedelta
from pathlib import Path

from .delivery_image_quotes import ImageQuoteRejected, QuoteSourceUnavailable, quote_payload
from .notification_text import timestamp
from .delivery_mentions import MentionSelectionError


class DeliveryCallQuotes:
    def __init__(self, store, bridge, source, clock, stop_event):
        self.store, self.bridge, self.source = store, bridge, source
        self.now, self.stop_event = clock, stop_event
        self.settle_delay = 2.5
        self.mentions = None

    async def account(self):
        profile = await self.bridge._post('/api/userInfo', {})
        if not isinstance(profile, dict) or not profile.get('wxid'):
            raise QuoteSourceUnavailable('无法确认当前微信账号')
        return profile['wxid']

    def current(self, row):
        with self.store.connect() as db:
            main = db.execute('SELECT * FROM deliveries WHERE event_key=? AND room_id=?', (row['event_key'], row['room_id'])).fetchone()
            quote = db.execute('SELECT * FROM call_quote_receipts WHERE event_key=? AND room_id=?', (row['event_key'], row['room_id'])).fetchone()
        return (dict(main) if main else None, dict(quote) if quote else None)

    def allowed(self, row):
        config = self.store.configuration()
        return (config['enabled'] and row['room_id'] in {g['room_id'] for g in config['target_groups']}
                and timestamp(config.get('activated_at')) and timestamp(row['created_at'])
                and timestamp(row['created_at']) >= timestamp(config['activated_at']))

    def defer(self, row, state, error):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            changed = db.execute("UPDATE deliveries SET status='quote_waiting',error=? WHERE event_key=? AND room_id=? AND status IN ('queued','render_failed','quote_waiting')",
                                 (error, row['event_key'], row['room_id'])).rowcount
            if changed:
                db.execute('UPDATE call_quote_receipts SET status=?,error=?,next_check_at=?,updated_at=? WHERE event_key=? AND room_id=?',
                           (state, error, (self.now()+timedelta(seconds=30)).isoformat(), self.now().isoformat(), row['event_key'], row['room_id']))

    async def verify(self, row, quote):
        if self.source is None or not quote.get('reference_json') or not quote.get('submitted_at'):
            return
        try:
            proof = await asyncio.to_thread(self.source.verify, json.loads(quote['reference_json']), quote['submitted_at'])
        except Exception:
            proof = None
        with self.store.connect() as db:
            db.execute("UPDATE call_quote_receipts SET status=?,evidence_json=?,next_check_at=?,updated_at=? WHERE event_key=? AND room_id=? AND status='awaiting_evidence'",
                       ('local_verified' if proof else 'awaiting_evidence', json.dumps(proof, ensure_ascii=False) if proof else None,
                        (self.now()+timedelta(seconds=30)).isoformat(), self.now().isoformat(), row['event_key'], row['room_id']))

    async def send(self, row, image_path):
        row, quote = self.current(row)
        if not row or row['media_type'] != 'call_bundle' or row['status'] not in {'queued', 'render_failed', 'quote_waiting'}:
            return
        if quote and quote['status'] not in {'waiting_source', 'waiting_reference', 'waiting_mentions', 'image_unavailable'}:
            return
        if not self.allowed(row):
            with self.store.connect() as db:
                db.execute("UPDATE deliveries SET status='cancelled',error='通话通知已关闭或目标群/启用时间已变化' WHERE event_key=? AND room_id=? AND status IN ('queued','render_failed','quote_waiting')",
                           (row['event_key'], row['room_id']))
            return
        if self.stop_event.is_set():
            return
        if not quote and row['text_status'] is not None:
            self.store.mark(row, 'uncertain', '旧文字已有提交记录，禁止补发或重新提交图文')
            return
        path = Path(image_path).resolve()
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            if quote:
                self.defer(row, 'image_unavailable', '冻结的统计图片暂不可用，未发送')
            return
        if quote and (quote['image_sha256'] != digest or quote['image_path'] != str(path)):
            self.defer(row, 'image_unavailable', '冻结的统计图片发生变化，暂缓发送')
            return
        with self.store.connect() as db:
            db.execute('''INSERT OR IGNORE INTO call_quote_receipts
                (event_key,room_id,status,image_path,image_sha256,next_check_at,updated_at)
                VALUES(?,?,'waiting_source',?,?,?,?)''',
                (row['event_key'], row['room_id'], str(path), digest, self.now().isoformat(), self.now().isoformat()))
        try:
            if self.source is None:
                raise QuoteSourceUnavailable('引用消息源未配置')
            account = await self.account()
            if quote and quote.get('account') and quote['account'] != account:
                raise QuoteSourceUnavailable('微信发送账号已变化')
            await asyncio.to_thread(self.source.preflight, account)
        except Exception:
            self.defer(row, 'waiting_source', '当前微信账号的引用消息源暂不可用，未发送图片或重复文字')
            return
        if self.stop_event.is_set() or not self.allowed(row):
            return
        if row['text_status'] is None:
            mention = None
            if self.mentions:
                try:
                    report = self.store.call_report(row['event_key']) or {}
                    mention = await self.mentions.prepare(row['room_id'], 'call', report.get('call'), row['message'])
                except Exception as exc:
                    self.defer(row, 'waiting_mentions', str(exc) if isinstance(exc, MentionSelectionError) else '@ 群成员查询暂不可用，文字未发送')
                    return
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                claimed = db.execute("UPDATE deliveries SET status='sending',text_status='sending',error=NULL WHERE event_key=? AND room_id=? AND status IN ('queued','render_failed','quote_waiting') AND text_status IS NULL",
                                     (row['event_key'], row['room_id'])).rowcount
                if not claimed:
                    return
                if mention:
                    self.mentions.freeze(db, row['event_key'], row['room_id'], mention)
                    db.execute('UPDATE deliveries SET message=? WHERE event_key=? AND room_id=?',
                               (mention['message'], row['event_key'], row['room_id']))
                db.execute("UPDATE call_quote_receipts SET status='text_sending',account=?,updated_at=? WHERE event_key=? AND room_id=?",
                           (account, self.now().isoformat(), row['event_key'], row['room_id']))
            try:
                if mention and mention['members']:
                    await self.mentions.send(row['room_id'], mention)
                else:
                    await self.bridge.send_text(row['room_id'], row['message'])
            except Exception:
                self.store.mark(row, 'uncertain', '文字发送结果不明，不发送引用图片，不自动重发')
                with self.store.connect() as db:
                    db.execute("UPDATE call_quote_receipts SET status='uncertain',error='文字发送结果不明' WHERE event_key=? AND room_id=?", (row['event_key'], row['room_id']))
                return
            with self.store.connect() as db:
                db.execute("UPDATE deliveries SET status='quote_waiting',text_status='sent',text_sent_at=? WHERE event_key=? AND room_id=? AND status='sending'",
                           (self.now().isoformat(), row['event_key'], row['room_id']))
                db.execute("UPDATE call_quote_receipts SET status='waiting_reference',updated_at=? WHERE event_key=? AND room_id=?",
                           (self.now().isoformat(), row['event_key'], row['room_id']))
            if self.settle_delay:
                await asyncio.sleep(self.settle_delay)
        row, quote = self.current(row)
        if row['status'] != 'quote_waiting' or row['text_status'] != 'sent' or self.stop_event.is_set():
            return
        try:
            reference = await asyncio.to_thread(self.source.resolve, account, row['room_id'],
                {'status': 'sent', 'sent_at': row['text_sent_at'], 'message': row['message']})
            payload = quote_payload(row['room_id'], path, reference)
        except Exception:
            self.defer(row, 'waiting_reference', '等待本条通话文字的真实消息编号；文字已提交，仅暂缓引用图片')
            return
        if self.stop_event.is_set() or not self.allowed(row):
            return
        try:
            unchanged = hashlib.sha256(path.read_bytes()).hexdigest() == quote['image_sha256']
        except OSError:
            unchanged = False
        if not unchanged:
            self.defer(row, 'image_unavailable', '冻结的统计图片发生变化，暂缓发送')
            return
        submitted = self.now().isoformat()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            claimed = db.execute("UPDATE deliveries SET status='sending',error=NULL WHERE event_key=? AND room_id=? AND status='quote_waiting' AND text_status='sent'",
                                 (row['event_key'], row['room_id'])).rowcount
            if not claimed:
                return
            db.execute("UPDATE call_quote_receipts SET status='image_sending',reference_json=?,submitted_at=?,error=NULL,updated_at=? WHERE event_key=? AND room_id=?",
                       (json.dumps(reference, ensure_ascii=False), submitted, submitted, row['event_key'], row['room_id']))
        try:
            result = await self.bridge._post('/api/sendImageQuoteMsg', payload)
            if not isinstance(result, dict) or result.get('accepted') != 1 or result.get('stage') != 'bridge-accepted':
                raise ValueError('Unexpected quote receipt')
            status, state, error = 'sent', 'awaiting_evidence', None
        except ImageQuoteRejected:
            status, state, error = 'rejected', 'rejected', '引用图片被明确拒绝，不降级、不自动重试'
        except Exception:
            status, state, error = 'uncertain', 'uncertain', '引用图片结果不明，不降级、不自动重发'
        with self.store.connect() as db:
            db.execute("UPDATE deliveries SET status=?,error=?,sent_at=? WHERE event_key=? AND room_id=? AND status='sending'",
                       (status, error, self.now().isoformat() if status == 'sent' else None, row['event_key'], row['room_id']))
            db.execute('UPDATE call_quote_receipts SET status=?,error=?,next_check_at=?,updated_at=? WHERE event_key=? AND room_id=?',
                       (state, error, self.now().isoformat(), self.now().isoformat(), row['event_key'], row['room_id']))
        if status == 'sent':
            _, quote = self.current(row)
            await self.verify(row, quote)

    async def resume(self):
        with self.store.connect() as db:
            rows = [dict(r) for r in db.execute('''SELECT d.* FROM deliveries d JOIN call_quote_receipts q
                ON q.event_key=d.event_key AND q.room_id=d.room_id WHERE q.next_check_at<=? AND
                (d.status='quote_waiting' OR (d.status='sent' AND q.status='awaiting_evidence'))
                ORDER BY q.next_check_at,q.updated_at LIMIT 10''', (self.now().isoformat(),))]
        for row in rows:
            if self.stop_event.is_set():
                break
            _, quote = self.current(row)
            if row['status'] == 'sent':
                await self.verify(row, quote)
            else:
                await self.send(row, quote['image_path'])
