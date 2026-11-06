from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import secrets

from sqlalchemy import or_, select

from .db import Event, Receipt, Task, User, now, uid
from .errors import require



ACTIVE = {'open', 'in_progress', 'in_review', 'changes_requested'}


def owner(actor):
    require(actor.owner, 'owner_required', '仅所有者可以执行此操作。', 403)


class Tasks:
    def __init__(self, database, settings, auth):
        self.db, self.settings, self.auth = database, settings, auth

    def visible(self, db, actor, task_id):
        task = db.get(Task, task_id)
        require(task is not None and (task.status != 'draft' or actor.owner or task.created_by == actor.id),
                'not_found', '任务不存在。', 404)
        return task

    def serialize(self, db, task):
        lead = db.get(User, task.lead_id) if task.lead_id else None
        data = deepcopy(task.data)
        data = {k: v for k, v in data.items() if k not in ('pulls', 'submissions', 'sync_error', 'last_synced_at')}
        data.setdefault('deliveries', [])
        return {'id': task.id, 'number': f'TASK-{task.number:04}', 'title': task.title,
                'month': task.month, 'status': task.status, 'created_by': task.created_by, 'lead_id': task.lead_id,
                'lead_unavailable': bool(lead and (lead.disabled or not (lead.password_hash or lead.git_email))),
                'version': task.version, 'created_at': task.created_at, 'updated_at': task.updated_at,
                'completed_at': task.completed_at, **data}

    def snapshot(self, actor, *, month='', mine='', status='', cursor=0, limit=30):
        with self.db.transaction() as db:
            # Reads share this short project lock to pair the snapshot with an exact committed cursor.
            project = self.db.lock(db)
            self.auth.guard(db, actor)
            query = select(Task).order_by(Task.number.desc())
            if not actor.owner:
                query = query.where(or_(Task.status != 'draft', Task.created_by == actor.id))
            if cursor:
                query = query.where(Task.number < cursor)
            if month:
                query = query.where(Task.month == month)
            if status:
                query = query.where(Task.status == status)
            if mine == 'lead':
                query = query.where(Task.lead_id == actor.id)
            elif mine == 'created':
                query = query.where(Task.created_by == actor.id)
            elif mine == 'joined':
                query = query.where(Task.data['members'][actor.id]['active'].as_boolean().is_(True))
            elif mine == 'outstanding':
                current_month = datetime.fromtimestamp(now() + 8 * 3600, tz=timezone.utc).strftime('%Y-%m')
                query = query.where(Task.month < current_month, Task.status.in_(ACTIVE))
            rows = db.scalars(query.limit(limit + 1)).all()
            return {'items': [self.serialize(db, t) for t in rows[:limit]], 'event_cursor': project.sequence,
                    'next_cursor': rows[limit - 1].number if len(rows) > limit else None}

    def detail(self, actor, task_id):
        with self.db.transaction() as db:
            self.db.lock(db)
            self.auth.guard(db, actor)
            return self.serialize(db, self.visible(db, actor, task_id))

    def members(self, actor):
        with self.db.transaction() as db:
            self.auth.guard(db, actor)
            return [{'id': u.id, 'name': u.git_email or u.name, 'login': u.git_email or u.login, 'disabled': u.disabled,
                     'has_account': bool(u.password_hash or u.git_email)} for u in db.scalars(select(User).order_by(User.name))]

    def receipt(self, db, actor, key, request):
        require(key and len(key) <= 100, 'idempotency_required', '操作缺少幂等标识。', 400)
        key = actor.id + ':' + key
        fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        previous = db.get(Receipt, key)
        if previous:
            require(secrets.compare_digest(previous.request_hash, fingerprint), 'idempotency_conflict', '操作标识已用于不同请求。', 409)
            if previous.task_id:
                self.visible(db, actor, previous.task_id)
            return key, fingerprint, previous.response
        return key, fingerprint, None

    def save_result(self, db, key, fingerprint, result):
        db.add(Receipt(key=key, request_hash=fingerprint, task_id=result['id'], response=result))
        return result

    def create(self, actor, fields, key):
        with self.db.transaction() as db:
            project = self.db.lock(db)
            self.auth.guard(db, actor)
            key, fingerprint, prior = self.receipt(db, actor, key, {'create': fields})
            if prior:
                return prior
            task = self.new_task(db, project, fields, actor.id)
            self.db.event(db, actor.id, 'task.created', task)
            return self.save_result(db, key, fingerprint, self.serialize(db, task))

    def new_task(self, db, project, fields, created_by):
        fields = deepcopy(fields)
        task = Task(number=project.sequence + 1, title=fields.pop('title'), month=fields.pop('month'), created_by=created_by,
                    data={**fields, 'scope_revision': 1, 'members': {}, 'work_items': [], 'comments': [],
                          'deliveries': [], 'transfer': None})
        db.add(task)
        db.flush()
        return task

    def check_lead(self, actor, task):
        require(actor.owner or task.lead_id == actor.id, 'lead_required', '仅负责人或所有者可以操作。', 403)

    def check_creator(self, actor, task):
        require(actor.owner or task.created_by == actor.id, 'creator_required', '仅创建人或所有者可以操作。', 403)

    def is_member(self, actor, data):
        return actor.owner or bool(data['members'].get(actor.id, {}).get('active'))

    def active_round(self, data):
        return next((r for r in reversed(data.get('deliveries', [])) if r['state'] == 'active'), None)

    def target(self, db, user_id):
        target = db.get(User, user_id)
        require(target is not None and not target.disabled and (target.password_hash or target.git_email),
                'invalid_member', '目标成员不可用，请先填写邮箱或启用成员。')
        return target

    def join(self, data, user_id):
        previous = data['members'].get(user_id)
        require(previous is not None or len(data['members']) < 200, 'member_limit', '任务成员已达到上限。')
        data['members'][user_id] = {**(previous or {'joined_at': now()}), 'active': True, 'left_at': None}

    def mutate(self, actor, task_id, action, body, key):
        with self.db.transaction() as db:
            project = self.db.lock(db)
            self.auth.guard(db, actor)
            task = self.visible(db, actor, task_id)
            key, fingerprint, prior = self.receipt(db, actor, key, {'task': task_id, 'action': action, **body})
            if prior:
                return prior
            require(task.version == body['expected_version'], 'version_conflict', '任务已被更新，请刷新后重试。', 409)
            data = deepcopy(task.data)
            data.setdefault('deliveries', [])
            current = self.active_round(data)
            reason = body.get('reason', '')
            active = task.status in ACTIVE
            target_id = body.get('target_id', '')
            text = body.get('text', '')
            detail = {'reason': reason} if reason else {}
            if action == 'edit':
                self.check_creator(actor, task)
                require(not current and task.status not in ('done', 'cancelled'), 'scope_locked', '请先撤回验收或重新打开任务。')
                before = {k: data[k] for k in ('objective', 'criteria', 'deadline', 'priority')}
                detail = {'before': {**before, 'title': task.title, 'month': task.month}}
                task.title, task.month = body['title'], body['month']
                for k in ('objective', 'criteria', 'deadline', 'priority'):
                    data[k] = body[k]
                data['scope_revision'] += 1
            elif action == 'publish':
                self.check_creator(actor, task)
                require(task.status == 'draft', 'invalid_state', '仅草稿可以发布。')
                task.status = 'in_progress' if task.lead_id else 'open'
            elif action == 'copy':
                owner(actor)
                require(body.get('month'), 'month_required', '请选择月份。')
                fields = {k: data[k] for k in ('objective', 'criteria', 'deadline', 'priority')}
                task = self.new_task(db, project, {**fields, 'title': task.title, 'month': body['month']}, actor.id)
                data = deepcopy(task.data)
            elif action in ('cancel', 'reopen'):
                owner(actor)
                require(reason, 'reason_required', '请填写原因。')
                if action == 'cancel':
                    require(task.status not in ('done', 'cancelled'), 'invalid_state', '该任务不能取消。')
                    task.status = 'cancelled'
                else:
                    require(task.status in ('done', 'cancelled'), 'invalid_state', '仅已完成或已取消任务可重新打开。')
                    task.status = 'in_progress' if task.lead_id else 'open'
                    task.completed_at = None
                if current:
                    current.update(state='withdrawn', ended_at=now(), reason=reason)
                data['transfer'] = None
            else:
                require(active, 'task_not_active', '任务当前不可操作。')
                if action == 'claim':
                    require(task.lead_id is None and task.status == 'open', 'already_claimed', '任务已有负责人。', 409)
                    task.lead_id, task.status = actor.id, 'in_progress'
                    self.join(data, actor.id)
                elif action == 'join':
                    self.join(data, actor.id)
                elif action == 'leave':
                    require(task.lead_id != actor.id, 'lead_cannot_leave', '请先完成负责人转交。')
                    require(data['members'].get(actor.id, {}).get('active'), 'not_member', '你不是当前协作者。')
                    data['members'][actor.id].update(active=False, left_at=now())
                elif action in ('assign-owner', 'owner-transfer'):
                    self.check_lead(actor, task)
                    self.target(db, target_id)
                    require(target_id != task.lead_id, 'same_lead', '目标成员已是负责人。')
                    if action == 'assign-owner':
                        owner(actor)
                        require(reason, 'reason_required', '请填写重新指派原因。')
                        task.lead_id, data['transfer'] = target_id, None
                        self.join(data, target_id)
                        if task.status == 'open':
                            task.status = 'in_progress'
                    else:
                        require(task.lead_id and not data['transfer'], 'transfer_pending', '无负责人或已有待接受转交。')
                        data['transfer'] = {'from': task.lead_id, 'to': target_id, 'at': now()}
                    detail.update(target_id=target_id)
                elif action in ('accept-transfer', 'cancel-transfer'):
                    transfer = data['transfer']
                    require(transfer is not None, 'no_transfer', '没有待处理转交。')
                    if action == 'accept-transfer':
                        require(actor.id == transfer['to'], 'transfer_target', '仅接收人可接受转交。', 403)
                        task.lead_id = actor.id
                        self.join(data, actor.id)
                    else:
                        self.check_lead(actor, task)
                    detail.update(transfer=transfer)
                    data['transfer'] = None
                elif action == 'add-work':
                    self.check_lead(actor, task)
                    self.target(db, target_id)
                    require(data['members'].get(target_id, {}).get('active'), 'not_member', '请先加入任务协作。')
                    require(body.get('title') and len(data['work_items']) < 200, 'work_limit', '请填写分工标题，最多 200 项。')
                    data['work_items'].append({'id': uid(), 'title': body['title'], 'assignee': target_id, 'state': 'todo'})
                elif action == 'update-work':
                    work = next((w for w in data['work_items'] if w['id'] == body.get('item_id')), None)
                    require(work is not None, 'not_found', '分工不存在。', 404)
                    require(actor.owner or task.lead_id == actor.id or
                            (work['assignee'] == actor.id and self.is_member(actor, data)),
                            'work_permission', '不能修改其他成员的分工进度。', 403)
                    work['state'] = body['work_state']
                elif action == 'comment':
                    require(text and len(data['comments']) < 1000, 'comment_limit', '请填写讨论内容，最多 1000 条。')
                    data['comments'].append({'id': uid(), 'author': actor.id, 'text': text, 'at': now()})
                elif action == 'submit':
                    self.check_lead(actor, task)
                    self.target(db, task.lead_id)
                    require(text and body.get('pr_links'), 'delivery_required', '请填写成果说明和至少一个 PR 链接。')
                    require(current is None, 'already_submitted', '成果正在等待确认，请先撤回再修改。', 409)
                    require(len(data['deliveries']) < 100, 'delivery_limit', '成果记录达到上限。')
                    data['deliveries'].append({'id': uid(), 'state': 'active', 'author': actor.id,
                        'summary': text, 'pr_links': body['pr_links'], 'at': now()})
                    task.status = 'in_review'
                elif action in ('complete', 'request-changes'):
                    owner(actor)
                    require(current is not None and task.status == 'in_review', 'no_delivery', '当前没有待确认成果。', 409)
                    if action == 'request-changes':
                        require(reason, 'reason_required', '请填写退回原因。')
                    current.update(state='accepted' if action == 'complete' else 'rejected',
                                   reviewed_by=actor.id, reviewed_at=now(), reason=reason)
                    task.status = 'done' if action == 'complete' else 'changes_requested'
                    task.completed_at = now() if action == 'complete' else None
                    detail['delivery_id'] = current['id']
                elif action == 'withdraw':
                    self.check_lead(actor, task)
                    require(current is not None and reason, 'withdraw_reason', '请填写撤回原因。')
                    current.update(state='withdrawn', ended_at=now(), reason=reason)
                    task.status = 'in_progress'
                else:
                    require(False, 'unknown_action', '不支持此操作。', 404)
            task.data = data
            task.version += 1
            task.updated_at = now()
            detail['version'] = task.version
            self.db.event(db, actor.id, 'task.' + action, task, detail)
            return self.save_result(db, key, fingerprint, self.serialize(db, task))

    def events(self, actor, after=0, task_id=None):
        with self.db.transaction() as db:
            self.db.lock(db)
            self.auth.guard(db, actor)
            if task_id:
                self.visible(db, actor, task_id)
            query = select(Event).where(Event.id > after).order_by(Event.id).limit(200)
            if task_id:
                query = query.where(Event.task_id == task_id)
            if not actor.owner:
                query = query.where(or_(Event.owner_only.is_(False), Event.task_id.in_(
                    select(Task.id).where(Task.created_by == actor.id))))
            return [{'id': e.id, 'task_id': e.task_id, 'actor': e.actor, 'action': e.action,
                     'at': e.at, 'detail': e.detail} for e in db.scalars(query)]

    def activity(self, actor, task_id, before=0):
        with self.db.transaction() as db:
            self.db.lock(db)
            self.auth.guard(db, actor)
            self.visible(db, actor, task_id)
            query = select(Event).where(Event.task_id == task_id).order_by(Event.id.desc())
            if before:
                query = query.where(Event.id < before)
            if not actor.owner:
                query = query.where(or_(Event.owner_only.is_(False), Event.task_id.in_(
                    select(Task.id).where(Task.created_by == actor.id))))
            rows = db.scalars(query.limit(51)).all()
            return {'items': [{'id': e.id, 'actor': e.actor, 'action': e.action, 'at': e.at,
                              'detail': e.detail} for e in rows[:50]],
                    'next_cursor': rows[49].id if len(rows) > 50 else None}
