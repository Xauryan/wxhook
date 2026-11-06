"""Read one verified account/group/window from the existing snapshot pipeline."""
import asyncio
import base64
from collections import Counter
from datetime import datetime
import hashlib
import re

from fastapi import HTTPException

from ..chat_history import readonly
from ..chat_messages import present_content, server_identity
from ..parsing import decode_wcdb_value
from .models import TIMEZONE


class GroupMessageSource:
    def __init__(self, live):
        self.live = live

    async def directory(self, account):
        await self.live.check_account(account, force=True)
        snapshot = await self.live.roster.read_async(account)
        if snapshot.get('account') != account or snapshot.get('source') != 'active_contact_db':
            raise HTTPException(409, '当前账号通讯录核验失败')
        await self.live.check_account(account, force=True)
        await asyncio.to_thread(self.live._replace_roster, account, snapshot)
        return snapshot

    async def groups(self, account):
        snapshot = await self.directory(account)
        return [{'id': row['username'], 'name': row['name']} for row in snapshot['contacts']
                if row['relation'] == 'group']

    async def verify_group(self, account, room):
        snapshot = await self.directory(account)
        group = next((row for row in snapshot['contacts']
                      if row['username'] == room and row['relation'] == 'group'), None)
        if group is None:
            raise HTTPException(409, '所选群聊不在当前账号已核验的通讯录中')
        return group, snapshot

    async def members(self, account, room):
        _, snapshot = await self.verify_group(account, room)
        names = {row['username']: row['name'] for row in snapshot['names']}
        group = next((item for item in snapshot['automation']['groups'] if item['username'] == room), {})
        if not group.get('members_known'):
            raise HTTPException(409, '当前群成员名单暂不可核验，请先在微信中打开群资料并刷新')
        return [{'id': ident, 'name': names.get(ident) or ident} for ident in group['members']]

    async def collect(self, account, room, start, end, maximum):
        group, _ = await self.verify_group(account, room)
        if not self.live.history:
            raise HTTPException(503, '当前账号历史读取服务不可用')
        snapshot = await self.live.history.prepare_recent(account)
        return await self.live._consume_history_snapshot(account, snapshot,
            lambda: self._read(account, room, group['name'], start, end, maximum, snapshot))

    def _read(self, account, room, name, start, end, maximum, snapshot):
        if snapshot['account'] != account or snapshot['read_started_at'] < end - 2:
            raise ValueError('消息快照未覆盖报告截止时间')
        with self.live.db() as db:
            names = dict(db.execute('SELECT username,name FROM live_names WHERE account=?', (account,)))
        table = 'Msg_' + hashlib.md5(room.encode()).hexdigest()
        records, seen = [], set()
        for shard, path in snapshot['databases'].items():
            if not re.fullmatch(r'message/(?:biz_)?message_\d+\.db', shard):
                continue
            with readonly(path) as db:
                if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                    continue
                authors = dict(db.execute('SELECT rowid,user_name FROM Name2Id'))
                rows = db.execute(f'''SELECT local_id,server_id,local_type,sort_seq,real_sender_id,create_time,
                    CAST(message_content AS BLOB) body,CAST(compress_content AS BLOB) compressed
                    FROM "{table}" WHERE create_time>=? AND create_time<? ORDER BY create_time,sort_seq,local_id''',
                    (start, end))
                for row in rows:
                    identity = server_identity(row['server_id'])
                    identity = 's:' + identity if identity != '0' else f'{shard}:{row["local_id"]}'
                    if identity in seen:
                        continue
                    seen.add(identity)
                    decoded = decode_wcdb_value(row['body'] or row['compressed'])
                    message = present_content(decoded.text, int(row['local_type'] or 0), True)
                    if message.get('category') == 'system':
                        continue
                    sender = message.get('sender_prefix') or authors.get(row['real_sender_id'], '')
                    content = str(message.get('content') or message.get('summary') or '')
                    category = message.get('category', 'unknown')
                    if category not in {'text', 'quote', 'link'}:
                        content = f'[{category}] ' + str(message.get('summary') or '')[:300]
                    records.append({'sender_id': sender, 'time': int(row['create_time']),
                                    'content': content, 'category': category,
                                    'quoted': bool(message.get('reference')), 'identity': identity})
                    if len(records) > 200000:
                        raise ValueError('时间范围超过 20 万条消息，请缩短统计窗口')
        records.sort(key=lambda row: (row['time'], row['identity']))
        users, hours = {}, Counter()
        for index, row in enumerate(records):
            stamp = datetime.fromtimestamp(row['time'], TIMEZONE)
            hours[stamp.hour] += 1
            sender = row['sender_id']
            if sender not in users:
                alias = f'u{len(users) + 1}'
                users[sender] = {'userId': alias, 'nickname': names.get(sender) or f'成员 {alias}',
                                 'messageCount': 0, 'charCount': 0, 'emojiCount': 0, 'nightCount': 0, 'replyCount': 0}
            user = users[sender]
            user['messageCount'] += 1
            user['charCount'] += len(row['content']) if row['category'] in {'text', 'quote'} else 0
            user['emojiCount'] += row['category'] == 'emoji'
            user['nightCount'] += stamp.hour < 6
            user['replyCount'] += row['quoted']
            row.update(id=f'm{index + 1}', sender=user['nickname'], user_id=user['userId'],
                       at=stamp.strftime('%m-%d %H:%M:%S'))
            row.pop('sender_id')
            row.pop('identity')
        for user in users.values():
            count = user['messageCount']
            user.update(avgChars=round(user['charCount'] / count, 1),
                        emojiRatio=user['emojiCount'] / count, nightRatio=user['nightCount'] / count,
                        replyRatio=user['replyCount'] / count)
        for sender, user in sorted(users.items(), key=lambda item: -item[1]['messageCount'])[:15]:
            try:
                payload, mime = self.live.media.avatar(account, sender)
                if len(payload) <= 2 * 1024 * 1024:
                    user['avatar'] = f'data:{mime};base64,' + base64.b64encode(payload).decode('ascii')
            except (HTTPException, OSError, ValueError):
                pass
        selected = records
        if len(records) > maximum:
            selected = [records[index * (len(records) - 1) // (maximum - 1)] for index in range(maximum)]
        most_active = hours.most_common(1)
        return {'groupName': name, 'start': start, 'end': end,
                'timeRange': f'{datetime.fromtimestamp(start, TIMEZONE):%Y-%m-%d %H:%M} 至 {datetime.fromtimestamp(end, TIMEZONE):%Y-%m-%d %H:%M}',
                'totalMessages': len(records), 'totalParticipants': len(users),
                'totalChars': sum(user['charCount'] for user in users.values()),
                'emojiCount': sum(user['emojiCount'] for user in users.values()),
                'mostActivePeriod': f'{most_active[0][0]:02d}:00–{most_active[0][0]+1:02d}:00' if most_active else '暂无',
                'activeHoursData': {str(hour): hours[hour] for hour in range(24)},
                'userStats': sorted(users.values(), key=lambda user: -user['messageCount']),
                'messages': selected, 'analyzedMessages': len(selected),
                'source': 'verified_wechat_snapshot', 'verified_at': snapshot['verified_at']}
