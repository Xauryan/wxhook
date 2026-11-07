import asyncio
from datetime import datetime, timezone
import json
import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from backend.app.ai.models import ChatMessage, ImageInput
from backend.app.ai.service import AIError
from backend.app.auto_reply.context import ConversationContext
from backend.app.auto_reply.engine import ReplyEngine, candidate_reason, is_explicit, source_footer
from backend.app.auto_reply.faq import FAQStore
from backend.app.auto_reply.models import ReplyRuleEdit
from backend.app.auto_reply.service import GroupReplyService
from backend.app.retrieval.config import RetrievalConfig
from backend.app.retrieval.models import Document, RetrievalError, RetrievalResult, SearchHit
from backend.app.retrieval.providers import BWIKISource
from backend.app.retrieval.service import Retriever
from backend.tests.test_live_chat import service  # noqa: F401
from backend.tests.test_group_replies import (LocalMessages, ModelFixture, SourceFixture, configuration,
                                              engine_context)

SOURCES = [{'title': '破轮', 'url': 'https://wiki.biligame.com/dwrg/a', 'source_kind': 'community_wiki'},
           {'title': '公告', 'url': 'https://id5.163.com/b', 'source_kind': 'official'}]


def test_mention_and_reply_to_assistant_are_explicit_requests():
    config = configuration()
    mentioned = {'text': '@小助手 拔刺多久', 'category': 'text', 'mentions_assistant': True}
    follow_up = {'text': '那一个人呢', 'category': 'quote', 'quote': {'kind': 'text', 'text': '…', 'from_assistant': True}}
    assert is_explicit(mentioned, config) and is_explicit(follow_up, config)
    assert not is_explicit(mentioned, configuration(mention_trigger=False))
    assert candidate_reason({**mentioned, 'is_self': True}, configuration(proactive=False)) == ''
    assert ConversationContext.mentions('@小助手 拔刺多久', ['小助手'])
    assert not ConversationContext.mentions('@小助手手 hi', ['小助手'])


def test_context_marks_mentions_and_quotes_of_the_assistant(tmp_path):
    live = LocalMessages(tmp_path / 'chat.sqlite')
    live.add(1, 100, '@小助手 破轮拔刺多久')
    with live.db() as db:
        data = json.loads(db.execute('SELECT data FROM chat_messages WHERE id=1').fetchone()[0])
        data.update(reference={'kind': 'text', 'content': '上一轮回答'}, reference_from_username='self')
        db.execute('UPDATE chat_messages SET data=? WHERE id=1', (json.dumps(data, ensure_ascii=False),))
    context, _ = ConversationContext(live).load('self', 'room@chatroom', 1, configuration(), assistant_names=['小助手'])
    assert context['current']['mentions_assistant']
    assert context['current']['quote']['from_assistant']


def test_mentions_answer_verified_questions_but_never_force_a_reply():
    engine = ReplyEngine(ModelFixture(), SourceFixture())
    bare = {'text': '@小助手\u2005', 'category': 'text', 'mentions_assistant': True}
    result = asyncio.run(engine.evaluate({**engine_context(), 'current': bare}, configuration(), model_revision=1))
    assert not result['should_reply'] and result['reason'] == 'missing_context' and result['trigger'] == 'mention'
    asked = {'text': '@小助手\u2005技能冷却是多少？', 'category': 'text', 'mentions_assistant': True}
    # Cooldown and the chat filter do not apply to someone who addressed the account directly.
    result = asyncio.run(engine.evaluate({**engine_context(), 'current': asked}, configuration(proactive=False),
                                         model_revision=1, last_reply_at=1793937599))
    assert result['should_reply'] and result['reason'] == 'supported'

    async def empty(*args, **kwargs):
        return RetrievalResult()
    quiet = asyncio.run(ReplyEngine(ModelFixture(), SimpleNamespace(retrieve=empty)).evaluate(
        {**engine_context(), 'current': asked}, configuration(), model_revision=1))
    assert not quiet['should_reply'] and quiet['reason'] == 'retrieval_empty'


class FailingFirst(ModelFixture):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def complete(self, profile_id, messages, *, purpose, **kwargs):
        self.calls.append((profile_id, purpose, kwargs.get('reasoning_effort')))
        if profile_id == 'fixture':
            raise AIError('模型接口返回 HTTP 500')
        return await super().complete(profile_id, messages, purpose=purpose)


def test_stages_fall_back_to_backup_models_and_quick_stages_use_low_reasoning():
    model = FailingFirst()
    engine = ReplyEngine(model, SourceFixture())
    result = asyncio.run(engine.evaluate(engine_context('/ask 技能冷却是多少？'),
                                         configuration(fallback_profile_ids=['backup']), model_revision=1,
                                         profile_revisions={'backup': 3}))
    assert result['reason'] == 'supported'
    assert [call[0] for call in model.calls if call[1].endswith('plan')] == ['fixture', 'backup']
    efforts = {purpose.split('.')[-1]: effort for profile, purpose, effort in model.calls if profile == 'backup'}
    assert efforts == {'plan': 'low', 'answer': None, 'verify': 'low'}
    assert {'stage': 'plan', 'profile_id': 'fixture', 'status': 'failed'}.items() <= result['attempts'][0].items()
    slow = FailingFirst()
    asyncio.run(ReplyEngine(slow, SourceFixture()).evaluate(engine_context('/ask 技能冷却是多少？'),
        configuration(fallback_profile_ids=['backup'], fast_stages=False), model_revision=1, profile_revisions={'backup': 3}))
    assert {effort for _, _, effort in slow.calls} == {None}


def test_source_footer_styles_keep_chat_bubbles_short():
    assert source_footer(SOURCES, 'one_link') == '来源：https://wiki.biligame.com/dwrg/a'
    assert source_footer(SOURCES, 'names') == '来源：BWIKI《破轮》、官方《公告》'
    assert source_footer(SOURCES, 'links').count('https://') == 2
    assert source_footer([], 'names') == ''


def test_faq_reuses_matching_answers_within_ttl(tmp_path):
    faq = FAQStore(tmp_path / 'faq.sqlite')
    plan = {'question': '第五人格破轮拔刺在两人和一人存活时耗时有区别吗', 'domain': 'identityv', 'time_sensitive': False}
    faq.add('self', 'room', 'run-1', plan, '有区别：2人存活8秒，1人存活5秒。', SOURCES[:1])
    faq.add('self', 'room', 'run-1', plan, 'duplicate', [])
    hit = faq.find('self', 'room', '第五人格破轮拔刺在两人和一人存活时耗时有区别吗？', 'identityv', False, 7)
    assert hit and hit['answer'].startswith('有区别') and len(faq.list('self', 'room')) == 1
    assert faq.find('self', 'room', '雅思报名费是多少', 'identityv', False, 7) is None
    assert faq.find('self', 'other-room', plan['question'], 'identityv', False, 7) is None
    assert faq.find('self', 'room', plan['question'], 'identityv', False, 7, now=time.time() + 8 * 86400) is None
    assert faq.find('self', 'room', plan['question'], 'identityv', True, 7, now=time.time() + 2 * 86400) is None
    assert faq.list('self', 'room')[0]['hits'] == 1
    faq.delete('self', hit['id'])
    assert faq.list('self', 'room') == []


def test_faq_hit_skips_search_and_answers_directly():
    async def no_search(*args, **kwargs):
        raise AssertionError('FAQ hit must not search')
    engine = ReplyEngine(ModelFixture(), SimpleNamespace(retrieve=no_search))
    hit = {'id': 'f1', 'question': '技能冷却时间是多少？', 'answer': '冷却为30秒。', 'sources': SOURCES[:1]}
    result = asyncio.run(engine.evaluate(engine_context(), configuration(source_style='names'), model_revision=1,
                                         faq_lookup=lambda question, domain, sensitive: hit))
    assert result['reason'] == 'faq' and result['answer'] == '冷却为30秒。\n\n来源：BWIKI《破轮》'


def test_glossary_is_passed_to_planning_and_answering():
    seen = {}
    class Recording(ModelFixture):
        async def complete(self, profile_id, messages, *, purpose, **kwargs):
            seen[purpose] = json.loads(messages[1].content)
            return await super().complete(profile_id, messages, purpose=purpose)
    asyncio.run(ReplyEngine(Recording(), SourceFixture()).evaluate(engine_context('/ask 摸金多少页'),
        configuration(glossary='摸金=加页手记'), model_revision=1))
    assert seen['group_reply.plan']['group_glossary'] == '摸金=加页手记'
    assert seen['group_reply.answer']['group_glossary'] == '摸金=加页手记'
    assert 'group_glossary' not in seen['group_reply.verify']['conversation']


def test_chat_message_images_use_each_protocols_format():
    message = ChatMessage(role='user', content='看图', images=[ImageInput(mime='image/png', data='aGk=')])
    assert message.wire('responses')['content'][1] == {'type': 'input_image', 'image_url': 'data:image/png;base64,aGk='}
    assert message.wire('chat_completions')['content'][1]['image_url']['url'] == 'data:image/png;base64,aGk='
    assert message.wire('anthropic')['content'][0]['source'] == {'type': 'base64', 'media_type': 'image/png', 'data': 'aGk='}
    assert ChatMessage(role='user', content='纯文本').wire('responses') == {'role': 'user', 'content': '纯文本'}


class Cache:
    def key(self, parts):
        return json.dumps(parts)

    def get(self, key):
        return None

    def put(self, *args):
        pass


def test_retrieval_runs_sources_in_parallel_within_time_budget():
    class Wiki:
        async def page(self, title, as_of):
            await asyncio.sleep(5)
        async def search(self, query, count):
            await asyncio.sleep(5)
    class DeepSeek:
        async def search(self, query, original_question, count):
            return [SearchHit(title='官方', url='https://id5.163.com/a', authority=3, score=1)]
        async def fetch(self, url):
            await asyncio.sleep(.1)
            return '官方', '正文'
    Wiki.title_from_url = staticmethod(lambda url: None)
    retriever = Retriever(Wiki(), DeepSeek(), None, Cache())
    plan = SimpleNamespace(question='破轮拔刺', queries=['破轮拔刺'], wiki_titles=['破轮'], domain='identityv')
    started = time.monotonic()
    result = asyncio.run(retriever.retrieve(plan, 'hybrid', 'p', 1, datetime(2026, 11, 6, tzinfo=timezone.utc),
                                            options=RetrievalConfig(time_budget_seconds=5)))
    assert time.monotonic() - started < 5.5
    assert [document.url for document in result.documents] == ['https://id5.163.com/a']
    assert any('未完成' in error for error in result.errors)


def test_bwiki_pauses_after_rate_limit_instead_of_retrying():
    calls = []
    def handle(request):
        calls.append(1)
        return httpx.Response(567)
    wiki = BWIKISource(transport=httpx.MockTransport(handle))
    with pytest.raises(RetrievalError, match='567'):
        asyncio.run(wiki.search('破轮'))
    with pytest.raises(RetrievalError, match='跳过'):
        asyncio.run(wiki.search('破轮'))
    assert len(calls) == 1


def reply_service(tmp_path, live, ai):
    async def verify(*args):
        return {'name': 'Fixture'}, {}
    service = GroupReplyService(live, ai, None, tmp_path / 'replies')
    service.groups = SimpleNamespace(verify_group=verify)
    return service


def queued_run(service, live, config, text='技能冷却是多少？'):
    existing = service.store.get_rule('self', 'room@chatroom')
    rule = service.store.save_rule(ReplyRuleEdit(account='self', room='room@chatroom',
                                                 revision=existing['revision'] if existing else 0, config=config), 'Fixture', 0)
    context = engine_context(text)
    context['current']['message_id'] = 1
    with service.store.db() as db:
        ident = service.store.enqueue(db, key='event-' + uuid4().hex, rule=rule, context=context, context_hash='hash',
                                      model_revision=1, origin='live')
    return service.store.run(ident), context


def test_proactive_answer_is_skipped_when_group_already_answered(tmp_path):
    live = LocalMessages(tmp_path / 'chat.sqlite')
    live.add(1, 100, '技能冷却是多少？')
    live.add(2, 110, '30秒啊')
    class Checker:
        async def complete(self, profile_id, messages, *, purpose, **kwargs):
            assert purpose == 'group_reply.stale_check' and kwargs['reasoning_effort'] == 'low'
            assert json.loads(messages[1].content)['later_messages'][0]['text'] == '30秒啊'
            return SimpleNamespace(text='{"already_answered": true, "still_relevant": true, "reason": "群友已回答30秒"}')
    service = reply_service(tmp_path, live, Checker())
    run, _ = queued_run(service, live, configuration(enabled=True))
    asyncio.run(service._deliver(run, {'should_reply': True, 'answer': '冷却为30秒。', 'trigger': 'proactive'}))
    assert live.sent == []
    assert service.store.run(run['id'])['delivery_status'] == 'skipped'
    asked = {'should_reply': True, 'answer': '冷却为30秒。', 'trigger': 'ask'}
    run2, _ = queued_run(service, live, configuration(enabled=True))
    asyncio.run(service._deliver(run2, asked))
    assert len(live.sent) == 1


def test_reply_quotes_the_triggering_text_message(tmp_path):
    live = LocalMessages(tmp_path / 'chat.sqlite')
    live.add(1, 100, '技能冷却是多少？')
    with live.db() as db:
        data = json.loads(db.execute('SELECT data FROM chat_messages WHERE id=1').fetchone()[0])
        data.update(local_type=1, server_id=7340012345678901234, local_id=88, create_time=1793900000,
                    sender_username='wxid_asker')
        db.execute('UPDATE chat_messages SET data=? WHERE id=1', (json.dumps(data, ensure_ascii=False),))
    service = reply_service(tmp_path, live, None)
    run, _ = queued_run(service, live, configuration(enabled=True))
    asyncio.run(service._deliver(run, {'should_reply': True, 'answer': '冷却为30秒。', 'trigger': 'ask'}))
    quote = live.sent[0].quote
    assert quote.msg_id == '7340012345678901234' and quote.local_id == 88 and quote.sender == 'wxid_asker'
    plain, _ = queued_run(service, live, configuration(enabled=True, reply_style='plain'))
    asyncio.run(service._deliver(plain, {'should_reply': True, 'answer': '冷却为30秒。', 'trigger': 'ask'}))
    assert live.sent[1].quote is None


def test_quote_send_uses_bridge_quote_api_and_falls_back_to_plain_text_on_rejection(service, monkeypatch):
    from backend.app.live_chat import SendQuote, SendText
    quote = SendQuote(msg_id='7340012345678901234', local_id=88, content='拔刺多久', sender='wxid_asker',
                      create_time=1793900000, chat='peer')
    result = asyncio.run(service.send(SendText(account='self_a', username='peer', text='5秒', request_id=uuid4(), quote=quote)))
    sent = [call for call in service.calls if call[0] in {'/api/sendQuoteMsg', '/api/sendTextMsg'}]
    assert result['status'] in {'accepted', 'dispatching'} and [call[0] for call in sent] == ['/api/sendQuoteMsg']
    assert sent[0][1]['quotedMsgId'] == '7340012345678901234' and sent[0][1]['quotedFrom'] == 'wxid_asker'
    original = service.bridge
    async def reject_quotes(path, payload, timeout=8):
        if path == '/api/sendQuoteMsg':
            service.calls.append((path, payload))
            return {'code': -1, 'msg': 'unsupported'}
        return await original(path, payload, timeout)
    monkeypatch.setattr(service, 'bridge', reject_quotes)
    service.calls.clear()
    asyncio.run(service.send(SendText(account='self_a', username='peer', text='5秒', request_id=uuid4(), quote=quote)))
    assert [call[0] for call in service.calls if call[0].startswith('/api/send')] == ['/api/sendQuoteMsg', '/api/sendTextMsg']


def test_screenshots_are_described_once_and_fed_to_the_question_context(tmp_path):
    image = tmp_path / 'shot.png'
    image.write_bytes(b'\x89PNG fixture')
    live = LocalMessages(tmp_path / 'chat.sqlite')
    live.media = SimpleNamespace(specs=lambda account, ident: [{'kind': 'image'}],
                                 resolve=lambda account, ident, index: (str(image), 'image/png'))
    seen = []
    class Vision:
        async def complete(self, profile_id, messages, *, purpose, **kwargs):
            seen.append((profile_id, purpose, messages[1].images[0].mime))
            return SimpleNamespace(text='合成柜界面，显示“机械零件 x3”')
    service = reply_service(tmp_path, live, Vision())
    context = engine_context('这是什么合成柜')
    context['group'] = 'Fixture'
    context['previous'] = [{'message_id': 7, 'category': 'image', 'text': '[image] '}]
    described = asyncio.run(service.describe_images({'account': 'self'}, configuration(vision_profile_id='vision'),
                                                    context, {'vision': 2}))
    assert seen == [('vision', 'group_reply.vision', 'image/png')]
    assert described[0]['message_id'] == 7 and '机械零件' in context['previous'][0]['text']
