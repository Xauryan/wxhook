"""Frozen stage snapshots rendered with the approved deterministic report layout."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

from .node_runtime import node_executable
from .notification_text import count, date_label, interval_text, time_text, timestamp

TEMPLATE_VERSION = 1
METRICS = ('call_count', 'dialed_customer_count', 'connected_customer_count')


def report_data(slot, cumulative, period):
    if not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', slot):
        raise ValueError('Invalid stage slot')
    end, start, cumulative_end = timestamp(period.get('snapshot_at')), timestamp(period.get('since')), timestamp(cumulative.get('snapshot_at'))
    if not start or not end or not cumulative_end or start >= end or end != cumulative_end:
        raise ValueError('Stage snapshot time missing or inconsistent')
    groups = []
    for data in (cumulative, period):
        items = data.get('items')
        if not isinstance(items, list):
            raise ValueError('Stage roster missing')
        index = {}
        for row in items:
            key = row.get('staff_id')
            if type(key) is not int or key <= 0 or key in index or not row.get('staff_name'):
                raise ValueError('Invalid or duplicate staff identity')
            index[key] = row
        groups.append(index)
    daily, stage = groups
    rows, zero = [], []
    for key in sorted(set(daily) | set(stage)):
        left, right = daily.get(key), stage.get(key)
        values = {'staff_id': key, 'name': (left or right)['staff_name'],
                  'daily': [count((left or {}).get(k)) for k in METRICS],
                  'period': [count((right or {}).get(k)) for k in METRICS]}
        if values['daily'] == [0,0,0] and values['period'] == [0,0,0]:
            zero.append(values['name'])
        else:
            rows.append(values)
    rows.sort(key=lambda row: (row['daily'][0] is None, -(row['daily'][0] or 0), row['staff_id']))
    all_rows = rows + [{'daily':[0,0,0], 'period':[0,0,0]} for _ in zero]
    def total(side):
        values = [row[side][0] for row in all_rows]
        return sum(values) if all(value is not None for value in values) else None
    complete_daily = all(row['daily'][0] is not None for row in all_rows)
    complete_stage = all(row['period'][0] is not None for row in all_rows)
    return {'template':TEMPLATE_VERSION,'date':date_label(cumulative.get('date')),'slot':slot,
            'period_label':interval_text(start,end),'cutoff':time_text(cumulative_end,clock=True),
            'daily_calls':total('daily'),'period_calls':total('period'),
            'active_staff':sum(row['daily'][0]>0 for row in all_rows) if complete_daily else None,
            'period_active_staff':sum(row['period'][0]>0 for row in all_rows) if complete_stage else None,
            'total_staff':len(all_rows),'rows':rows,'zero_names':zero,
            'roster_policy':cumulative.get('roster_policy','snapshot_roster'),
            'has_missing':any(None in row['daily']+row['period'] for row in rows)}


class DeliveryStageImageRenderer:
    def __init__(self, root, renderer):
        self.root, self.renderer = Path(root), Path(renderer)
        self.lock = asyncio.Lock()

    async def render(self, report_key, payload):
        data = report_data(report_key[-5:], payload['cumulative'], payload['period'])
        raw = json.dumps(data, sort_keys=True, ensure_ascii=False)
        image_id = hashlib.sha256((report_key + ':' + raw).encode()).hexdigest()
        target = self.root / (image_id + '.png')
        async with self.lock:
            self.root.mkdir(parents=True, exist_ok=True)
            if target.is_file() and target.stat().st_size > 24:
                return target.resolve()
            source = self.root / (uuid.uuid4().hex + '.json')
            output = self.root / (uuid.uuid4().hex + '.png')
            try:
                source.write_text(raw, encoding='utf8')
                result = await asyncio.to_thread(subprocess.run, [node_executable(), str(self.renderer), str(source), str(output)],
                    capture_output=True, text=True, encoding='utf8', timeout=90,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                if result.returncode or not output.is_file():
                    raise ValueError('Stage image rendering failed')
                png = output.read_bytes()
                if not png.startswith(b'\x89PNG\r\n\x1a\n') or int.from_bytes(png[20:24],'big') > 100000:
                    raise ValueError('Invalid stage image')
                output.replace(target)
                return target.resolve()
            finally:
                source.unlink(missing_ok=True)
                output.unlink(missing_ok=True)
