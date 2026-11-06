import asyncio
import time
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app.live_chat import LiveChat, SendText, create_live_router


class FakeRoster:
    def verify(self, account, force=False):
        if account != 'self_a':
            raise ValueError('wrong active account')
        return {'account':account}

    def read(self, account):
        self.verify(account)
        return {'account':account,'source':'active_contact_db',
                'contacts':[{'username':'peer','name':'Test peer','relation':'friend'}],
                'names':[{'username':'peer','name':'Test peer'}]}


@pytest.fixture
def service(tmp_path):
    service = LiveChat(tmp_path / 'live.sqlite', 'http://127.0.0.1:19088', tmp_path / 'relay.json', roster=FakeRoster())
    service.calls = []
    async def bridge(path, payload, timeout=8):
        service.calls.append((path,payload))
        if path == '/api/userInfo':
            return {'code':1, 'data':{'wxid':'self_a','nickname':'Test account'}}
        if path == '/api/getContactList':
            return {'code':1,'data':[{'wxid':'peer','nickname':'Test peer'}]}
        return {'code':1,'data':{}}
    service.bridge = bridge
    with service.db() as db:
        db.execute('INSERT INTO live_roster VALUES (?,?,?,?)',('self_a','peer','Test peer','friend'))
    return service


def event(account='self_a', number=1, outgoing=False):
    return {'event_id':f'event-{number}', 'event_type':'message','source':'test-fixture',
            'selfWxId':account, 'peerWxId':'peer','senderWxId':account if outgoing else 'peer',
            'msgId':str(number), 'localId':str(number),'type':1,
            'timestamp_unix':int(time.time()),'direction':'outgoing' if outgoing else 'incoming',
            'content':'test-only-message'}


def test_real_event_deduplication_and_account_isolation(service):
    assert not service.ingest(event())['duplicate']
    assert service.ingest(event())['duplicate']
    assert not service.ingest(event('self_b'))['duplicate']
    assert len(service.detail('self_a','peer',180)['messages']) == 1
    assert len(service.detail('self_b','peer',180)['messages']) == 1


def test_concurrent_profile_checks_share_only_the_inflight_request(service):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        original = service.bridge
        calls = 0
        async def bridge(path, payload, timeout=8):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return await original(path, payload, timeout)
        service.bridge = bridge
        requests = [asyncio.create_task(service.profile(force=True)) for _ in range(12)]
        await started.wait()
        assert calls == 1
        requests[0].cancel()
        release.set()
        results = await asyncio.gather(*requests, return_exceptions=True)
        assert isinstance(results[0], asyncio.CancelledError)
        assert all(result['username'] == 'self_a' for result in results[1:])
        await service.profile(force=True)
        assert calls == 2
    asyncio.run(scenario())


def test_roster_writer_wait_does_not_block_the_api_event_loop(service, monkeypatch):
    import threading
    entered, release = threading.Event(), threading.Event()
    original = service._replace_roster
    def blocked_writer(account, result):
        entered.set()
        assert release.wait(2)
        original(account, result)
    monkeypatch.setattr(service, '_replace_roster', blocked_writer)
    async def scenario():
        task = asyncio.create_task(service.sync_contacts('self_a'))
        try:
            await asyncio.wait_for(asyncio.to_thread(entered.wait), 1)
            started = time.perf_counter()
            await asyncio.sleep(.02)
            assert time.perf_counter() - started < .2
            assert not task.done()
        finally:
            release.set()
            await task
    asyncio.run(scenario())


def test_delta_includes_current_session_and_history_boundary(service):
    for number in range(1, 4):
        service.ingest(event(number=number))
    page = service.detail('self_a', 'peer', 2)
    floor = int(page['messages'][0]['id'])
    delta = service.changes('self_a', 'peer', 0, floor)
    assert delta['session']['message_count'] == 3
    assert delta['session']['display_name'] == 'Test peer'
    assert delta['boundary_cursor']
    assert len(delta['messages']) == 3
    app = FastAPI(); app.include_router(create_live_router(service))
    with TestClient(app) as client:
        response = client.get('/api/chat/sessions/peer/changes', params={'account': 'self_a', 'after': delta['revision'], 'floor': floor})
        assert response.status_code == 200
        assert response.json()['messages'] == []
        assert response.json()['boundary_cursor']


def test_sse_targets_changed_conversations_without_other_account_data(service):
    service.ingest(event(number=1))
    service.ingest({**event(number=2), 'peerWxId': 'other'})
    service.ingest(event(account='self_b', number=3))
    summary = service.messages.changed_sessions('self_a', 0)
    assert set(summary['sessions']) == {'peer', 'other'}
    assert service.messages.changed_sessions('self_a', summary['cursor'])['sessions'] == []
    app = FastAPI(); app.include_router(create_live_router(service))
    with TestClient(app) as client:
        response = client.get('/api/chat/events', params={'account': 'self_a', 'once': True})
        assert '"sessions":' in response.text
        assert 'self_b' not in response.text
    service.ingest(event(number=2,outgoing=True))
    assert service.detail('self_a','peer',180)['messages'][-1]['is_self'] is True
    assert len(service.detail('self_b','peer',180)['messages']) == 1


def test_unknown_account_event_is_retained_but_not_displayed(service):
    payload = event(); del payload['selfWxId']
    assert service.ingest(payload)['status'] == 'unparsed'
    assert service.detail('self_a','peer',180)['messages'] == []


def test_contacts_are_not_snapshot_data(service):
    asyncio.run(service.sync_contacts('self_a'))
    result = service.sessions('self_a','Test','contacts',0,80)
    assert result['total'] == 1
    assert result['items'][0]['message_count'] == 0
    assert result['items'][0]['username'] == 'peer'


def test_send_idempotency_does_not_create_fake_message(service):
    payload = SendText(account='self_a',username='peer',text='mock send only',request_id=uuid4())
    assert asyncio.run(service.send(payload))['status'] == 'accepted'
    assert asyncio.run(service.send(payload))['status'] == 'accepted'
    assert len([call for call in service.calls if call[0] == '/api/sendTextMsg']) == 1
    assert service.detail('self_a','peer',20)['messages'] == []
    with pytest.raises(HTTPException) as error:
        asyncio.run(service.send(payload.model_copy(update={'text':'different'})))
    assert error.value.status_code == 409


def test_changed_login_account_never_dispatches(service):
    payload = SendText(account='self_b',username='peer',text='mock only',request_id=uuid4())
    with pytest.raises(HTTPException) as error:
        asyncio.run(service.send(payload))
    assert error.value.status_code == 409
    assert not any(call[0] == '/api/sendTextMsg' for call in service.calls)


def test_timeout_is_unknown_and_not_retried(service):
    original = service.bridge
    async def bridge(path, payload, timeout=8):
        if path == '/api/sendTextMsg':
            service.calls.append((path,payload))
            raise httpx.ReadTimeout('test timeout')
        return await original(path,payload,timeout)
    service.bridge = bridge
    payload = SendText(account='self_a',username='peer',text='mock only',request_id=uuid4())
    assert asyncio.run(service.send(payload))['status'] == 'unknown'
    assert asyncio.run(service.send(payload))['status'] == 'unknown'
    assert len([call for call in service.calls if call[0] == '/api/sendTextMsg']) == 1


def test_external_origin_cannot_send(service):
    app = FastAPI(); app.include_router(create_live_router(service))
    with TestClient(app) as client:
        result = client.post('/api/chat/send',headers={'Origin':'https://evil.example'},json={'account':'self_a','username':'peer','text':'mock','request_id':str(uuid4())})
        assert result.status_code == 403
        assert not service.calls


def test_stream_sequence_and_resume(service):
    service.ingest(event())
    app = FastAPI(); app.include_router(create_live_router(service))
    with TestClient(app) as client:
        result = client.get('/api/chat/events',params={'account':'self_a','once':True})
        assert 'event: chat.updated' in result.text
        assert '"account": "self_a"' in result.text
        result = client.get('/api/chat/events',params={'account':'self_a','once':True},headers={'Last-Event-ID':'1'})
        assert 'event: chat.updated' not in result.text
        result = client.get('/api/chat/events',params={'account':'self_b','once':True})
        assert result.status_code == 409


def test_blank_message_rejected_before_dispatch(service):
    with pytest.raises(HTTPException):
        asyncio.run(service.send(SendText(account='self_a',username='peer',text='   ',request_id=uuid4())))
    assert not service.calls

def test_legacy_memory_scan_contacts_are_not_shown_or_sendable(service):
    with service.db() as db:
        db.execute('INSERT INTO live_contacts VALUES (?,?,?)',('self_a','foreign_peer','Not my contact'))
    assert [row['username'] for row in service.sessions('self_a','','all',0,80)['items']] == ['peer']
    with pytest.raises(HTTPException) as error:
        asyncio.run(service.send(SendText(account='self_a',username='foreign_peer',text='test only',request_id=uuid4())))
    assert error.value.status_code == 409
    assert not any(call[0] == '/api/sendTextMsg' for call in service.calls)


def test_refresh_replaces_only_this_accounts_roster(service):
    with service.db() as db:
        db.execute('INSERT INTO live_roster VALUES (?,?,?,?)',('self_a','removed','Old member','friend'))
        db.execute('INSERT INTO live_roster VALUES (?,?,?,?)',('self_b','other','Other account','friend'))
    asyncio.run(service.sync_contacts('self_a'))
    assert [row['username'] for row in service.sessions('self_a','','all',0,80)['items']] == ['peer']
    assert service.sessions('self_b','','all',0,80)['total'] == 1
    assert service.roster_state('self_a')['contacts_revision'] == 1
    assert not any(call[0] == '/api/getContactList' for call in service.calls)


def test_slow_contact_refresh_does_not_block_and_is_coalesced(service):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        calls = 0
        original = service.sync_contacts
        async def slow_sync(account):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            await original(account)
        service.sync_contacts = slow_sync
        app = FastAPI(); app.include_router(create_live_router(service))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
            result = await asyncio.wait_for(client.get('/api/chat/sessions',params={'account':'self_a'}),timeout=1)
            assert result.status_code == 200 and result.json()['items'][0]['username'] == 'peer'
            await started.wait()
            await asyncio.gather(*(client.get('/api/chat/sessions',params={'account':'self_a'}) for _ in range(3)))
            assert calls == 1
            assert result.json()['contacts_syncing'] is True
            release.set(); await service.contact_tasks['self_a']
            assert service.roster_state('self_a')['contacts_verified'] is True
        await service.close()
    asyncio.run(scenario())


def test_failed_background_refresh_keeps_valid_cache_and_exposes_error(service):
    async def scenario():
        async def fail(_):
            raise RuntimeError('test read failure')
        service.sync_contacts = fail
        service.refresh_contacts('self_a')
        await service.contact_tasks['self_a']
        assert service.sessions('self_a','','all',0,80)['total'] == 1
        assert service.roster_state('self_a')['contacts_error']
        first = service.contact_tasks['self_a']
        service.refresh_contacts('self_a')
        assert service.contact_tasks['self_a'] is first
        await service.close()
    asyncio.run(scenario())


def test_roster_owner_mismatch_does_not_replace_existing_cache(service):
    service.roster.read = lambda _: {'account':'self_b','source':'active_contact_db','contacts':[],'names':[]}
    with pytest.raises(HTTPException):
        asyncio.run(service.sync_contacts('self_a'))
    assert service.sessions('self_a','','all',0,80)['total'] == 1


def test_process_binding_failure_never_dispatches(service):
    def fail(*_):
        raise ValueError('wrong process owner')
    service.roster.verify = fail
    with pytest.raises(HTTPException):
        asyncio.run(service.send(SendText(account='self_a',username='peer',text='test only',request_id=uuid4())))
    assert not any(call[0] == '/api/sendTextMsg' for call in service.calls)
