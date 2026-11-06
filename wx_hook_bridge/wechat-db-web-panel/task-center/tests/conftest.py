import os
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.config import Settings
from app.main import create_app

TEST_URL = os.getenv('TASK_CENTER_TEST_DATABASE_URL', 'postgresql+psycopg://postgres:task_center_test@127.0.0.1:55439/task_center_test')
PASSWORD = 'Local-test-only-1234'
P = '/api/task-center/v1'
ORIGIN = 'http://127.0.0.1:5195'


def settings():
    return Settings(database_url=TEST_URL, public_url=ORIGIN, local_http=True)


def login(client, username='owner'):
    response = client.post(P + ('/auth/owner-session' if username == 'owner' else '/auth/identify'), json={'login': username, 'password': PASSWORD} if username == 'owner' else {'email': username + '@example.test'}, headers={'Origin': ORIGIN})
    assert response.status_code == 200, response.text
    me = client.get(P + '/me').json()
    client.headers.update({'Origin': ORIGIN, 'X-CSRF-Token': me['csrf']})
    return me


@pytest.fixture
def env(monkeypatch):
    # These two explicitly named loopback databases are disposable test fixtures only.
    target = urlsplit(TEST_URL)
    assert target.hostname in ('localhost','127.0.0.1') and target.port == 55439
    assert target.path in ('/task_center_test','/task_center_unit_test')
    engine = create_engine(TEST_URL)
    with engine.begin() as db:
        names = db.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'tc_%'")).scalars().all()
        db.execute(text('TRUNCATE ' + ', '.join('"' + n + '"' for n in names) + ' CASCADE'))
    engine.dispose()
    # A task operation must never need an outbound HTTP client.
    import httpx
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('Unexpected outbound request')))
    app = create_app(settings())
    with TestClient(app, base_url=ORIGIN) as owner:
        app.state.auth.bootstrap('owner', 'Owner', PASSWORD)
        users = [login(owner)]
        peers = []
        for username in ('alice','bob'):
            peer = TestClient(app, base_url=ORIGIN, headers={'Origin': ORIGIN})
            assert peer.post(P + '/auth/identify', json={'email': username + '@example.test'}).status_code == 200
            me = peer.get(P + '/me').json()
            peer.headers['X-CSRF-Token'] = me['csrf']
            peers.append(peer)
            users.append(me)
        yield app, owner, peers[0], peers[1], users
        for peer in peers:
            peer.close()
