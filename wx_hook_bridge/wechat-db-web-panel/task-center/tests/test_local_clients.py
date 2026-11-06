from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.main import create_app
from tests.conftest import P, PASSWORD, TEST_URL
from tests.test_center import FIELDS, create, cmd

CLOUD = 'https://jqr.47jsy.com'


@pytest.mark.parametrize('origin', ['http://127.0.0.1:5174', 'http://localhost:5195', 'http://[::1]:5174'])
def test_local_cookie_session_writes_and_logout(env, origin):
    app = create_app(Settings(database_url=TEST_URL, public_url=CLOUD))
    with TestClient(app, base_url=CLOUD, headers={'Origin': origin}) as client:
        response = client.post(P + '/auth/identify', json={'email': 'local-client@example.test'})
        assert response.status_code == 200
        cookie = response.headers['set-cookie']
        assert cookie.startswith('tc_remote_session=')
        assert 'HttpOnly' in cookie and 'Path=/api/task-center' in cookie and 'Secure' not in cookie
        me = client.get(P + '/me').json()
        assert not me['owner']
        assert client.post(P + '/tasks', json=FIELDS).status_code == 403
        client.headers['X-CSRF-Token'] = me['csrf']
        task = cmd(client, create(client), 'publish')
        assert task['status'] == 'open'
        assert client.post(P + '/auth/logout', json={}).status_code == 200
        assert 'tc_remote_session' not in client.cookies
        assert client.get(P + '/me').status_code == 401


def test_two_local_origins_and_direct_owner_share_one_center(env):
    app = create_app(Settings(database_url=TEST_URL, public_url=CLOUD))
    with TestClient(app, base_url=CLOUD, headers={'Origin': 'http://127.0.0.1:5195'}) as alice, \
         TestClient(app, base_url=CLOUD, headers={'Origin': 'http://localhost:5174'}) as bob, \
         TestClient(app, base_url=CLOUD, headers={'Origin': CLOUD}) as owner:
        for peer, email in ((alice, 'local-a@example.test'), (bob, 'local-b@example.test')):
            assert peer.post(P + '/auth/identify', json={'email': email}).status_code == 200
            peer.headers['X-CSRF-Token'] = peer.get(P + '/me').json()['csrf']
        response = owner.post(P + '/auth/owner-session', json={'login': 'owner', 'password': PASSWORD})
        assert response.status_code == 200
        assert response.headers['set-cookie'].startswith('__Host-tc_session=')
        assert 'Secure' in response.headers['set-cookie']
        owner.headers['X-CSRF-Token'] = owner.get(P + '/me').json()['csrf']
        task = cmd(alice, create(alice), 'publish')
        assert bob.get(P + '/tasks/' + task['id']).json()['created_by'] == task['created_by']
        task = cmd(bob, task, 'claim')
        task = cmd(bob, task, 'submit', text='Shared result', pr_links=['https://github.com/example/project/pull/1'])
        cmd(alice, task, 'complete', expected=403)
        cmd(bob, task, 'complete', expected=403)
        assert cmd(owner, task, 'complete')['status'] == 'done'
        assert alice.get(P + '/tasks/' + task['id']).json()['status'] == 'done'


@pytest.mark.parametrize('origin', [None, 'null', 'https://evil.test', 'http://localhost.evil.test:5174',
    'http://127.0.0.1:5174/path', 'http://user@localhost:5174', 'http://localhost:99999'])
def test_untrusted_origins_still_rejected(origin):
    settings = Settings(database_url=TEST_URL, public_url=CLOUD)
    assert not settings.allows_origin(origin)
    app = create_app(settings)
    with TestClient(app, base_url=CLOUD, headers={'Origin': origin} if origin else {}) as client:
        response = client.post(P + '/auth/identify', json={'email': 'rejected@example.test'})
        assert response.status_code == 403
        assert 'set-cookie' not in response.headers
        assert 'access-control-allow-origin' not in response.headers
