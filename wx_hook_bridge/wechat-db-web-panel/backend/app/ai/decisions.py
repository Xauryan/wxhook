"""Provider-neutral classification with ordered failure recovery."""
import asyncio
import json
import math
import time
from urllib.parse import urlsplit
from urllib.request import getproxies, proxy_bypass

import httpx

from .errors import AIError
from .models import ChatMessage


class DecisionUnavailable(AIError):
    def __init__(self, attempts):
        super().__init__('配置的决策服务均暂不可用')
        self.attempts = attempts


def normalize_choice(answer, choices):
    if not isinstance(answer, dict) or answer.get('type', 'choice') != 'choice':
        raise AIError('决策接口未返回有效分类')
    probabilities = answer.get('probabilities')
    if isinstance(probabilities, list):
        probabilities = {row['value']: row['probability'] for row in probabilities}
    if not isinstance(probabilities, dict) or set(probabilities) != set(choices):
        raise AIError('决策返回的候选项不匹配')
    if any(type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= 1
           for value in probabilities.values()):
        raise AIError('决策概率格式无效')
    if not .98 <= sum(probabilities.values()) <= 1.02:
        raise AIError('决策概率总和无效')
    selected = answer.get('choice')
    if selected not in choices or probabilities[selected] < max(probabilities.values()) - .001:
        raise AIError('决策选项与概率不一致')
    return {'choice': selected, 'probabilities': probabilities}


class OpenAIDecisionsAdapter:
    endpoint = '/decisions'

    def request(self, config, state, instructions, choices):
        return {'model': config.model, 'input': json.dumps(state, ensure_ascii=False),
                'questions': [{'type': 'choice', 'name': 'classification', 'instructions': instructions,
                               'choices': [{'value': value, 'description': description}
                                           for value, description in choices.items()]}]}

    def answer(self, payload):
        return next(row for row in payload['answers'] if row.get('name') == 'classification')


class TypeSafeDecisionsAdapter:
    endpoint = '/decisions'

    def request(self, config, state, instructions, choices):
        return {'model': config.model, 'state': state, 'questions': {
            'classification': {'type': 'choice', 'instructions': instructions, 'criteria': choices}}}

    def answer(self, payload):
        return payload['answers']['classification']


class TypeSafeSystemOneAdapter(TypeSafeDecisionsAdapter):
    endpoint = '/systemone'


class DecisionService:
    def __init__(self, ai, *, transport=None):
        self.ai, self.store, self.transport = ai, ai.store, transport
        self.adapters = {'openai_decisions': OpenAIDecisionsAdapter(),
                         'typesafe_decisions': TypeSafeDecisionsAdapter(),
                         'typesafe_systemone': TypeSafeSystemOneAdapter()}

    def revisions(self, profile_ids):
        revisions = {}
        for ident in profile_ids:
            try:
                _, _, revisions[ident] = self.store.get(ident)
            except AIError:
                revisions[ident] = None
        return revisions

    async def choose(self, profile_id, state, instructions, choices, *, expected_revision=None):
        config, key, revision = await asyncio.to_thread(self.store.get, profile_id)
        if expected_revision is not None and revision != expected_revision:
            raise AIError('决策配置在任务期间发生变化')
        started = time.monotonic()
        if not config.decision_only:
            prompt = (instructions + '\n仅返回JSON，包含choice和probabilities。'
                      'choice必须是候选项之一，probabilities须包含所有候选项，概率之和为1。')
            response = await self.ai.complete(profile_id,
                [ChatMessage(role='system', content=prompt), ChatMessage(role='user', content=json.dumps(
                    {'state': state, 'choices': choices}, ensure_ascii=False))],
                purpose='decision.classify', expected_revision=revision, json_output=True)
            try:
                content = response.text.strip()
                if content.startswith('```'):
                    content = content.split('\n', 1)[1].rsplit('```', 1)[0]
                answer = normalize_choice(json.loads(content), choices)
            except (ValueError, KeyError, TypeError, IndexError):
                raise AIError('分类模型返回格式无效') from None
            return {**answer, 'profile_id': profile_id, 'model': config.model,
                    'revision': revision, 'usage': response.usage, 'elapsed_ms': response.elapsed_ms}
        adapter = self.adapters[config.protocol]
        body = adapter.request(config, state, instructions, choices)
        host = urlsplit(config.base_url).hostname
        proxy = None
        if self.transport is None and host not in {'localhost', '127.0.0.1', '::1'} and not proxy_bypass(host):
            proxy = getproxies().get('https')
        usage, status = {}, 'failed'
        try:
            async with httpx.AsyncClient(timeout=config.timeout_seconds, transport=self.transport,
                                         proxy=proxy, trust_env=False, follow_redirects=False) as client:
                try:
                    response = await client.post(config.base_url + adapter.endpoint, json=body,
                                                 headers={'Authorization': 'Bearer ' + key} if key else {})
                except httpx.TimeoutException:
                    raise AIError('决策请求超时') from None
                except httpx.HTTPError:
                    raise AIError('决策接口连接失败') from None
            if response.status_code != 200:
                raise AIError(f'决策接口返回 HTTP {response.status_code}')
            if len(response.content) > 1024 * 1024:
                raise AIError('决策响应超过大小限制')
            try:
                payload = response.json()
                raw = payload.get('usage') or {}
                usage = {name: value for name, value in raw.items() if type(value) is int}
                for name, value in (raw.get('input_tokens_details') or {}).items():
                    if type(value) is int:
                        usage[name] = value
                answer = normalize_choice(adapter.answer(payload), choices)
            except (ValueError, KeyError, TypeError, IndexError, StopIteration):
                raise AIError('决策接口响应格式无效') from None
            status = 'completed'
            return {**answer, 'profile_id': profile_id, 'model': config.model, 'revision': revision,
                    'usage': usage, 'elapsed_ms': round((time.monotonic() - started) * 1000)}
        finally:
            await asyncio.to_thread(self.store.record, profile_id, 'decision.classify', config.model,
                status, usage, round((time.monotonic() - started) * 1000))

    async def choose_chain(self, profile_ids, state, instructions, choices, *, revisions=None):
        attempts = []
        for ident in dict.fromkeys(profile_ids):
            started = time.monotonic()
            try:
                if revisions is not None and revisions.get(ident) is None:
                    raise AIError('任务创建时该决策配置不可用')
                result = await self.choose(ident, state, instructions, choices,
                    expected_revision=revisions.get(ident) if revisions is not None else None)
            except AIError as error:
                attempts.append({'profile_id': ident, 'status': 'failed', 'detail': str(error),
                                 'elapsed_ms': round((time.monotonic() - started) * 1000)})
                continue
            attempts.append({'profile_id': ident, 'status': 'completed', 'elapsed_ms': result['elapsed_ms']})
            return {**result, 'attempts': attempts}
        raise DecisionUnavailable(attempts)
