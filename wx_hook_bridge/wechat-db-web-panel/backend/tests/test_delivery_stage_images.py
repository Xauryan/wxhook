import copy
import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from backend.app.delivery_stage_images import report_data, DeliveryStageImageRenderer
from backend.app import delivery_notifications as notifications
from backend.tests.test_delivery_notifications import service, GROUP

KEY='delivery.stage:2026-09-20:21:00'


def snapshot():
    items=[{'staff_id':1,'staff_name':'Active <A>','call_count':5,'dialed_customer_count':3,'connected_customer_count':2},
           {'staff_id':2,'staff_name':'Enabled zero','call_count':0,'dialed_customer_count':0,'connected_customer_count':0}]
    daily={'date':'2026-09-20','snapshot_at':'2026-09-20T21:00:00+08:00','since':'2026-09-20T00:00:00+08:00',
           'roster_policy':'currently_active','items':items}
    period=copy.deepcopy(daily);period['since']='2026-09-20T18:30:00+08:00';period['items'][0].update(call_count=2,dialed_customer_count=1,connected_customer_count=1)
    return {'cumulative':daily,'period':period}


def test_counts_and_explicit_zero_roster_are_preserved():
    data=report_data('21:00',**snapshot())
    assert data['daily_calls']==5 and data['period_calls']==2
    assert data['active_staff']==1 and data['total_staff']==2
    assert data['zero_names']==['Enabled zero']
    assert data['rows'][0]['period']==[2,1,1]


def test_missing_row_is_not_zero_and_phone_counts_are_not_subtracted():
    payload=snapshot();payload['period']['items']=[]
    data=report_data('21:00',**payload)
    assert data['period_calls'] is None and data['period_active_staff'] is None
    assert data['zero_names']==[] and data['has_missing']
    assert all(row['period']==[None,None,None] for row in data['rows'])


def test_duplicate_identity_or_invalid_time_rejected():
    payload=snapshot();payload['period']['items'].append(payload['period']['items'][0])
    with pytest.raises(ValueError,match='duplicate'):report_data('21:00',**payload)
    payload=snapshot();payload['period']['snapshot_at']=None
    with pytest.raises(ValueError,match='time'):report_data('21:00',**payload)


@pytest.mark.asyncio
async def test_real_renderer_data_and_cache_are_deterministic(tmp_path):
    renderer=DeliveryStageImageRenderer(tmp_path,Path(__file__).resolve().parents[2]/'scripts/render-delivery-stage.cjs')
    output=await renderer.render(KEY,snapshot());before=output.stat().st_mtime_ns
    body=output.read_bytes();assert body.startswith(b'\x89PNG') and int.from_bytes(body[16:20],'big')==980
    assert await renderer.render(KEY,snapshot())==output and output.stat().st_mtime_ns==before
    assert not list(tmp_path.glob('*.json'))


def enqueue(service,media_type='image'):
    cutoff=notifications.datetime(2026,9,20,21,tzinfo=notifications.BEIJING)
    service.store.persist_stage(KEY,cutoff,snapshot(),[GROUP],'original frozen text',media_type=media_type)


@pytest.mark.asyncio
async def test_image_sent_once_without_text_and_preserves_historical_text(service):
    enqueue(service)
    await service.drain();await service.drain()
    service.bridge.send_text.assert_not_awaited();service.bridge._post.assert_awaited_once()
    assert service.bridge._post.call_args.args[0]=='/api/sendImagesMsg'
    assert Path(service.bridge._post.call_args.args[1]['imagePath']).is_absolute()
    old='delivery.stage:2026-09-19:21:00'
    service.store.persist_stage(old,notifications.now(),snapshot(),[GROUP],'saved legacy text')
    await service.drain()
    assert service.bridge.send_text.call_args.args[1]=='saved legacy text'
    assert service.store.stage(old)['media_type']=='text'


@pytest.mark.asyncio
async def test_render_failure_never_falls_back_to_text_and_retry_is_bounded(service,monkeypatch):
    enqueue(service)
    clock=notifications.now();monkeypatch.setattr(notifications,'now',lambda:clock)
    service.stage_images.render.side_effect=ValueError('fixture')
    for _ in range(5):
        await service.drain();clock+=timedelta(minutes=2)
    service.bridge.send_text.assert_not_awaited();service.bridge._post.assert_not_awaited()
    assert service.stage_images.render.await_count==3
    with service.store.connect() as db:
        row=db.execute('SELECT * FROM deliveries').fetchone()
        assert row['status']=='render_failed' and row['render_attempts']==3


@pytest.mark.asyncio
async def test_uncertain_image_result_never_retries_or_changes_snapshot(service):
    enqueue(service);original=service.store.stage(KEY)['payload']
    service.bridge._post.side_effect=TimeoutError()
    await service.drain();service.store.recover();await service.drain()
    service.bridge._post.assert_awaited_once();service.bridge.send_text.assert_not_awaited()
    assert service.store.recent()[0]['status']=='uncertain'
    assert service.store.stage(KEY)['payload']==original


@pytest.mark.asyncio
async def test_configuration_change_cancels_unsent_render_retry(service):
    enqueue(service);service.stage_images.render.side_effect=ValueError('fixture')
    await service.drain()
    service.store.configure(True,[GROUP])
    assert service.store.recent()[0]['status']=='cancelled'


def test_image_preview_does_not_send_and_retry_cannot_reset_accepted_or_uncertain(service,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.app import delivery_management_router as router
    service.store.configure(False,[GROUP]);enqueue(service)
    monkeypatch.setattr(router,'notification_service',service)
    app=FastAPI();app.include_router(router.router)
    url='/api/delivery-management/notifications/stages/'+KEY
    with TestClient(app) as client:
        data=client.get(url).json();assert data['media_type']=='image'
        assert client.get(url+'/image').headers['content-type']=='image/png'
        service.bridge._post.assert_not_awaited();service.bridge.send_text.assert_not_awaited()
        for state in ['sent','uncertain','render_failed']:
            with service.store.connect() as db:
                db.execute('UPDATE deliveries SET status=?,render_attempts=3 WHERE event_key=?',(state,KEY))
            assert client.post(url+'/retry-render',json={}).status_code==200
            assert service.store.recent()[0]['status']==('queued' if state=='render_failed' else state)
        with service.store.connect() as db:
            db.execute("UPDATE stage_reports SET media_type='text' WHERE report_key=?",(KEY,))
        assert client.get(url+'/image').status_code==404
