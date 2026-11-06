import asyncio
import copy
import json
import sqlite3
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app.contact_data import fields, in_directory, profile_extra, read_directory
from backend.app.contacts import RemarkEdit
from backend.app.live_chat import LiveChat, create_live_router


def varint(value):
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def field(number, value):
    if isinstance(value, str):
        value = value.encode()
    return varint(number << 3) + varint(value) if isinstance(value, int) else varint(number << 3 | 2) + varint(len(value)) + value


def test_known_profile_fields_and_unknown_data_are_separated():
    payload = b''.join(field(k, v) for k, v in {2: 2, 4: 'Fixture signature', 5: 'CN', 6: 'Guangdong', 7: 'Zhaoqing', 8: 14, 41: 1789352295, 90: 'PRIVATE-TICKET'}.items())
    profile = profile_extra(payload)
    assert profile['gender'] == 'female'
    assert profile['signature'] == 'Fixture signature'
    assert profile['region'] == {'country': 'CN', 'province': 'Guangdong', 'city': 'Zhaoqing'}
    assert profile['source_label'] == '通过群聊添加'
    assert profile['added_date'] == '2026-09-14'
    assert 'PRIVATE-TICKET' not in json.dumps(profile)


@pytest.mark.parametrize('payload', [b'\x22\x7fshort', b'\x80', b'\x00', b'\x09abc', b'x' * 300000, None], ids=['truncated', 'varint', 'zero', 'fixed', 'oversized', 'none'])
def test_bad_extra_buffer_does_not_invent_contact_profile(payload):
    assert fields(payload) == {}
    assert profile_extra(payload)['added_date'] is None
    assert profile_extra(payload)['gender'] is None


@pytest.mark.parametrize('username,kind,deleted,flag,expected', [
    ('friend', 1, 0, 3, True), ('self', 1, 0, 1, True), ('enterprise@openim', 1, 0, 3, True),
    ('former', 1, 1, 3, False), ('member', 3, 0, 4, False), ('room@chatroom', 2, 0, 2, False),
    ('weixin', 1, 0, 3, True), ('newsapp', 1, 0, 3, False), ('medianote', 1, 0, 1, False),
    ('fmessage', 1, 0, 1, False), ('gh_public', 1, 0, 3, False), ('stranger', 1, 0, 0, False),
])
def test_directory_membership_not_inferred_from_chat_history(username, kind, deleted, flag, expected):
    assert in_directory({'username': username, 'local_type': kind, 'delete_flag': deleted, 'flag': flag}) is expected


def test_directory_pinyin_and_common_groups_use_snapshot_identity():
    db = sqlite3.connect(':memory:')
    db.executescript('''CREATE TABLE contact(id INTEGER,username TEXT,local_type INTEGER,flag INTEGER,delete_flag INTEGER,nick_name TEXT,remark TEXT,alias TEXT,quan_pin TEXT,remark_quan_pin TEXT,is_in_chat_room INTEGER,extra_buffer BLOB);
        CREATE TABLE name2id(username TEXT); CREATE TABLE chatroom_member(room_id INTEGER,member_id INTEGER);
        INSERT INTO contact VALUES (1,'friend',1,3,0,'朋友','张三','alias','PengYou','ZhangSan',0,NULL);
        INSERT INTO contact VALUES (2,'self',1,1,0,'Self','','','Self','',0,NULL);
        INSERT INTO contact VALUES (3,'room@chatroom',2,2,0,'Room','','','',NULL,1,NULL);
        INSERT INTO contact VALUES (4,'left@chatroom',2,2,0,'Left','','','',NULL,0,NULL);
        INSERT INTO contact VALUES (5,'member',3,4,0,'Stranger','','','',NULL,0,NULL);
        INSERT INTO name2id VALUES ('friend'),('self'),('room@chatroom'),('left@chatroom'),('member');
        INSERT INTO chatroom_member VALUES(3,1),(3,2),(4,1),(3,5);''')
    rows = read_directory(db, 'self')
    assert len(rows) == 2
    friend = next(r for r in rows if r['username'] == 'friend')
    assert friend['display_name'] == '张三' and friend['initial'] == 'Z'
    assert 'zhangsan' in friend['search_text'] and 'alias' in friend['search_text']
    assert friend['common_groups'] == [{'username': 'room@chatroom', 'display_name': 'Room'}]
    assert next(r for r in rows if r['username'] == 'self')['is_self']
    db.close()


def contact(remark=''):
    return {'username': 'peer', 'display_name': remark or 'Alice', 'nickname': 'Alice', 'remark': remark,
            'alias': 'alice-id', 'initial': 'A', 'sort_key': 'alice', 'search_text': f'alice peer alice-id {remark}'.casefold(),
            'is_self': False, 'common_groups': [], 'common_groups_known': True, **profile_extra(None)}


class Roster:
    remark = ''
    def read(self, account):
        name = self.remark or 'Alice'
        return {'account': account, 'source': 'active_contact_db', 'directory': [contact(self.remark)],
                'contacts': [{'username': 'peer', 'name': name, 'relation': 'friend'}], 'names': [{'username': 'peer', 'name': name}]}


@pytest.fixture
def service(tmp_path):
    value = LiveChat(tmp_path / 'live.sqlite', 'http://127.0.0.1:1', tmp_path / 'relay.json', roster=Roster())
    value.active_account, value.mode, value.writes = 'self', 'confirmed', []
    async def bridge(path, body, timeout=8):
        if path == '/api/userInfo':
            return {'code': 1, 'data': {'wxid': value.active_account, 'nickname': 'Self'}}
        assert path == '/api/modifyRemark'
        value.writes.append(copy.deepcopy(body))
        if value.mode in {'confirmed', 'timeout-confirmed'}:
            value.roster.remark = body['remark']
        if value.mode.startswith('timeout'):
            raise httpx.ReadTimeout('fixture timeout')
        if value.mode == 'switch':
            value.active_account = 'other'
        return {'code': 0 if value.mode == 'failed' else 1}
    value.bridge = bridge
    return value


def edit(remark='New remark', expected='', request=None):
    return RemarkEdit(account='self', remark=remark, expected_remark=expected, request_id=request or uuid4())


def test_directory_api_shape_excludes_private_fields_and_is_account_scoped(service):
    assert not service.contacts.list('self')['ready']
    asyncio.run(service.sync_contacts('self'))
    result = service.contacts.list('self', 'ALICE-ID')
    assert result['ready'] and result['directory_count'] == 1 and result['total'] == 1
    assert 'search_text' not in result['items'][0]
    assert service.contacts.list('other')['items'] == []
    with pytest.raises(HTTPException):
        service.contacts.detail('other', 'peer')
    assert service.detail('self', 'peer')['messages'] == []


def test_recent_legacy_roster_does_not_delay_first_directory_sync(service):
    import time
    with service.db() as db:
        db.execute('INSERT INTO live_roster_state VALUES (?,?,1)', ('self', time.time()))
    async def scenario():
        service.refresh_contacts('self')
        assert 'self' in service.contact_tasks
        await service.contact_tasks['self']
        assert service.contacts.list('self')['ready']
    asyncio.run(scenario())


def test_remark_requires_readback_and_is_idempotent(service):
    request = edit()
    result = asyncio.run(service.contacts.edit('peer', request))
    assert result['status'] == 'confirmed'
    assert service.contacts.detail('self', 'peer')['remark'] == 'New remark'
    assert service.sessions('self', '', 'all', 0, 10)['items'][0]['display_name'] == 'New remark'
    assert asyncio.run(service.contacts.edit('peer', request))['status'] == 'confirmed'
    assert len(service.writes) == 1
    with pytest.raises(HTTPException) as error:
        asyncio.run(service.contacts.edit('peer', edit('different', request=request.request_id)))
    assert error.value.status_code == 409


def test_remark_conflict_never_dispatches(service):
    service.roster.remark = 'Changed in WeChat'
    with pytest.raises(HTTPException) as error:
        asyncio.run(service.contacts.edit('peer', edit()))
    assert error.value.status_code == 409 and service.writes == []


def test_builtin_directory_contact_does_not_offer_remark_writes(service):
    profile = {**contact(), 'username': 'weixin', 'is_builtin': True}
    assert not service.contacts.present('self', profile, True)['can_edit_remark']


@pytest.mark.parametrize('mode,status', [('pending', 'pending'), ('timeout', 'unknown'), ('failed', 'failed'), ('timeout-confirmed', 'confirmed')])
def test_delivery_status_never_becomes_false_success(service, mode, status):
    service.mode = mode
    request = edit()
    assert asyncio.run(service.contacts.edit('peer', request))['status'] == status
    assert asyncio.run(service.contacts.edit('peer', request))['status'] == status
    assert len(service.writes) == 1


def test_unconfirmed_remark_blocks_new_dispatch_and_background_refresh_confirms(service):
    service.mode = 'pending'
    request = edit()
    assert asyncio.run(service.contacts.edit('peer', request))['status'] == 'pending'
    with pytest.raises(HTTPException) as error:
        asyncio.run(service.contacts.edit('peer', edit('Second change')))
    assert error.value.status_code == 409 and len(service.writes) == 1
    service.roster.remark = request.remark
    asyncio.run(service.sync_contacts('self'))
    assert service.contacts.receipt('self', str(request.request_id))['status'] == 'confirmed'


def test_account_switch_during_write_never_returns_old_account_success(service):
    service.mode = 'switch'
    with pytest.raises(HTTPException) as error:
        asyncio.run(service.contacts.edit('peer', edit()))
    assert error.value.status_code == 409
    assert len(service.writes) == 1
    assert service.contacts.list('other')['items'] == []


def test_new_friend_records_hide_tickets_and_report_only_known_membership(service, tmp_path):
    assert not service.contacts.new_friends('self')['ready']
    asyncio.run(service.sync_contacts('self'))
    path = tmp_path / 'general.sqlite'
    db = sqlite3.connect(path)
    db.executescript('CREATE TABLE FMessageTable(user_name_,content_,timestamp_,type_,is_sender_,ticket_);')
    db.executemany('INSERT INTO FMessageTable VALUES (?,?,?,?,?,?)', [
        ('peer', '<msg fromnickname="Alice" content="Hello" ticket="SECRET"/>', 1000, 37, 0, 'SECRET'),
        ('stranger', 'Please add me', 1001, 37, 0, 'SECRET'),
        ('ignored', 'Outgoing', 1002, 37, 1, 'SECRET')])
    db.commit(); db.close()
    with service.messages.db() as db:
        db.execute('INSERT INTO chat_history_state VALUES (?,?)', ('self', json.dumps({'databases': {'general/general.db': str(path)}})))
    result = service.contacts.new_friends('self')
    assert result['ready'] and len(result['items']) == 2
    assert result['items'][0]['username'] == '' and not result['items'][0]['in_contacts']
    assert result['items'][1]['in_contacts'] and result['items'][1]['content'] == 'Hello'
    assert 'SECRET' not in json.dumps(result)


def test_contact_routes_are_mounted_and_reject_wrong_account_and_origin(service):
    asyncio.run(service.sync_contacts('self'))
    app = FastAPI(); app.include_router(create_live_router(service))
    with TestClient(app) as client:
        assert client.get('/api/chat/contacts?account=self').status_code == 200
        assert client.get('/api/chat/contacts/peer?account=self').status_code == 200
        assert client.get('/api/chat/contacts/new-friends?account=self').json()['ready'] is False
        assert client.get('/api/chat/contacts/peer?account=other').status_code == 409
        body = edit().model_dump(mode='json')
        assert client.post('/api/chat/contacts/peer/remark', json=body, headers={'Origin': 'https://untrusted.example'}).status_code == 403
        assert service.writes == []
