import asyncio
import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.app.delivery_dialogues import DialogueService, source
from backend.app.delivery_notifications import DeliveryNotificationStore
from backend.tests.quote_fixtures import fake_quote_source, ACCEPTED


def case():
    return {'id': 1, 'order_id': 10648, 'selected_calls': [{'id': 7, 'role': 'outbound'}],
            'notification_order': {'customer_phone':'13800001234','outbound_group_name':'模拟组','agent_name':'员工'},
            'candidates': {'order': {'customer_name': '不应出现在图中', 'customer_phone_masked': '138****1234'}, 'items': [
                {'id': 7, 'call_time': '2026-09-19T10:00:00+08:00', 'call_duration': 200, 'user_name': '员工',
                 'delivery_transcript': {'status': 'succeeded', 'segments': [
                     {'text': '唔係，19.9元，24个月。', 'speaker_id': 42, 'begin_time': 0, 'end_time': 1200},
                     {'text': '冇呀<&\n尾句', 'speaker_id': 99, 'begin_time': 1200, 'end_time': 2200}]}}]}}


@pytest.fixture
def service(tmp_path):
    store = DeliveryNotificationStore(tmp_path / 'delivery.sqlite')
    bridge = AsyncMock()
    service = DialogueService(store, tmp_path / 'images', Path(__file__).resolve().parents[2] / 'scripts/render-delivery-dialogue.cjs', bridge)
    service.initialize()
    fake_quote_source(service)
    store.configure(False, [{'room_id': 'test-only@chatroom', 'name': '模拟群'}, {'room_id': 'second@chatroom', 'name': '第二群'}])
    return service


def ready(service, image_id, call_id=7, time='2026-09-19T10:00:00'):
    with service.store.connect() as db:
        db.execute('INSERT INTO dialogue_images(id,case_id,call_id,version,call_time,title,status) VALUES(?,?,?,?,?,?,?)',
                   (image_id, 1, call_id, image_id, time, '外呼通话记录', 'ready'))
        db.execute('UPDATE dialogue_images SET context_json=? WHERE id=?',
                   (json.dumps({'order_id': 10648, 'customer': '客户 · 尾号 1234', 'employee': '员工', 'duration': 200}), image_id))
    (service.root / (image_id + '.png')).write_bytes(b'fixture')


def test_source_is_verbatim_and_private():
    data = source(case(), 7)
    assert data['segments'] == case()['candidates']['items'][0]['delivery_transcript']['segments']
    assert data['customer'] == '客户 · 尾号 1234'
    assert 'analysis' not in data
    broken = case()
    broken['candidates']['items'][0]['delivery_transcript']['segments'][1]['begin_time'] = -1
    with pytest.raises(ValueError):
        source(broken, 7)


def test_real_renderer_regenerate_and_failure(service, monkeypatch):
    async def run():
        before = service.store.configuration()
        rows = await service.generate(case(), 7)
        assert rows[0]['status'] == 'ready'
        assert rows[0]['height'] > 300
        assert await service.generate(case(), 7) == rows
        regenerated = await service.generate(case(), 7, True)
        assert regenerated[0]['id'] == rows[0]['id']
        assert service.store.configuration() == before
        service.renderer = Path('nonexistent-renderer.cjs')
        failed = await service.generate(case(), 7, True)
        assert failed[0]['status'] == 'failed'
    asyncio.run(run())


def test_simulation_dedup_order_partial_unknown(service, monkeypatch):
    ready(service, 'late', 8, '2026-09-19T11:00:00')
    ready(service, 'early')
    original_sleep = asyncio.sleep
    async def instant(_):
        await original_sleep(0)
    monkeypatch.setattr('backend.app.delivery_dialogues.asyncio.sleep', instant)
    async def run():
        result = await service.send(1, ['late', 'early'], ['test-only@chatroom'])
        assert result['bridge_called'] is False
        assert result['items'][0]['image_id'] == 'early'
        service.bridge._post.assert_not_called()
        with service.store.connect() as db:
            for room in ['test-only@chatroom','second@chatroom']:
                db.execute("INSERT INTO deliveries(event_key,room_id,room_name,message,status,created_at) VALUES('delivery.failure:1',?,'fixture','old primary','sent','fixture')",(room,))
        service.bridge._post.side_effect = [ACCEPTED, TimeoutError(), ACCEPTED, ACCEPTED]
        await service.send(1, ['late', 'early'], ['test-only@chatroom', 'second@chatroom'], False)
        assert service.bridge._post.call_count == 4
        await service.send(1, ['late', 'early'], ['test-only@chatroom', 'second@chatroom'], False)
        assert service.bridge._post.call_count == 4
        receipts = [r for item in service.listing(1) for r in item['receipts']]
        assert sum(r['status'] == 'uncertain' for r in receipts) == 1
        assert sum(r['status'] == 'bridge_accepted' for r in receipts) == 3
        with pytest.raises(ValueError):
            await service.send(1, ['early'], ['not-configured@chatroom'], False)
    asyncio.run(run())


def test_recovery_and_duplicate_click(service):
    ready(service, 'working')
    with service.store.connect() as db:
        db.execute("UPDATE dialogue_images SET status='generating'")
        db.execute("INSERT INTO dialogue_receipts VALUES('working','test-only@chatroom','模拟群','sending',NULL,NULL)")
    service.recover()
    row = service.listing(1)[0]
    assert row['status'] == 'failed'
    assert row['receipts'][0]['status'] == 'uncertain'


def test_concurrent_generation_is_single_task(service):
    async def run():
        a, b = await asyncio.gather(service.generate(case(), 7), service.generate(case(), 7))
        assert len(service.listing(1)) == 1
        assert a[0]['status'] == 'ready'
        assert b[0]['status'] == 'generating'
    asyncio.run(run())


def test_same_recording_version_across_cases_is_not_resent(service, monkeypatch):
    ready(service, 'original')
    ready(service, 'other-case')
    with service.store.connect() as db:
        db.execute("UPDATE dialogue_images SET case_id=2,version='original' WHERE id='other-case'")
        db.execute("INSERT INTO dialogue_receipts VALUES('original','test-only@chatroom','模拟群','uncertain',NULL,NULL)")
    asyncio.run(service.send(2, ['other-case'], ['test-only@chatroom'], False))
    service.bridge._post.assert_not_called()
    assert service.listing(2)[0]['receipts'][0]['status'] == 'uncertain'


def test_long_bubble_verbatim_and_explicit_height_failure(service):
    async def run():
        data = case()
        segments = data['candidates']['items'][0]['delivery_transcript']['segments']
        segments[0]['text'] = ('唔係免費，19.9元，24个月。\n' * 80) + '长气泡最后一句。'
        rows = await service.generate(data, 7)
        assert rows[0]['status'] == 'ready'
        assert rows[0]['height'] > 3000
        segments[0]['text'] = '唔係免費，19.9元，24个月。\n' * 2500
        rows = await service.generate(data, 7)
        assert any(r['status'] == 'failed' and '100000px' in r['error'] for r in rows)
    asyncio.run(run())


def test_routes_use_local_files_and_never_asr(service, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.app import delivery_management_router as router
    fetch = AsyncMock(return_value=case())
    monkeypatch.setattr(router, 'dialogue_service', service)
    monkeypatch.setattr(router, 'fetch_remote', fetch)
    monkeypatch.setattr(router, 'get_archive_link', AsyncMock(return_value={'url': 'https://youshenghe666.com/d/abcdefghijklmnop'}))
    app = FastAPI()
    app.include_router(router.router)
    with TestClient(app) as client:
        base = '/api/delivery-management'
        denied = client.post(base + '/cases/1/dialogues/generate', json={'call_id': 7}, headers={'origin': 'https://untrusted.invalid'})
        assert denied.status_code == 403
        response = client.post(base + '/cases/1/dialogues/generate', json={'call_id': 7})
        row = response.json()['items'][0]
        assert row['status'] == 'ready'
        image = client.get(base + f"/dialogues/{row['id']}/image?download=true")
        assert image.headers['content-type'] == 'image/png'
        assert 'attachment' in image.headers['content-disposition']
        assert client.get(base + '/dialogues/not-a-valid-id/image').status_code == 404
        simulated = client.post(base + '/cases/1/dialogues/send', json={'image_ids': [row['id']], 'room_ids': ['test-only@chatroom']})
        assert simulated.json()['bridge_called'] is False
        service.bridge._post.assert_not_called()
    assert fetch.await_count == 2
    fetch.assert_awaited_with('/cases/1', {})


def test_feedback_primary_once_before_materials_and_unknown_blocks(service, monkeypatch):
    ready(service, 'first')
    ready(service, 'second', 8)
    monkeypatch.setattr('backend.app.delivery_dialogues.asyncio.sleep', AsyncMock())
    data = {**case(), 'reported_reason': '单方反馈', 'created_at': '2026-09-20T10:00:00+08:00'}
    async def run():
        link = 'https://youshenghe666.com/d/abcdefghijklmnop'
        plan = await service.send(1, ['first', 'second'], ['test-only@chatroom'], True, archive_url=link, feedback_case=data)
        assert plan['bridge_called'] is False
        assert '单方反馈' in plan['items'][0]['feedback_body']
        assert '客户电话：13800001234' in plan['items'][0]['feedback_body']
        service.bridge._post.assert_not_called()
        with service.store.connect() as db:
            assert db.execute('SELECT count(*) FROM deliveries').fetchone()[0] == 0
        await service.send(1, ['first', 'second'], ['test-only@chatroom'], False, archive_url=link, feedback_case=data)
        calls = service.bridge._post.call_args_list
        assert len(calls) == 3
        assert '【无法配送反馈】' in calls[0].args[1]['msg']
        assert [c.args[0] for c in calls] == ['/api/sendTextMsg', '/api/sendImageQuoteMsg', '/api/sendImageQuoteMsg']
        with service.store.connect() as db:
            assert db.execute('SELECT count(*) FROM dialogue_context_receipts').fetchone()[0] == 0
        await service.send(1, ['first', 'second'], ['test-only@chatroom'], False, archive_url=link, feedback_case=data)
        assert service.bridge._post.call_count == 3
        service.bridge._post.reset_mock()
        service.bridge._post.side_effect = TimeoutError()
        await service.send(1, ['first'], ['second@chatroom'], False, archive_url=link, feedback_case=data)
        await service.send(1, ['first'], ['second@chatroom'], False, archive_url=link, feedback_case=data)
        assert service.bridge._post.call_count == 1
        with service.store.connect() as db:
            assert db.execute("SELECT status FROM deliveries WHERE room_id='second@chatroom'").fetchone()[0] == 'uncertain'
    asyncio.run(run())


def test_old_images_are_not_replayed_to_add_a_primary_message(service):
    ready(service, 'old')
    with service.store.connect() as db:
        db.execute("INSERT INTO dialogue_receipts VALUES('old','test-only@chatroom','模拟群','bridge_accepted',NULL,'fixture')")
    asyncio.run(service.send(1, ['old'], ['test-only@chatroom'], False,
        archive_url='https://youshenghe666.com/d/abcdefghijklmnop', feedback_case=case()))
    service.bridge._post.assert_not_called()
    with service.store.connect() as db:
        assert db.execute('SELECT count(*) FROM deliveries').fetchone()[0] == 0


def test_legacy_quote_is_rejected_and_uncertain_caption_still_blocks(service):
    ready(service, 'quoted')
    with service.store.connect() as db:
        db.execute("INSERT INTO dialogue_context_receipts VALUES('quoted','test-only@chatroom','old text',NULL,'uncertain','old uncertainty','fixture')")
    async def run():
        with pytest.raises(ValueError, match='不再追加'):
            await service.send(1, ['quoted'], ['test-only@chatroom'], True, {'test-only@chatroom':'source'})
        await service.send(1, ['quoted'], ['test-only@chatroom'], False,
            archive_url='https://youshenghe666.com/d/abcdefghijklmnop',feedback_case=case())
        service.bridge._post.assert_not_called()
        with service.store.connect() as db:
            assert db.execute('SELECT body,status FROM dialogue_context_receipts').fetchone()['status']=='uncertain'
    asyncio.run(run())
