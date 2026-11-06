import asyncio
import hashlib
import sqlite3
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx

from fastapi import FastAPI
from backend.app.chat_history import ActiveHistory, HistoryResources
from backend.app.chat_messages import ChatMessages
from backend.app.live_chat import SendText, create_live_router
from backend.tests.test_live_chat import service  # noqa: F401


def short_database_wait(service, monkeypatch):
    @contextmanager
    def db():
        connection = sqlite3.connect(service.path, timeout=.05)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()
    monkeypatch.setattr(service, 'db', db)


def test_writer_wait_keeps_api_responsive_and_dispatches_only_once(service, monkeypatch):
    async def scenario():
        entered = threading.Event()
        reserve = service._reserve_send
        def observe(*args):
            entered.set()
            return reserve(*args)
        monkeypatch.setattr(service, '_reserve_send', observe)
        app = FastAPI()
        app.include_router(create_live_router(service))
        payload = {'account': 'self_a', 'username': 'peer', 'text': 'fixture only', 'request_id': str(uuid4())}
        writer = sqlite3.connect(service.path)
        writer.execute('BEGIN IMMEDIATE')
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                request = asyncio.create_task(client.post('/api/chat/send', json=payload))
                assert await asyncio.to_thread(entered.wait, 1)
                start = time.perf_counter()
                status = await asyncio.wait_for(client.get('/api/chat/status'), timeout=.5)
                assert status.status_code == 200
                assert time.perf_counter() - start < .5
                assert not request.done()
                assert not any(call[0] == '/api/sendTextMsg' for call in service.calls)
                writer.commit()
                result = await asyncio.wait_for(request, 2)
                assert result.json()['status'] == 'accepted'
                repeated = await client.post('/api/chat/send', json=payload)
                assert repeated.json()['status'] == 'accepted'
        finally:
            writer.close()
        assert len([call for call in service.calls if call[0] == '/api/sendTextMsg']) == 1
    asyncio.run(scenario())


def test_busy_reservation_returns_json_and_never_dispatches(service, monkeypatch):
    short_database_wait(service, monkeypatch)
    app = FastAPI()
    app.include_router(create_live_router(service))
    async def scenario():
        writer = sqlite3.connect(service.path)
        writer.execute('BEGIN IMMEDIATE')
        payload = {'account': 'self_a', 'username': 'peer', 'text': 'fixture only', 'request_id': str(uuid4())}
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                result = await client.post('/api/chat/send', json=payload)
                assert result.status_code == 503
                assert result.headers['content-type'].startswith('application/json')
                assert '未提交微信' in result.json()['detail']
                assert not any(call[0] == '/api/sendTextMsg' for call in service.calls)
                with service.db() as db:
                    assert not db.execute('SELECT 1 FROM live_outbox WHERE request_id=?', (payload['request_id'],)).fetchone()
                writer.commit()
                retry = await client.post('/api/chat/send', json=payload)
                assert retry.json()['status'] == 'accepted'
        finally:
            writer.close()
        assert len([call for call in service.calls if call[0] == '/api/sendTextMsg']) == 1
    asyncio.run(scenario())


def test_receipt_write_failure_stays_uncertain_and_never_resends(service, monkeypatch):
    short_database_wait(service, monkeypatch)
    original = service.bridge
    writer = sqlite3.connect(service.path)
    async def bridge(path, payload, timeout=8):
        result = await original(path, payload, timeout)
        if path == '/api/sendTextMsg':
            writer.execute('BEGIN IMMEDIATE')
        return result
    monkeypatch.setattr(service, 'bridge', bridge)
    async def scenario():
        payload = SendText(account='self_a', username='peer', text='fixture only', request_id=uuid4())
        try:
            result = await service.send(payload)
            assert result['status'] == 'unknown'
            assert '回执保存失败' in result['detail']
            repeated = await service.send(payload)
            assert repeated['status'] == 'dispatching'
        finally:
            writer.rollback()
        assert len([call for call in service.calls if call[0] == '/api/sendTextMsg']) == 1
        assert service.messages.page('self_a', 'peer')['total'] == 0
    try:
        asyncio.run(scenario())
    finally:
        writer.close()


def test_concurrent_requests_share_one_persisted_dispatch(service):
    async def scenario():
        payload = SendText(account='self_a', username='peer', text='fixture only', request_id=uuid4())
        results = await asyncio.gather(*(service.send(payload) for _ in range(12)))
        assert all(row['status'] in {'accepted', 'dispatching'} for row in results)
        assert service.receipt('self_a', str(payload.request_id))['status'] == 'accepted'
        assert len([call for call in service.calls if call[0] == '/api/sendTextMsg']) == 1
    asyncio.run(scenario())


def test_resource_processing_does_not_hold_the_cache_write_lock(tmp_path, monkeypatch):
    store = ChatMessages(tmp_path / 'cache.sqlite')
    source = tmp_path / 'source.sqlite'
    table = 'Msg_' + hashlib.md5(b'peer').hexdigest()
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE Name2Id(user_name TEXT)')
        db.execute("INSERT INTO Name2Id VALUES ('peer')")
        db.execute(f'CREATE TABLE "{table}" (local_id,server_id,local_type,sort_seq,real_sender_id,create_time,message_content,compress_content)')
        db.executemany(f'INSERT INTO "{table}" VALUES (?,?,1,?,1,1000,?,NULL)',
                       [(i, i, i, b'fixture only') for i in range(1, 65)])
    checked = 0
    def resources(_self, message):
        nonlocal checked
        with sqlite3.connect(store.path, timeout=0) as foreground:
            foreground.execute('INSERT OR REPLACE INTO chat_history_state VALUES (?,?)', ('other_account', '{}'))
        checked += 1
        return []
    monkeypatch.setattr(HistoryResources, 'for_message', resources)
    history = ActiveHistory(SimpleNamespace(), store)
    result = history.apply('self_a', {'account': 'self_a', 'root': str(tmp_path),
        'databases': {'message/message_0.db': str(source)}, 'fingerprint': 'fixture',
        'verified_at': 1001, 'read_started_at': 1001})
    assert checked == 64
    assert result['message_count'] == 64
    assert store.page('self_a', 'peer')['total'] == 64
