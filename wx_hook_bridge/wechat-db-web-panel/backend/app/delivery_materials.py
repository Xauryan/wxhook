"""Continue an acknowledged feedback message with independently ready call images."""
import asyncio
from datetime import datetime, timedelta
import re
import sqlite3

from .delivery_dialogues import image_identity
from .delivery_notifications import archive_url, now
from .notification_text import feedback_recordings

FEEDBACK_WAIT_SECONDS = 600


def ready_calls(case):
    confirmed = list(feedback_recordings(case).values())
    if len(confirmed) > 20 or sum(call.get('call_duration') or 0 for call in confirmed) > 7200:
        return []
    return sorted([call for call in confirmed if (call.get('delivery_transcript') or {}).get('status') == 'succeeded'
                   and (call.get('delivery_transcript') or {}).get('segments')], key=lambda call: (call['role'] != 'outbound', call['call_time'], call['id']))


class DeliveryMaterialWorker:
    def __init__(self, notifications, dialogues, fetch):
        self.notifications, self.dialogues, self.fetch = notifications, dialogues, fetch
        self.store = notifications.store
        self.task = None
        self.stop_event = asyncio.Event()
        self.last_poll_at = None
        self.last_error = None
        notifications.material_worker = self

    def initialize(self):
        self.dialogues.initialize()
        with self.store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS dialogue_auto_state (
                case_id INTEGER PRIMARY KEY,status TEXT NOT NULL,last_checked_at TEXT NOT NULL,
                next_check_at TEXT NOT NULL,error TEXT)''')
            db.execute('''CREATE TABLE IF NOT EXISTS feedback_waits (
                case_id INTEGER PRIMARY KEY,order_id INTEGER NOT NULL,deadline_at TEXT NOT NULL,
                status TEXT NOT NULL,error TEXT,updated_at TEXT NOT NULL)''')

    def record_feedback_wait(self, case, status, error=None):
        deadline = datetime.fromisoformat(case['created_at']) + timedelta(seconds=FEEDBACK_WAIT_SECONDS)
        with self.store.connect() as db:
            db.execute('''INSERT INTO feedback_waits VALUES(?,?,?,?,?,?) ON CONFLICT(case_id) DO UPDATE SET
                status=excluded.status,error=excluded.error,updated_at=excluded.updated_at''',
                (case['id'], case['order_id'], deadline.isoformat(), status, error, now().isoformat()))

    def primary_targets(self, case, targets):
        config = self.store.configuration()
        if (not config['failure_notifications_enabled'] or not config.get('failure_activated_at')
                or datetime.fromisoformat(case['created_at']) < datetime.fromisoformat(config['failure_activated_at'])
                or case.get('closed_at') or case.get('status') == 'closed'):
            return []
        allowed = {g['room_id'] for g in config['target_groups']}
        with self.store.connect() as db:
            recorded = {r[0] for r in db.execute("SELECT room_id FROM deliveries WHERE event_key=? AND status!='queued'",
                                                (f"delivery.failure:{case['id']}",))}
        return [g['room_id'] for g in targets if g['room_id'] in allowed and g['room_id'] not in recorded]

    async def send_primary(self, case, targets):
        """Called under notifications.lock; prepare images before claiming any text."""
        self.initialize()
        if self.stop_event.is_set() or not self.primary_targets(case, targets):
            self.record_feedback_wait(case, 'excluded')
            return
        deadline = datetime.fromisoformat(case['created_at']) + timedelta(seconds=FEEDBACK_WAIT_SECONDS)
        calls = ready_calls(case)
        if not calls:
            reasons = '；'.join((case.get('material_scope') or {}).get('review_reasons') or [])
            self.record_feedback_wait(case, 'no_materials', reasons or '尚无就绪录音；继续复查，不发送空反馈')
            return
        if now() < deadline and {c['role'] for c in calls} != {'outbound', 'delivery'}:
            self.record_feedback_wait(case, 'waiting_window', '等待另一侧录音；10分钟后可先发送已就绪的一侧')
            return
        images = {}
        for call in calls:
            _, _, version, image_id = image_identity(case, call['id'])
            with self.store.connect() as db:
                row = db.execute('SELECT status FROM dialogue_images WHERE id=?', (image_id,)).fetchone()
            if row and row['status'] in {'failed', 'generating'}:
                continue
            if not row or row['status'] != 'ready' or not (self.dialogues.root/(image_id+'.png')).is_file():
                await self.dialogues.generate(case, call['id'])
            try:
                self.dialogues.image(image_id)
            except ValueError:
                continue
            images[call['id']] = (image_id, version)
        fresh = await self.fetch(f"/cases/{case['id']}", {})
        if fresh.get('id') != case['id'] or fresh.get('order_id') != case['order_id']:
            raise ValueError('Feedback identity changed')
        fresh_calls = {c['id']: c for c in ready_calls(fresh)}
        images = {cid: value for cid, value in images.items() if cid in fresh_calls
                  and image_identity(fresh, cid)[2] == value[1]}
        if not images or (now() < deadline and {fresh_calls[cid]['role'] for cid in images} != {'outbound', 'delivery'}):
            self.record_feedback_wait(case, 'image_not_ready', '长图尚未全部生成或资料发生变化，未发送反馈文字')
            return
        rooms = self.primary_targets(fresh, targets)
        if self.stop_event.is_set() or self.notifications.stop_event.is_set() or not rooms:
            self.record_feedback_wait(case, 'excluded')
            return
        link = archive_url(await self.fetch(f"/cases/{case['id']}/archive-link", {}, method='POST', payload={}))
        waiting_reference = False
        waiting_mentions = False
        for room in rooms:
            if self.stop_event.is_set() or self.notifications.stop_event.is_set() or self.blocked(case['id'], room):
                continue
            pending = {cid: value for cid, value in images.items()
                       if self.material_state(case['id'], cid, value[1], room) == 'pending'}
            if not pending:
                continue
            # Only advertise links for the exact ready images accompanying this message.
            bundle = {**fresh, 'notification_calls': [r for r in fresh.get('notification_calls', []) if r['id'] in pending]}
            result = await self.dialogues.send(case['id'], [v[0] for v in pending.values()], [room], False, {}, link, bundle,
                                               adopt_queued_primary=True)
            waiting_reference = waiting_reference or result.get('waiting_reference', False)
            waiting_mentions = waiting_mentions or result.get('waiting_mentions', False)
        with self.store.connect() as db:
            states = {r['room_id']: r['status'] for r in db.execute('SELECT room_id,status FROM deliveries WHERE event_key=?',
                                                                    (f"delivery.failure:{case['id']}",))}
        complete = not waiting_reference and not waiting_mentions and all(states.get(room) == 'sent' and not self.blocked(case['id'], room) for room in rooms)
        state = 'submitted' if complete else 'waiting_mentions' if waiting_mentions else 'waiting_reference' if waiting_reference else 'awaiting_quote_evidence'
        self.record_feedback_wait(case, state, None if complete else '所选 @ 人员需核对，文字未发送' if waiting_mentions else '等待真实原消息或图片引用入库证据；未降级，不重发已提交消息')

    def status(self):
        items = []
        if self.store.path.exists():
            with sqlite3.connect(self.store.path.resolve().as_uri() + '?mode=ro', uri=True) as db:
                db.row_factory = sqlite3.Row
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='dialogue_auto_state'").fetchone():
                    items = [dict(row) for row in db.execute('SELECT * FROM dialogue_auto_state ORDER BY last_checked_at DESC LIMIT 20')]
        return {'running': bool(self.task and not self.task.done()), 'last_poll_at': self.last_poll_at,
                'last_error': self.last_error, 'items': items}

    def record(self, case_id, status, error=None, delay=60):
        at = now()
        with self.store.connect() as db:
            db.execute('''INSERT INTO dialogue_auto_state VALUES(?,?,?,?,?) ON CONFLICT(case_id) DO UPDATE SET
                status=excluded.status,last_checked_at=excluded.last_checked_at,next_check_at=excluded.next_check_at,error=excluded.error''',
                (case_id, status, at.isoformat(), (at + timedelta(seconds=delay)).isoformat(), error))

    def rooms_for_case(self, case, config):
        if not config['failure_notifications_enabled'] or not config.get('failure_activated_at'):
            return []
        if datetime.fromisoformat(case['created_at']) < datetime.fromisoformat(config['failure_activated_at']):
            return []
        if case.get('closed_at') or case.get('status') == 'closed':
            return []
        with self.store.connect() as db:
            accepted = {row[0] for row in db.execute("SELECT room_id FROM deliveries WHERE event_key=? AND status='sent'", (f"delivery.failure:{case['id']}",))}
        return [group['room_id'] for group in config['target_groups'] if group['room_id'] in accepted]

    def blocked(self, case_id, room):
        if self.dialogues.pending_quote_evidence(case_id, room):
            return True
        with self.store.connect() as db:
            for table in ('dialogue_receipts', 'dialogue_context_receipts'):
                if db.execute(f'''SELECT 1 FROM {table} r JOIN dialogue_images i ON i.id=r.image_id
                    WHERE i.case_id=? AND r.room_id=? AND r.status!='bridge_accepted' LIMIT 1''', (case_id, room)).fetchone():
                    return True
        return False

    def material_state(self, case_id, call_id, version, room):
        with self.store.connect() as db:
            # A revision never automatically replays a call already submitted for this case.
            accepted = db.execute('''SELECT 1 FROM dialogue_receipts r JOIN dialogue_images i ON i.id=r.image_id
                WHERE i.case_id=? AND i.call_id=? AND r.room_id=? AND r.status='bridge_accepted' LIMIT 1''',
                (case_id, call_id, room)).fetchone()
            if accepted:
                return 'accepted'
            for table in ('dialogue_receipts', 'dialogue_context_receipts'):
                row = self.dialogues.receipt(db, table, {'call_id': call_id, 'version': version}, room)
                if row and row['status'] != 'bridge_accepted':
                    return 'blocked'
                if row and table == 'dialogue_receipts':
                    return 'accepted'
        return 'pending'

    async def process_case(self, case_id, max_new_images=4):
        case = await self.fetch(f'/cases/{case_id}', {})
        rooms = self.rooms_for_case(case, self.store.configuration())
        if not rooms:
            self.record(case_id, 'excluded', delay=3600)
            return
        for room in rooms:
            await self.dialogues.reconcile_quote_evidence(case_id, room)
        calls = ready_calls(case)
        if not (case.get('material_scope') or {}).get('calls'):
            scope = case.get('material_scope') or {}
            message = '；'.join(scope.get('review_reasons') or []) or 'DeepSeek 自动处理中，不需要人工选定'
            state = scope.get('status')
            if state == 'no_relevant':
                self.record(case_id, 'no_relevant', 'DeepSeek 已核对现有录音，未发现本次相关沟通，不发送无关图片', delay=300)
            else:
                self.record(case_id, state if state in {'waiting_source','ai_unavailable'} else 'waiting_ai', message[:1200], delay=60)
            return
        blocked = {room for room in rooms if self.blocked(case_id, room)}
        images = {room: [] for room in rooms}
        material_versions = {}
        failed_image = False
        generated = 0
        for call in calls:
            # source() derives the identical existing PNG content/version, without reporter metadata.
            _, _, version, image_id = image_identity(case, call['id'])
            pending = []
            for room in rooms:
                state = self.material_state(case_id, call['id'], version, room)
                if state == 'blocked': blocked.add(room)
                if room not in blocked and state == 'pending': pending.append(room)
            if not pending:
                continue
            with self.store.connect() as db:
                row = db.execute('SELECT * FROM dialogue_images WHERE id=?', (image_id,)).fetchone()
            if row and row['status'] == 'failed':
                failed_image = True
                continue
            if row and row['status'] == 'generating':
                continue
            if not row or row['status'] != 'ready' or not (self.dialogues.root/(image_id+'.png')).is_file():
                if generated >= max_new_images:
                    continue
                generated += 1
                await self.dialogues.generate(case, call['id'])
            with self.store.connect() as db:
                row = db.execute('SELECT * FROM dialogue_images WHERE id=?', (image_id,)).fetchone()
            if not row or row['status'] != 'ready':
                failed_image = True
                continue
            material_versions[image_id] = (call['id'], version)
            for room in pending:
                images[room].append(image_id)
        submitted = False
        waiting_reference = False
        if any(images.values()):
            link = archive_url(await self.fetch(f'/cases/{case_id}/archive-link', {}, method='POST', payload={}))
            fresh = await self.fetch(f'/cases/{case_id}', {})
            fresh_calls = {call['id']: call for call in ready_calls(fresh)}
            safe_ids = set()
            for image_id, (call_id, version) in material_versions.items():
                if call_id in fresh_calls and image_identity(fresh, call_id)[2] == version:
                    safe_ids.add(image_id)
            # Configuration changes and ordinary notification sends share this lock.
            async with self.notifications.lock:
                allowed = set(self.rooms_for_case(fresh, self.store.configuration()))
                for room, image_ids in images.items():
                    if self.stop_event.is_set() or room not in allowed or room in blocked or self.blocked(case_id, room):
                        continue
                    image_ids = [image_id for image_id in image_ids if image_id in safe_ids]
                    if not image_ids:
                        continue
                    result = await self.dialogues.send(case_id, image_ids, [room], False, {}, link, fresh)
                    submitted = submitted or result.get('submitted_images', 0) > 0
                    waiting_reference = waiting_reference or result.get('waiting_reference', False)
        quote_pending = [q for room in rooms for q in self.dialogues.pending_quote_evidence(case_id, room)]
        if quote_pending and all(q['status'] == 'awaiting_evidence' for q in quote_pending):
            self.record(case_id, 'awaiting_quote_evidence', '桥接已接受；等待图片引用入库确认，不重发已提交图片', delay=30)
        elif blocked or any(self.blocked(case_id, room) for room in rooms):
            self.record(case_id, 'blocked', '说明或图片回执不明，已停止本单该群自动续发；需人工核对', delay=300)
        elif waiting_reference:
            self.record(case_id, 'waiting_reference', '等待本次主通知的真实消息编号，暂缓引用图片；不会发送普通图片', delay=30)
        elif failed_image:
            self.record(case_id, 'image_failed', '部分图片生成失败；该内容版本不会自动反复生成，请人工重试', delay=300)
        else:
            self.record(case_id, 'submitted' if submitted else 'waiting_materials',
                        None if submitted else '等待已确认录音转写就绪；已提交材料不重发', delay=60)
        if not waiting_reference and all(not self.blocked(case_id, room) for room in rooms):
            with self.store.connect() as db:
                db.execute("UPDATE feedback_waits SET status='submitted',error=NULL,updated_at=? WHERE case_id=? AND status IN ('waiting_reference','awaiting_quote_evidence') AND EXISTS(SELECT 1 FROM dialogue_quote_receipts q JOIN dialogue_images i ON i.id=q.image_id WHERE i.case_id=? AND q.status='local_verified')",
                           (now().isoformat(), case_id, case_id))

    async def poll_once(self):
        self.initialize()
        config = self.store.configuration()
        if not config['failure_notifications_enabled'] or not config.get('failure_activated_at'):
            return
        with self.store.connect() as db:
            rows = db.execute("SELECT event_key,room_id FROM deliveries WHERE event_key LIKE 'delivery.failure:%' AND status='sent'").fetchall()
            states = {r['case_id']: dict(r) for r in db.execute('SELECT * FROM dialogue_auto_state')}
        current_rooms = {group['room_id'] for group in config['target_groups']}
        ids = {int(match.group(1)) for row in rows if row['room_id'] in current_rooms
               and (match := re.fullmatch(r'delivery\.failure:(\d+)', row['event_key']))}
        due = [case_id for case_id in ids if states.get(case_id, {}).get('next_check_at', '') <= now().isoformat()]
        due.sort(key=lambda case_id: (states.get(case_id, {}).get('last_checked_at', ''), case_id))
        for case_id in due[:5]:
            if self.stop_event.is_set():
                break
            try:
                await self.process_case(case_id)
            except Exception as exc:
                self.record(case_id, 'error', '自动资料处理暂不可用：' + type(exc).__name__, delay=60)
        self.last_poll_at = now().isoformat()

    async def run(self):
        while not self.stop_event.is_set():
            try:
                await self.poll_once()
                self.last_error = None
            except Exception as exc:
                self.last_error = type(exc).__name__
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=30)
            except TimeoutError:
                pass

    async def start(self):
        self.initialize()
        self.stop_event.clear()
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run(), name='delivery-ready-materials')

    async def stop(self):
        self.stop_event.set()
        if self.task:
            await self.task
            self.task = None
