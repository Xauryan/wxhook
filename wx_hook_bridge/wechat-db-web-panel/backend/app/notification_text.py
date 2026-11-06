"""Pure formatting helpers for notifications; no storage or sending side effects."""
from datetime import datetime, timezone, timedelta
import re

BEIJING = timezone(timedelta(hours=8))


def timestamp(value, *, stored_beijing=False):
    try:
        point = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    if point.tzinfo is None:
        if not stored_beijing:
            return None
        point = point.replace(tzinfo=BEIJING)
    return point.astimezone(BEIJING)


def date_label(value):
    try:
        point = datetime.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return '日期未记录'
    return point.strftime('%m-%d' if point.year == datetime.now(BEIJING).year else '%Y-%m-%d')


def time_text(value, *, seconds=False, clock=False, stored_beijing=False):
    point = timestamp(value, stored_beijing=stored_beijing)
    if point is None:
        return '未记录'
    prefix = '' if clock else date_label(point.date()) + ' '
    return prefix + point.strftime('%H:%M:%S' if seconds else '%H:%M')


def interval_text(start, end):
    first, last = timestamp(start), timestamp(end)
    if first is None or last is None or first > last:
        return '区间暂缺'
    if first.date() == last.date():
        return f"{first:%H:%M} 至 {last:%H:%M}"
    pattern = '%Y-%m-%d %H:%M' if first.year != last.year else '%m-%d %H:%M'
    return f'{first.strftime(pattern)} 至 {last.strftime(pattern)}'


def phone_tail(value):
    digits = re.sub(r'\D', '', str(value or ''))
    return digits[-4:] if len(digits) >= 4 else '未记录'


def count(value):
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def duration_text(value):
    seconds = count(value)
    if seconds is None:
        return '时长未记录'
    minutes, seconds = divmod(seconds, 60)
    return f'{minutes} 分 {seconds} 秒' if minutes else f'{seconds} 秒'


def metric_text(label, value, unit):
    number = count(value)
    return f'{label} {number} {unit}' if number is not None else f'{label}暂缺'


def call_metrics(row, *, short=False):
    unit = '号' if short else '个号码'
    return '｜'.join([metric_text('拨号', row.get('call_count'), '次'),
        metric_text('覆盖', row.get('dialed_customer_count'), unit),
        metric_text('接通', row.get('connected_customer_count'), unit)])


def first_call_text(row):
    first = timestamp(row.get('first_call_at'))
    if first is not None:
        return f'首拨 {first:%H:%M}'
    return '暂无通话记录' if count(row.get('call_count')) == 0 else '首拨时间未记录'


def verified_reporter(value):
    if not isinstance(value, dict) or value.get('verified') is not True:
        return None
    if value.get('source') not in {'intention_customer_logs', 'intention_order_events'}:
        return None
    if not isinstance(value.get('user_id'), int) or value['user_id'] <= 0 or not value.get('audit_id'):
        return None
    return value if isinstance(value.get('name'), str) and value['name'].strip() else None


def feedback_recordings(case):
    """One business-selected call per side, never a first-call fallback."""
    scope = case.get('material_scope') or {}
    if scope.get('policy') not in {'business-relevance-v1','business-relevance-v2-deepseek-auto'} or scope.get('status') != 'selected':
        return {}
    candidates = {call['id']: call for call in case.get('candidates', {}).get('items', [])}
    grouped = {'outbound': {}, 'delivery': {}}
    for ref in case.get('notification_calls') or []:
        role = ref.get('role')
        if role not in grouped or ref not in scope.get('calls', []):
            continue
        if ref.get('purpose') != {'outbound': 'outbound_sale', 'delivery': 'delivery_failure'}[role]:
            continue
        if ref.get('source') not in {'manual_business_confirmation', 'existing_transcript_relevance', 'deepseek_relevance'}:
            continue
        call = candidates.get(ref.get('id'))
        if call and ref.get('ec_waste_id') and ref['ec_waste_id'] == call.get('ec_waste_id'):
            grouped[role][call['id']] = {**call, 'role': role}
    return {role: next(iter(calls.values())) for role, calls in grouped.items() if len(calls) == 1}


def recording_page_url(case, call):
    ids = [case.get('order_id'), case.get('id'), call.get('id')]
    if any(type(value) is not int or value <= 0 for value in ids):
        raise ValueError('录音定位参数无效')
    return f'https://youshenghe666.com/outbound/recordings/orders/{ids[0]}?case={ids[1]}&call={ids[2]}'


def caption_message(row, context, index, total, archive_url=None, reporter=None):
    role = {'外呼通话记录': '外呼录音', '配送通话记录': '配送录音'}.get(row['title'], '归属待确认录音')
    lines = [f"【{role}｜{index}/{total}】订单 #{context['order_id']}"]
    identity = verified_reporter(reporter)
    if identity:
        lines.append(f"本次反馈人：{identity['name']}｜提交时间 {time_text(identity.get('submitted_at'))}")
    lines.extend([f"关联员工：{context['employee']}｜客户尾号 {phone_tail(context.get('customer'))}",
        f"{time_text(row['call_time'], seconds=True, stored_beijing=True)}｜{duration_text(context.get('duration'))}",
        '下附完整转写长图，A/B 仅代表分轨。'])
    if archive_url:
        lines.extend(['听录音 / 看原文（需登录）：', archive_url])
    return '\n'.join(lines)
