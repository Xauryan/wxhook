"""Independent delivery notification configuration and durable per-room receipts."""

import asyncio
import json
import sqlite3
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone
from .notification_text import call_metrics, count, date_label, first_call_text, interval_text, metric_text, phone_tail, time_text, verified_reporter, feedback_recordings, recording_page_url, duration_text, timestamp
from .delivery_stage_images import DeliveryStageImageRenderer
from .delivery_call_images import DeliveryCallImageRenderer, bundle_message, report_data
from .delivery_call_quotes import DeliveryCallQuotes

BEIJING = timezone(timedelta(hours=8))


def now():
    return datetime.now(BEIJING)


def failure_message(case, archive_url):
    order = case.get('notification_order') or {}
    event = next((e for e in case.get('history', {}).get('events', [])
                  if case.get('failure_event_id') is not None and e.get('id') == case['failure_event_id']), None)
    # Current assignee/owner is not evidence of the person who reported failure.
    stamp = event.get('created_at') if event else case.get('created_at')
    identity = verified_reporter(case.get('reporter'))
    reporter_name = identity['name'] if identity else '未记录'
    lines = [f"【无法配送反馈】订单 #{case['order_id']}",
             f"所属组：{order.get('outbound_group_name') or '未记录'}｜外呼员：{order.get('agent_name') or '未记录'}",
             f"客户电话：{order.get('customer_phone') or '未记录'}",
             f"反馈人：{reporter_name}｜{time_text(identity.get('submitted_at') if identity and identity.get('submitted_at') else stamp)}"]
    if not event and not (identity and identity.get('submitted_at')):
        lines[-1] = f"反馈人：{reporter_name}｜异常单记录时间 {time_text(stamp)}"
    lines.extend(['', '反馈原文：', case.get('reported_reason') or '未填写反馈原因'])
    selected = feedback_recordings(case)
    for role, label in [('outbound', '外呼录音'), ('delivery', '配送录音')]:
        call = selected.get(role)
        lines.append('')
        if not call:
            scope = case.get('material_scope') or {}
            if scope.get('policy') == 'business-relevance-v2-deepseek-auto':
                has_candidate = any(c.get('role') == role for c in case.get('candidates', {}).get('items', []))
                status = '待筛选' if has_candidate else '暂未查到可确认的本单录音，继续复查'
                if not has_candidate and role == 'outbound' and order.get('agent_name') and any(
                    c.get('role') == 'unverified' and c.get('user_name') == order['agent_name']
                    for c in case.get('candidates', {}).get('items', [])
                ):
                    status = '已发现候选录音，身份关联待确认'
                if has_candidate:
                    state = scope.get('status')
                    if state == 'no_relevant':
                        status = '暂未发现相关沟通'
                    elif state == 'waiting_source':
                        status = '等待可用的本单录音资料'
                    elif state == 'retry_pending':
                        status = '筛选失败，等待重试'
                    elif state == 'ai_unavailable':
                        status = '筛选失败，待处理'
                    elif state == 'awaiting_transcripts':
                        work = set(scope.get('work_call_ids') or [])
                        status = '录音转写中' if any(c.get('role') == role and c.get('id') in work for c in case.get('candidates', {}).get('items', [])) else '待筛选'
                lines.append(f'【{label}】{status}')
            else:
                lines.append(f'【{label}】录音待确认')
            continue
        lines.extend([f"【{label}】{call.get('user_name') or '人员未记录'}",
                      f"{time_text(call.get('call_time'), seconds=True)}｜{duration_text(call.get('call_duration'))}",
                      '听录音 / 看原文（需登录）：' + recording_page_url(case, call)])
    scope = case.get('material_scope') or {}
    if scope.get('policy') == 'business-relevance-v2-deepseek-auto':
        tail = '反馈原因待核实。符合条件的录音资料就绪后补充长图。'
        if not selected and scope.get('status') in {'ai_unavailable', 'retry_pending'}:
            tail = '反馈原因待核实。录音筛选尚未完成，当前暂无可补充长图。'
        elif not selected and scope.get('status') in {'no_relevant', 'waiting_source'}:
            tail = '反馈原因待核实。当前暂无符合条件的录音长图。'
        lines.extend(['', tail])
    else:
        lines.extend(['', '反馈原因待核实。相关录音转写就绪后补充完整长图。' if selected else
                      '反馈原因待核实。系统自动处理相关录音，就绪后补充长图。'])
    return '\n'.join(lines)


def call_message(call):
    stamp = time_text(call.get('call_time'), seconds=True)
    seconds = count(call.get('call_duration'))
    outcome = (f'已接通 {seconds} 秒' if seconds is not None else '已接通，时长未记录') if call.get('is_connected') else '未接通'
    assigned = time_text(call.get('assigned_at'))
    if call.get('assignment_time_source') == 'current_assignment_unverified' and assigned != '未记录':
        assigned += '（原记录，历史归属待核对）'
    elif call.get('assignment_time_source') == 'ambiguous':
        assigned = '待核对'
    return "\n".join([
        f"【配送通话】订单 #{call['order_id']}",
        f"{call.get('staff_name') or '配送员未记录'}｜客户尾号 {phone_tail(call.get('customer_phone_masked'))}",
        f"{call.get('outbound_group_name') or '小组未记录'}｜{call.get('agent_name') or '未记录'}（外呼员）",
        f'分配时间：{assigned}', f'{stamp}｜{outcome}',
        '录音：' + ('有链接' if call.get('recording_available') else '暂无链接'),
    ])


def overview_message(data):
    lines = [f"【配送跟进统计｜{date_label(data.get('date'))}】",
             '统计区间 ' + interval_text(data.get('since'), data.get('snapshot_at'))]
    for row in data.get("items", []):
        lines.extend(['', f"{row['staff_name']}｜{first_call_text(row)}", call_metrics(row),
                      '订单关联号码：' + metric_text('覆盖', row.get('order_customer_count'), '个')
                      + '，' + metric_text('接通', row.get('connected_order_customer_count'), '个')])
    if not data.get("items"):
        raise ValueError("没有配送员统计数据，未发送")
    return "\n".join(lines)


def reminder_message(item, at=None):
    if item.get('kind') == 'first_call':
        due = timestamp(item.get('due_at'))
        current = timestamp(at) or timestamp(item.get('checked_at')) or now()
        elapsed = max(0, int((current-due).total_seconds()//60)) if due else None
        if elapsed is None:
            overdue = '待核对'
        elif elapsed == 0:
            overdue = '不足1分钟'
        else:
            hours, minutes = divmod(elapsed, 60)
            overdue = (f'{hours}小时' if hours else '') + (f'{minutes}分钟' if minutes else '')
        return '\n'.join([
            f"【首拨提醒】{item.get('staff_name') or '待分配'}｜订单 #{item['order_id']}", '',
            f"客户：{item.get('customer_name') or '姓名未记录'}｜尾号 {phone_tail(item.get('customer_phone_masked'))}",
            f"外呼：{item.get('agent_name') or '未记录'}｜{item.get('outbound_group_name') or '小组未记录'}",
            f"分配：{time_text(item.get('assigned_at'), seconds=True)}",
            f"首拨截止：{time_text(item.get('due_at'))}", f'已超时：{overdue}', '',
            '暂未查到本次分配后的拨号记录。',
            '请尽快联系；已联系的请核对记录同步。'])
    formats = {
        'callback': ('回拨提醒', '负责人', '约定回拨', '请核对回拨进度；如已回拨，请更新跟进结果。'),
        'case_due': ('异常处理逾期', '负责人', '处理截止', '请更新处理进展和下一步安排。'),
        'appointment': ('预约配送待确认', '配送员', '预约', '请核对预约是否已履行，并更新配送状态。'),
    }
    kind = item['kind']
    if kind not in formats:
        raise ValueError('未知配送提醒类型，未生成文案')
    title, who, label, action = formats[kind]
    header = f"【{title}】订单 #{item['order_id']}"
    if kind in {'callback', 'case_due'}:
        header += f"｜异常单 #{item['case_id']}" if item.get('case_id') else '｜异常单编号未记录'
    return '\n'.join([header, f"{who}：{item.get('staff_name') or '待分配'}｜{label} {time_text(item.get('due_at'))}", action])


def archive_url(link):
    path = link.get('path', '')
    if not re.fullmatch(r'/d/[A-Za-z0-9_-]{16}', path):
        raise ValueError('订单通话短链接无效，未推送')
    return 'https://youshenghe666.com' + path


def analysis_message(case, url):
    report = case.get('analysis') or {}
    summary = report.get('summary') or ''
    lines = [f"【AI辅助复盘】订单 #{case['order_id']}｜异常单 #{case['id']}"]
    # A substring can lose a negation; use a whole-message fallback, not truncation.
    lines.append('初步判断：' + summary if isinstance(summary, str) and 0 < len(summary) <= 180
                 else '复盘已生成，请在配送管理中复核。')
    actions = [value for value in report.get('next_actions') or [] if isinstance(value, str) and value.strip()]
    if actions:
        lines.extend(['', '建议跟进：'])
        for index, action in enumerate(actions[:2], 1):
            lines.append(f'{index}. {action}' if len(action) <= 240 else f'{index}. 建议内容较长，请在配送管理中查看完整建议。')
        if len(actions) > 2:
            lines.append(f'另有 {len(actions) - 2} 条建议，请在配送管理中复核。')
    gaps = [value for value in report.get('evidence_gaps') or [] if isinstance(value, str) and value.strip()]
    if gaps:
        body = '；'.join(gaps)
        lines.extend(['', '待核对：' + (body if len(body) <= 240 else f'存在 {len(gaps)} 项证据缺口，请在配送管理中查看完整内容。')])
    lines.extend(['', 'AI 辅助意见，待人工核实。通话证据（需登录）：', url])
    return '\n'.join(lines)


class DeliveryNotificationStore:
    def __init__(self, path):
        self.path = path

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self):
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS config (
                    id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL DEFAULT 0,
                    target_groups TEXT NOT NULL DEFAULT '[]', activated_at TEXT);
                INSERT OR IGNORE INTO config(id) VALUES(1);
                CREATE TABLE IF NOT EXISTS deliveries (
                    event_key TEXT NOT NULL, room_id TEXT NOT NULL, room_name TEXT NOT NULL,
                    message TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
                    created_at TEXT NOT NULL, sent_at TEXT, error TEXT,
                    PRIMARY KEY(event_key, room_id));
                CREATE TABLE IF NOT EXISTS stage_reports (
                    report_key TEXT PRIMARY KEY, cutoff TEXT NOT NULL, status TEXT NOT NULL,
                    payload TEXT, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS call_reports (
                    event_key TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS call_report_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS call_quote_receipts (
                    event_key TEXT NOT NULL,room_id TEXT NOT NULL,status TEXT NOT NULL,account TEXT,
                    image_path TEXT NOT NULL,image_sha256 TEXT NOT NULL,reference_json TEXT,submitted_at TEXT,
                    evidence_json TEXT,error TEXT,next_check_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                    PRIMARY KEY(event_key,room_id));
            """)
            db.execute("INSERT OR IGNORE INTO call_report_meta(key,value) VALUES('enabled_since',?)", (now().isoformat(),))
            columns={r[1] for r in db.execute('PRAGMA table_info(config)')}
            for name,definition in {
                'scheduled_enabled':'INTEGER NOT NULL DEFAULT 0',
                'reminder_enabled':'INTEGER NOT NULL DEFAULT 0',
                'result_notifications_enabled':'INTEGER NOT NULL DEFAULT 0',
                'failure_notifications_enabled':'INTEGER NOT NULL DEFAULT 0',
                'failure_activated_at':'TEXT',
                'stage_times':'''TEXT NOT NULL DEFAULT '["12:00","16:00","18:30","21:00"]' ''',
                'first_call_minutes':'INTEGER NOT NULL DEFAULT 60',
                'automation_activated_at':'TEXT',
            }.items():
                if name not in columns:
                    db.execute(f'ALTER TABLE config ADD COLUMN {name} {definition}')
            for table, fields in {
                'stage_reports': {'media_type': "TEXT NOT NULL DEFAULT 'text'"},
                'deliveries': {'media_type': "TEXT NOT NULL DEFAULT 'text'", 'render_attempts': 'INTEGER NOT NULL DEFAULT 0', 'next_render_at': 'TEXT',
                               'text_status': 'TEXT', 'text_sent_at': 'TEXT'},
            }.items():
                existing = {row[1] for row in db.execute(f'PRAGMA table_info({table})')}
                for name, definition in fields.items():
                    if name not in existing:
                        db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')

    def configuration(self):
        with self.connect() as db:
            row = db.execute("SELECT * FROM config WHERE id=1").fetchone()
        return {"enabled": bool(row["enabled"]), "target_groups": json.loads(row["target_groups"]),
                "activated_at": row["activated_at"],
                'scheduled_enabled':bool(row['scheduled_enabled']), 'reminder_enabled':bool(row['reminder_enabled']),
                'result_notifications_enabled':bool(row['result_notifications_enabled']),
                'failure_notifications_enabled':bool(row['failure_notifications_enabled']),
                'failure_activated_at':row['failure_activated_at'],
                'stage_times':json.loads(row['stage_times']),'first_call_minutes':row['first_call_minutes'],
                'automation_activated_at':row['automation_activated_at']}

    def configure(self, enabled, groups, **policy):
        old = self.configuration()
        changed = old["enabled"] != enabled or {g["room_id"] for g in old["target_groups"]} != {g["room_id"] for g in groups}
        cutoff = now().isoformat() if changed and enabled else old["activated_at"]
        with self.connect() as db:
            db.execute("UPDATE config SET enabled=?,target_groups=?,activated_at=? WHERE id=1",
                       (enabled, json.dumps(groups, ensure_ascii=False), cutoff))
            if changed:
                db.execute("UPDATE deliveries SET status='cancelled' WHERE status IN ('queued','render_failed','quote_waiting')")
            effective={key:policy.get(key,old[key]) for key in ('scheduled_enabled','reminder_enabled','result_notifications_enabled','failure_notifications_enabled','stage_times','first_call_minutes')}
            policy_changed=any(effective[key]!=old[key] for key in effective) or {g['room_id'] for g in groups}!={g['room_id'] for g in old['target_groups']}
            if policy_changed:
                db.execute("UPDATE deliveries SET status='cancelled' WHERE status IN ('queued','render_failed') AND event_key NOT LIKE 'delivery.call:%'")
            failure_enabled = effective['failure_notifications_enabled']
            failure_cutoff = now().isoformat() if failure_enabled and (not old['failure_notifications_enabled'] or {g['room_id'] for g in groups} != {g['room_id'] for g in old['target_groups']}) else old['failure_activated_at']
            db.execute('UPDATE config SET failure_notifications_enabled=?,failure_activated_at=? WHERE id=1', (failure_enabled, failure_cutoff))
            db.execute('''UPDATE config SET scheduled_enabled=?,reminder_enabled=?,result_notifications_enabled=?,stage_times=?,
                first_call_minutes=?,automation_activated_at=? WHERE id=1''',
                (effective['scheduled_enabled'],effective['reminder_enabled'],effective['result_notifications_enabled'],
                 json.dumps(effective['stage_times']),effective['first_call_minutes'],
                 now().isoformat() if policy_changed else old['automation_activated_at']))
        return self.configuration()

    def stage(self,key):
        with self.connect() as db:
            row=db.execute('SELECT * FROM stage_reports WHERE report_key=?',(key,)).fetchone()
            return dict(row) if row else None

    def persist_stage(self,key,cutoff,payload,groups,message,media_type='text'):
        with self.connect() as db:
            inserted=db.execute('INSERT OR IGNORE INTO stage_reports(report_key,cutoff,status,payload,created_at,media_type) VALUES(?,?,?,?,?,?)',
                (key,cutoff.isoformat(),'ready',json.dumps(payload,ensure_ascii=False),now().isoformat(),media_type)).rowcount
            if inserted:
                for group in groups:
                    db.execute('INSERT OR IGNORE INTO deliveries(event_key,room_id,room_name,message,created_at,media_type) VALUES(?,?,?,?,?,?)',
                               (key,group['room_id'],group['name'],message,now().isoformat(),media_type))

    def missed_stage(self,key,cutoff):
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO stage_reports(report_key,cutoff,status,payload,created_at) VALUES(?,?,?,?,?)',(key,cutoff.isoformat(),'missed',None,now().isoformat()))

    def stages(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT report_key,cutoff,status,created_at,media_type FROM stage_reports ORDER BY cutoff DESC LIMIT 12')]

    def enqueue(self, event_key, groups, message):
        with self.connect() as db:
            for group in groups:
                db.execute("INSERT OR IGNORE INTO deliveries(event_key,room_id,room_name,message,created_at) VALUES(?,?,?,?,?)",
                           (event_key, group["room_id"], group["name"], message, now().isoformat()))

    def call_bundle_since(self):
        with self.connect() as db:
            return datetime.fromisoformat(db.execute("SELECT value FROM call_report_meta WHERE key='enabled_since'").fetchone()[0])

    def call_report(self, key):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM call_reports WHERE event_key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def call_targets(self, key, groups):
        with self.connect() as db:
            recorded = {r[0] for r in db.execute('SELECT room_id FROM deliveries WHERE event_key=?', (key,))}
        return [g for g in groups if g['room_id'] not in recorded]

    def enqueue_call(self, payload, groups):
        report_data(payload)
        key = payload['call']['event_key']
        with self.connect() as db:
            # Never retrofit images onto pre-existing text receipts, even for another room.
            legacy = db.execute("SELECT 1 FROM deliveries WHERE event_key=? AND media_type<>'call_bundle'", (key,)).fetchone()
            if legacy:
                return
            db.execute('INSERT OR IGNORE INTO call_reports VALUES(?,?,?)',
                       (key, json.dumps(payload, ensure_ascii=False), now().isoformat()))
            frozen = json.loads(db.execute('SELECT payload FROM call_reports WHERE event_key=?', (key,)).fetchone()[0])
            message = bundle_message(frozen)
            for group in groups:
                db.execute("""INSERT OR IGNORE INTO deliveries(event_key,room_id,room_name,message,created_at,media_type)
                    VALUES(?,?,?,?,?,'call_bundle')""", (key, group['room_id'], group['name'], message, now().isoformat()))

    def mark_call_text(self, row, status):
        with self.connect() as db:
            db.execute("UPDATE deliveries SET text_status=?,text_sent_at=? WHERE event_key=? AND room_id=? AND status='sending'",
                       (status, now().isoformat() if status == 'sent' else None, row['event_key'], row['room_id']))

    def update_pending_reminder(self, row, status, *, message=None, error=None):
        with self.connect() as db:
            return db.execute("""UPDATE deliveries SET status=?,error=?,message=COALESCE(?,message)
                WHERE event_key=? AND room_id=? AND status='queued' AND media_type='text'""",
                (status,error,message,row['event_key'],row['room_id'])).rowcount == 1

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE deliveries SET status='uncertain',error='进程中断，发送结果待核对，未自动重发' WHERE status='sending'")
            db.execute("UPDATE call_quote_receipts SET status='uncertain',error='发送中断，仅保留记录，不自动重发' WHERE status IN ('text_sending','image_sending')")

    def pending(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("""SELECT * FROM deliveries WHERE event_key NOT LIKE 'delivery.failure:%' AND
                (status='queued' OR (status='render_failed' AND render_attempts<3 AND next_render_at<=?))
                ORDER BY created_at LIMIT 50""", (now().isoformat(),))]

    def render_failed(self, row):
        with self.connect() as db:
            db.execute("""UPDATE deliveries SET status='render_failed',render_attempts=render_attempts+1,next_render_at=?,
                error='报表图片生成失败，未调用微信；最多自动尝试3次' WHERE event_key=? AND room_id=? AND status IN ('queued','render_failed')""",
                ((now()+timedelta(seconds=60)).isoformat(),row['event_key'],row['room_id']))

    def mark(self, row, status, error=None):
        with self.connect() as db:
            guard = " AND status IN ('queued','render_failed')" if status == "sending" else ""
            result = db.execute("UPDATE deliveries SET status=?,error=?,sent_at=? WHERE event_key=? AND room_id=?" + guard,
                                (status, error, now().isoformat() if status == "sent" else None, row["event_key"], row["room_id"]))
            return result.rowcount == 1

    def recent(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT event_key,room_id,room_name,status,created_at,sent_at,error,media_type,text_status FROM deliveries ORDER BY created_at DESC LIMIT 20")]


class DeliveryNotificationService:
    def __init__(self, store, bridge, fetch, send_interval=2.5, stage_images=None, call_images=None, quote_source=None):
        self.store, self.bridge, self.fetch = store, bridge, fetch
        self.lock = asyncio.Lock()
        self.stop_event = asyncio.Event()
        self.task = None
        self.last_error = None
        self.last_poll_at = None
        self.material_worker = None
        self.call_quotes = DeliveryCallQuotes(store, bridge, quote_source, lambda: now(), self.stop_event)
        self.send_interval = send_interval
        self.stage_images = stage_images or DeliveryStageImageRenderer(store.path.parent/'delivery_stage_reports',
            Path(__file__).resolve().parents[2]/'scripts/render-delivery-stage.cjs')
        self.call_images = call_images or DeliveryCallImageRenderer(store.path.parent/'delivery_call_reports',
            Path(__file__).resolve().parents[2]/'scripts/render-delivery-call.cjs')

    async def start(self):
        self.store.initialize()
        self.store.recover()
        self.stop_event.clear()
        if self.task is None:
            self.task = asyncio.create_task(self.run())

    async def stop(self):
        self.stop_event.set()
        if self.task:
            await self.task
            self.task = None
        await self.bridge.close()

    def status(self):
        self.store.initialize()
        waits = []
        with self.store.connect() as db:
            call_quotes = [dict(r) for r in db.execute('SELECT event_key,room_id,status,error,updated_at FROM call_quote_receipts ORDER BY updated_at DESC LIMIT 30')]
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='feedback_waits'").fetchone():
                waits = [dict(r) for r in db.execute("SELECT * FROM feedback_waits WHERE status NOT IN ('submitted','excluded') ORDER BY updated_at DESC LIMIT 50")]
        return {"config": self.store.configuration(), "recent": self.store.recent(),
                'stages':self.store.stages(),
                'feedback_wait': {'minutes': 10, 'items': waits},
                'call_quotes': call_quotes,
                "runtime": {"running": self.task is not None and not self.task.done(),
                            "last_poll_at": self.last_poll_at, "last_error": self.last_error}}

    async def configure(self, enabled, groups, **policy):
        old=self.store.configuration()
        effective={**old,**policy}
        times=effective['stage_times']
        if not times or len(times)>8 or len(set(times))!=len(times) or any(not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]',value) for value in times):
            raise ValueError('报表时点格式为 HH:MM，不可重复，最多 8 个')
        if not 5<=effective['first_call_minutes']<=1440:
            raise ValueError('首拨期限需为 5 到 1440 分钟')
        if 'stage_times' in policy:
            policy['stage_times']=sorted(times)
        active=enabled or any(effective[key] for key in ('scheduled_enabled','reminder_enabled','result_notifications_enabled','failure_notifications_enabled'))
        if active and not groups:
            raise ValueError("开启自动推送前至少选择一个微信群")
        # Disabling must remain possible even if the bridge is offline.
        if active:
            available = {group.room_id: group.name for group in await self.bridge.groups()}
            if any(group["room_id"] not in available for group in groups):
                raise ValueError("所选微信群不在当前微信联系人中，请刷新群列表")
            groups = [{"room_id": group["room_id"], "name": available[group["room_id"]]} for group in groups]
        async with self.lock:
            return self.store.configure(enabled, groups, **policy)

    async def drain(self):
        await self.call_quotes.resume()
        stage_images = {}
        for index, row in enumerate(self.store.pending()):
            if index and self.send_interval:
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=self.send_interval)
                except TimeoutError:
                    pass
            if self.stop_event.is_set():
                break
            if row['event_key'].startswith('delivery.failure:'):
                # Legacy unsent text must pass the same material gate as new feedback.
                self.store.update_pending_reminder(row, 'queued', error='等待已就绪录音长图，不单独发送反馈文字')
                continue
            if row.get('media_type') == 'call_bundle' and row.get('text_status') is not None:
                self.store.mark(row, 'uncertain', '文字已有提交记录，禁止重新提交图文；请核对群消息')
                continue
            image_path = None
            if row.get('media_type') in ('image', 'call_bundle'):
                try:
                    if row['event_key'] not in stage_images:
                        if row['media_type'] == 'call_bundle':
                            payload = self.store.call_report(row['event_key'])
                            if not payload:
                                raise ValueError('Call snapshot missing')
                            stage_images[row['event_key']] = await self.call_images.render(row['event_key'], payload)
                        else:
                            stage = self.store.stage(row['event_key'])
                            if not stage or stage['media_type'] != 'image' or not stage['payload']:
                                raise ValueError('Stage snapshot missing')
                            stage_images[row['event_key']] = await self.stage_images.render(row['event_key'], json.loads(stage['payload']))
                    image_path = stage_images[row['event_key']]
                    if image_path is None:
                        raise ValueError('Stage render already failed this pass')
                    if not Path(image_path).is_file():
                        raise ValueError('Stage image missing')
                except Exception:
                    stage_images[row['event_key']] = None
                    self.store.render_failed(row)
                    continue
            if self.stop_event.is_set():
                break
            if row.get('media_type') == 'call_bundle' and image_path is not None:
                await self.call_quotes.send(row, image_path)
                continue
            if row['event_key'].startswith('delivery.first_call_overdue:'):
                config = self.store.configuration()
                if not config['reminder_enabled'] or row['room_id'] not in {g['room_id'] for g in config['target_groups']}:
                    self.store.update_pending_reminder(row, 'cancelled', error='首拨提醒已关闭或目标群已变更，未发送')
                    continue
                try:
                    assignment_id = int(row['event_key'].split(':',1)[1])
                    check = await self.fetch(f'/alerts/first-call/{assignment_id}', {'first_call_minutes': config['first_call_minutes']})
                    if check.get('assignment_id') != assignment_id or type(check.get('eligible')) is not bool:
                        raise ValueError('Invalid reminder recheck')
                    if check['eligible'] is False:
                        self.store.update_pending_reminder(row, 'cancelled', error='发送前复查已不满足首拨提醒条件，未发送')
                        continue
                    item = check.get('item') or {}
                    due = timestamp(item.get('due_at'))
                    activated = timestamp(config.get('automation_activated_at'))
                    if item.get('event_key') != row['event_key'] or item.get('kind') != 'first_call' or not due or not timestamp(item.get('assigned_at')):
                        raise ValueError('Reminder evidence missing')
                    if not activated or due < activated or due >= now():
                        self.store.update_pending_reminder(row, 'cancelled', error='首拨提醒时间已不符合当前设置，未发送')
                        continue
                    row['message'] = reminder_message(item, at=now())
                except Exception:
                    # No bridge call occurred: preserve the queue for a later read-only recheck.
                    self.store.update_pending_reminder(row, 'queued', error='首拨发送前复查暂不可用，暂缓发送，下轮重试')
                    continue
                if self.stop_event.is_set() or not self.store.update_pending_reminder(row, 'sending', message=row['message']):
                    continue
            elif not self.store.mark(row, "sending"):
                continue
            try:
                if image_path:
                    await self.bridge._post('/api/sendImagesMsg', {'wxid':row['room_id'],'imagePath':str(Path(image_path).resolve())})
                else:
                    await self.bridge.send_text(row["room_id"], row["message"])
            except Exception:
                # The bridge may have sent before the response was lost. Never blindly retry.
                self.store.mark(row, "uncertain", "微信发送失败或回执不明确，请核对群消息；未自动重发")
            else:
                self.store.mark(row, "sent")

    async def poll_once(self):
        async with self.lock:
            config = self.store.configuration()
            if not config["enabled"] or not config["target_groups"] or not config["activated_at"]:
                return
            cutoff = datetime.fromisoformat(config["activated_at"])
            bundle_cutoff = self.store.call_bundle_since()
            current = now()
            report_failed = False
            # Re-scan a bounded overlap, including yesterday to cover midnight ingestion delay.
            for day in (current.date()-timedelta(days=1), current.date()):
                if self.stop_event.is_set():
                    return
                if day < cutoff.astimezone(BEIJING).date():
                    continue
                cursor = None
                for _ in range(100):
                    if self.stop_event.is_set():
                        return
                    page = await self.fetch("/calls", {"day": day.isoformat(), "limit": 200, "before_id": cursor})
                    for call in page["items"]:
                        when = datetime.fromisoformat(call["call_time"])
                        if call.get("match_status") != "matched" or not call.get("order_id") or not cutoff <= when <= current:
                            continue
                        if when < bundle_cutoff:
                            continue
                        targets = self.store.call_targets(call['event_key'], config['target_groups'])
                        if not targets:
                            continue
                        try:
                            payload = self.store.call_report(call['event_key'])
                            if payload is None:
                                payload = await self.fetch(f"/calls/{call['id']}/monthly-report", {'day': day.isoformat()})
                            context = payload.get('call') or {}
                            if any(context.get(k) != call.get(k) for k in ('id', 'event_key', 'order_id', 'staff_id', 'call_time')):
                                raise ValueError('配送通话统计关联发生变化，待核对')
                            self.store.enqueue_call(payload, targets)
                        except Exception:
                            report_failed = True
                    if not page["has_more"]:
                        break
                    next_cursor = page["next_before_id"]
                    if not next_cursor or next_cursor == cursor:
                        raise ValueError("配送通话分页游标异常")
                    cursor = next_cursor
                else:
                    raise ValueError("配送通话超过单次安全扫描上限，待人工核查")
            self.last_poll_at = now().isoformat()
            await self.drain()
            if report_failed:
                raise ValueError('部分配送通话统计暂不可用，未发送；下次轮询重试')

    async def poll_automation(self):
        async with self.lock:
            config=self.store.configuration()
            if not config['target_groups'] or not config['automation_activated_at']:
                return
            activated=datetime.fromisoformat(config['automation_activated_at'])
            current=now()
            if config['scheduled_enabled']:
                previous=current.replace(hour=0,minute=0,second=0,microsecond=0)
                for slot in sorted(config['stage_times']):
                    hour,minute=map(int,slot.split(':'))
                    cutoff=current.replace(hour=hour,minute=minute,second=0,microsecond=0)
                    start=previous
                    previous=cutoff
                    key=f'delivery.stage:{current.date()}:{slot}'
                    if cutoff<=activated or cutoff>current or self.store.stage(key):
                        continue
                    if current-cutoff>timedelta(minutes=30):
                        self.store.missed_stage(key,cutoff)
                        continue
                    params={'day':str(current.date()),'at':cutoff.isoformat()}
                    cumulative=await self.fetch('/overview',params)
                    period=await self.fetch('/overview',{**params,'since':start.isoformat()})
                    payload={'cumulative':cumulative,'period':period}
                    self.store.persist_stage(key,cutoff,payload,config['target_groups'],stage_message(slot,cumulative,period),media_type='image')
            if config['reminder_enabled']:
                result=await self.fetch('/alerts',{'first_call_minutes':config['first_call_minutes']})
                for item in result['items']:
                    if datetime.fromisoformat(item['due_at'])<activated:
                        continue
                    message = reminder_message(item)
                    self.store.enqueue(item['event_key'],config['target_groups'],message)
            if config['failure_notifications_enabled'] and config['failure_activated_at']:
                failure_cutoff = datetime.fromisoformat(config['failure_activated_at'])
                cursor = None
                for _ in range(100):
                    page = await self.fetch('/cases', {'limit': 100, 'before_id': cursor})
                    for case in page['items']:
                        if datetime.fromisoformat(case['created_at']) < failure_cutoff:
                            continue
                        key = f"delivery.failure:{case['id']}"
                        with self.store.connect() as db:
                            recorded = {r[0] for r in db.execute("SELECT room_id FROM deliveries WHERE event_key=? AND status!='queued'", (key,))}
                        targets = [g for g in config['target_groups'] if g['room_id'] not in recorded]
                        if not targets:
                            continue
                        if self.material_worker is None:
                            continue
                        try:
                            detail = await self.fetch(f"/cases/{case['id']}", {})
                            await self.material_worker.send_primary(detail, targets)
                        except Exception as exc:
                            self.material_worker.record_feedback_wait(case, 'error', '资料准备失败，未发送：' + type(exc).__name__)
                    if not page['has_more']:
                        break
                    if not page['next_before_id'] or page['next_before_id'] == cursor:
                        raise ValueError('配送反馈分页异常')
                    cursor = page['next_before_id']
            if config['result_notifications_enabled']:
                cursor=None
                for _ in range(100):
                    page=await self.fetch('/cases',{'limit':100,'before_id':cursor})
                    for case in page['items']:
                        if case['analysis_status']!='succeeded' or datetime.fromisoformat(case['created_at'])<activated:
                            continue
                        report=case.get('analysis') or {}
                        if report.get('validation_status')=='partial' and case.get('review_status')!='confirmed':
                            continue
                        key = f"delivery.analysis:{case['id']}:{report.get('analysis_run_id', 'initial')}"
                        with self.store.connect() as db:
                            recorded = {row[0] for row in db.execute('SELECT room_id FROM deliveries WHERE event_key=?', (key,))}
                        targets = [group for group in config['target_groups'] if group['room_id'] not in recorded]
                        if not targets:
                            continue
                        link = await self.fetch(f"/cases/{case['id']}/archive-link", {}, method='POST', payload={})
                        self.store.enqueue(key, targets, analysis_message(case, archive_url(link)))
                    if not page['has_more']:
                        break
                    if not page['next_before_id'] or page['next_before_id']==cursor:
                        raise ValueError('异常单分页异常')
                    cursor=page['next_before_id']
            await self.drain()

    async def send_overview(self, day, request_id):
        async with self.lock:
            config = self.store.configuration()
            if not config["target_groups"]:
                raise ValueError("请先保存配送推送目标群")
            available = {group.room_id for group in await self.bridge.groups()}
            if any(group["room_id"] not in available for group in config["target_groups"]):
                raise ValueError("目标群已不可用，请刷新并重新保存群设置")
            data = await self.fetch("/overview", {"day": day.isoformat()})
            self.store.enqueue(f"delivery.overview:{day}:{request_id}", config["target_groups"], overview_message(data))
            # Return promptly; the worker sends durable jobs even if automatic calls are disabled.
            return self.status()

    async def run(self):
        while not self.stop_event.is_set():
            try:
                async with self.lock:
                    await self.drain()
                await self.poll_once()
                await self.poll_automation()
                self.last_error = None
            except Exception:
                self.last_error = "配送通知取数或处理失败，将在下次轮询重试；请检查连接及报表记录"
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=30)
            except TimeoutError:
                pass


def stage_message(slot,cumulative,period):
    by_id={row['staff_id']:row for row in period['items']}
    lines=[f"【配送阶段报表｜{date_label(cumulative.get('date'))} {slot}】",
           '本阶段 ' + interval_text(period.get('since'), period.get('snapshot_at'))
           + '｜今日累计截至 ' + time_text(cumulative.get('snapshot_at'), clock=True)]
    for row in cumulative['items']:
        delta=by_id.get(row['staff_id'])
        label = row['staff_name'] + ('｜暂无通话记录' if count(row.get('call_count')) == 0 else '')
        lines.extend(['', label,
            '本阶段：' + (call_metrics(delta, short=True) if delta is not None else '数据暂缺，暂不计算阶段变化。'),
            '今日累计：' + call_metrics(row, short=True)])
    lines.append('\n“号”指去重电话号码，按配送员分别统计，跨人及跨阶段不累加。')
    return '\n'.join(lines)
