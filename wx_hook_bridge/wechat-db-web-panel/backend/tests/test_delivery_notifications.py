from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.app import delivery_notifications as module
from backend.tests.quote_fixtures import fake_call_quote_source


GROUP = {"room_id": "fixture-room@chatroom", "name": "Fixture delivery group"}


@pytest.fixture
def service(tmp_path):
    store = module.DeliveryNotificationStore(tmp_path / "delivery.sqlite")
    store.initialize()
    bridge = SimpleNamespace(groups=AsyncMock(return_value=[SimpleNamespace(**GROUP)]),
                             send_text=AsyncMock(), _post=AsyncMock(), close=AsyncMock())
    fetch = AsyncMock()
    image = tmp_path/'stage.png';image.write_bytes(b'fixture-image')
    result = module.DeliveryNotificationService(store, bridge, fetch, send_interval=0,
        stage_images=SimpleNamespace(render=AsyncMock(return_value=image)))
    fake_call_quote_source(result)
    return result


def call(key, when, **extra):
    return dict(event_key=key, call_time=when.isoformat(), match_status="matched", order_id=1,
                staff_name="Fixture driver", customer_phone_masked="***1234", is_connected=True,
                call_duration=12, **extra)


def test_default_is_disabled_and_independent(service):
    config=service.store.configuration()
    assert {key:config[key] for key in ('enabled','target_groups','activated_at')} == {"enabled": False, "target_groups": [], "activated_at": None}
    assert not config['scheduled_enabled'] and not config['reminder_enabled'] and not config['result_notifications_enabled']
    assert service.store.path.name == "delivery.sqlite"
    assert config['failure_notifications_enabled'] is False
    assert config['failure_activated_at'] is None


@pytest.mark.asyncio
async def test_failure_notice_without_material_worker_fails_closed(service, monkeypatch):
    fixed = module.datetime(2026, 9, 20, 11, 0, tzinfo=module.BEIJING)
    monkeypatch.setattr(module, 'now', lambda: fixed)
    await service.configure(False, [GROUP], failure_notifications_enabled=True)
    async def fetch(path, params, **kwargs):
        if path.endswith('/archive-link'):
            return {'path': '/d/abcdefghijklmnop'}
        if path == '/cases/2':
            return {'id': 2, 'order_id': 2, 'created_at': fixed.isoformat(), 'reported_reason': '原始反馈，不是AI结论',
                    'candidates': {'order': {'customer_phone_masked': '138****1234'}}}
        return {'items': [{'id': 1, 'order_id': 1, 'created_at': (fixed-timedelta(hours=1)).isoformat(), 'reported_reason': '旧反馈'},
                          {'id': 2, 'order_id': 2, 'created_at': fixed.isoformat(), 'reported_reason': '原始反馈，不是AI结论'}], 'has_more': False}
    service.fetch.side_effect = fetch
    await service.poll_automation()
    await service.poll_automation()
    service.bridge.send_text.assert_not_awaited()
    assert not service.store.recent()
    assert service.store.configuration()['enabled'] is False


@pytest.mark.asyncio
async def test_requires_available_group_before_enabling(service):
    with pytest.raises(ValueError):
        await service.configure(True, [])
    with pytest.raises(ValueError):
        await service.configure(True, [dict(room_id="missing@chatroom", name="Missing")])
    await service.configure(True, [GROUP])
    first = service.store.configuration()["activated_at"]
    await service.configure(True, [GROUP])
    assert service.store.configuration()["activated_at"] == first


@pytest.mark.asyncio
async def test_disabled_never_fetches_or_sends(service):
    await service.poll_once()
    service.fetch.assert_not_awaited()
    service.bridge.send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_only_new_unique_calls_once_and_all_pages(service, monkeypatch):
    from backend.tests.test_delivery_call_images import payload
    await service.configure(True, [GROUP])
    cutoff = module.datetime.fromisoformat(service.store.configuration()["activated_at"])
    monkeypatch.setattr(module, "now", lambda: cutoff+timedelta(seconds=30))
    old = call("old", cutoff-timedelta(seconds=1))
    fresh = call("new", cutoff+timedelta(seconds=1), id=101, staff_id=1)
    ambiguous = {**fresh, "event_key": "ambiguous", "match_status": "ambiguous"}
    second = call("new2", cutoff+timedelta(seconds=2), id=102, staff_id=1)
    async def fetch(path, params):
        if path == '/calls':
            return ({"items": [second], "has_more": False} if params['before_id'] else
                    {"items": [old, fresh, ambiguous], "has_more": True, "next_before_id": 10})
        data = payload(); data['call'] = fresh if '/101/' in path else second
        return data
    service.fetch.side_effect = fetch
    service.call_images.render = AsyncMock(return_value=service.stage_images.render.return_value)
    await service.poll_once()
    await service.poll_once()
    assert service.bridge.send_text.await_count == 2
    assert all(row["status"] == "sent" for row in service.store.recent())
    assert "尾号 1234" in service.bridge.send_text.call_args.args[1]
    assert service.bridge._post.await_count == 2


@pytest.mark.asyncio
async def test_uncertain_response_is_not_retried(service):
    service.store.enqueue("event", [GROUP], "Fixture message")
    service.bridge.send_text.side_effect = TimeoutError()
    await service.drain()
    await service.drain()
    assert service.bridge.send_text.await_count == 1
    assert service.store.recent()[0]["status"] == "uncertain"


def test_crash_recovery_does_not_resend(service):
    service.store.enqueue("event", [GROUP], "Fixture message")
    service.store.mark(service.store.pending()[0], "sending")
    service.store.recover()
    assert not service.store.pending()
    assert service.store.recent()[0]["status"] == "uncertain"


@pytest.mark.asyncio
async def test_disabling_offline_cancels_unsent_messages(service):
    await service.configure(True, [GROUP])
    service.store.enqueue("event", [GROUP], "Fixture message")
    service.bridge.groups.side_effect = RuntimeError("offline")
    await service.configure(False, [GROUP])
    assert service.store.recent()[0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_manual_summary_is_idempotent_and_queued_when_auto_disabled(service):
    await service.configure(False, [GROUP])
    service.fetch.return_value = {"date": "2026-09-20", "items": [dict(staff_name="Fixture driver",
        first_call_at=None,call_count=0,dialed_customer_count=0,connected_customer_count=0,
        order_customer_count=0,connected_order_customer_count=0)]}
    day=module.now().date()
    await service.send_overview(day, "same-request")
    await service.send_overview(day, "same-request")
    assert len(service.store.pending()) == 1
    service.bridge.send_text.assert_not_awaited()
    await service.drain()
    assert service.bridge.send_text.await_count == 1


def test_atomic_claim_rejects_second_sender(service):
    service.store.enqueue("event", [GROUP], "Fixture message")
    row=service.store.pending()[0]
    assert service.store.mark(row,"sending")
    assert not service.store.mark(row,"sending")


@pytest.mark.asyncio
async def test_stage_cutoff_interval_and_restart_dedup(service,monkeypatch):
    fixed=module.datetime(2026,9,20,11,0,tzinfo=module.BEIJING)
    monkeypatch.setattr(module,'now',lambda:fixed)
    await service.configure(False,[GROUP],scheduled_enabled=True,stage_times=['12:00'])
    monkeypatch.setattr(module,'now',lambda:fixed+timedelta(hours=1,minutes=5))
    row=dict(staff_id=1,staff_name='Fixture',first_call_at=None,call_count=5,dialed_customer_count=3,connected_customer_count=2)
    service.fetch.return_value={'date':'2026-09-20','items':[row]}
    await service.poll_automation()
    await service.poll_automation()
    assert service.fetch.await_count==2
    assert service.fetch.call_args_list[0].args[1]['at']=='2026-09-20T12:00:00+08:00'
    assert service.fetch.call_args_list[1].args[1]['since']=='2026-09-20T00:00:00+08:00'
    service.bridge.send_text.assert_not_awaited()
    service.bridge._post.assert_awaited_once()
    assert service.bridge._post.call_args.args[0]=='/api/sendImagesMsg'
    assert service.store.stages()[0]['status']=='ready'


@pytest.mark.asyncio
async def test_missed_stage_is_not_backfilled(service,monkeypatch):
    fixed=module.datetime(2026,9,20,11,0,tzinfo=module.BEIJING)
    monkeypatch.setattr(module,'now',lambda:fixed)
    await service.configure(False,[GROUP],scheduled_enabled=True,stage_times=['12:00'])
    monkeypatch.setattr(module,'now',lambda:fixed+timedelta(hours=2))
    await service.poll_automation()
    service.fetch.assert_not_awaited()
    assert service.store.stages()[0]['status']=='missed'


@pytest.mark.asyncio
async def test_reminders_are_event_deduped_and_skip_old_backlog(service,monkeypatch):
    fixed=module.datetime(2026,9,20,11,0,tzinfo=module.BEIJING)
    monkeypatch.setattr(module,'now',lambda:fixed)
    await service.configure(False,[GROUP],reminder_enabled=True)
    service.fetch.return_value={'items':[
        {'event_key':'delivery.callback:old','kind':'callback','order_id':1,'due_at':(fixed-timedelta(hours=1)).isoformat()},
        {'event_key':'delivery.callback:new','kind':'callback','order_id':2,'due_at':fixed.isoformat()}]}
    await service.poll_automation()
    await service.poll_automation()
    assert service.bridge.send_text.await_count==1


@pytest.mark.asyncio
async def test_stage_configuration_rejects_invalid_time(service):
    with pytest.raises(ValueError,match='HH:MM'):
        await service.configure(False,[GROUP],scheduled_enabled=True,stage_times=['25:00'])


@pytest.mark.asyncio
async def test_partial_ai_draft_is_not_broadcast_without_human_confirmation(service,monkeypatch):
    fixed=module.datetime(2026,9,20,11,0,tzinfo=module.BEIJING)
    monkeypatch.setattr(module,'now',lambda:fixed)
    await service.configure(False,[GROUP],result_notifications_enabled=True)
    case={'id':1,'order_id':1,'created_at':fixed.isoformat(),'analysis_status':'succeeded','review_status':'pending',
          'analysis':{'validation_status':'partial','analysis_run_id':'fixture','summary':'Draft','next_actions':[]}}
    service.fetch.return_value={'items':[case],'has_more':False}
    await service.poll_automation()
    service.bridge.send_text.assert_not_awaited()
    case['review_status']='confirmed'
    async def fetch(path, params, **kwargs):
        return {'path': '/d/abcdefghijklmnop'} if path.endswith('/archive-link') else {'items': [case], 'has_more': False}
    service.fetch.side_effect = fetch
    await service.poll_automation()
    assert service.bridge.send_text.await_count==1
