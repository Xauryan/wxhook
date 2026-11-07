import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from backend.app.ai.decisions import DecisionService, DecisionUnavailable
from backend.app.ai.models import ModelProfile, ProfileEdit
from backend.app.ai.service import AIService
from backend.app.ai.store import AIStore
from backend.app.auto_reply.engine import ReplyEngine, candidate_reason
from backend.app.auto_reply.models import ReplyConfig
from backend.app.auto_reply.store import ReplyStore
from backend.tests.test_ai import codec
from backend.tests.test_group_replies import ModelFixture, SourceFixture, engine_context

CHOICES = {'question': '求助', 'ignore': '闲聊', 'unclear': '不确定'}


def decision_store(tmp_path):
    return AIStore(tmp_path / 'ai.sqlite', secret_codec=codec)


def add_profile(store, name, provider):
    return store.save(None, ProfileEdit(config=ModelProfile(name=name,
        base_url=f'https://{name}.invalid/v1', model='fixture', provider=provider,
        timeout_seconds=10), api_key='private-fixture-key'))


def native_response(choice='question'):
    return {'answers': [{'name': 'classification', 'type': 'choice', 'choice': choice,
        'probabilities': [{'value': name, 'probability': int(name == choice)} for name in CHOICES]}],
        'usage': {'input_tokens': 10, 'output_tokens': 0, 'total_tokens': 10}}


@pytest.mark.parametrize('provider,endpoint', [('openai_decisions', '/v1/decisions'),
    ('typesafe_decisions', '/v1/decisions'), ('typesafe_systemone', '/v1/systemone')])
def test_native_decision_schemas_and_usage(tmp_path, provider, endpoint):
    store = decision_store(tmp_path)
    profile = add_profile(store, 'fixture', provider)
    def handle(request):
        assert request.url.path == endpoint
        body = json.loads(request.content)
        if provider == 'openai_decisions':
            assert 'input' in body and body['questions'][0]['name'] == 'classification'
            payload = native_response()
        else:
            assert body['state'] == {'current': '求助'}
            assert body['questions']['classification']['criteria'] == CHOICES
            payload = {'answers': {'classification': {'type': 'choice', 'choice': 'question',
                'probabilities': {'question': .9, 'ignore': .05, 'unclear': .05}}}}
        return httpx.Response(200, json=payload)
    service = DecisionService(AIService(store), transport=httpx.MockTransport(handle))
    result = asyncio.run(service.choose(profile['id'], {'current': '求助'}, '分类', CHOICES))
    assert result['choice'] == 'question'
    assert store.list()['recent_usage'][0]['status'] == 'completed'


def test_failure_moves_to_next_profile_but_valid_ignore_stops(tmp_path):
    store = decision_store(tmp_path)
    profiles = [add_profile(store, name, 'openai_decisions') for name in ['broken', 'healthy', 'unused']]
    called = []
    def handle(request):
        called.append(request.url.host)
        if request.url.host == 'broken.invalid':
            return httpx.Response(503, text='private-fixture-key must not be shown')
        return httpx.Response(200, json=native_response('ignore'))
    service = DecisionService(AIService(store), transport=httpx.MockTransport(handle))
    result = asyncio.run(service.choose_chain([p['id'] for p in profiles], {}, '分类', CHOICES))
    assert called == ['broken.invalid', 'healthy.invalid']
    assert result['choice'] == 'ignore' and len(result['attempts']) == 2
    assert 'private-fixture-key' not in json.dumps(result)
    store.delete(profiles[1]['id'], profiles[1]['revision'])
    assert all(p['id'] != profiles[1]['id'] for p in store.list()['profiles'])
    revisions = service.revisions([profiles[1]['id']])
    assert revisions[profiles[1]['id']] is None
    with pytest.raises(DecisionUnavailable):
        asyncio.run(service.choose_chain([profiles[1]['id']], {}, '分类', CHOICES, revisions=revisions))


@pytest.mark.parametrize('text', ['这个登不上去', '不命中也算', '帮看看', '需要一份报名说明', '啥情况'])
def test_permissive_local_filter_does_not_require_question_words(text):
    assert candidate_reason(engine_context(text)['current'], ReplyConfig(ai_profile_id='fixture')) == ''


@pytest.mark.parametrize('text', ['？', '这？', '啥', '[发呆][发呆]', '……'])
def test_local_filter_drops_punctuation_emoji_and_single_characters(text):
    assert candidate_reason(engine_context(text)['current'], ReplyConfig(ai_profile_id='fixture')) == 'empty_content'


def test_negative_decision_skips_planning_and_ask_bypasses_gate():
    class Decisions:
        calls = 0
        async def choose_chain(self, *args, **kwargs):
            self.calls += 1
            return {'choice': 'ignore', 'probabilities': {'ignore': .99}, 'attempts': []}
    class Model(ModelFixture):
        calls = 0
        async def complete(self, *args, **kwargs):
            self.calls += 1
            return await super().complete(*args, **kwargs)
    decisions, model = Decisions(), Model()
    engine = ReplyEngine(model, SourceFixture(), decisions)
    config = ReplyConfig(ai_profile_id='fixture', decision_profile_ids=['decision'])
    result = asyncio.run(engine.evaluate(engine_context('今天真开心'), config, model_revision=1))
    assert result['reason'] == 'not_actionable' and model.calls == 0
    result = asyncio.run(engine.evaluate(engine_context('/ask 技能冷却是多少？'), config, model_revision=1))
    assert result['should_reply'] and decisions.calls == 1


def test_stage_models_custom_prompts_and_retrieval_settings_are_used():
    class Model(ModelFixture):
        calls = []
        async def complete(self, ident, messages, **kwargs):
            self.calls.append((ident, messages[0].content, kwargs.get('expected_revision')))
            return await super().complete(ident, messages, **kwargs)
    class Source(SourceFixture):
        async def retrieve(self, *args, **kwargs):
            assert kwargs['options'].deepseek_count == 9
            assert kwargs['search_prompt'] == 'custom search {question}'
            return await super().retrieve(*args, **kwargs)
    model = Model()
    config = ReplyConfig(ai_profile_id='answer', planning_profile_id='planner', review_profile_id='reviewer',
        plan_prompt='custom plan', answer_prompt='custom answer', review_prompt='custom review',
        search_prompt='custom search {question}', retrieval={'deepseek_count': 9})
    result = asyncio.run(ReplyEngine(model, Source()).evaluate(engine_context(), config,
        model_revision=3, profile_revisions={'planner': 4, 'reviewer': 5}))
    assert result['should_reply']
    assert model.calls == [('planner', 'custom plan', 4), ('answer', 'custom answer', 3), ('reviewer', 'custom review', 5)]


def test_queue_freezes_default_and_custom_prompts(tmp_path):
    from backend.app.auto_reply.prompts import PROMPTS
    store = ReplyStore(tmp_path)
    context = engine_context()
    context['current']['message_id'] = 1
    config = ReplyConfig(ai_profile_id='fixture', answer_prompt='custom answer')
    rule = {'account': 'self', 'room': 'room@chatroom', 'revision': 1, 'config': config.model_dump()}
    with store.db() as db:
        ident = store.enqueue(db, key='snapshot', rule=rule, context=context,
            context_hash='hash', model_revision=1, origin='replay', profile_revisions={'decision': 2})
    run = store.run(ident)
    assert run['config']['answer_prompt'] == 'custom answer'
    assert run['config']['plan_prompt'] == PROMPTS['plan_prompt']
    assert run['profile_revisions'] == {'decision': 2}
