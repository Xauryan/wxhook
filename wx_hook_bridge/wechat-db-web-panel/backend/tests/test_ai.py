import asyncio
import json

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import httpx
import pytest

from backend.app.ai.models import ChatMessage, ModelProfile, ProfileEdit
from backend.app.ai.router import create_ai_router
from backend.app.ai.service import AIError, AIService, OpenAICompatibleProvider
from backend.app.ai.store import AIStore


def profile():
    return ModelProfile(name='Fixture', base_url='https://model.invalid/v1', model='fixture-model')


def codec(value, *, decrypt=False):
    return bytes(byte ^ 0x5a for byte in value)


@pytest.fixture
def store(tmp_path):
    return AIStore(tmp_path / 'ai.sqlite', secret_codec=codec)


def test_profile_revision_secrets_and_provider_change(store):
    created = store.save(None, ProfileEdit(config=profile(), api_key='test-private-key'))
    assert created['api_key_set']
    assert 'test-private-key' not in json.dumps(store.list())
    assert b'test-private-key' not in store.path.read_bytes()
    assert store.get(created['id'])[1] == 'test-private-key'
    with pytest.raises(AIError):
        store.save(created['id'], ProfileEdit(config=profile(), revision=0))
    changed = profile().model_copy(update={'base_url': 'https://another.invalid/v1'})
    with pytest.raises(AIError):
        store.save(created['id'], ProfileEdit(config=changed, revision=1))
    saved = store.save(created['id'], ProfileEdit(config=profile(), revision=1, clear_api_key=True))
    assert not saved['api_key_set']


def test_invalid_configuration_never_echoes_credentials(store):
    app = FastAPI()
    app.include_router(create_ai_router(AIService(store)))
    with TestClient(app) as client:
        payload = {'config': profile().model_dump(), 'api_key': 'test-private-key', 'clear_api_key': True}
        result = client.post('/api/ai/profiles', json=payload)
        assert result.status_code == 422
        assert 'test-private-key' not in result.text
        assert client.post('/api/ai/profiles', json={'config': profile().model_dump()},
                           headers={'Origin': 'https://external.invalid'}).status_code == 403


def test_completion_adapter_and_usage_are_business_independent(store):
    saved = store.save(None, ProfileEdit(config=profile(), api_key='test-private-key'))
    calls = []
    def handle(request):
        calls.append(json.loads(request.content))
        assert request.url.path == '/v1/chat/completions'
        assert request.headers['Authorization'] == 'Bearer test-private-key'
        return httpx.Response(200, json={'choices': [{'message': {'content': 'OK'}, 'finish_reason': 'stop'}],
                                        'usage': {'prompt_tokens': 10, 'completion_tokens': 1, 'total_tokens': 11}})
    service = AIService(store, OpenAICompatibleProvider(httpx.MockTransport(handle)))
    result = asyncio.run(service.complete(saved['id'], [ChatMessage(role='user', content='private fixture message')], purpose='future_feature'))
    assert result.text == 'OK'
    assert calls[0]['model'] == 'fixture-model'
    assert result.usage['total_tokens'] == 11
    assert 'private fixture message' not in json.dumps(store.list())
    with pytest.raises(AIError):
        asyncio.run(service.complete(saved['id'], [ChatMessage(role='user', content='x')], purpose='fixture', expected_revision=99))


def test_provider_errors_hide_response_body(store):
    saved = store.save(None, ProfileEdit(config=profile()))
    service = AIService(store, OpenAICompatibleProvider(httpx.MockTransport(
        lambda _: httpx.Response(401, text='secret-provider-detail'))))
    with pytest.raises(AIError, match='HTTP 401') as error:
        asyncio.run(service.complete(saved['id'], [ChatMessage(role='user', content='hello')], purpose='fixture'))
    assert 'secret-provider-detail' not in str(error.value)


@pytest.mark.parametrize('base_url,bypass,expected_proxy', [
    ('https://model.invalid/v1', False, 'http://127.0.0.1:7897'),
    ('https://model.invalid/v1', True, None),
    ('http://127.0.0.1:1234/v1', False, None),
])
def test_remote_provider_uses_configured_proxy_and_honors_bypass(monkeypatch, base_url, bypass, expected_proxy):
    import backend.app.ai.service as module
    original_client = httpx.AsyncClient
    seen = []
    def client(**kwargs):
        seen.append(kwargs.pop('proxy'))
        kwargs['transport'] = httpx.MockTransport(lambda _: httpx.Response(200, json={
            'choices': [{'message': {'content': 'OK'}, 'finish_reason': 'stop'}]}))
        return original_client(**kwargs)
    monkeypatch.setattr(module, 'getproxies', lambda: {'https': 'http://127.0.0.1:7897'})
    monkeypatch.setattr(module, 'proxy_bypass', lambda _: bypass)
    monkeypatch.setattr(module.httpx, 'AsyncClient', client)
    config = profile().model_copy(update={'base_url': base_url})
    response, _ = asyncio.run(OpenAICompatibleProvider().complete(
        config, '', [ChatMessage(role='user', content='hello')]))
    assert response == 'OK'
    assert seen == [expected_proxy]

@pytest.mark.parametrize('protocol,path', [('responses', '/v1/responses'), ('anthropic', '/v1/messages')])
def test_protocol_adapters_preserve_messages_and_normalize_usage(store, protocol, path):
    config = profile().model_copy(update={'api_format': protocol})
    saved = store.save(None, ProfileEdit(config=config, api_key='fixture-key'))
    def handle(request):
        assert request.url.path == path
        body = json.loads(request.content)
        if protocol == 'responses':
            assert body['instructions'] == 'system rule'
            assert body['input'] == [{'role': 'user', 'content': 'hello'}]
            return httpx.Response(200, json={'status': 'completed', 'output': [
                {'type': 'message', 'content': [{'type': 'output_text', 'text': 'OK'}]}],
                'usage': {'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12}})
        assert request.headers['x-api-key'] == 'fixture-key'
        assert body['system'] == 'system rule'
        assert body['messages'] == [{'role': 'user', 'content': 'hello'}]
        return httpx.Response(200, json={'stop_reason': 'end_turn', 'content': [
            {'type': 'text', 'text': 'OK'}], 'usage': {'input_tokens': 10, 'output_tokens': 2}})
    service = AIService(store, OpenAICompatibleProvider(httpx.MockTransport(handle)))
    result = asyncio.run(service.complete(saved['id'], [ChatMessage(role='system', content='system rule'),
        ChatMessage(role='user', content='hello')], purpose='protocol-test'))
    assert result.text == 'OK' and result.usage['total_tokens'] == 12


def test_deepseek_migration_reuses_shared_profile_and_secret(store):
    from backend.app.ai.tool_store import ToolStore
    with store.db() as db:
        db.execute('CREATE TABLE ai_tool_settings(provider TEXT PRIMARY KEY,revision INTEGER,enabled INTEGER,secret BLOB,updated_at REAL)')
        db.execute('INSERT INTO ai_tool_settings VALUES (?,?,?,?,?)', ('deepseek', 1, 1, codec(b'fixture-key'), 0))
    tools = ToolStore(store)
    ident, config, key, endpoint, revision = tools.connection()
    assert key == 'fixture-key' and config.provider == 'deepseek'
    assert config.protocol == 'chat_completions' and config.model == 'deepseek-flash' and endpoint == 'https://api.deepseek.com/v1/tools'
    assert len(store.list()['profiles']) == 1
    ToolStore(store)
    assert len(store.list()['profiles']) == 1
    store.save(ident, ProfileEdit(revision=revision, config=config, api_key='replaced-key'))
    assert tools.credential() == 'replaced-key'
    with store.db() as db:
        assert db.execute('SELECT secret FROM ai_tool_settings').fetchone()[0] is None


def test_responses_stream_uses_completed_payload_and_usage(store):
    config = profile().model_copy(update={'api_format': 'responses'})
    saved = store.save(None, ProfileEdit(config=config))
    def handle(request):
        assert json.loads(request.content)['stream'] is True
        completed = {'type': 'response.completed', 'response': {'status': 'completed',
            'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'OK'}]}],
            'usage': {'input_tokens': 9, 'output_tokens': 1, 'total_tokens': 10}}}
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
            text='event: response.created\ndata: {"type":"response.created"}\n\n' +
                 'event: response.completed\ndata: ' + json.dumps(completed) + '\n\n')
    service = AIService(store, OpenAICompatibleProvider(httpx.MockTransport(handle)))
    result = asyncio.run(service.complete(saved['id'], [ChatMessage(role='user', content='hello')], purpose='test'))
    assert result.text == 'OK' and result.usage['total_tokens'] == 10

@pytest.mark.parametrize('model,provider,limit,efforts', [
    ('deepseek-flash', 'deepseek', 393216, ['none', 'low', 'high', 'max']),
    ('gpt-6-astra', 'openai', 128000, ['low', 'medium', 'high', 'xhigh', 'max']),
])
def test_official_model_limits_and_efforts_save_and_reload(store, model, provider, limit, efforts):
    config = ModelProfile(name=model, model=model, provider=provider, base_url='https://model.invalid/v1')
    assert config.max_output_tokens == limit and config.context_chars is None
    saved = store.save(None, ProfileEdit(config=config))
    for effort in efforts:
        saved = store.save(saved['id'], ProfileEdit(revision=saved['revision'],
            config=config.model_copy(update={'reasoning_effort': effort})))
        assert store.get(saved['id'])[0].reasoning_effort == effort
    with pytest.raises(ValueError):
        ProfileEdit(config=config.model_copy(update={'max_output_tokens': limit + 1}))
    invalid = 'medium' if provider == 'deepseek' else 'none'
    with pytest.raises(ValueError):
        ProfileEdit(config=config.model_copy(update={'reasoning_effort': invalid}))


def test_deepseek_native_adapter_and_long_input_have_no_legacy_character_cap(store):
    from backend.app.ai.service import DeepSeekProvider
    config = ModelProfile(name='DeepSeek', model='deepseek-flash', provider='deepseek',
        base_url='https://api.deepseek.com', reasoning_effort='max')
    saved = store.save(None, ProfileEdit(config=config, api_key='fixture-key'))
    def handle(request):
        body = json.loads(request.content)
        assert request.url.path == '/chat/completions'
        assert body['thinking'] == {'type': 'enabled'}
        assert body['reasoning_effort'] == 'max'
        assert body['max_tokens'] == 393216
        assert len(body['messages'][0]['content']) == 1048576
        return httpx.Response(200, json={'choices': [{'message': {'content': 'OK'}, 'finish_reason': 'stop'}]})
    service = AIService(store, DeepSeekProvider(httpx.MockTransport(handle)))
    result = asyncio.run(service.complete(saved['id'], [ChatMessage(role='user', content='a' * 1048576)], purpose='test'))
    assert result.text == 'OK'
    disabled = config.model_copy(update={'reasoning_effort': 'none'})
    _, body, _ = DeepSeekProvider().prepare_request(disabled, '', [ChatMessage(role='user', content='hello')], False)
    assert body['thinking'] == {'type': 'disabled'} and 'reasoning_effort' not in body


def test_save_api_explains_invalid_effort_without_echoing_secret(store):
    app = FastAPI()
    app.include_router(create_ai_router(AIService(store)))
    config = ModelProfile(name='Astra', model='gpt-6-astra', base_url='https://model.invalid/v1').model_dump()
    config['reasoning_effort'] = 'none'
    with TestClient(app) as client:
        response = client.post('/api/ai/profiles', json={'config': config, 'api_key': 'fixture-private-key'})
        assert response.status_code == 422
        assert 'max' in response.json()['detail']
        assert 'fixture-private-key' not in response.text
