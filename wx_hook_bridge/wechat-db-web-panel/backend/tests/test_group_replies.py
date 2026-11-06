import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3
import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest

from backend.app.auto_reply.context import ConversationContext
from backend.app.auto_reply.engine import ReplyEngine
from backend.app.auto_reply.models import QueryPlan, ReplyConfig, ReplyRuleEdit, ReplayRequest
from backend.app.auto_reply.router import create_reply_router
from backend.app.auto_reply.service import GroupReplyService
from backend.app.retrieval.models import Document, RetrievalError, RetrievalResult
from backend.app.retrieval.providers import BWIKISource, DeepSeekSearchFetch, GPTWebSearch


class LocalMessages:
    def __init__(self, path):
        self.path = path
        self.sent = []
        with self.db() as db:
            db.executescript('''CREATE TABLE chat_messages(id INTEGER PRIMARY KEY,account TEXT,session TEXT,timestamp INTEGER,data TEXT);
                CREATE TABLE live_names(account TEXT,username TEXT,name TEXT);
                CREATE TABLE chat_aliases(account TEXT,session TEXT,alias TEXT,message_id INTEGER);
                CREATE TABLE live_messages(sequence INTEGER PRIMARY KEY,account TEXT,session TEXT,event_id TEXT,timestamp INTEGER,message TEXT);
                INSERT INTO live_names VALUES ('self','room@chatroom','Fixture');''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, ident, stamp, text, *, room='room@chatroom', account='self', sender='alice', reference=None):
        message = {'content': text, 'category': 'text', 'sender_username': sender, 'reference': reference}
        with self.db() as db:
            db.execute('INSERT INTO chat_messages VALUES (?,?,?,?,?)', (ident, account, room, stamp, json.dumps(message)))

    async def check_account(self, account, force=False):
        assert account == 'self'

    async def send(self, payload):
        self.sent.append(payload)
        return {'status': 'accepted', 'detail': 'fixture accepted'}


def configuration(**values):
    return ReplyConfig(ai_profile_id='fixture', **values)


def test_context_excludes_future_other_rooms_and_same_second(tmp_path):
    live = LocalMessages(tmp_path / 'chat.sqlite')
    live.add(1, 100, 'earlier')
    live.add(2, 150, 'another room', room='other@chatroom')
    live.add(3, 160, 'another account', account='other')
    live.add(4, 200, '请问机制是什么', reference={'kind': 'text', 'content': 'quoted material'})
    live.add(5, 200, 'same second later import')
    live.add(6, 201, 'future answer')
    contexts = ConversationContext(live)
    context, checksum = contexts.load('self', 'room@chatroom', 4, configuration())
    assert [message['text'] for message in context['previous']] == ['earlier']
    assert context['current']['quote']['text'] == 'quoted material'
    assert len(checksum) == 64
    forced, _ = contexts.load('self', 'room@chatroom', 4, configuration(), ask_prefix=True)
    assert forced['previous'] == context['previous']
    assert forced['current']['text'] == '/ask ' + context['current']['text']


def test_live_and_replay_share_identical_inputs_and_only_live_delivers(tmp_path):
    live = LocalMessages(tmp_path / 'chat.sqlite')
    stamp = int(time.time())
    live.add(1, stamp - 10, '前文')
    live.add(2, stamp, '这个技能多少秒？')
    with live.db() as db:
        db.execute("INSERT INTO live_messages VALUES (1,'self','room@chatroom','event',?, '{}')", (stamp,))
        db.execute("INSERT INTO chat_aliases VALUES ('self','room@chatroom','event:event',2)")
    captured = []

    async def evaluate(context, config, **kwargs):
        captured.append(context)
        return {'should_reply': True, 'answer': 'fixture answer', 'reason': 'supported'}

    async def verify(*args):
        return {'name': 'Fixture'}, {}

    service = GroupReplyService(live, SimpleNamespace(store=SimpleNamespace(get=lambda _: (None, '', 1))),
                                SimpleNamespace(evaluate=evaluate), tmp_path / 'replies')
    service.groups = SimpleNamespace(verify_group=verify)
    service.store.save_rule(ReplyRuleEdit(account='self', room='room@chatroom', revision=0,
                                        config=configuration(enabled=True)), 'Fixture', 0)
    asyncio.run(service.poll_live())
    asyncio.run(service.execute(service.store.claim()))
    assert len(live.sent) == 1
    payload = ReplayRequest(account='self', room='room@chatroom', message_ids=[2],
                            search_modes=['hybrid'], request_id='a' * 32)
    app = FastAPI()
    app.include_router(create_reply_router(service))
    with TestClient(app) as client:
        batch = client.post('/api/group-replies/replays', json=payload.model_dump())
        assert batch.status_code == 200
        assert client.post('/api/group-replies/replays', json=payload.model_dump()).json()['id'] == batch.json()['id']
        asyncio.run(service.execute(service.store.claim()))
    assert captured[0] == captured[1]
    assert len(live.sent) == 1
    assert service.store.claim() is None


def engine_context(text='技能冷却是多少？'):
    return {'at': '2026-11-06T12:00:00+08:00', 'previous': [],
            'current': {'text': text, 'category': 'text', 'is_self': False}}


class ModelFixture:
    def __init__(self, *, quote='技能冷却时间为30秒，效果持续时间为5秒。', supported=True):
        self.quote, self.supported = quote, supported

    async def complete(self, profile_id, messages, *, purpose, **kwargs):
        if purpose.endswith('plan'):
            data = {'need_answer': True, 'enough_context': True, 'question': '技能冷却时间是多少？',
                    'intent': 'fact', 'domain': 'identityv', 'queries': ['技能冷却'], 'wiki_titles': []}
        elif purpose.endswith('answer'):
            data = {'confident': True, 'confidence': .99, 'answer': '冷却为30秒。',
                    'citations': [{'source_id': 'S1', 'quote': self.quote}]}
        else:
            data = {'supported': self.supported, 'answers_question': True, 'version_consistent': True,
                    'confidence': .99, 'reason': 'fixture review'}
        return SimpleNamespace(text=json.dumps(data), usage={'total_tokens': 1})


class SourceFixture:
    async def retrieve(self, *args, **kwargs):
        return RetrievalResult(documents=[Document('技能说明', 'https://id5.163.com/fixture',
            '技能冷却时间为30秒，效果持续时间为5秒。', 'fixture', 'official')])


def test_grounded_answer_requires_matching_quote_and_independent_review():
    engine = ReplyEngine(ModelFixture(), SourceFixture())
    result = asyncio.run(engine.evaluate(engine_context(), configuration(), model_revision=1))
    assert result['should_reply']
    assert 'https://id5.163.com/fixture' in result['answer']
    invalid = ReplyEngine(ModelFixture(quote='完全虚构的引用内容不在原文中'), SourceFixture())
    result = asyncio.run(invalid.evaluate(engine_context(), configuration(), model_revision=1))
    assert not result['should_reply'] and result['reason'] == 'invalid_citation'
    rejected = ReplyEngine(ModelFixture(supported=False), SourceFixture())
    result = asyncio.run(rejected.evaluate(engine_context(), configuration(), model_revision=1))
    assert not result['should_reply'] and result['reason'] == 'insufficient_evidence'


def test_ask_always_replies_when_sources_missing_and_bypasses_cooldown():
    async def empty(*args, **kwargs):
        return RetrievalResult()
    engine = ReplyEngine(ModelFixture(), SimpleNamespace(retrieve=empty))
    ordinary = asyncio.run(engine.evaluate(engine_context(), configuration(), model_revision=1))
    forced = asyncio.run(engine.evaluate(engine_context('/ask 技能冷却是多少？'), configuration(),
                                         model_revision=1, last_reply_at=1793937599))
    assert not ordinary['should_reply']
    assert forced['should_reply'] and forced['answer']
    assert forced['reason'] == 'retrieval_empty'
    empty_ask = asyncio.run(engine.evaluate(engine_context('/ask'), configuration(), model_revision=1))
    assert empty_ask['should_reply'] and empty_ask['reason'] == 'missing_context'


def test_delivery_reservation_survives_restart_without_replay(tmp_path):
    live = LocalMessages(tmp_path / 'chat.sqlite')
    service = GroupReplyService(live, None, None, tmp_path / 'replies')
    rule = service.store.save_rule(ReplyRuleEdit(account='self', room='room@chatroom', revision=0,
                                               config=configuration(enabled=True)), 'Fixture', 0)
    context = engine_context('/ask test')
    context['current']['message_id'] = 1
    with service.store.db() as db:
        ident = service.store.enqueue(db, key='event', rule=rule, context=context, context_hash='hash', model_revision=1, origin='live')
    assert service.store.reserve_delivery(ident)
    service.store.recover()
    assert not service.store.reserve_delivery(ident)
    assert service.store.run(ident)['delivery_status'] == 'unknown'


def test_deepseek_adapter_matches_official_search_and_fetch_schema():
    calls = []
    def handle(request):
        assert request.url.host == 'api.deepseek.com'
        assert request.headers['authorization'] == 'Bearer fixture-key'
        calls.append((request.url.path, json.loads(request.content)))
        if request.url.path.endswith('/search'):
            return httpx.Response(200, json={'results': [{'title': 'Fixture', 'url': 'https://id5.163.com/fixture',
                'authority': 3, 'rerank_score': 2, 'summary': 'Search summary'}]})
        return httpx.Response(200, json={'title': 'Fixture', 'content': '# 原始正文'})
    adapter = DeepSeekSearchFetch(SimpleNamespace(connection=lambda: ('deepseek', SimpleNamespace(model='fixture', timeout_seconds=30), 'fixture-key', 'https://api.deepseek.com/v1/tools', 1)), transport=httpx.MockTransport(handle))
    hits = asyncio.run(adapter.search('机制', original_question='机制是什么？', count=3))
    assert len(hits) == 1
    assert calls[0][1] == {'query': '机制', 'count': 3, 'ori_question': '机制是什么？', 'need_content': False}
    assert asyncio.run(adapter.fetch(hits[0].url)) == ('Fixture', '# 原始正文')
    with pytest.raises(RetrievalError):
        asyncio.run(adapter.fetch('http://127.0.0.1/private'))
    with pytest.raises(RetrievalError):
        asyncio.run(adapter.search('x' * 129))


def test_deepseek_rejects_auth_error_without_retry_or_secret_echo():
    calls = []
    def handle(request):
        calls.append(1)
        return httpx.Response(401, json={'error': {'message': 'fixture-key secret details'}})
    adapter = DeepSeekSearchFetch(SimpleNamespace(connection=lambda: ('deepseek', SimpleNamespace(model='fixture', timeout_seconds=30), 'fixture-key', 'https://api.deepseek.com/v1/tools', 1)), transport=httpx.MockTransport(handle))
    with pytest.raises(RetrievalError, match='HTTP 401') as error:
        asyncio.run(adapter.search('test'))
    assert len(calls) == 1
    assert 'fixture-key' not in str(error.value)


def test_bwiki_reads_revision_at_message_time():
    def handle(request):
        assert request.url.params['rvstart'] == '2026-10-10T00:00:00Z'
        assert request.url.params['rvdir'] == 'older'
        return httpx.Response(200, json={'query': {'pages': {'1': {'title': '穿刺', 'revisions': [{
            'revid': 12, 'timestamp': '2026-10-09T00:00:00Z', 'slots': {'main': {'*': '原始修订正文'}}}]}}}})
    wiki = BWIKISource(transport=httpx.MockTransport(handle))
    page = asyncio.run(wiki.page('穿刺', datetime(2026, 10, 10, tzinfo=timezone.utc)))
    assert page.revision == '12' and page.content == '原始修订正文'


def test_gpt_requires_actual_hosted_search_call():
    class Store:
        def get(self, _):
            return SimpleNamespace(model='fixture', base_url='https://model.invalid/v1', max_output_tokens=32768, timeout_seconds=30), 'key', 1
        def record(self, *args):
            pass
    adapter = GPTWebSearch(Store(), transport=httpx.MockTransport(lambda _: httpx.Response(200, json={
        'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'pretend search result'}]}]})))
    with pytest.raises(RetrievalError, match='真实 web_search'):
        asyncio.run(adapter.search('fixture', 'test', as_of=datetime.now(timezone.utc)))
