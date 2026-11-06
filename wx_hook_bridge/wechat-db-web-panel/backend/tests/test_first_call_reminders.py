from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from backend.app import delivery_notifications as notice
from backend.tests.test_delivery_notifications import service, GROUP


def item(at):
    return dict(kind='first_call', event_key='delivery.first_call_overdue:81', assignment_id=81,
                order_id=10725, staff_name='高伟坚', assigned_at=(at-timedelta(hours=2,minutes=7)).isoformat(),
                due_at=(at-timedelta(hours=1,minutes=7)).isoformat(), checked_at=at.isoformat(),
                customer_name='测试客户',customer_phone_masked='***1234',agent_name='测试外呼员',outbound_group_name='测试组')


def queue(service, monkeypatch):
    at = notice.now()
    monkeypatch.setattr(notice,'now',lambda:at)
    service.store.configure(False,[GROUP],reminder_enabled=True)
    with service.store.connect() as db:
        db.execute('UPDATE config SET automation_activated_at=?',((at-timedelta(days=1)).isoformat(),))
    data=item(at)
    service.store.enqueue(data['event_key'],[GROUP],'original queued message')
    service.fetch.return_value={'assignment_id':81,'eligible':True,'item':data}
    return at,data


def test_message_has_action_context_elapsed_and_no_link():
    at=notice.now(); data=item(at)
    text=notice.reminder_message(data,at=at+timedelta(minutes=2))
    assert '【首拨提醒】高伟坚｜订单 #10725' in text
    assert '客户：测试客户｜尾号 1234' in text and '外呼：测试外呼员｜测试组' in text
    assert '已超时：1小时9分钟' in text
    assert '分配：' in text and '首拨截止：' in text
    assert '拨号记录' in text and '记录同步' in text
    assert '查看订单' not in text and 'http' not in text


@pytest.mark.asyncio
async def test_rechecks_then_claims_updated_text_once(service,monkeypatch):
    at,data=queue(service,monkeypatch)
    await service.drain();await service.drain()
    service.fetch.assert_awaited_once_with('/alerts/first-call/81',{'first_call_minutes':60})
    service.bridge.send_text.assert_awaited_once()
    assert '已超时：1小时7分钟' in service.bridge.send_text.call_args.args[1]
    assert service.store.recent()[0]['status']=='sent'


@pytest.mark.asyncio
async def test_no_longer_eligible_cancels_without_send(service,monkeypatch):
    queue(service,monkeypatch)
    service.fetch.return_value={'assignment_id':81,'eligible':False,'item':None}
    await service.drain()
    assert service.store.recent()[0]['status']=='cancelled'
    service.bridge.send_text.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['network','wrong_assignment','missing_context'])
async def test_recheck_failures_keep_pending_and_do_not_block_other_notices(service,monkeypatch,failure):
    _,data=queue(service,monkeypatch)
    if failure=='network':service.fetch.side_effect=TimeoutError()
    elif failure=='wrong_assignment':service.fetch.return_value={'assignment_id':82,'eligible':False}
    else:service.fetch.return_value={'assignment_id':81,'eligible':True,'item':{'event_key':data['event_key'],'kind':'first_call'}}
    service.store.enqueue('unrelated',[GROUP],'other notice')
    await service.drain()
    service.bridge.send_text.assert_awaited_once_with(GROUP['room_id'],'other notice')
    rows={r['event_key']:r for r in service.store.recent()}
    assert rows[data['event_key']]['status']=='queued'
    with service.store.connect() as db:
        assert db.execute('SELECT message FROM deliveries WHERE event_key=?',(data['event_key'],)).fetchone()[0]=='original queued message'


@pytest.mark.asyncio
async def test_bridge_uncertainty_is_not_retried(service,monkeypatch):
    queue(service,monkeypatch);service.bridge.send_text.side_effect=TimeoutError()
    await service.drain();await service.drain()
    assert service.store.recent()[0]['status']=='uncertain'
    service.fetch.assert_awaited_once();service.bridge.send_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_existing_sent_receipt_is_never_rewritten(service,monkeypatch):
    _,data=queue(service,monkeypatch)
    with service.store.connect() as db:
        db.execute("UPDATE deliveries SET status='sent',sent_at='original'")
        before=dict(db.execute('SELECT * FROM deliveries').fetchone())
    service.store.enqueue(data['event_key'],[GROUP],notice.reminder_message(data))
    await service.drain()
    with service.store.connect() as db:assert dict(db.execute('SELECT * FROM deliveries').fetchone())==before
    service.fetch.assert_not_awaited();service.bridge.send_text.assert_not_awaited()
