import asyncio
import sqlite3

from fastapi import FastAPI
import httpx
from fastapi.testclient import TestClient
import pytest

from backend.app.ai.errors import AIConfigurationError, AIError
from backend.app.ai.models import ModelEdit, ModelProfile, ModelSettings, ProviderEdit, ProviderSettings
from backend.app.ai.router import create_ai_router
from backend.app.ai.service import AIService
from backend.app.ai.store import AIStore
from backend.tests.test_ai import codec


def provider(**update):
    return ProviderSettings(**{'name': 'Relay', 'kind': 'openai', 'base_url': 'https://relay.invalid/v1', **update})


def legacy_database(path, rows):
    """Write the pre-provider schema: every profile carries its own endpoint and key."""
    db = sqlite3.connect(path)
    db.executescript('''
        CREATE TABLE ai_profiles (id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
            config TEXT NOT NULL, secret BLOB, updated_at REAL NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE ai_usage (id INTEGER PRIMARY KEY, profile_id TEXT NOT NULL, purpose TEXT NOT NULL,
            model TEXT NOT NULL, status TEXT NOT NULL, usage TEXT NOT NULL,
            elapsed_ms INTEGER NOT NULL, created_at REAL NOT NULL);
    ''')
    for ident, revision, config, key in rows:
        db.execute('INSERT INTO ai_profiles VALUES (?,?,?,?,0,0)',
                   (ident, revision, config.model_dump_json(), codec(key.encode()) if key else None))
    db.commit()
    db.close()


def test_migration_groups_profiles_by_endpoint_and_key_and_keeps_ids(tmp_path):
    relay = 'https://relay.invalid/v1'
    legacy_database(tmp_path / 'ai.sqlite', [
        ('sol', 3, ModelProfile(name='Relay · sol', provider='openai', base_url=relay, model='sol'), 'relay-key'),
        ('astra', 2, ModelProfile(name='Relay · astra', provider='openai', base_url=relay, model='astra'), 'relay-key'),
        ('luna', 5, ModelProfile(name='Relay · Luna 决策', provider='openai_decisions', base_url=relay, model='luna'), 'relay-key'),
        ('other', 1, ModelProfile(name='Other key', provider='openai', base_url=relay, model='sol'), 'other-key'),
    ])
    store = AIStore(tmp_path / 'ai.sqlite', secret_codec=codec)
    listed = store.list()
    assert len(listed['providers']) == 2
    by_id = {row['id']: row for row in listed['profiles']}
    assert by_id['sol']['provider_id'] == by_id['astra']['provider_id'] == by_id['luna']['provider_id']
    assert by_id['other']['provider_id'] != by_id['sol']['provider_id']
    # Revisions are preserved so runs pinned to a model revision stay valid.
    assert [by_id[name]['revision'] for name in ('sol', 'astra', 'luna')] == [3, 2, 5]
    luna, key, _ = store.get('luna')
    assert luna.decision_only and luna.protocol == 'openai_decisions' and key == 'relay-key'
    assert by_id['luna']['config']['name'] == 'Relay · Luna 决策'
    assert store.get('other')[1] == 'other-key'
    AIStore(tmp_path / 'ai.sqlite', secret_codec=codec)
    assert len(store.list()['providers']) == 2


def test_provider_key_is_shared_and_connection_changes_bump_model_revisions(tmp_path):
    store = AIStore(tmp_path / 'ai.sqlite', secret_codec=codec)
    created = store.save_provider(None, ProviderEdit(config=provider(), api_key='first-key'))
    first = store.save_model(None, ModelEdit(provider_id=created['id'], config=ModelSettings(model='sol')))
    second = store.save_model(None, ModelEdit(provider_id=created['id'], config=ModelSettings(model='astra', label='Astra')))
    assert store.get(first['id'])[1] == store.get(second['id'])[1] == 'first-key'
    assert second['config']['name'] == 'Relay · Astra'

    renamed = store.save_provider(created['id'], ProviderEdit(revision=created['revision'], config=provider(name='Renamed')))
    assert store.get(first['id'])[2] == 1 and store.get(first['id'])[0].name == 'Renamed · sol'

    store.save_provider(created['id'], ProviderEdit(revision=renamed['revision'], config=provider(name='Renamed'),
                                                    api_key='second-key'))
    config, key, revision = store.get(second['id'])
    assert key == 'second-key' and revision == 2
    assert b'second-key' not in store.path.read_bytes()


def test_provider_rules_disable_and_cascade_delete(tmp_path):
    store = AIStore(tmp_path / 'ai.sqlite', secret_codec=codec)
    created = store.save_provider(None, ProviderEdit(config=provider(), api_key='key'))
    model = store.save_model(None, ModelEdit(provider_id=created['id'], config=ModelSettings(model='sol')))
    with pytest.raises(AIConfigurationError, match='重新填写密钥'):
        store.save_provider(created['id'], ProviderEdit(revision=1, config=provider(base_url='https://moved.invalid/v1')))
    with pytest.raises(AIConfigurationError, match='供应商不存在'):
        store.save_model(None, ModelEdit(provider_id='missing', config=ModelSettings(model='sol')))
    disabled = store.save_provider(created['id'], ProviderEdit(revision=1, config=provider(enabled=False)))
    with pytest.raises(AIConfigurationError, match='停用'):
        store.get(model['id'])
    assert not provider(kind='openai', search_fetch=True).search_fetch
    store.delete_provider(created['id'], disabled['revision'])
    assert store.list()['providers'] == [] and store.list()['profiles'] == []
    with pytest.raises(AIConfigurationError):
        store.get(model['id'])


def test_provider_and_model_endpoints_never_echo_keys(tmp_path):
    store = AIStore(tmp_path / 'ai.sqlite', secret_codec=codec)
    app = FastAPI()
    app.include_router(create_ai_router(AIService(store)))
    with TestClient(app) as client:
        created = client.post('/api/ai/providers', json={'config': provider().model_dump(), 'api_key': 'fixture-private-key'})
        assert created.status_code == 200 and created.json()['api_key_set']
        assert 'fixture-private-key' not in created.text
        model = client.post('/api/ai/models', json={'provider_id': created.json()['id'],
                                                    'config': {'model': 'gpt-6-astra', 'reasoning_effort': 'none'}})
        assert model.status_code == 422 and 'max' in model.json()['detail']
        model = client.post('/api/ai/models', json={'provider_id': created.json()['id'], 'config': {'model': 'sol'}})
        assert model.status_code == 200 and model.json()['config']['name'] == 'Relay · sol'
        listed = client.get('/api/ai/profiles').json()
        assert len(listed['providers']) == 1 and len(listed['profiles']) == 1
        assert 'fixture-private-key' not in client.get('/api/ai/profiles').text
        removed = client.delete(f"/api/ai/providers/{created.json()['id']}", params={'revision': 1})
        assert removed.status_code == 200


def discovery_service(tmp_path, kind, base_url, handler):
    store = AIStore(tmp_path / 'ai.sqlite', secret_codec=codec)
    created = store.save_provider(None, ProviderEdit(config=provider(kind=kind, base_url=base_url), api_key='fixture-key'))
    return AIService(store, transport=httpx.MockTransport(handler)), created['id']


@pytest.mark.parametrize('kind,base_url,path,auth', [
    ('openai', 'https://relay.invalid/v1', '/v1/models', ('authorization', 'Bearer fixture-key')),
    ('deepseek', 'https://api.deepseek.invalid', '/models', ('authorization', 'Bearer fixture-key')),
    ('anthropic', 'https://claude.invalid/v1', '/v1/models', ('x-api-key', 'fixture-key')),
])
def test_discover_models_uses_provider_endpoint_and_credentials(tmp_path, kind, base_url, path, auth):
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={'data': [{'id': 'model-b'}, {'id': 'model-a', 'display_name': 'Model A'},
                                                  {'id': 'model-a'}, {'object': 'no id'}]})
    service, ident = discovery_service(tmp_path, kind, base_url, handle)
    result = asyncio.run(service.list_models(ident))
    assert [item['id'] for item in result['models']] == ['model-a', 'model-b']
    assert seen[0].url.path == path and seen[0].headers[auth[0]] == auth[1]
    assert (seen[0].url.params.get('limit') == '1000') == (kind == 'anthropic')


@pytest.mark.parametrize('status,message', [(401, '密钥无效'), (404, '手动填写'), (500, 'HTTP 500')])
def test_discover_models_explains_failures_without_echoing_body(tmp_path, status, message):
    service, ident = discovery_service(tmp_path, 'openai', 'https://relay.invalid/v1',
                                       lambda request: httpx.Response(status, text='upstream secret detail'))
    with pytest.raises(AIError, match=message) as error:
        asyncio.run(service.list_models(ident))
    assert 'upstream secret detail' not in str(error.value)


def test_batch_add_skips_existing_models_and_endpoints_work(tmp_path):
    handler = lambda request: httpx.Response(200, json={'data': [{'id': 'sol'}, {'id': 'astra'}]})
    service, ident = discovery_service(tmp_path, 'openai', 'https://relay.invalid/v1', handler)
    service.store.save_model(None, ModelEdit(provider_id=ident, config=ModelSettings(model='sol')))
    app = FastAPI()
    app.include_router(create_ai_router(service))
    with TestClient(app) as client:
        found = client.post(f'/api/ai/providers/{ident}/discover-models')
        assert found.status_code == 200 and len(found.json()['models']) == 2
        added = client.post(f'/api/ai/providers/{ident}/models', json={'models': [
            {'model': 'sol'}, {'model': 'astra'}, {'model': 'astra'},
            {'model': 'luna', 'api_format': 'openai_decisions'}]})
        assert added.status_code == 200
        assert [item['model']['model'] for item in added.json()['items']] == ['astra', 'luna']
        assert added.json()['items'][1]['decision_only']
        assert client.post(f'/api/ai/providers/{ident}/models', json={'models': []}).status_code == 422
    assert len(service.store.list()['profiles']) == 3
