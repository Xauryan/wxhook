"""Deterministic call-report PNGs from immutable database snapshots."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid
from decimal import Decimal, ROUND_HALF_UP

from .notification_text import count, timestamp, time_text, phone_tail


def report_data(payload):
    if payload.get('version') != 1 or payload.get('cohort') != 'created_at' or payload.get('timezone') != 'Asia/Shanghai':
        raise ValueError('Unsupported monthly cohort')
    call = payload['call']
    snapshot, called = timestamp(payload.get('snapshot_at')), timestamp(call.get('call_time'))
    if not snapshot or not called or called > snapshot or payload.get('month') != called.strftime('%Y-%m'):
        raise ValueError('Invalid monthly snapshot time')
    if call.get('match_status') != 'matched' or not call.get('order_id'):
        raise ValueError('Call order unverified')
    for key in ('overall', 'pair'):
        metrics = payload.get(key)
        if metrics is None:
            if not payload.get('unavailable_reason'):
                raise ValueError('Missing monthly data without reason')
            continue
        for field in ('all_orders', 'delivery_total', 'non_delivery', 'success', 'in_progress', 'failed', 'other', 'unassigned', 'conflicts'):
            if count(metrics.get(field)) is None:
                raise ValueError('Invalid monthly count')
        total = metrics['delivery_total']
        if sum(metrics[field] for field in ('success', 'in_progress', 'failed', 'other')) != total:
            raise ValueError('Monthly categories do not balance')
        if metrics['all_orders'] != total + metrics['non_delivery'] or metrics['unassigned'] > metrics['other']:
            raise ValueError('Invalid monthly denominator')
        expected = float((Decimal(metrics['success']) * 100 / total).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)) if total else None
        uncertain = metrics['conflicts'] > 0 or (key == 'pair' and not metrics.get('complete'))
        if metrics.get('rate') != (None if uncertain else expected):
            raise ValueError('Monthly rate inconsistent with evidence')
    assigned = time_text(call.get('assigned_at'), seconds=True)
    if call.get('assignment_time_source') == 'current_assignment_unverified':
        assigned += '（历史待核）'
    elif call.get('assignment_time_source') == 'ambiguous':
        assigned = '待核对'
    candidates = call.get('order_candidates') or []
    customer = candidates[0].get('customer_name') if len(candidates) == 1 else None
    return {'template': 1, 'order_id': call['order_id'], 'agent_name': call.get('agent_name') or '外呼员待核',
            'group_name': call.get('outbound_group_name') or '小组未记录',
            'staff_name': call.get('staff_name') or '配送员未记录', 'customer_name': customer or '客户',
            'phone_tail': phone_tail(call.get('customer_phone_masked')), 'assigned_at': assigned,
            'call_time': time_text(called, seconds=True), 'connected': bool(call.get('is_connected')),
            'duration': count(call.get('call_duration')), 'recording': bool(call.get('recording_available')),
            'month': called.strftime('%Y年%m月建单'), 'snapshot_at': snapshot.strftime('%m-%d %H:%M:%S'),
            'overall': payload.get('overall'), 'pair': payload.get('pair'),
            'unavailable_reason': payload.get('unavailable_reason')}


def bundle_message(payload):
    data = report_data(payload)
    duration = '时长未记录' if data['duration'] is None else f"{data['duration']} 秒"
    result = ('已接通 ' + duration) if data['connected'] else '未接通'
    return '\n'.join([f"【配送通话】订单 #{data['order_id']}",
        f"{data['staff_name']} → {data['customer_name']}（尾号 {data['phone_tail']}）",
        f"外呼员：{data['agent_name']}｜{data['group_name']}",
        f"分配：{data['assigned_at']}", f"拨打：{data['call_time']}",
        result + '｜录音：' + ('有链接' if data['recording'] else '暂无链接'),
        '本月建单概况与归属统计见图。'])


class DeliveryCallImageRenderer:
    def __init__(self, root, renderer):
        self.root, self.renderer = Path(root), Path(renderer)
        self.lock = asyncio.Lock()

    async def render(self, key, payload):
        raw = json.dumps(report_data(payload), ensure_ascii=False, sort_keys=True)
        target = self.root / (hashlib.sha256((key + ':' + raw).encode()).hexdigest() + '.png')
        async with self.lock:
            self.root.mkdir(parents=True, exist_ok=True)
            if target.is_file() and target.stat().st_size > 24:
                return target.resolve()
            source, output = (self.root / (uuid.uuid4().hex + suffix) for suffix in ('.json', '.png'))
            try:
                source.write_text(raw, encoding='utf8')
                result = await asyncio.to_thread(subprocess.run,
                    ['node', str(self.renderer), str(source), str(output)], capture_output=True,
                    text=True, encoding='utf8', timeout=90,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                if result.returncode or not output.is_file():
                    raise ValueError('Call image rendering failed')
                png = output.read_bytes()
                if not png.startswith(b'\x89PNG\r\n\x1a\n') or not 1 <= int.from_bytes(png[20:24], 'big') <= 10000:
                    raise ValueError('Invalid call image')
                output.replace(target)
                return target.resolve()
            finally:
                source.unlink(missing_ok=True)
                output.unlink(missing_ok=True)
