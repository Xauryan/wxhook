import asyncio
import json
import sqlite3
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app.integration_data import HongbaoControl, read_automation_directory
from backend.app.integrations import ConfigEdit, FriendConfig, GroupConfig, Integrations, create_integrations_router
from backend.app.live_chat import LiveChat


class Roster:
    def __init__(self):
        self.account = 'self_a'
        self.friends = [{'username': 'peer', 'encrypt_username': 'v3_peer'}]
        self.groups = [{'username': 'room@chatroom', 'name': 'Fixture group', 'role': 'owner',
                        'can_manage': True, 'reason': 'owner', 'members': ['self_a'], 'members_known': True}]

    async def verify_async(self, account):
        if account != self.account:
            raise ValueError('account changed')
        return {'account': account, 'pid': 12, 'created_at': 1}

    async def read_async(self, account):
        await self.verify_async(account)
        return {'account': account, 'source': 'active_contact_db',
                'contacts': [{'username': f['username'], 'name': f['username'], 'relation': 'friend'} for f in self.friends],
                'names': [], 'automation': {'friends': self.friends, 'groups': self.groups}}


@pytest.fixture
def service(tmp_path):
    roster = Roster()
    live = LiveChat(tmp_path / 'live.sqlite', 'http://127.0.0.1:19088', tmp_path / 'relay.json', roster=roster)
    calls = []
    async def bridge(path, payload, timeout=8):
        if path == '/api/userInfo':
            return {'code': 1, 'data': {'wxid': roster.account, 'nickname': 'Fixture'}}
        calls.append((path, payload))
        return {'code': 1, 'data': {'ok': True}}
    live.bridge = bridge
    live.native_enabled = False
    original_bridge = live.bridge
    async def with_control(path, payload, timeout=8):
        if path == '/api/autoAcceptFriend':
            if 'enable' in payload:
                live.native_enabled = payload['enable']
            return {'code': 1, 'data': {'enabled': live.native_enabled}}
        return await original_bridge(path, payload, timeout)
    live.bridge = with_control
    native = SimpleNamespace(status=lambda _: {'available': True, 'enabled': False})
    result = Integrations(live, tmp_path / 'integrations.sqlite', hongbao=native)
    result.calls = calls
    result._task = SimpleNamespace(done=lambda: False)
    return result


def save(service, feature, **config):
    account = service.live.roster.account
    current = service.config(account, feature)
    return asyncio.run(service.save(feature, ConfigEdit(account=account, revision=current['revision'],
                                                       config={**current['config'], **config})))


def event(service, number=1, **fields):
    return service.live.ingest({'event_id': f'fixture-{number}', 'selfWxId': 'self_a',
                               'peerWxId': 'peer', 'senderWxId': 'peer', 'msgId': str(number),
                               'localId': str(number), 'type': 1, 'timestamp_unix': int(time.time()),
                               'direction': 'incoming', 'content': '进群', **fields})


def actions(service):
    with service.db() as db:
        return [dict(row) for row in db.execute('SELECT * FROM integration_actions ORDER BY rowid')]


def test_default_off_persistence_and_account_isolation(service):
    assert not service.config('self_a', 'friends')['config']['enabled']
    save(service, 'friends', enabled=True, reply_enabled=True, reply_text='Hello fixture')
    restored = Integrations(service.live, service.path)
    assert restored.config('self_a', 'friends')['config']['reply_text'] == 'Hello fixture'
    assert not restored.config('self_b', 'friends')['config']['enabled']
    assert not service.calls


def test_stale_revision_and_wrong_account_refused(service):
    save(service, 'friends', enabled=True)
    for account, expected in [('self_a', 409), ('self_b', 409)]:
        with pytest.raises(HTTPException) as error:
            asyncio.run(service.save('friends', ConfigEdit(account=account, revision=0, config={})))
        assert error.value.status_code == expected
    assert not service.calls


def test_existing_native_mode_is_reported_and_only_explicit_save_takes_over(service):
    service.live.native_enabled = True
    assert asyncio.run(service.status())['native_friends']['enabled'] is True
    assert service.live.native_enabled is True
    save(service, 'friends', enabled=False)
    assert service.live.native_enabled is False
    assert not service.calls


def test_external_native_reenable_blocks_duplicate_accept(service):
    save(service, 'friends', enabled=True)
    service.live.native_enabled = True
    friend_event(service)
    asyncio.run(service.run_once())
    assert not service.calls
    assert actions(service)[0]['state'] == 'blocked'


@pytest.mark.parametrize('model,config', [(FriendConfig, {'reply_enabled': True}),
                                        (FriendConfig, {'reply_enabled': True, 'reply_text': '  '}),
                                        (FriendConfig, {'enabled': 'true'}),
                                        (FriendConfig, {'extra': True}),
                                        (GroupConfig, {'enabled': True}),
                                        (GroupConfig, {'keyword': ' '}),
                                        (GroupConfig, {'room': 'peer'})])
def test_invalid_config(model, config):
    with pytest.raises(ValueError):
        model.model_validate(config)


def test_permission_checked_at_enable_and_dispatch(service):
    service.live.roster.groups[0]['can_manage'] = False
    with pytest.raises(HTTPException) as error:
        save(service, 'groups', enabled=True, room='room@chatroom')
    assert error.value.status_code == 403
    service.live.roster.groups[0]['can_manage'] = True
    save(service, 'groups', enabled=True, room='room@chatroom')
    service.live.roster.groups[0]['can_manage'] = False
    event(service)
    asyncio.run(service.run_once())
    assert not service.calls
    assert actions(service)[0]['state'] == 'blocked'


def test_only_new_incoming_private_exact_keyword_and_friends(service):
    event(service, 1)
    save(service, 'groups', enabled=True, room='room@chatroom')
    event(service, 2, direction='outgoing')
    event(service, 3, room_id='another@chatroom')
    event(service, 4, content='请进群')
    event(service, 5, senderWxId='stranger', peerWxId='stranger')
    event(service, 6, timestamp_unix=int(time.time()) - 600)
    event(service, 7, selfWxId='self_b')
    event(service, 8, direction='unknown')
    asyncio.run(service.run_once())
    assert not service.calls
    event(service, 9, content=' 进群 ')
    asyncio.run(service.run_once())
    assert [path for path, _ in service.calls] == ['/api/addMemberToChatRoom']
    assert service.calls[0][1]['memberIds'] == 'peer'


def test_duplicate_cooldown_and_already_in_room(service):
    save(service, 'groups', enabled=True, room='room@chatroom')
    event(service, 1)
    event(service, 1)
    event(service, 2)
    asyncio.run(service.run_once())
    assert len(service.calls) == 1
    service.live.roster.groups[0]['members'].append('peer')
    event(service, 3)
    asyncio.run(service.run_once())
    assert len(service.calls) == 1
    assert actions(service)[-1]['state'] == 'skipped'


def test_large_group_uses_invitation_without_fallback(service):
    service.live.roster.groups[0]['members'] = [f'member-{i}' for i in range(40)]
    save(service, 'groups', enabled=True, room='room@chatroom')
    original = service.live.bridge
    async def timeout(path, payload, timeout=8):
        if path == '/api/userInfo':
            return await original(path, payload)
        service.calls.append((path, payload))
        raise httpx.ReadTimeout('fixture timeout')
    service.live.bridge = timeout
    event(service)
    asyncio.run(service.run_once())
    asyncio.run(service.run_once())
    assert len(service.calls) == 1
    assert service.calls[0][0] == '/api/InviteMemberToChatRoom'
    assert actions(service)[0]['state'] == 'unknown'


def friend_event(service, number=1):
    event(service, number, type=37, content='', encrypt_username='v3_new',
          candidate_username='new_friend', ticket='fixture-ticket', scene=3)


def test_accept_then_reply_only_after_verified_friend_relation(service):
    save(service, 'friends', enabled=True, reply_enabled=True, reply_text='Fixture welcome')
    friend_event(service)
    asyncio.run(service.run_once())
    assert [path for path, _ in service.calls] == ['/api/acceptFriend']
    assert actions(service)[-1]['state'] == 'pending'
    service.live.roster.friends.append({'username': 'new_friend', 'encrypt_username': 'v3_new'})
    asyncio.run(service.run_once())
    asyncio.run(service.run_once())
    assert [path for path, _ in service.calls] == ['/api/acceptFriend', '/api/sendTextMsg']
    assert service.calls[-1][1] == {'wxid': 'new_friend', 'msg': 'Fixture welcome'}
    assert 'fixture-ticket' not in json.dumps(actions(service))


def test_disable_cancels_pending_replies(service):
    save(service, 'friends', enabled=True, reply_enabled=True, reply_text='Fixture welcome')
    friend_event(service)
    asyncio.run(service.run_once())
    save(service, 'friends', enabled=False)
    service.live.roster.friends.append({'username': 'new_friend', 'encrypt_username': 'v3_new'})
    asyncio.run(service.run_once())
    assert len(service.calls) == 1
    assert actions(service)[-1]['state'] == 'skipped'


def test_restart_does_not_replay_history_or_pending_welcome(service):
    save(service, 'friends', enabled=True)
    friend_event(service)
    async def scenario():
        service._task = None
        await service.start()
        await asyncio.sleep(.02)
        await service.stop()
    asyncio.run(scenario())
    assert not service.calls


def test_account_switch_before_dispatch_stops_execution(service):
    save(service, 'groups', enabled=True, room='room@chatroom')
    event(service)
    service.live.roster.account = 'self_b'
    with pytest.raises(HTTPException):
        asyncio.run(service.run_once())
    assert not service.calls


def test_api_origin_gate_and_body_validation(service):
    app = FastAPI()
    app.include_router(create_integrations_router(service))
    with TestClient(app) as client:
        body = {'account': 'self_a', 'revision': 0, 'config': {'enabled': True}}
        assert client.post('/api/integrations/friends', json=body, headers={'Origin': 'https://external.invalid'}).status_code == 403
        assert client.post('/api/integrations/friends', json={**body, 'config': {'enabled': 'yes'}}).status_code == 422
        assert client.post('/api/integrations/friends', json=body).status_code == 200
        assert client.get('/api/integrations').json()['friends']['config']['enabled']


@pytest.mark.parametrize('group_type', [1, 2])
def test_group_permission_reader_never_guesses_admin_flags(group_type):
    db = sqlite3.connect(':memory:')
    try:
        db.executescript('''CREATE TABLE contact(username TEXT,local_type INTEGER,is_in_chat_room INTEGER,
            delete_flag INTEGER,flag INTEGER,remark TEXT,nick_name TEXT,encrypt_username TEXT);
            CREATE TABLE chat_room(username TEXT,owner TEXT,ext_buffer BLOB);
            CREATE TABLE name2id(username TEXT); CREATE TABLE chatroom_member(room_id INTEGER,member_id INTEGER);
            INSERT INTO contact VALUES ('own@chatroom',2,1,0,0,'Own','',''),('other@chatroom',2,1,0,0,'Other','',''),
              ('left@chatroom',2,0,0,0,'Left','',''),('peer',1,0,0,1,'Peer','','v3_peer');
            INSERT INTO chat_room VALUES ('own@chatroom','self_a',NULL),('other@chatroom','another',X'1811');
            INSERT INTO name2id VALUES ('own@chatroom'),('self_a');
            INSERT INTO chatroom_member VALUES (1,2);''')
        db.execute("UPDATE contact SET local_type=? WHERE username='own@chatroom'", (group_type,))
        result = read_automation_directory(db, 'self_a')
        assert len(result['groups']) == 2
        assert result['groups'][0]['can_manage']
        assert result['groups'][0]['members'] == ['self_a']
        assert not result['groups'][1]['can_manage']
        assert result['friends'] == [{'username': 'peer', 'encrypt_username': 'v3_peer'}]
    finally:
        db.close()


def test_hongbao_control_preserves_existing_flags_and_delay(tmp_path, monkeypatch):
    controller = HongbaoControl(tmp_path)
    monkeypatch.setattr(controller, '_verify', lambda _: None)
    (tmp_path / 'hb_cold_delay.json').write_text('{"delay_ms":"10-200"}')
    fastlane = tmp_path / 'hb_cold_fastlane.enable'
    fastlane.touch()
    assert controller.status({})['enabled'] is True
    assert not (tmp_path / 'hb_cold.disable').exists()
    assert controller.set_enabled({}, False, True)['enabled'] is False
    assert (tmp_path / 'hb_cold.disable').exists()
    with pytest.raises(ValueError):
        controller.set_enabled({}, True, True)
    assert controller.set_enabled({}, True, False)['enabled'] is True
    assert controller.status({})['delay_ms'] == '10-200'
    assert fastlane.exists()


def test_hongbao_unknown_is_not_false_and_cannot_write(tmp_path, monkeypatch):
    controller = HongbaoControl(tmp_path)
    def failure(_):
        raise ValueError('not bound')
    monkeypatch.setattr(controller, '_verify', failure)
    assert controller.status({})['enabled'] is None
    with pytest.raises(ValueError):
        controller.set_enabled({}, False, True)
    assert not list(tmp_path.iterdir())


def test_hongbao_delay_api_roundtrip_and_concurrent_edit(service, tmp_path, monkeypatch):
    controller = HongbaoControl(tmp_path)
    monkeypatch.setattr(controller, '_verify', lambda _: None)
    service.hongbao = controller
    disabled = tmp_path / 'hb_cold.disable'
    disabled.touch()
    app = FastAPI()
    app.include_router(create_integrations_router(service))
    body = {'account': 'self_a', 'delay_ms': ' 300 - 800 ', 'expected_delay_ms': '0'}
    with TestClient(app) as client:
        assert client.post('/api/integrations/hongbao/delay', json=body,
                           headers={'Origin': 'https://external.invalid'}).status_code == 403
        assert client.post('/api/integrations/hongbao/delay', json={**body, 'account': 'self_b'}).status_code == 409
        saved = client.post('/api/integrations/hongbao/delay', json=body)
        assert saved.status_code == 200
        assert saved.json()['delay_ms'] == '300-800'
        assert saved.json()['enabled'] is False
        assert disabled.exists()
        assert json.loads((tmp_path / 'hb_cold_delay.json').read_text()) == {'delay_ms': '300-800'}
        assert client.post('/api/integrations/hongbao/delay', json=body).status_code == 409
        assert client.get('/api/integrations').json()['hongbao']['delay_ms'] == '300-800'
        assert client.post('/api/integrations/hongbao/delay', json={**body, 'delay_ms': '600000', 'expected_delay_ms': '300-800'}).json()['delay_ms'] == '600000'
    assert not service.calls


@pytest.mark.parametrize('delay', ['-1', '800-300', '600001', '1-600001', '1.5', '', 'abc', 300, True])
def test_hongbao_delay_rejects_invalid_input(service, delay):
    app = FastAPI()
    app.include_router(create_integrations_router(service))
    with TestClient(app) as client:
        result = client.post('/api/integrations/hongbao/delay', json={
            'account': 'self_a', 'delay_ms': delay, 'expected_delay_ms': '0'})
        assert result.status_code == 422
    assert not service.calls
