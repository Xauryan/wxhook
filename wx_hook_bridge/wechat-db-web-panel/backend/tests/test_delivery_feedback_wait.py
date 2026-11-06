import copy
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from backend.app import delivery_materials as materials
from backend.app.delivery_notifications import failure_message
from backend.tests.test_delivery_materials import system, primary, ROOM
from backend.tests.quote_fixtures import ACCEPTED

TARGETS = [{'room_id': ROOM, 'name': 'Fixture'}]


def clock(system, monkeypatch, seconds):
    value = datetime.fromisoformat(system.case['created_at']) + timedelta(seconds=seconds)
    monkeypatch.setattr(materials, 'now', lambda: value)
    monkeypatch.setattr('backend.app.delivery_notifications.now', lambda: value)
    return value


def one_side(system):
    system.case['notification_calls'] = [system.case['notification_calls'][0]]


def state(system):
    with system.store.connect() as db:
        return dict(db.execute('SELECT * FROM feedback_waits WHERE case_id=38').fetchone())


def receipt(system):
    with system.store.connect() as db:
        return dict(db.execute("SELECT * FROM deliveries WHERE event_key='delivery.failure:38'").fetchone())


@pytest.mark.asyncio
async def test_one_side_waits_until_deadline_then_sends_text_and_image(system, monkeypatch):
    one_side(system)
    clock(system, monkeypatch, 599)
    await system.worker.send_primary(system.case, TARGETS)
    system.bridge._post.assert_not_awaited()
    system.dialogues.generate.assert_not_awaited()
    assert state(system)['status'] == 'waiting_window'
    clock(system, monkeypatch, 600)
    await system.worker.send_primary(system.case, TARGETS)
    assert [c.args[0] for c in system.bridge._post.call_args_list] == ['/api/sendTextMsg', '/api/sendImageQuoteMsg']
    assert state(system)['status'] == 'submitted'
    assert system.bridge._post.call_args_list[0].args[1]['msg'].count('https://') == 1
    await system.worker.send_primary(system.case, TARGETS)
    assert system.bridge._post.await_count == 2


@pytest.mark.asyncio
async def test_both_sides_ready_send_early_only_after_images_exist(system, monkeypatch):
    clock(system, monkeypatch, 30)
    async def send(*args, **kwargs):
        assert len(list(system.dialogues.root.glob('*.png'))) == 2
        return ACCEPTED
    system.bridge._post.side_effect = send
    await system.worker.send_primary(system.case, TARGETS)
    assert [c.args[0] for c in system.bridge._post.call_args_list] == ['/api/sendTextMsg', '/api/sendImageQuoteMsg', '/api/sendImageQuoteMsg']


@pytest.mark.asyncio
@pytest.mark.parametrize('condition', ['no_recordings', 'not_transcribed', 'not_selected', 'closed', 'before_activation'])
async def test_never_sends_empty_feedback_even_after_timeout(system, monkeypatch, condition):
    clock(system, monkeypatch, 1800)
    if condition == 'no_recordings':
        system.case['candidates']['items'] = []
    elif condition == 'not_transcribed':
        for c in system.case['candidates']['items']:
            c['delivery_transcript'] = {'status': 'processing'}
    elif condition == 'not_selected':
        system.case['notification_calls'] = []
    elif condition == 'closed':
        system.case['closed_at'] = system.case['created_at']
    else:
        system.case['created_at'] = '2026-09-19T00:00:00+08:00'
    await system.worker.send_primary(system.case, TARGETS)
    system.bridge._post.assert_not_awaited()
    assert not system.store.recent()


@pytest.mark.asyncio
async def test_image_failure_does_not_send_text_or_retry_failed_version(system, monkeypatch):
    clock(system, monkeypatch, 700)
    one_side(system)
    async def fail(case, cid):
        _, _, version, iid = materials.image_identity(case, cid)
        with system.store.connect() as db:
            db.execute("INSERT INTO dialogue_images(id,case_id,call_id,version,call_time,title,status) VALUES(?,?,?,?,?,?,'failed')",
                       (iid, case['id'], cid, version, 'time', 'title'))
    system.dialogues.generate.side_effect = fail
    await system.worker.send_primary(system.case, TARGETS)
    await system.worker.send_primary(system.case, TARGETS)
    system.bridge._post.assert_not_awaited()
    system.dialogues.generate.assert_awaited_once()
    assert state(system)['status'] == 'image_not_ready'


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['selection', 'transcript', 'closed', 'disabled', 'groups'])
async def test_recheck_rejects_stale_materials_or_configuration(system, monkeypatch, change):
    clock(system, monkeypatch, 60)
    async def fetch(path, params, **kwargs):
        fresh = copy.deepcopy(system.case)
        if change == 'selection':
            fresh['notification_calls'] = []
        elif change == 'transcript':
            for c in fresh['candidates']['items']:
                c['delivery_transcript']['segments'][0]['text'] = 'changed'
        elif change == 'closed':
            fresh['closed_at'] = fresh['created_at']
        else:
            with system.store.connect() as db:
                db.execute('UPDATE config SET failure_notifications_enabled=0' if change == 'disabled'
                           else "UPDATE config SET target_groups='[]'")
        return fresh
    system.worker.fetch = AsyncMock(side_effect=fetch)
    await system.worker.send_primary(system.case, TARGETS)
    system.bridge._post.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['sent', 'uncertain', 'sending', 'cancelled'])
async def test_existing_primary_receipts_never_rewritten_or_replayed(system, monkeypatch, status):
    clock(system, monkeypatch, 700)
    primary(system, status=status, body='original')
    await system.worker.send_primary(system.case, TARGETS)
    system.bridge._post.assert_not_awaited()
    assert receipt(system)['message'] == 'original'
    assert system.store.recent()[0]['status'] == status


@pytest.mark.asyncio
async def test_old_queued_empty_text_is_gated_and_adopted_with_images(system, monkeypatch):
    clock(system, monkeypatch, 60)
    one_side(system)
    primary(system, status='queued', body='old empty text')
    await system.worker.notifications.drain()
    await system.worker.send_primary(system.case, TARGETS)
    system.bridge._post.assert_not_awaited()
    clock(system, monkeypatch, 600)
    await system.worker.send_primary(system.case, TARGETS)
    assert [c.args[0] for c in system.bridge._post.call_args_list] == ['/api/sendTextMsg', '/api/sendImageQuoteMsg']
    assert receipt(system)['message'] != 'old empty text'
    assert system.store.recent()[0]['status'] == 'sent'


@pytest.mark.asyncio
async def test_primary_timeout_blocks_images_and_automatic_retry(system, monkeypatch):
    clock(system, monkeypatch, 700)
    system.bridge._post.side_effect = TimeoutError()
    await system.worker.send_primary(system.case, TARGETS)
    await system.worker.send_primary(system.case, TARGETS)
    assert system.bridge._post.await_count == 1
    assert system.store.recent()[0]['status'] == 'uncertain'


@pytest.mark.asyncio
async def test_restart_preserves_deadline_and_late_second_side_is_image_only(system, monkeypatch):
    original = copy.deepcopy(system.case['notification_calls'])
    one_side(system)
    clock(system, monkeypatch, 100)
    await system.worker.send_primary(system.case, TARGETS)
    deadline = state(system)['deadline_at']
    replacement = materials.DeliveryMaterialWorker(system.worker.notifications, system.dialogues, system.fetch)
    replacement.initialize()
    clock(system, monkeypatch, 601)
    await replacement.send_primary(system.case, TARGETS)
    assert state(system)['deadline_at'] == deadline
    system.case['notification_calls'] = original
    await replacement.process_case(38)
    assert [c.args[0] for c in system.bridge._post.call_args_list] == ['/api/sendTextMsg', '/api/sendImageQuoteMsg', '/api/sendImageQuoteMsg']


def test_unbound_outbound_candidate_is_not_reported_as_no_recording(system):
    system.case['material_scope']['policy'] = 'business-relevance-v2-deepseek-auto'
    system.case['notification_calls'] = []
    system.case['notification_order'] = {'agent_name': 'Fixture agent'}
    call = system.case['candidates']['items'][0]
    call.update(role='unverified', user_name='Fixture agent')
    assert '已发现候选录音，身份关联待确认' in failure_message(system.case, '')


@pytest.mark.asyncio
async def test_automation_polls_waiting_case_and_never_replays_primary(system, monkeypatch):
    clock(system, monkeypatch, 30)
    system.store.configure(False, TARGETS, failure_notifications_enabled=True)
    with system.store.connect() as db:
        db.execute("UPDATE config SET failure_activated_at='2026-09-20T17:45:00+08:00'")
    async def fetch(path, params, **kwargs):
        if path == '/cases':
            return {'items': [copy.deepcopy(system.case)], 'has_more': False}
        if path.endswith('/archive-link'):
            return {'path': '/d/abcdefghijklmnop'}
        return copy.deepcopy(system.case)
    system.fetch.side_effect = fetch
    one_side(system)
    await system.worker.notifications.poll_automation()
    system.bridge._post.assert_not_awaited()
    clock(system, monkeypatch, 600)
    await system.worker.notifications.poll_automation()
    await system.worker.notifications.poll_automation()
    assert system.bridge._post.await_count == 2
    assert not system.worker.notifications.status()['feedback_wait']['items']


@pytest.mark.asyncio
async def test_one_failed_image_does_not_block_other_ready_side_after_deadline(system, monkeypatch):
    clock(system, monkeypatch, 700)
    original_generate = system.dialogues.generate.side_effect
    async def generate(case, call_id):
        if call_id == 1:
            return []
        return await original_generate(case, call_id)
    system.dialogues.generate.side_effect = generate
    await system.worker.send_primary(system.case, TARGETS)
    assert system.bridge._post.await_count == 2
    body = system.bridge._post.call_args_list[0].args[1]['msg']
    assert '&call=2' in body and '&call=1' not in body


def test_waiting_feedback_cannot_starve_other_notification_queue(system):
    for i in range(60):
        system.store.enqueue(f'delivery.failure:{i}', TARGETS, 'old unsent feedback')
    system.store.enqueue('delivery.overview:fixture', TARGETS, 'other notification')
    assert [r['event_key'] for r in system.store.pending()] == ['delivery.overview:fixture']
