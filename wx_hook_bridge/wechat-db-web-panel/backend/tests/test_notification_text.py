"""Confirmed 03 and 05-14 copy only; all sends use fake bridges or pure functions."""
from dataclasses import replace
from datetime import datetime, timedelta
import json
from unittest.mock import AsyncMock

import pytest

from backend.app import delivery_notifications as delivery
from backend.app import intention_notifications as intention
from backend.app import notification_text as text
from backend.tests.test_delivery_notifications import service, GROUP
from backend.tests.test_delivery_dialogues import service as image_service, ready


def team(point='2026-09-20T16:02:00+08:00', **kwargs):
    value = intention.TeamSnapshot('4', '呼叫中心四组', point, 15, 12, 58, (
        intention.TeamMemberSnapshot('1', '小陈', 20, 6),
        intention.TeamMemberSnapshot('2', '小李', 20, 4),
        intention.TeamMemberSnapshot('3', '小周', 20, 2),
        intention.TeamMemberSnapshot('4', '小林', 20, 2),
        intention.TeamMemberSnapshot('5', '小吴', 20, 1),
        intention.TeamMemberSnapshot('6', '小郑', 20, 0),
    ))
    return replace(value, **kwargs)


def stage(snapshot, previous=None):
    return intention.render_scheduled_group_report(slot=intention.SCHEDULED_REPORT_SLOTS[1],
        snapshot_at=datetime.fromisoformat(snapshot.snapshot_at), snapshot=snapshot, previous_snapshot=previous)


def test_stage_uses_actual_cutoff_nonduplicated_roster_and_actual_prior():
    output = stage(team(), team('2026-09-20T12:02:00+08:00', group_today=8))
    assert output.startswith('【意向阶段战报｜呼叫中心四组】\n09-20 16:00 场｜数据截至 16:02')
    assert '今日 15 单｜昨日同期 12 单｜增加 3 单' in output
    assert '较上次 12:02 快照增加 7 单' in output
    assert output.count('小郑') == 1
    assert '0 单：' not in output and '全场累计' not in output and '在岗' not in output
    assert '通话统计组员 6 人：已破零 5 人，待破零 1 人' in output


@pytest.mark.parametrize('previous', [
    team('2026-09-19T12:00:00+08:00'),
    team('2026-09-20T12:00:00+08:00', group_key='other'),
    team('2026-09-20T17:00:00+08:00'),
    team('2026-09-20T12:00:00+08:00', group_today=None),
])
def test_no_false_stage_comparison(previous):
    assert '较上次' not in stage(team(), previous)


def test_stage_zero_roster_difference_and_decreases_are_honest():
    empty = team(members=())
    output = stage(empty)
    assert '人数为 0，不计算破零率和人均' in output
    assert '0/0' not in output and '0%' not in output
    output = stage(team(group_today=17), team('2026-09-20T12:00:00+08:00', group_today=19))
    assert '另有 2 单不在上述成员分布内' in output
    assert '快照减少 2 单' in output


def test_delivery_call_context_and_recording_link_semantics():
    call = dict(order_id=90003, staff_name='宋师傅', customer_phone_masked='***1234',
        outbound_group_name='呼叫中心四组', agent_name='小周', assigned_at='2026-09-20T06:30:00Z',
        assignment_time_source='recorded_history', call_time='2026-09-20T08:12:08Z',
        is_connected=True, call_duration=24, recording_available=True)
    assert delivery.call_message(call) == '\n'.join([
        '【配送通话】订单 #90003', '宋师傅｜客户尾号 1234', '呼叫中心四组｜小周（外呼员）',
        '分配时间：09-20 14:30', '09-20 16:12:08｜已接通 24 秒', '录音：有链接'])
    output = delivery.call_message({**call, 'is_connected': False, 'recording_available': False,
                                    'assigned_at': None, 'agent_name': None, 'outbound_group_name': None})
    assert '未接通' in output and '录音：暂无链接' in output
    assert '分配时间：未记录' in output and '小组未记录｜未记录（外呼员）' in output
    assert '已到达' not in output and '可播放' not in output and '已转写' not in output
    assert '历史归属待核对' in delivery.call_message({**call, 'assignment_time_source': 'current_assignment_unverified'})


def test_overview_has_no_removed_footer_and_no_synthetic_zero():
    row = dict(staff_name='宋师傅', first_call_at='2026-09-20T01:12:00Z', call_count=20,
               dialed_customer_count=12, connected_customer_count=8, order_customer_count=10, connected_order_customer_count=7)
    output = delivery.overview_message({'date': '2026-09-20', 'since': '2026-09-20T00:00:00+08:00',
        'snapshot_at': '2026-09-20T16:00:00+08:00', 'items': [row]})
    assert '统计区间 00:00 至 16:00' in output and '宋师傅｜首拨 09:12' in output
    assert output.endswith('订单关联号码：覆盖 10 个，接通 7 个')
    assert '跨人' not in output and '快照：' not in output and ' 人' not in output
    assert text.interval_text('2026-09-19T00:00:00+08:00', '2026-09-20T00:00:00+08:00') == '09-19 00:00 至 09-20 00:00'
    assert text.time_text(None) == '未记录'


def test_delivery_stage_missing_is_not_zero_or_cumulative_subtraction():
    row = dict(staff_id=1, staff_name='宋师傅', call_count=20, dialed_customer_count=12, connected_customer_count=8)
    cumulative = {'date': '2026-09-20', 'since': '2026-09-20T00:00:00+08:00', 'snapshot_at': '2026-09-20T16:00:00+08:00', 'items': [row]}
    period = {**cumulative, 'since': '2026-09-20T12:00:00+08:00', 'items': []}
    missing = delivery.stage_message('16:00', cumulative, period)
    assert '本阶段 12:00 至 16:00' in missing
    assert '本阶段：数据暂缺' in missing and '拨号 0 次' not in missing
    period['items'] = [{**row, 'call_count': 0, 'dialed_customer_count': 0, 'connected_customer_count': 0}]
    assert '本阶段：拨号 0 次｜覆盖 0 号｜接通 0 号' in delivery.stage_message('16:00', cumulative, period)
    assert '今日累计：拨号 20 次｜覆盖 12 号｜接通 8 号' in missing


@pytest.mark.parametrize('kind,title,action', [
    ('callback', '回拨提醒', '如已回拨，请更新跟进结果。'),
    ('case_due', '异常处理逾期', '请更新处理进展和下一步安排。'),
    ('appointment', '预约配送待确认', '请核对预约是否已履行'),
])
def test_reminders_use_specific_time_and_action(kind, title, action):
    output = delivery.reminder_message({'kind': kind, 'order_id': 90004, 'case_id': 88, 'staff_name': None, 'due_at': '2026-09-20T07:30:00Z'})
    assert f'【{title}】订单 #90004' in output and '待分配' in output
    assert '09-20 15:30' in output and action in output
    assert ('异常单 #88' in output) == (kind in {'callback', 'case_due'})
    assert '+00:00' not in output


def test_feedback_preserves_verbatim_reason_without_fabricating_author():
    reason = '  客户说“并非不办理”。\n费用仍待核实。  '
    case = {'id': 88, 'order_id': 90008, 'created_at': '2026-09-20T02:25:00Z', 'owner_name': '不能冒充反馈人',
            'reported_reason': reason, 'candidates': {'order': {'customer_phone_masked': '***1234'}}}
    output = delivery.failure_message(case, 'https://fixture.invalid/d/code')
    assert '反馈原文：\n' + reason + '\n\n' in output
    assert '异常单记录时间 09-20 10:25' in output
    assert '反馈人：未记录' in output and case['owner_name'] not in output
    case.update(failure_event_id=5, history={'events': [{'id': 5, 'created_at': '2026-09-20T02:00:00Z'}]})
    assert '反馈人：未记录｜09-20 10:00' in delivery.failure_message(case, 'https://fixture.invalid/d/code')


def test_ai_does_not_truncate_negation_or_lose_gap_notice():
    report = {'summary': '现有录音不足以确认原因。', 'next_actions': ['核对费用', '回访客户', '补齐证据'], 'evidence_gaps': ['缺少客户明确确认']}
    case = {'id': 88, 'order_id': 90008, 'analysis': report}
    output = delivery.analysis_message(case, 'https://fixture.invalid/d/code')
    assert '初步判断：现有录音不足以确认原因。' in output
    assert '1. 核对费用\n2. 回访客户' in output and '另有 1 条建议' in output
    assert '待核对：缺少客户明确确认' in output and '通话证据（需登录）' in output
    report['summary'] = 'x' * 181 + '并非已确认'
    output = delivery.analysis_message(case, 'https://fixture.invalid/d/code')
    assert '复盘已生成，请在配送管理中复核。' in output and 'xxx' not in output


@pytest.mark.asyncio
async def test_ai_link_resolved_once_and_existing_bodies_never_rewritten(service, monkeypatch):
    fixed = datetime(2026, 9, 20, 11, tzinfo=delivery.BEIJING)
    monkeypatch.setattr(delivery, 'now', lambda: fixed)
    await service.configure(False, [GROUP], result_notifications_enabled=True)
    case = {'id': 88, 'order_id': 90008, 'created_at': fixed.isoformat(), 'analysis_status': 'succeeded',
            'analysis': {'analysis_run_id': 'once', 'validation_status': 'passed', 'summary': '未确定原因'}}
    async def fetch(path, params, **kwargs):
        return {'path': '/d/abcdefghijklmnop'} if path.endswith('/archive-link') else {'items': [case], 'has_more': False}
    service.fetch.side_effect = fetch
    await service.poll_automation()
    original = service.bridge.send_text.call_args.args[1]
    case['analysis']['summary'] = '后续不同文案'
    await service.poll_automation()
    assert service.bridge.send_text.await_count == 1
    assert sum(c.args[0].endswith('/archive-link') for c in service.fetch.call_args_list) == 1
    with service.store.connect() as db:
        assert db.execute('SELECT message FROM deliveries').fetchone()[0] == original


def test_single_text_plan_preserves_images_and_historical_caption_receipts(image_service):
    ready(image_service, 'a')
    with image_service.store.connect() as db:
        images_before = [tuple(r) for r in db.execute('SELECT * FROM dialogue_images')]
    image_before = (image_service.root/'a.png').read_bytes()
    plan = image_service.plan(1, ['a'], ['test-only@chatroom'], {}, 'https://fixture.invalid/d/code')
    assert plan[0]['body'] == '' and plan[0]['context_required'] is False
    assert plan[0]['context_status'] == 'not_required'
    with image_service.store.connect() as db:
        assert images_before == [tuple(r) for r in db.execute('SELECT * FROM dialogue_images')]
        db.execute('INSERT INTO dialogue_context_receipts VALUES(?,?,?,?,?,?,?)', ('a', 'test-only@chatroom', '已保存的旧文案', None, 'bridge_accepted', None, 'fixture'))
    assert image_service.plan(1, ['a'], ['test-only@chatroom'], {})[0]['body'] == ''
    with image_service.store.connect() as db:
        assert db.execute('SELECT body FROM dialogue_context_receipts').fetchone()[0] == '已保存的旧文案'
    assert (image_service.root/'a.png').read_bytes() == image_before
    image_service.bridge._post.assert_not_called()
