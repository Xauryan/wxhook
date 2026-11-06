"""Verified local inputs for integrations, separate from legacy automation."""
from __future__ import annotations

import json
import re
import os
import sqlite3
import time
from pathlib import Path

import psutil

from .contact_data import in_directory


def read_automation_directory(db: sqlite3.Connection, account: str) -> dict:
    db.row_factory = sqlite3.Row
    contacts = [dict(row) for row in db.execute('SELECT * FROM contact')]
    friends = [{'username': row['username'], 'encrypt_username': row.get('encrypt_username') or ''}
               for row in contacts if in_directory(row) and row['username'] != account]
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    owners = {}
    if 'chat_room' in tables:
        columns = {row[1] for row in db.execute('PRAGMA table_info(chat_room)')}
        if {'username', 'owner'} <= columns:
            owners = dict(db.execute('SELECT username,owner FROM chat_room'))
    members: dict[str, list[str]] = {}
    if {'chatroom_member', 'name2id'} <= tables:
        for room, member in db.execute('''SELECT r.username,u.username FROM chatroom_member m
                JOIN name2id r ON r.rowid=m.room_id JOIN name2id u ON u.rowid=m.member_id'''):
            members.setdefault(room, []).append(member)
    groups = []
    for row in contacts:
        if row.get('local_type') not in {1, 2} or row.get('is_in_chat_room') != 1 or row.get('delete_flag'):
            continue
        room = row['username']
        if not room.endswith('@chatroom'):
            continue
        owner = owners.get(room)
        # Member state in ext_buffer is not a documented administrator flag.
        # Do not infer management authority from it, nicknames, or membership.
        can_manage = owner == account
        groups.append({'username': room, 'name': row.get('remark') or row.get('nick_name') or room,
                       'role': 'owner' if can_manage else 'unverified', 'can_manage': can_manage,
                       'reason': '当前账号为群主' if can_manage else '未取得可核验的管理员身份，禁止自动拉人',
                       'members': members.get(room, []), 'members_known': room in members,
                       'source': 'active_contact_db.chat_room.owner'})
    return {'friends': friends, 'groups': groups}


def normalize_hongbao_delay(value: str) -> str:
    match = re.fullmatch(r'([0-9]+)(?:\s*-\s*([0-9]+))?', value.strip())
    if not match:
        raise ValueError('请输入毫秒数或随机范围，例如 300 或 300-800')
    low = int(match[1])
    high = int(match[2]) if match[2] is not None else low
    if not 0 <= low <= high <= 600000:
        raise ValueError('延迟范围须满足 0 ≤ 最小值 ≤ 最大值 ≤ 600000 毫秒')
    return str(low) if low == high else f'{low}-{high}'


class HongbaoControl:
    """Native cold mode is process-wide, not an account-scoped panel setting."""

    def __init__(self, directory: Path):
        self.directory = directory.resolve()

    def _verify(self, binding: dict) -> None:
        process = psutil.Process(binding['pid'])
        if process.create_time() != binding['created_at']:
            raise ValueError('微信进程已变化，请重新读取状态')
        roots = {Path(item.path).resolve().parent.parent for item in process.memory_maps()
                 if Path(item.path).name.startswith('wx_hook_bridge.')
                 and Path(item.path).parent.name == 'inject_dlls'}
        if roots != {self.directory} or self.directory.name != 'control':
            raise ValueError('无法将红包控制目录唯一绑定到当前微信桥接进程')
        relay = self.directory / 'realtime_relay_status.json'
        state = json.loads(relay.read_text(encoding='utf-8'))
        if state.get('running') is not True or time.time() - relay.stat().st_mtime > 45:
            raise ValueError('原生工作进程状态未确认，暂不修改红包开关')
        for name in ('hb_cold.disable', 'hb_cold_open.disable', 'hb_cold_delay.json'):
            path = self.directory / name
            if path.is_symlink() or path.resolve().parent != self.directory:
                raise ValueError('红包控制路径校验失败')

    def status(self, binding: dict) -> dict:
        try:
            self._verify(binding)
            receive = not (self.directory / 'hb_cold.disable').exists()
            opened = not (self.directory / 'hb_cold_open.disable').exists()
            delay_file = self.directory / 'hb_cold_delay.json'
            delay = json.loads(delay_file.read_text(encoding='utf-8')).get('delay_ms', '0') if delay_file.exists() else '0'
            return {'available': True, 'enabled': receive and opened, 'receive_only': receive and not opened,
                    'delay_ms': str(delay), 'scope': 'native_process',
                    'detail': '已读取原生控制文件；桥接约每 100ms 读取，不代表已成功领取。开关跟随微信进程，不按账号隔离。'}
        except (OSError, ValueError, KeyError, psutil.Error):
            return {'available': False, 'enabled': None, 'receive_only': False, 'delay_ms': None,
                    'scope': 'native_process', 'detail': '无法确认当前微信的原生控制目录或工作进程，未修改开关。'}

    def set_enabled(self, binding: dict, enabled: bool, expected: bool) -> dict:
        self._verify(binding)
        current = self.status(binding)
        if not current['available'] or current['enabled'] != expected:
            raise ValueError('红包控制状态已变化，请刷新后重试')
        disabled = self.directory / 'hb_cold.disable'
        if enabled:
            (self.directory / 'hb_cold_open.disable').unlink(missing_ok=True)
            disabled.unlink(missing_ok=True)
        else:
            temporary = self.directory / f'hb_cold.disable.{os.getpid()}.tmp'
            try:
                with temporary.open('x', encoding='ascii') as stream:
                    stream.write('Disabled by local integrations panel.\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, disabled)
            finally:
                temporary.unlink(missing_ok=True)
        return self.status(binding)

    def set_delay(self, binding: dict, delay: str, expected: str) -> dict:
        delay = normalize_hongbao_delay(delay)
        self._verify(binding)
        current = self.status(binding)
        if not current['available'] or current['delay_ms'] != expected:
            raise ValueError('红包延迟配置已变化，请刷新后重试')
        path = self.directory / 'hb_cold_delay.json'
        temporary = self.directory / f'hb_cold_delay.{os.getpid()}.tmp'
        try:
            with temporary.open('x', encoding='utf-8') as stream:
                json.dump({'delay_ms': delay}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return self.status(binding)
