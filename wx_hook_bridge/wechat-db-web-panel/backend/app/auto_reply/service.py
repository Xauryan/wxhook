import asyncio
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException

from ..ai.errors import AIError
from ..friend_automation import _AutomationLeaderLock
from ..group_reports.source import GroupMessageSource
from ..live_chat import SendText
from .context import ConversationContext
from .engine import candidate_reason
from .models import ReplyConfig
from .store import ReplyStore
from .prompts import PROMPTS


class GroupReplyService:
    def __init__(self, live, ai, engine, root, cases_path=None):
        self.live, self.ai, self.engine = live, ai, engine
        self.store = ReplyStore(root)
        self.contexts = ConversationContext(live)
        self.groups = GroupMessageSource(live)
        self.cases_path = Path(cases_path) if cases_path else None
        self._leader = _AutomationLeaderLock(Path(root) / '.worker.lock')
        self._tasks = []
        self._cursor = 0
        self.error = None

    async def status(self):
        profile = await self.live.profile()
        account = profile.get('username') or ''
        return {'account': account, 'profile': profile, 'rules': self.store.rules(account),
                'batches': self.store.batches(account), 'recent': self.store.recent(account),
                'prompts': PROMPTS, 'running': bool(self._tasks and all(not task.done() for task in self._tasks)), 'error': self.error}

    async def save_rule(self, edit):
        group, _ = await self.groups.verify_group(edit.account, edit.room)
        answer_config, _, _ = await asyncio.to_thread(self.ai.store.get, edit.config.ai_profile_id)
        if answer_config.decision_only:
            raise HTTPException(422, '请选择能够生成文本的回答模型')
        for ident in edit.config.decision_profile_ids:
            try:
                await asyncio.to_thread(self.ai.store.get, ident)
            except AIError as error:
                if error.status_code != 409:
                    raise
        for ident in [edit.config.planning_profile_id, edit.config.review_profile_id, edit.config.search_profile_id]:
            if ident:
                stage_config, _, _ = await asyncio.to_thread(self.ai.store.get, ident)
                if stage_config.decision_only:
                    raise HTTPException(422, '规划、核验和搜索阶段需要文本模型')
        with self.live.db() as db:
            sequence = db.execute('SELECT coalesce(max(sequence),0) FROM live_messages').fetchone()[0]
        return self.store.save_rule(edit, group['name'], sequence)

    def cases(self, account, room):
        if not self.cases_path or not self.cases_path.is_file():
            return []
        data = json.loads(self.cases_path.read_text(encoding='utf-8-sig'))
        result = []
        for category in data.get('categories', []):
            for question in category.get('questions', []):
                evidence = question.get('evidence') or []
                if not evidence:
                    continue
                ident = evidence[0]['message_id']
                try:
                    row = self.contexts.message(account, room, ident)
                except HTTPException:
                    continue
                result.append({'message_id': ident, 'category': category['category'],
                               'label': question['normalized_question'], 'text': row['message'].get('content') or '',
                               'timestamp': row['timestamp']})
        return result

    async def profile_revisions(self, config):
        decisions = getattr(self.engine, 'decisions', None)
        if decisions is None:
            return {}
        profile_ids = list(config.decision_profile_ids)
        for ident in [config.planning_profile_id, config.review_profile_id, config.search_profile_id]:
            if ident:
                profile_ids.append(ident)
        return await asyncio.to_thread(decisions.revisions, profile_ids)

    async def replay(self, payload):
        await self.groups.verify_group(payload.account, payload.room)
        rule = self.store.get_rule(payload.account, payload.room)
        if rule is None:
            raise HTTPException(409, '请先保存该群的问答配置')
        _, _, model_revision = await asyncio.to_thread(self.ai.store.get, rule['config']['ai_profile_id'])
        config = ReplyConfig.model_validate(rule['config'])
        profile_revisions = await self.profile_revisions(config)
        cases = {}
        for ident in dict.fromkeys(payload.message_ids):
            for trigger in dict.fromkeys(payload.trigger_modes):
                context, checksum = await asyncio.to_thread(self.contexts.load,
                    payload.account, payload.room, ident, config, ask_prefix=trigger == 'ask')
                for mode in dict.fromkeys(payload.search_modes):
                    selected = {**rule, 'config': {**rule['config'], 'search_mode': mode}}
                    cases[(mode, checksum)] = {'rule': selected, 'context': context,
                                              'context_hash': checksum, 'model_revision': model_revision, 'profile_revisions': profile_revisions}
        ordered = sorted(cases.values(), key=lambda case: (case['context']['at'], case['context']['current']['message_id']))
        fingerprint = hashlib.sha256(json.dumps({'request': payload.model_dump(), 'rule': rule,
            'model_revision': model_revision, 'profile_revisions': profile_revisions, 'contexts': sorted(cases)}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        ident = self.store.create_batch(payload.account + ':' + payload.request_id, fingerprint, rule, ordered)
        return self.store.batch(ident)

    async def poll_live(self):
        with self.live.db() as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM live_messages WHERE sequence>? ORDER BY sequence LIMIT 100', (self._cursor,))]
        for row in rows:
            rule = self.store.get_rule(row['account'], row['session'])
            if not rule or not rule['config']['enabled'] or row['sequence'] <= rule['enabled_after_seq']:
                self._cursor = row['sequence']
                continue
            ident = self.contexts.from_event(row['account'], row['session'], row['event_id'])
            if not ident:
                if row['message'] and time.time() - row['timestamp'] < 30:
                    # The durable event is written just before the canonical message transaction.
                    break
                self._cursor = row['sequence']
                continue
            config = ReplyConfig.model_validate(rule['config'])
            context, checksum = await asyncio.to_thread(self.contexts.load, row['account'], row['session'], ident, config)
            if not candidate_reason(context['current'], config):
                try:
                    _, _, revision = await asyncio.to_thread(self.ai.store.get, config.ai_profile_id)
                except AIError:
                    self._cursor = row['sequence']
                    continue
                profile_revisions = await self.profile_revisions(config)
                with self.store.db() as db:
                    self.store.enqueue(db, key=f'live:{row["account"]}:{row["event_id"]}', rule=rule,
                        context=context, context_hash=checksum, model_revision=revision, origin='live',
                        profile_revisions=profile_revisions)
            self._cursor = row['sequence']

    async def _deliver(self, run, result):
        if run['origin'] != 'live' or not result['should_reply']:
            return
        current = self.store.get_rule(run['account'], run['room'])
        if not current or not current['config']['enabled'] or current['revision'] != run['rule_revision']:
            self.store.delivery(run['id'], 'cancelled', '配置已关闭或改变')
            return
        await self.groups.verify_group(run['account'], run['room'])
        if not self.store.reserve_delivery(run['id']):
            return
        status, detail = 'unknown', '发送结果待核对'
        try:
            receipt = await self.live.send(SendText(account=run['account'], username=run['room'],
                text=result['answer'], request_id=uuid5(NAMESPACE_URL, 'group-reply:' + run['id'])))
            status, detail = receipt['status'], receipt['detail']
        finally:
            self.store.delivery(run['id'], status, detail)

    async def execute(self, run):
        config = ReplyConfig.model_validate_json(run['config'])
        context = json.loads(run['context'])
        result = json.loads(run['result']) if run.get('result') else None
        try:
            if run['origin'] == 'live':
                await self.live.check_account(run['account'], force=True)
                if run['trigger_mode'] != 'ask' and time.time() - run['trigger_at'] > config.live_expiry_seconds:
                    result = {'should_reply': False, 'answer': '', 'reason': 'expired', 'detail': '消息已超过实时回复时限'}
            if result is None:
                result = await asyncio.wait_for(self.engine.evaluate(context, config,
                    model_revision=run['model_revision'], last_reply_at=self.store.last_reply(run),
                    profile_revisions=json.loads(run.get('profile_revisions') or '{}')), timeout=config.processing_timeout_seconds)
            self.store.save_result(run['id'], result)
            await self._deliver(run, result)
        except asyncio.CancelledError:
            raise
        except Exception:
            if result is None:
                forced = run['trigger_mode'] == 'ask'
                result = {'should_reply': forced, 'answer': '这次查询暂时失败，请稍后再试。' if forced else '',
                          'reason': 'error', 'detail': '问答流程暂时不可用'}
                self.store.save_result(run['id'], result)
                try:
                    await self._deliver(run, result)
                except Exception:
                    self.store.delivery(run['id'], 'failed', '当前微信连接暂不可用')
            else:
                latest = self.store.run(run['id'])
                if not latest['delivery_status']:
                    self.store.delivery(run['id'], 'failed', '当前微信连接或群聊状态暂不可用')
        self.store.complete(run['id'])

    async def start(self):
        if not self._leader.acquire():
            self.error = '已有群聊问答执行器运行'
            return
        self.store.recover()
        with self.live.db() as db:
            self._cursor = db.execute('SELECT coalesce(max(sequence),0) FROM live_messages').fetchone()[0]

        async def observe():
            while True:
                try:
                    await self.poll_live()
                    self.error = None
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.error = '当前消息读取暂不可用，将继续检查'
                await asyncio.sleep(2)

        async def work():
            while True:
                run = self.store.claim()
                if run:
                    await self.execute(run)
                else:
                    await asyncio.sleep(.5)

        self._tasks = [asyncio.create_task(observe()), *(asyncio.create_task(work()) for _ in range(3))]

    async def stop(self):
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        self._leader.release()
