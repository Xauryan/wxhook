import asyncio
import hashlib
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app.active_roster import ActiveRosterError
from backend.app.chat_history import ActiveHistory
from backend.app.chat_messages import ChatMessages
from backend.app.live_chat import LiveChat, SendText


@pytest.fixture
def snapshot(tmp_path):
    path = tmp_path / 'source.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE Name2Id(user_name TEXT)')
        db.executemany('INSERT INTO Name2Id VALUES (?)', [('self_a',), ('peer',), ('room@chatroom',)])
        for session in ('peer', 'room@chatroom', 'other'):
            table = 'Msg_' + hashlib.md5(session.encode()).hexdigest()
            db.execute(f'CREATE TABLE "{table}" (local_id,server_id,local_type,sort_seq,real_sender_id,create_time,message_content,compress_content)')
    return {'account': 'self_a', 'root': str(tmp_path), 'databases': {'message/message_0.db': str(path)},
            'fingerprint': 'fixture', 'verified_at': 1001, 'read_started_at': 1001}


def source_message(snapshot, ident=1, session='peer', author=1, timestamp=1000, body=b'hello', server=None, kind=1):
    table = 'Msg_' + hashlib.md5(session.encode()).hexdigest()
    with sqlite3.connect(snapshot['databases']['message/message_0.db']) as db:
        db.execute(f'INSERT INTO "{table}" VALUES (?,?,?,?,?,?,?,NULL)',
                   (ident, ident if server is None else server, kind, ident, author, timestamp, body))


def history(tmp_path):
    return ActiveHistory(SimpleNamespace(), ChatMessages(tmp_path / 'cache.sqlite'))


def test_readback_imports_only_actual_recent_self_text_in_the_requested_session(tmp_path, snapshot):
    store = history(tmp_path)
    source_message(snapshot, 1)
    source_message(snapshot, 2, author=2)
    source_message(snapshot, 3, timestamp=994)
    source_message(snapshot, 4, session='other')
    source_message(snapshot, 5, kind=3)
    assert store.apply_sent('self_a', snapshot, 'peer', 1000, 'hello')
    page = store.store.page('self_a', 'peer')
    assert page['total'] == 1
    assert page['messages'][0]['server_id'] == '1'
    assert page['messages'][0]['source'] == 'history'
    assert page['messages'][0]['is_self'] is True
    assert store.store.page('self_a', 'other')['total'] == 0
    assert store.store.state('self_a') == {}


def test_no_source_message_creates_no_optimistic_echo(tmp_path, snapshot):
    store = history(tmp_path)
    assert not store.apply_sent('self_a', snapshot, 'peer', 1000, 'hello')
    assert store.store.page('self_a', 'peer')['total'] == 0


def test_readback_replay_preserves_ids_and_does_not_collapse_repeated_text(tmp_path, snapshot):
    store = history(tmp_path)
    source_message(snapshot, 1, server=9007199254740993123)
    source_message(snapshot, 2, server=9007199254740993124)
    assert store.apply_sent('self_a', snapshot, 'peer', 1000, 'hello')
    before = store.store.page('self_a', 'peer')
    assert store.apply_sent('self_a', snapshot, 'peer', 1000, 'hello')
    after = store.store.page('self_a', 'peer')
    assert before == after
    assert after['total'] == 2


def test_readback_rejects_other_account_snapshot(tmp_path, snapshot):
    store = history(tmp_path)
    source_message(snapshot)
    with pytest.raises(ActiveRosterError):
        store.apply_sent('self_b', snapshot, 'peer', 1000, 'hello')
    assert store.store.page('self_a', 'peer')['total'] == 0
    assert store.store.page('self_b', 'peer')['total'] == 0


def test_group_sender_prefix_must_agree_with_current_account(tmp_path, snapshot):
    store = history(tmp_path)
    source_message(snapshot, 1, session='room@chatroom', body=b'peer:\nhello')
    source_message(snapshot, 2, session='room@chatroom', body=b'self_a:\nhello')
    assert store.apply_sent('self_a', snapshot, 'room@chatroom', 1000, 'hello')
    assert [m['server_id'] for m in store.store.page('self_a', 'room@chatroom')['messages']] == ['2']


def test_database_pending_row_is_not_a_delivery_confirmation(tmp_path, snapshot):
    store = history(tmp_path)
    source_message(snapshot, server=0)
    assert not store.apply_sent('self_a', snapshot, 'peer', 1000, 'hello')
    assert store.store.page('self_a', 'peer')['messages'][0]['server_id'] == '0'


def test_readback_never_restores_a_recalled_message(tmp_path, snapshot):
    store = history(tmp_path)
    source_message(snapshot)
    store.apply_sent('self_a', snapshot, 'peer', 1000, 'hello')
    with store.store.db() as db:
        store.store.upsert(db, 'self_a', {'session_username': 'peer', 'sender_username': 'peer',
            'server_id': '2', 'local_id': 2, 'create_time': 1001, 'source': 'realtime',
            'category': 'system', 'content': 'recalled', 'recalled_server_id': '1'})
    store.apply_sent('self_a', snapshot, 'peer', 1000, 'hello')
    original = next(m for m in store.store.page('self_a', 'peer')['messages'] if m['server_id'] == '1')
    assert original['content'] == 'recalled'
    assert original['_recalled']


class Roster:
    def verify(self, account, force=False):
        assert account == 'self_a'


def live(tmp_path, snapshot, monkeypatch):
    service = LiveChat(tmp_path / 'live.sqlite', 'http://127.0.0.1:19088', tmp_path / 'relay.json', Roster())
    with service.db() as db:
        db.execute('INSERT INTO live_roster VALUES (?,?,?,?)', ('self_a', 'peer', 'Peer', 'friend'))
    calls = []
    async def bridge(path, payload, timeout=8):
        calls.append(path)
        return {'code': 1, 'data': {'wxid': 'self_a'}} if path == '/api/userInfo' else {'code': 1}
    service.bridge = bridge
    service.sent_retry_delay = 0
    monkeypatch.setattr('backend.app.live_chat.time.time', lambda: 1000)
    return service, calls


def test_send_readback_bypasses_full_import_and_never_resends(tmp_path, snapshot, monkeypatch):
    service, calls = live(tmp_path, snapshot, monkeypatch)
    prepares = 0
    actual = ActiveHistory(SimpleNamespace(), service.messages)
    async def prepare(account):
        nonlocal prepares
        prepares += 1
        if prepares == 2:
            source_message(snapshot)
        return snapshot
    service.history = SimpleNamespace(prepare=prepare, apply_sent=actual.apply_sent)
    payload = SendText(account='self_a', username='peer', text='hello', request_id=uuid4())
    async def scenario():
        # The long history import remains pending throughout the readback.
        service.history_tasks['self_a'] = asyncio.create_task(asyncio.Event().wait())
        response = await service.send(payload)
        assert response['status'] == 'accepted'
        assert service.messages.page('self_a', 'peer')['total'] == 0
        assert (await service.send(payload))['status'] == 'accepted'
        await asyncio.gather(*list(service.sent_tasks.values()))
        assert not service.history_tasks['self_a'].done()
        assert service.messages.page('self_a', 'peer')['total'] == 1
        assert service.receipt('self_a', str(payload.request_id))['status'] == 'accepted'
        assert not service.sent_tasks
        await service.close()
    asyncio.run(scenario())
    assert prepares == 2
    assert calls.count('/api/sendTextMsg') == 1


def test_existing_accepted_receipt_can_recover_echo_without_sending_again(tmp_path, snapshot, monkeypatch):
    service, calls = live(tmp_path, snapshot, monkeypatch)
    source_message(snapshot)
    actual = ActiveHistory(SimpleNamespace(), service.messages)
    async def prepare(account):
        return snapshot
    service.history = SimpleNamespace(prepare=prepare, apply_sent=actual.apply_sent)
    request = str(uuid4())
    with service.db() as db:
        db.execute('INSERT INTO live_outbox VALUES (?,?,?,?,?,?,?,?)',
                   ('self_a', request, 'peer', 'hello', 'fixture', 'accepted', 'accepted only', 1000))
    async def scenario():
        assert service.receipt('self_a', request)['status'] == 'accepted'
        await asyncio.gather(*list(service.sent_tasks.values()))
        assert service.messages.page('self_a', 'peer')['total'] == 1
        await service.close()
    asyncio.run(scenario())
    assert '/api/sendTextMsg' not in calls


@pytest.mark.parametrize('status', ['unknown', 'failed', 'dispatching'])
def test_unaccepted_receipt_does_not_query_or_resubmit(tmp_path, snapshot, monkeypatch, status):
    service, calls = live(tmp_path, snapshot, monkeypatch)
    async def prepare(account):
        pytest.fail('unaccepted sends must not launch readback')
    service.history = SimpleNamespace(prepare=prepare, apply_sent=lambda *args: False)
    request = str(uuid4())
    with service.db() as db:
        db.execute('INSERT INTO live_outbox VALUES (?,?,?,?,?,?,?,?)',
                   ('self_a', request, 'peer', 'hello', 'fixture', status, '', 1000))
    async def scenario():
        service.receipt('self_a', request)
        assert not service.sent_tasks
        await service.close()
    asyncio.run(scenario())
    assert calls == []


def test_account_switch_during_snapshot_never_imports_into_other_account(tmp_path, snapshot, monkeypatch):
    service, calls = live(tmp_path, snapshot, monkeypatch)
    async def prepare(account):
        async def switched(path, payload, timeout=8):
            return {'code': 1, 'data': {'wxid': 'self_b'}}
        service.bridge = switched
        return snapshot
    service.history = SimpleNamespace(prepare=prepare, apply_sent=lambda *args: pytest.fail('account changed'))
    async def scenario():
        service.refresh_sent('self_a', str(uuid4()), 'peer', 1000, 'hello')
        await asyncio.gather(*list(service.sent_tasks.values()))
        assert service.messages.page('self_a', 'peer')['total'] == 0
        await service.close()
    asyncio.run(scenario())


def test_shutdown_cancels_inflight_send_readback(tmp_path, snapshot, monkeypatch):
    service, calls = live(tmp_path, snapshot, monkeypatch)
    async def scenario():
        started = asyncio.Event()
        async def prepare(account):
            started.set()
            await asyncio.Event().wait()
        service.history = SimpleNamespace(prepare=prepare, apply_sent=lambda *args: False)
        service.refresh_sent('self_a', str(uuid4()), 'peer', 1000, 'hello')
        await started.wait()
        await service.close()
        assert not service.sent_tasks
    asyncio.run(scenario())
