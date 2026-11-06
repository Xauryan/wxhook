"""Whitelist presentation fields from a verified WeChat 4.x address-book copy."""
from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from .parsing import _read_varint

SERVICE_ACCOUNTS = {'filehelper', 'fmessage', 'medianote', 'newsapp', 'floatbottle',
                    'notifymessage', 'notification_messages', 'brandsessionholder', 'masssendapp',
                    'qqmail', 'tmessage', 'qmessage', 'blogapp', 'feedsapp', 'shakeapp', 'voipapp',
                    'voicevoipapp', 'weixinreminder', 'officialaccounts', 'appbrandcustomerservicemsg'}


def fields(payload):
    if not isinstance(payload, (bytes, memoryview)) or len(payload) > 256 * 1024:
        return {}
    data, offset, result = bytes(payload), 0, {}
    try:
        while offset < len(data):
            tag, offset = _read_varint(data, offset)
            number, wire = tag >> 3, tag & 7
            if not number:
                return {}
            if wire == 0:
                value, offset = _read_varint(data, offset)
            elif wire == 2:
                length, offset = _read_varint(data, offset)
                if length > len(data) - offset:
                    return {}
                value, offset = data[offset:offset + length], offset + length
            elif wire in {1, 5}:
                offset += 8 if wire == 1 else 4
                if offset > len(data):
                    return {}
                continue
            else:
                return {}
            result[number] = value
    except (ValueError, IndexError):
        return {}
    return result


def profile_extra(payload):
    data = fields(payload)
    def text(number):
        value = data.get(number)
        if not isinstance(value, bytes):
            return ''
        try:
            return value.decode('utf-8').replace('\x00', '')[:2000]
        except UnicodeDecodeError:
            return ''
    added = data.get(41)
    date = datetime.fromtimestamp(added, ZoneInfo('Asia/Shanghai')).date().isoformat() if isinstance(added, int) and 946684800 <= added <= 4102444800 else None
    scene = data.get(8) if isinstance(data.get(8), int) else None
    return {'gender': {1: 'male', 2: 'female'}.get(data.get(2)), 'signature': text(4),
            'region': {'country': text(5), 'province': text(6), 'city': text(7)},
            'source_scene': scene, 'source_label': '通过群聊添加' if scene == 14 else None,
            'added_date': date}


def in_directory(row):
    username = row.get('username') or ''
    return bool(username and row.get('local_type') == 1 and (row.get('flag') or 0) & 1
                and not row.get('delete_flag') and username not in SERVICE_ACCOUNTS
                and not username.startswith(('gh_', 'sys_')) and not username.endswith('@chatroom'))


def read_directory(db, account):
    db.row_factory = sqlite3.Row
    rows = [dict(row) for row in db.execute('SELECT * FROM contact')]
    names = {r['username']: r.get('remark') or r.get('nick_name') or r['username'] for r in rows}
    groups = {r['username'] for r in rows if r.get('local_type') == 2 and r.get('is_in_chat_room') == 1 and not r.get('delete_flag')}
    common = defaultdict(list)
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    groups_known = {'chatroom_member', 'name2id'} <= tables
    if groups_known:
        for row in db.execute('SELECT room.username,member.username FROM chatroom_member m JOIN name2id room ON room.rowid=m.room_id JOIN name2id member ON member.rowid=m.member_id'):
            if row[0] in groups:
                common[row[1]].append({'username': row[0], 'display_name': names.get(row[0], row[0])})
    result = []
    for row in rows:
        if not in_directory(row):
            continue
        username = row['username']
        name = names[username]
        pinyin = (row.get('remark_quan_pin') or row.get('remark_pin_yin_initial')) if row.get('remark') else None
        pinyin = pinyin or row.get('quan_pin') or row.get('pin_yin_initial') or name
        initial = re.search('[a-zA-Z]', pinyin)
        result.append({'username': username, 'display_name': name, 'nickname': row.get('nick_name') or '',
                       'remark': row.get('remark') or '', 'alias': row.get('alias') or '',
                       'description': row.get('description') or '', 'is_self': username == account,
                       'is_builtin': username == 'weixin',
                       'initial': initial[0].upper() if initial else '#', 'sort_key': pinyin.casefold(),
                       'search_text': ' '.join(str(row.get(key) or '') for key in ('username', 'alias', 'remark', 'nick_name', 'remark_quan_pin', 'remark_pin_yin_initial', 'quan_pin', 'pin_yin_initial')).casefold(),
                       'common_groups': list({g['username']: g for g in common[username]}.values()),
                       'common_groups_known': groups_known, **profile_extra(row.get('extra_buffer'))})
    return sorted(result, key=lambda row: (row['initial'] == '#', row['initial'], row['sort_key'], row['username']))
