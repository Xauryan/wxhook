"""Local dialogue images and durable receipts. Never calls an ASR API."""
import asyncio
import hashlib
import json
import subprocess
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from .delivery_notifications import failure_message, now
from .delivery_image_quotes import QuoteSourceUnavailable, ImageQuoteRejected, quote_payload
from .delivery_mentions import MentionSelectionError


def source(case, call_id):
    call = next((c for c in case['candidates']['items'] if c['id'] == call_id), None)
    if not call:
        raise ValueError('录音不在当前候选中')
    transcript = call.get('delivery_transcript') or {}
    segments = transcript.get('segments')
    if transcript.get('status') != 'succeeded' or not segments:
        raise ValueError('暂无成功的分段转写；未提交 ASR')
    if any(not isinstance(s.get('text'), str) for s in segments):
        raise ValueError('转写段文字无效')
    starts = [s['begin_time'] for s in segments if s.get('begin_time') is not None]
    if starts != sorted(starts):
        raise ValueError('原始转写时间顺序异常，请先核对；未重排')
    role = next((s['role'] for s in case.get('selected_calls') or [] if s['id'] == call_id), call.get('role'))
    order = case['candidates'].get('order') or {}
    phone = str(order.get('customer_phone_masked') or '')
    call_time = call.get('call_time')
    display_time = datetime.fromisoformat(call_time.replace('Z', '+00:00')).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S') if call_time else '未记录'
    return dict(template=1, call_id=call_id, order_id=case['order_id'],
                title={'outbound': '外呼通话记录', 'delivery': '配送通话记录'}.get(role, '归属待确认通话记录'),
                customer='客户' + (' · 尾号 ' + phone[-4:] if len(phone) >= 4 else ''),
                employee=call.get('user_name') or '未记录', call_time=display_time,
                duration=call.get('call_duration', 0), segments=[{k: s.get(k) for k in ('text', 'speaker_id', 'begin_time', 'end_time')} for s in segments])


def image_identity(case, call_id):
    content = source(case, call_id)
    raw = json.dumps(content, ensure_ascii=False, sort_keys=True)
    version = hashlib.sha256(raw.encode()).hexdigest()
    image_id = hashlib.sha256(f"{case['id']}:{call_id}:{version}".encode()).hexdigest()[:32]
    return content, raw, version, image_id


class DialogueService:
    def __init__(self, store, root, renderer, bridge, message_cache=None, quote_source=None, mentions=None):
        self.store, self.root, self.renderer, self.bridge = store, Path(root), Path(renderer), bridge
        self.lock = asyncio.Lock()
        self.message_cache = Path(message_cache) if message_cache else None
        self.quote_source = quote_source
        self.mentions = mentions

    def initialize(self):
        self.store.initialize()
        self.root.mkdir(parents=True, exist_ok=True)
        with self.store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS dialogue_images (
                  id TEXT PRIMARY KEY, case_id INTEGER, call_id INTEGER, version TEXT,
                  call_time TEXT, title TEXT, status TEXT, error TEXT, width INTEGER, height INTEGER,
                  updated_at TEXT, UNIQUE(case_id,call_id,version));
                CREATE TABLE IF NOT EXISTS dialogue_receipts (
                  image_id TEXT, room_id TEXT, room_name TEXT, status TEXT, error TEXT, updated_at TEXT,
                  PRIMARY KEY(image_id,room_id));
                CREATE TABLE IF NOT EXISTS dialogue_context_receipts (
                  image_id TEXT, room_id TEXT, body TEXT, reference_key TEXT,
                  status TEXT, error TEXT, updated_at TEXT,
                  PRIMARY KEY(image_id,room_id));
                CREATE TABLE IF NOT EXISTS feedback_quote_sources (
                  case_id INTEGER,room_id TEXT,status TEXT,reference_json TEXT,error TEXT,updated_at TEXT,
                  PRIMARY KEY(case_id,room_id));
                CREATE TABLE IF NOT EXISTS dialogue_quote_receipts (
                  image_id TEXT,room_id TEXT,reference_json TEXT NOT NULL,submitted_at TEXT NOT NULL,
                  status TEXT NOT NULL,evidence_json TEXT,error TEXT,updated_at TEXT,
                  PRIMARY KEY(image_id,room_id));
            ''')
            if 'context_json' not in {r[1] for r in db.execute('PRAGMA table_info(dialogue_images)')}:
                db.execute('ALTER TABLE dialogue_images ADD COLUMN context_json TEXT')

    def recover(self):
        self.initialize()
        with self.store.connect() as db:
            db.execute("UPDATE dialogue_images SET status='failed',error='生成中断，可重新生成' WHERE status='generating'")
            db.execute("UPDATE dialogue_receipts SET status='uncertain',error='发送中断，需人工核对；禁止盲目重发' WHERE status='sending'")
            db.execute("UPDATE dialogue_context_receipts SET status='uncertain',error='说明发送中断，未继续发图' WHERE status='sending'")
            db.execute("UPDATE dialogue_quote_receipts SET status='uncertain',error='引用图片提交中断，只查库不重发' WHERE status='sending'")

    def listing(self, case_id):
        self.initialize()
        with self.store.connect() as db:
            items = [dict(r) for r in db.execute('SELECT * FROM dialogue_images WHERE case_id=? ORDER BY call_time,call_id,updated_at DESC', (case_id,))]
            for item in items:
                item.pop('context_json', None)
                item['receipts'] = [dict(r) for r in db.execute('''SELECT r.room_id,r.room_name,r.status,r.error
                    FROM dialogue_receipts r JOIN dialogue_images i ON i.id=r.image_id
                    WHERE i.call_id=? AND i.version=?''', (item['call_id'], item['version']))]
                item['context_receipts'] = [dict(r) for r in db.execute('''SELECT r.room_id,r.status,r.error
                    FROM dialogue_context_receipts r JOIN dialogue_images i ON i.id=r.image_id
                    WHERE i.call_id=? AND i.version=?''', (item['call_id'], item['version']))]
                item['quote_receipts'] = [dict(r) for r in db.execute('''SELECT room_id,status,error,evidence_json
                    FROM dialogue_quote_receipts WHERE image_id=?''', (item['id'],))]
        return items

    def record_quote_source(self, case_id, room, status, reference=None, error=None):
        with self.store.connect() as db:
            db.execute('''INSERT INTO feedback_quote_sources VALUES(?,?,?,?,?,?) ON CONFLICT(case_id,room_id)
                DO UPDATE SET status=excluded.status,reference_json=excluded.reference_json,error=excluded.error,updated_at=excluded.updated_at''',
                (case_id, room, status, json.dumps(reference, ensure_ascii=False) if reference else None, error, now().isoformat()))

    async def resolve_feedback_reference(self, case_id, room):
        if self.quote_source is None:
            raise QuoteSourceUnavailable('未配置当前账号的真实消息查询，暂缓引用图片')
        profile = await self.bridge._post('/api/userInfo', {})
        account = profile.get('wxid') if isinstance(profile, dict) else None
        if not account:
            raise QuoteSourceUnavailable('无法确认当前发送账号')
        with self.store.connect() as db:
            primary = db.execute('SELECT * FROM deliveries WHERE event_key=? AND room_id=?',
                                 (f'delivery.failure:{case_id}', room)).fetchone()
        if not primary:
            raise QuoteSourceUnavailable('本次反馈主通知尚未提交')
        reference = await asyncio.to_thread(self.quote_source.resolve, account, room, dict(primary))
        self.record_quote_source(case_id, room, 'ready', reference)
        return reference

    async def preflight_quote_source(self):
        if self.quote_source is None:
            raise QuoteSourceUnavailable('未配置当前账号的真实消息查询')
        profile = await self.bridge._post('/api/userInfo', {})
        account = profile.get('wxid') if isinstance(profile, dict) else None
        if not account:
            raise QuoteSourceUnavailable('无法确认当前发送账号')
        await asyncio.to_thread(self.quote_source.preflight, account)

    def pending_quote_evidence(self, case_id, room):
        with self.store.connect() as db:
            return [dict(r) for r in db.execute('''SELECT q.* FROM dialogue_quote_receipts q
                JOIN dialogue_images i ON i.id=q.image_id WHERE i.case_id=? AND q.room_id=?
                AND q.status NOT IN ('local_verified','rejected')''', (case_id, room))]

    async def reconcile_quote_evidence(self, case_id, room):
        pending = self.pending_quote_evidence(case_id, room)
        for row in pending:
            if self.quote_source is None:
                continue
            try:
                proof = await asyncio.to_thread(self.quote_source.verify, json.loads(row['reference_json']), row['submitted_at'])
            except Exception:
                proof = None
            if proof:
                with self.store.connect() as db:
                    db.execute("UPDATE dialogue_quote_receipts SET status='local_verified',evidence_json=?,error=NULL,updated_at=? WHERE image_id=? AND room_id=?",
                               (json.dumps(proof, ensure_ascii=False), now().isoformat(), row['image_id'], room))
        # An uncertain transport receipt remains blocked even if local evidence later arrives.
        return not self.pending_quote_evidence(case_id, room)

    def image(self, image_id):
        self.initialize()
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM dialogue_images WHERE id=?', (image_id,)).fetchone()
        if not row or row['status'] != 'ready':
            raise ValueError('长图尚未生成成功')
        path = self.root / (row['id'] + '.png')
        if not path.is_file():
            raise ValueError('图片文件缺失，请重新生成')
        return dict(row), path.resolve()

    async def generate(self, case, call_id, regenerate=False):
        content, raw, version, image_id = image_identity(case, call_id)
        self.initialize()
        with self.store.connect() as db:
            db.execute('INSERT OR IGNORE INTO dialogue_images(id,case_id,call_id,version,call_time,title,status) VALUES(?,?,?,?,?,?,?)',
                       (image_id, case['id'], call_id, version, content['call_time'], content['title'], 'pending'))
            row = db.execute('SELECT status FROM dialogue_images WHERE id=?', (image_id,)).fetchone()
            db.execute('UPDATE dialogue_images SET context_json=? WHERE id=?',
                       (json.dumps({k: v for k, v in content.items() if k != 'segments'}, ensure_ascii=False), image_id))
            reuse = row['status'] == 'generating' or (row['status'] == 'ready' and not regenerate and (self.root / (image_id + '.png')).is_file())
            if not reuse:
                db.execute("UPDATE dialogue_images SET status='generating',error=NULL,updated_at=? WHERE id=?", (datetime.now().isoformat(), image_id))
        if reuse:
            return self.listing(case['id'])
        temporary = self.root / (uuid.uuid4().hex + '.json')
        output = temporary.with_suffix('.png')
        try:
            temporary.write_text(raw, encoding='utf-8')
            result = await asyncio.to_thread(subprocess.run, ['node', str(self.renderer), str(temporary), str(output)], capture_output=True, timeout=150)
            if result.returncode:
                raise ValueError('固定模板渲染失败；请检查 Node、Playwright 或长图高度限制（100000px），未截断')
            metrics = json.loads(result.stdout)
            output.replace(self.root / (image_id + '.png'))
            with self.store.connect() as db:
                db.execute("UPDATE dialogue_images SET status='ready',width=?,height=?,error=NULL WHERE id=?", (metrics['width'], metrics['height'], image_id))
        except Exception:
            with self.store.connect() as db:
                db.execute("UPDATE dialogue_images SET status='failed',error=? WHERE id=?", ('生成失败：检查渲染依赖和 100000px 本地高度上限；未分页或截断，可重试', image_id))
        finally:
            temporary.unlink(missing_ok=True)
            output.unlink(missing_ok=True)
        return self.listing(case['id'])

    def quote_messages(self, room, search='', key=None):
        self.initialize()
        if room not in {g['room_id'] for g in self.store.configuration()['target_groups']}:
            raise ValueError('引用只允许读取已配置的配送群')
        if not self.message_cache or not self.message_cache.is_file():
            if key:
                raise ValueError('本地消息快照不可用，未发送引用')
            return []
        try:
            with sqlite3.connect(self.message_cache.resolve().as_uri() + '?mode=ro', uri=True) as db:
                db.row_factory = sqlite3.Row
                rows = db.execute('''SELECT message_key,server_id,local_id,sender_username,sender_name,
                    content,create_time FROM messages WHERE session_username=? AND base_type=1
                    AND decoded=1 AND server_id>0 AND local_id>0 AND create_time>0
                    AND sender_username IS NOT NULL AND sender_username!='' AND length(content) BETWEEN 1 AND 8000
                    AND (? IS NULL OR message_key=?) AND instr(content,?)>0
                    ORDER BY create_time DESC,local_id DESC LIMIT 50''', (room, key, key, search)).fetchall()
        except sqlite3.Error as exc:
            raise ValueError('本地消息快照读取失败，未发送引用') from exc
        if key and not rows:
            raise ValueError('引用消息不存在、字段不完整或不属于目标群；未发送')
        return [{**dict(r), 'server_id': str(r['server_id'])} for r in rows]

    def receipt(self, db, table, row, room):
        return db.execute(f'''SELECT r.* FROM {table} r JOIN dialogue_images i ON i.id=r.image_id
            WHERE i.call_id=? AND i.version=? AND r.room_id=?''', (row['call_id'], row['version'], room)).fetchone()

    def plan(self, case_id, image_ids, room_ids, references, archive_url=None, feedback_case=None):
        self.initialize()
        if any(references.values()):
            raise ValueError('不再追加引用或录音说明文字；仅发送一条反馈主消息及对应图片')
        groups = {g['room_id']: g for g in self.store.configuration()['target_groups']}
        if not room_ids or any(room not in groups for room in room_ids):
            raise ValueError('仅允许明确选择已配置的独立配送群')
        images = [self.image(i) for i in image_ids]
        if not images or any(row['case_id'] != case_id for row, _ in images):
            raise ValueError('图片不属于本异常单')
        if len({row['call_id'] for row, _ in images}) != len(images):
            raise ValueError('每通录音只能选择一个内容版本')
        images.sort(key=lambda item: (item[0]['title'] != '外呼通话记录', item[0]['call_time'], item[0]['call_id']))
        plans = []
        for room in dict.fromkeys(room_ids):
            reference_key = references.get(room) or None
            quote = None
            blocked = False
            for index, (row, path) in enumerate(images):
                if not row.get('context_json'):
                    raise ValueError('旧图片缺少消息说明，请点击生成长图补齐（复用已有图片，不重复转写）')
                body = ''
                with self.store.connect() as db:
                    receipt = self.receipt(db, 'dialogue_receipts', row, room)
                    caption = self.receipt(db, 'dialogue_context_receipts', row, room)
                status = receipt['status'] if receipt else 'pending'
                if blocked and status == 'pending':
                    status = 'blocked'
                if status not in {'pending', 'bridge_accepted'} or (caption and caption['status'] != 'bridge_accepted'):
                    blocked = True
                if caption:
                    if caption['reference_key'] != reference_key and status != 'bridge_accepted':
                        raise ValueError('该录音说明已有提交记录，不能更换引用后继续发送；请核对原记录')
                plans.append(dict(image_id=row['id'], room_id=room, room_name=groups[room]['name'],
                                  title=row['title'], body=body, reference=quote, status=status,
                                  context_status=caption['status'] if caption else 'not_required',
                                  context_required=False,
                                  row=row, path=path))
        return plans

    async def send(self, case_id, image_ids, room_ids, simulate=True, references=None, archive_url=None, feedback_case=None,
                   *, adopt_queued_primary=False):
        async with self.lock:
            plans = self.plan(case_id, image_ids, room_ids, references or {}, archive_url, feedback_case)
            feedback_body = failure_message(feedback_case, archive_url) if feedback_case else None
            base_feedback_body = feedback_body
            if feedback_case and (feedback_case['id'] != case_id or not archive_url):
                raise ValueError('反馈主消息与异常单或订单链接不匹配')
            for plan in plans:
                with self.store.connect() as db:
                    feedback = db.execute('SELECT status,message FROM deliveries WHERE event_key=? AND room_id=?',
                        (f'delivery.failure:{case_id}', plan['room_id'])).fetchone()
                plan['feedback_body'] = feedback['message'] if feedback else feedback_body
                plan['feedback_status'] = feedback['status'] if feedback else 'pending'
            if simulate:
                return {'mode': 'simulated', 'bridge_called': False,
                        'items': [{k: v for k, v in p.items() if k not in {'row', 'path'}} for p in plans]}
            blocked_rooms = set()
            submitted_images = 0
            waiting_reference = False
            waiting_mentions = False
            for room in dict.fromkeys(room_ids):
                if not await self.reconcile_quote_evidence(case_id, room):
                    blocked_rooms.add(room)
            for plan in plans:
                room, row, path = plan['room_id'], plan['row'], plan['path']
                feedback_body = base_feedback_body
                mention = None
                if room in blocked_rooms:
                    continue
                with self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    receipt = self.receipt(db, 'dialogue_receipts', row, room)
                    if receipt:
                        if receipt['status'] != 'bridge_accepted':
                            blocked_rooms.add(room)
                        continue
                    caption = self.receipt(db, 'dialogue_context_receipts', row, room)
                    if caption and caption['status'] != 'bridge_accepted':
                        blocked_rooms.add(room)
                        continue
                    existing_primary = db.execute('SELECT status FROM deliveries WHERE event_key=? AND room_id=?',
                        (f'delivery.failure:{case_id}', room)).fetchone()
                    if not existing_primary and not feedback_body:
                        raise ValueError('缺少本次反馈主消息，未单独发送图片')
                    if existing_primary and existing_primary['status'] != 'sent' and not (adopt_queued_primary and existing_primary['status'] == 'queued'):
                        blocked_rooms.add(room)
                        continue
                    if not caption:
                        # The primary feedback uses the same receipt key as automation.
                        # Do not enqueue it: a manual send must not enable a worker or replay history.
                        if feedback_body:
                            primary = db.execute('SELECT status FROM deliveries WHERE event_key=? AND room_id=?',
                                (f'delivery.failure:{case_id}', room)).fetchone()
                            if primary and primary['status'] != 'sent' and not (adopt_queued_primary and primary['status'] == 'queued'):
                                blocked_rooms.add(room)
                                continue
                if feedback_body:
                    if not existing_primary or existing_primary['status'] == 'queued':
                        if self.mentions:
                            try:
                                mention = await self.mentions.prepare(room, 'failure', (feedback_case or {}).get('notification_order'), feedback_body)
                                feedback_body = mention['message']
                            except Exception as exc:
                                self.record_quote_source(case_id, room, 'waiting_mentions', error=str(exc) if isinstance(exc, MentionSelectionError) else '@ 群成员查询暂不可用')
                                waiting_mentions = True
                                blocked_rooms.add(room)
                                continue
                        try:
                            await self.preflight_quote_source()
                        except Exception:
                            self.record_quote_source(case_id, room, 'waiting_reference', error='当前账号消息源尚不可用，未发送文字或图片')
                            waiting_reference = True
                            blocked_rooms.add(room)
                            continue
                    with self.store.connect() as db:
                        db.execute('BEGIN IMMEDIATE')
                        primary = db.execute('SELECT status FROM deliveries WHERE event_key=? AND room_id=?',
                            (f'delivery.failure:{case_id}', room)).fetchone()
                        if primary and primary['status'] != 'sent' and not (adopt_queued_primary and primary['status'] == 'queued'):
                            blocked_rooms.add(room)
                            continue
                        if primary and primary['status'] == 'queued' and adopt_queued_primary:
                            if mention:
                                self.mentions.freeze(db, f'delivery.failure:{case_id}', room, mention)
                            db.execute("UPDATE deliveries SET message=?,status='sending',error=NULL WHERE event_key=? AND room_id=? AND status='queued'",
                                       (feedback_body, f'delivery.failure:{case_id}', room))
                            primary = None
                        elif not primary:
                            if mention:
                                self.mentions.freeze(db, f'delivery.failure:{case_id}', room, mention)
                            db.execute('INSERT INTO deliveries(event_key,room_id,room_name,message,status,created_at) VALUES(?,?,?,?,?,?)',
                                (f'delivery.failure:{case_id}', room, plan['room_name'], feedback_body, 'sending', now().isoformat()))
                    if not primary:
                        try:
                            if mention and mention['members']:
                                await self.mentions.send(room, mention)
                            else:
                                await self.bridge._post('/api/sendTextMsg', {'wxid': room, 'msg': feedback_body})
                            primary_status, primary_error = 'sent', None
                        except Exception:
                            primary_status, primary_error = 'uncertain', '反馈主消息结果不明，停止后续资料发送'
                        self.store.mark({'event_key': f'delivery.failure:{case_id}', 'room_id': room}, primary_status, primary_error)
                        if primary_status != 'sent':
                            blocked_rooms.add(room)
                            continue
                        await asyncio.sleep(2.5)
                try:
                    reference = await self.resolve_feedback_reference(case_id, room)
                    payload = quote_payload(room, path, reference)
                except Exception as exc:
                    message = str(exc) if isinstance(exc, QuoteSourceUnavailable) else '真实原消息查询暂不可用'
                    self.record_quote_source(case_id, room, 'waiting_reference', error=message)
                    waiting_reference = True
                    blocked_rooms.add(room)
                    continue
                submitted_at = now().isoformat()
                with self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    if self.receipt(db, 'dialogue_receipts', row, room):
                        blocked_rooms.add(room)
                        continue
                    db.execute('INSERT INTO dialogue_receipts VALUES(?,?,?,?,?,?)', (row['id'], room, plan['room_name'], 'sending', None, datetime.now().isoformat()))
                    db.execute("INSERT INTO dialogue_quote_receipts VALUES(?,?,?,?,'sending',NULL,NULL,?)",
                               (row['id'], room, json.dumps(reference, ensure_ascii=False), submitted_at, submitted_at))
                try:
                    accepted = await self.bridge._post('/api/sendImageQuoteMsg', payload)
                    if not isinstance(accepted, dict) or accepted.get('accepted') != 1 or accepted.get('stage') != 'bridge-accepted':
                        raise ValueError('Unexpected image quote receipt')
                    status, error = 'bridge_accepted', None
                    quote_status = 'awaiting_evidence'
                    submitted_images += 1
                except ImageQuoteRejected:
                    status, quote_status, error = 'rejected', 'rejected', '引用图片被明确拒绝；未降级为普通图片，不自动重试'
                except Exception:
                    status, quote_status, error = 'uncertain', 'uncertain', '引用图片结果不明；未降级，不自动重发'
                with self.store.connect() as db:
                    db.execute('UPDATE dialogue_receipts SET status=?,error=?,updated_at=? WHERE image_id=? AND room_id=?', (status, error, datetime.now().isoformat(), row['id'], room))
                    db.execute('UPDATE dialogue_quote_receipts SET status=?,error=?,updated_at=? WHERE image_id=? AND room_id=?',
                               (quote_status, error, now().isoformat(), row['id'], room))
                if status != 'bridge_accepted':
                    blocked_rooms.add(room)
                await asyncio.sleep(2.5)
                if not await self.reconcile_quote_evidence(case_id, room):
                    blocked_rooms.add(room)
            return {'mode': 'real', 'items': self.listing(case_id), 'submitted_images': submitted_images,
                    'waiting_reference': waiting_reference, 'waiting_mentions': waiting_mentions,
                    'pending_evidence': any(self.pending_quote_evidence(case_id, room) for room in room_ids)}
