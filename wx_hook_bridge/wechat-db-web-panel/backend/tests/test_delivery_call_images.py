import copy
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.app import delivery_notifications as notifications
from backend.app.delivery_call_images import report_data, DeliveryCallImageRenderer
from backend.tests.test_delivery_notifications import service, GROUP
from backend.tests.quote_fixtures import ACCEPTED


def payload():
    at = notifications.now()
    metrics = dict(all_orders=7, delivery_total=7, non_delivery=0, success=3, in_progress=2,
                   failed=2, other=0, unassigned=0, conflicts=0, rate=42.9)
    return dict(version=1, cohort='created_at', timezone='Asia/Shanghai', month=at.strftime('%Y-%m'),
        snapshot_at=at.isoformat(), overall=metrics, pair={**metrics, 'complete': True, 'attribution_unknown': 0},
        call=dict(id=101, event_key='delivery.call:fixture', order_id=1, staff_id=1, match_status='matched',
                  call_time=(at-timedelta(seconds=1)).isoformat(), call_duration=69, is_connected=True,
                  assigned_at=(at-timedelta(hours=1)).isoformat(), assignment_time_source='recorded_history',
                  customer_phone_masked='***1234', recording_available=True, staff_name='Fixture driver',
                  agent_name='Fixture agent', outbound_group_name='Fixture group',
                  order_candidates=[{'customer_name': 'Fixture customer'}]))


def setup_bundle(service, tmp_path):
    service.store.configure(True, [GROUP])
    image = tmp_path/'call.png'; image.write_bytes(b'fixture image')
    service.call_images.render = AsyncMock(return_value=image)
    data = payload()
    service.store.enqueue_call(data, [GROUP])
    return data


@pytest.mark.asyncio
async def test_ordered_text_then_image_once_and_frozen_snapshot(service, tmp_path):
    data = setup_bundle(service, tmp_path)
    sequence = []
    service.bridge.send_text.side_effect = lambda *a: sequence.append('text')
    service.bridge._post.side_effect = lambda *a: (sequence.append('image'), ACCEPTED)[1]
    await service.drain(); await service.drain()
    assert sequence == ['text', 'image']
    data['overall']['success'] = 2; data['overall']['other'] = 1; data['overall']['rate'] = 28.6
    service.store.enqueue_call(data, [GROUP])
    assert service.store.call_report(data['call']['event_key'])['overall']['success'] == 3
    assert service.store.recent()[0]['text_status'] == 'sent'
    assert service.store.recent()[0]['status'] == 'sent'
    assert service.bridge._post.call_args.args[0] == '/api/sendImageQuoteMsg'


@pytest.mark.asyncio
@pytest.mark.parametrize('step', ['text', 'image'])
async def test_unknown_result_blocks_remaining_parts_and_retries(service, tmp_path, step):
    setup_bundle(service, tmp_path)
    (service.bridge.send_text if step == 'text' else service.bridge._post).side_effect = TimeoutError()
    await service.drain(); service.store.recover(); await service.drain()
    assert service.store.recent()[0]['status'] == 'uncertain'
    assert service.bridge.send_text.await_count == 1
    assert service.bridge._post.await_count == (0 if step == 'text' else 1)


@pytest.mark.asyncio
async def test_render_failure_has_no_bridge_call_and_config_cancels(service, tmp_path):
    setup_bundle(service, tmp_path)
    service.call_images.render.side_effect = ValueError('fixture')
    await service.drain()
    service.bridge.send_text.assert_not_awaited(); service.bridge._post.assert_not_awaited()
    service.store.configure(False, [GROUP])
    assert service.store.recent()[0]['status'] == 'cancelled'


def test_old_receipts_not_rewritten_or_backfilled(service):
    data = payload(); key = data['call']['event_key']
    service.store.enqueue(key, [GROUP], 'original text')
    service.store.enqueue_call(data, [GROUP])
    assert service.store.call_report(key) is None
    with service.store.connect() as db:
        row = db.execute('SELECT * FROM deliveries').fetchone()
        assert row['message'] == 'original text' and row['media_type'] == 'text'


@pytest.mark.parametrize('field,value', [('rate', 99), ('success', -1), ('delivery_total', 8)])
def test_bad_metrics_rejected(field, value):
    data = payload(); data['overall'][field] = value
    with pytest.raises(ValueError): report_data(data)


def test_unknown_rate_and_missing_counts_do_not_become_zero():
    data = payload(); data['pair'].update(complete=False, attribution_unknown=1, rate=None)
    assert report_data(data)['pair']['rate'] is None
    data.update(overall=None, pair=None, unavailable_reason='Identity missing')
    assert report_data(data)['overall'] is None


@pytest.mark.asyncio
async def test_renderer_real_png_and_cache(tmp_path):
    renderer = DeliveryCallImageRenderer(tmp_path, Path(__file__).resolve().parents[2]/'scripts/render-delivery-call.cjs')
    data = payload(); path = await renderer.render('fixture', data)
    first = path.stat().st_mtime_ns
    assert path.read_bytes().startswith(b'\x89PNG')
    assert int.from_bytes(path.read_bytes()[16:20], 'big') == 720
    assert await renderer.render('fixture', data) == path and path.stat().st_mtime_ns == first
    assert not list(tmp_path.glob('*.json'))


def test_preview_never_enqueues_or_calls_bridge(service, tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.app import delivery_management_router as router
    data = payload()
    image = tmp_path/'preview.png'; image.write_bytes(b'fixture image')
    service.call_images.render = AsyncMock(return_value=image)
    monkeypatch.setattr(router, 'notification_service', service)
    monkeypatch.setattr(router, 'fetch_remote', AsyncMock(return_value=data))
    app = FastAPI(); app.include_router(router.router)
    with TestClient(app) as client:
        response = client.get('/api/delivery-management/calls/101/report-preview?day=' + data['call']['call_time'][:10])
        assert response.status_code == 200 and response.json()['bridge_called'] is False
    assert not service.store.recent() and service.store.call_report(data['call']['event_key']) is None
    service.bridge.send_text.assert_not_awaited(); service.bridge._post.assert_not_awaited()


@pytest.mark.asyncio
async def test_rollout_boundary_skips_unnotified_old_calls(service, tmp_path, monkeypatch):
    setup = payload()
    await service.configure(True, [GROUP])
    boundary = service.store.call_bundle_since()
    with service.store.connect() as db:
        db.execute('UPDATE config SET activated_at=?', ((boundary-timedelta(days=1)).isoformat(),))
    setup['call']['call_time'] = (boundary-timedelta(seconds=1)).isoformat()
    monkeypatch.setattr(notifications, 'now', lambda: boundary+timedelta(seconds=1))
    service.fetch.return_value = {'items': [setup['call']], 'has_more': False}
    await service.poll_once()
    assert not service.store.recent()
    assert all(call.args[0] == '/calls' for call in service.fetch.call_args_list)


@pytest.mark.asyncio
async def test_corrupt_queued_text_receipt_is_not_resent(service, tmp_path):
    setup_bundle(service, tmp_path)
    with service.store.connect() as db:
        db.execute("UPDATE deliveries SET text_status='sent'")
    await service.drain()
    assert service.store.recent()[0]['status'] == 'uncertain'
    service.bridge.send_text.assert_not_awaited(); service.bridge._post.assert_not_awaited()
