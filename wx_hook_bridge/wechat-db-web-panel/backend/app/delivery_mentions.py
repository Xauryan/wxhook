"""Explicit per-business-team, per-chatroom mention choices; no inferred people."""
import json
import re
from datetime import datetime, timezone

from .intention_notifications import SCHEDULED_REPORT_GROUPS

TEAMS = dict(SCHEDULED_REPORT_GROUPS)
KINDS = {'call', 'failure'}


class MentionSelectionError(ValueError):
    pass


def member_id(value):
    return (isinstance(value, str) and 0 < len(value) <= 256 and re.fullmatch(r'[A-Za-z0-9_.@-]+', value)
            and value not in {'notify@all', '@all', 'all'} and not value.endswith('@chatroom'))


class DeliveryMentions:
    def __init__(self, store, bridge):
        self.store, self.bridge = store, bridge

    def initialize(self):
        self.store.initialize()
        with self.store.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS delivery_mention_meta (
                id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,account TEXT);
                INSERT OR IGNORE INTO delivery_mention_meta VALUES(1,0,NULL);
                CREATE TABLE IF NOT EXISTS delivery_mention_rules (
                room_id TEXT,team_id TEXT,members_json TEXT NOT NULL,PRIMARY KEY(room_id,team_id));
                CREATE TABLE IF NOT EXISTS delivery_mention_receipts (
                event_key TEXT,room_id TEXT,kind TEXT,team_id TEXT,revision INTEGER,account TEXT,
                members_json TEXT,base_message TEXT,message TEXT,created_at TEXT,
                PRIMARY KEY(event_key,room_id));''')

    def configuration(self):
        self.initialize()
        with self.store.connect() as db:
            meta = dict(db.execute('SELECT revision,account FROM delivery_mention_meta WHERE id=1').fetchone())
            rows = [dict(r) for r in db.execute('SELECT * FROM delivery_mention_rules ORDER BY room_id,team_id')]
        return {**meta, 'teams': [{'id': key, 'name': value} for key, value in TEAMS.items()],
                'kinds': sorted(KINDS), 'selections': [{'room_id': r['room_id'], 'team_id': r['team_id'],
                    'members': json.loads(r['members_json'])} for r in rows]}

    async def members(self, room):
        if room not in {g['room_id'] for g in self.store.configuration()['target_groups']}:
            raise MentionSelectionError('请先将此微信群保存为通知目标群')
        profile = await self.bridge._post('/api/userInfo', {})
        account = profile.get('wxid') if isinstance(profile, dict) else None
        if not account:
            raise MentionSelectionError('无法确认当前微信账号')
        data = await self.bridge._post('/api/getMemberFromChatRoom', {'chatRoomId': room})
        if (not isinstance(data, dict) or data.get('ok') is False or not isinstance(data.get('members'), list)
                or data.get('chatRoomId', room) != room):
            raise MentionSelectionError('群成员列表不可用，未使用其他群的成员')
        reported = data.get('count', data.get('number'))
        if reported is not None and (not str(reported).isdigit() or int(reported) != len(data['members'])):
            raise MentionSelectionError('群成员列表不完整，请刷新后重试')
        by_id = {}
        for raw in data['members']:
            wxid = raw.get('wxid') if isinstance(raw, dict) else None
            if not member_id(wxid):
                continue
            name = next((str(raw[key]).strip() for key in ['group_nickname', 'display_name', 'contact_remark',
                        'contact_nickname', 'nickname', 'Nickname', 'name'] if raw.get(key) and str(raw[key]).strip()), wxid)
            name = ' '.join(name.split())
            if wxid in by_id and by_id[wxid]['name'] != name:
                raise MentionSelectionError('群成员身份重复且名称冲突，请刷新')
            by_id[wxid] = {'wxid': wxid, 'name': name}
        return {'room_id': room, 'account': account, 'items': sorted(by_id.values(), key=lambda m: (m['name'], m['wxid']))}

    async def save(self, revision, account, selections):
        self.initialize()
        rooms = {g['room_id'] for g in self.store.configuration()['target_groups']}
        seen, resolved, cache = set(), [], {}
        for row in selections:
            room, team, ids = row['room_id'], row['team_id'], row['wxids']
            if room not in rooms or team not in TEAMS or (room, team) in seen:
                raise MentionSelectionError('目标群、业务组或重复配置无效')
            seen.add((room, team))
            if len(ids) > 50 or len(ids) != len(set(ids)) or any(not member_id(i) for i in ids):
                raise MentionSelectionError('所选成员无效，不能选择所有人或重复人员')
            if not ids:
                continue
            if room not in cache:
                cache[room] = await self.members(room)
            current = cache[room]
            if not account or current['account'] != account:
                raise MentionSelectionError('微信账号已变化，请刷新群成员后重新选择')
            people = {m['wxid']: m for m in current['items']}
            if any(i not in people for i in ids):
                raise MentionSelectionError('部分所选成员已不在此群，请刷新并重新选择')
            members = [people[i] for i in ids]
            if len(' @'.join(m['name'] for m in members).encode('utf-8')) > 4096:
                raise MentionSelectionError('@ 人员显示名称过长，请减少所选人员')
            resolved.append((room, team, json.dumps(members, ensure_ascii=False)))
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT revision FROM delivery_mention_meta WHERE id=1').fetchone()[0] != revision:
                raise MentionSelectionError('配置已被其他页面修改，请刷新后重试')
            db.execute('DELETE FROM delivery_mention_rules')
            db.executemany('INSERT INTO delivery_mention_rules VALUES(?,?,?)', resolved)
            db.execute('UPDATE delivery_mention_meta SET revision=revision+1,account=? WHERE id=1', (account if resolved else None,))
        return self.configuration()

    async def prepare(self, room, kind, context, body):
        plain = {'message': body, 'base_message': body, 'members': [], 'kind': kind}
        if kind not in KINDS:
            return plain
        name = (context or {}).get('outbound_group_name')
        team = next((key for key, value in TEAMS.items() if value == name), None)
        if team is None:
            return plain
        config = self.configuration()
        selected = next((r['members'] for r in config['selections'] if r['room_id'] == room and r['team_id'] == team), [])
        if not selected:
            return plain
        identity = (context or {}).get('outbound_group_id')
        if identity is not None and str(identity) != team:
            raise MentionSelectionError('订单业务组编号与名称不一致，暂缓 @ 通知')
        current = await self.members(room)
        if current['account'] != config['account']:
            raise MentionSelectionError('当前微信账号与 @ 配置不一致，请重新选择')
        people = {m['wxid']: m for m in current['items']}
        if any(m['wxid'] not in people for m in selected):
            raise MentionSelectionError('所选 @ 人员已不在目标群，暂缓文字通知，请修改勾选')
        members = [people[m['wxid']] for m in selected]
        nickname = ' @'.join(m['name'] for m in members)
        if len(nickname.encode('utf-8')) > 4096 or len(body.encode('utf-8')) > 65536:
            raise MentionSelectionError('@ 通知超出接口长度限制')
        return {**plain, 'message': '@' + nickname + ' ' + body, 'members': members, 'nickname': nickname,
                'revision': config['revision'], 'account': config['account'], 'team_id': team}

    def freeze(self, db, event_key, room, plan):
        if not plan['members']:
            return
        revision = db.execute('SELECT revision FROM delivery_mention_meta WHERE id=1').fetchone()[0]
        if revision != plan['revision']:
            raise MentionSelectionError('@ 配置已变化，尚未发送，请重试')
        db.execute('''INSERT INTO delivery_mention_receipts VALUES(?,?,?,?,?,?,?,?,?,?)''',
                   (event_key, room, plan['kind'], plan['team_id'], plan['revision'], plan['account'],
                    json.dumps(plan['members'], ensure_ascii=False), plan['base_message'], plan['message'],
                    datetime.now(timezone.utc).isoformat()))

    async def send(self, room, plan):
        if not plan['members']:
            await self.bridge._post('/api/sendTextMsg', {'wxid': room, 'msg': plan['message']})
            return
        await self.bridge._post('/api/sendAtText', {'chatRoomId': room,
            'wxids': ','.join(m['wxid'] for m in plan['members']), 'nickname': plan['nickname'], 'msg': plan['base_message']})
