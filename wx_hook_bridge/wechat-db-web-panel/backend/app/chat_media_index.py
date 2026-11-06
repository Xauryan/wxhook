"""Bounded local attachment lookup for one message, without archive import."""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from collections import defaultdict
from pathlib import Path

from .chat_history import HistoryResources, readonly
from .chat_messages import server_identity
from .parsing import extract_protobuf_strings


@contextmanager
def bounded_readonly(path):
    with readonly(path) as db:
        deadline = time.monotonic() + .5
        db.set_progress_handler(lambda: time.monotonic() > deadline, 10000)
        yield db


class MessageResources(HistoryResources):
    def __init__(self, root, sources, message, *, file_limit=20000):
        self.root, self.sources = root.resolve(), sources
        self.files, self.hardlinks, self.links = (defaultdict(list) for _ in range(3))
        self.voices = {}
        session = message['session_username']
        server = server_identity(message.get('server_id'))
        number = int(server)
        signed = number if number < 2 ** 63 else number - 2 ** 64
        hints = set(message.get('media_hints') or [])
        resource = sources.get('message/message_resource.db')
        if resource and server != '0':
            try:
                with bounded_readonly(resource) as db:
                    rows = db.execute('''SELECT i.packed_info parent,d.packed_info detail,d.status
                        FROM MessageResourceInfo i JOIN ChatName2Id n ON n.rowid=i.chat_id
                        LEFT JOIN MessageResourceDetail d ON d.message_id=i.message_id
                        WHERE n.user_name=? AND i.message_svr_id=? LIMIT 32''', (session, signed))
                    for row in rows:
                        strings = extract_protobuf_strings(row['parent']) + extract_protobuf_strings(row['detail'])
                        self.links[(session, server)].append({'status': row['status'], 'hints': strings})
                        for text in strings:
                            hints.update(re.findall(r'(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])', text.lower()))
            except (OSError, sqlite3.DatabaseError):
                pass
        hints = sorted(h for h in hints if isinstance(h, str) and re.fullmatch('[0-9a-f]{32}', h))[:128]
        if message['category'] == 'voice':
            for relative, path in sources.items():
                if not re.fullmatch(r'message/media_\d+\.db', relative):
                    continue
                try:
                    with bounded_readonly(path) as db:
                        row = db.execute('''SELECT v.rowid,length(v.voice_data) size FROM VoiceInfo v
                            JOIN Name2Id n ON n.rowid=v.chat_name_id
                            WHERE n.user_name=? AND v.svr_id=? LIMIT 1''', (session, signed)).fetchone()
                        if row and row['size']:
                            self.voices[(session, server)] = {'kind': 'voice', 'database': path,
                                'rowid': row['rowid'], 'size': row['size'], 'state': 'local'}
                            break
                except (OSError, sqlite3.DatabaseError):
                    pass
            return
        names = set()
        hardlink = sources.get('hardlink/hardlink.db')
        kind = 'image' if message['category'] == 'sticker' else message['category']
        if hardlink and hints and kind in {'image', 'video', 'file'}:
            try:
                with bounded_readonly(hardlink) as db:
                    markers = ','.join('?' for _ in hints)
                    for row in db.execute(f'SELECT md5,file_name,file_size,type FROM {kind}_hardlink_info_v4 WHERE md5 IN ({markers}) LIMIT 256', hints):
                        record = {**dict(row), 'kind': kind}
                        for key in {str(row['md5']).lower(), Path(row['file_name']).stem.lower().removesuffix('_t').removesuffix('_h')}:
                            self.hardlinks[key].append(record)
                        names.add(row['file_name'].casefold())
            except (OSError, sqlite3.DatabaseError):
                pass
        if message['category'] == 'sticker':
            names.update(hint + suffix for hint in hints for suffix in ('', '.gif', '.png', '.webp', '.dat'))
        if not names:
            return
        conversation = self.root / 'msg' / 'attach' / hashlib.md5(session.encode()).hexdigest()
        # Search the message month first; years of older files must not consume
        # the scan budget before a newly downloaded attachment is reached.
        timestamp = int(message.get('create_time') or 0)
        months = list(dict.fromkeys(time.strftime('%Y-%m', time.gmtime(max(0, timestamp + offset)))
                                    for offset in (0, -86400, 86400)))
        bases = [conversation / month for month in months] + [conversation]
        if kind == 'file':
            bases.append(self.root / 'msg' / 'file')
        elif kind == 'video':
            bases.append(self.root / 'msg' / 'video')
        if message['category'] == 'sticker':
            bases += [self.root / 'business' / 'emoticon', self.root / 'cache', self.root / 'resource']
        visited = 0
        deadline = time.monotonic() + 1
        for base in bases:
            if not base.is_dir() or not base.resolve().is_relative_to(self.root):
                continue
            for directory, dirs, files in os.walk(base, followlinks=False):
                visited += 1 + len(files)
                if visited > file_limit or time.monotonic() > deadline:
                    return
                dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()
                           and (Path(directory) / d).resolve().is_relative_to(self.root)]
                if not Path(directory).resolve().is_relative_to(self.root):
                    dirs[:] = []
                    continue
                for name in files:
                    if name.casefold() not in names:
                        continue
                    path = Path(directory) / name
                    if not path.is_symlink() and path.is_file() and path.resolve().is_relative_to(self.root):
                        if path not in self.files[name.casefold()]:
                            self.files[name.casefold()].append(path)
            if self.files:
                return
