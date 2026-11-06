import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.app.ai.errors import AIConfigurationError
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
