"""Account-bound, read-only WCDB evidence for native image replies."""
import hashlib
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

from .fmessage_source import FMessageSource
from .parsing import GROUP_PREFIX, decode_wcdb_value, parse_message_content


class QuoteSourceUnavailable(ValueError):
    pass


class ImageQuoteRejected(RuntimeError):
    pass


class MessageSnapshot(FMessageSource):
    def __init__(self, source, manifest, repository_root, snapshot):
        super().__init__(manifest, repository_root, snapshot)
        self.message_source, self.manifest = source, manifest

    def _configuration(self):
        return self.message_source, self.manifest

    @staticmethod
    def _validate_database(path):
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise QuoteSourceUnavailable('消息快照完整性检查失败')
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='Name2Id'").fetchone():
                raise QuoteSourceUnavailable('消息快照缺少身份映射')

    @contextmanager
    def read(self):
        with self._lock:
            self._refresh_locked()
            db = sqlite3.connect(self.snapshot_path.as_uri() + '?mode=ro&immutable=1', uri=True)
            db.row_factory = sqlite3.Row
            try:
                yield db
            finally:
                db.close()


class ImageQuoteSource:
    def __init__(self, config_path, repository_root, snapshot_root):
        self.config_path = Path(config_path)
        self.repository_root = Path(repository_root)
        self.snapshot_root = Path(snapshot_root)
        self.snapshots = {}

    def _databases(self, account):
        try:
            config = json.loads(self.config_path.read_text(encoding='utf-8'))
            root, manifest = Path(config['source_root']).resolve(), Path(config['key_manifest']).resolve()
            if not re.fullmatch(r'wxid_[A-Za-z0-9_-]+', account) or config['account'] != account:
                raise ValueError('Account mismatch')
            if root.name != 'db_storage' or not root.parent.name.startswith(account + '_'):
                raise ValueError('Source belongs to a different account')
            sources = sorted(p for p in (root/'message').glob('message_*.db') if re.fullmatch(r'message_\d+\.db', p.name))
            if not sources or not manifest.is_file():
                raise ValueError('Message source unavailable')
            result = []
            for path in sources:
                key = (str(path), str(manifest))
                if key not in self.snapshots:
                    self.snapshots[key] = MessageSnapshot(path, manifest, self.repository_root,
                                                         self.snapshot_root/account/path.name)
                result.append((path.name, self.snapshots[key]))
            return result
        except (OSError, ValueError, KeyError) as exc:
            raise QuoteSourceUnavailable('当前微信账号的引用消息源未配置或不匹配') from exc

    @staticmethod
    def _table(db, room, account):
        sender = db.execute('SELECT rowid FROM Name2Id WHERE user_name=?', (account,)).fetchone()
        session = db.execute('SELECT rowid FROM Name2Id WHERE user_name=?', (room,)).fetchone()
        table = 'Msg_' + hashlib.md5(room.encode()).hexdigest()
        if not sender or not session or not db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone():
            return None, None
        return table, sender[0]

    @staticmethod
    def _content(row):
        decoded = decode_wcdb_value(row['message_content'] or row['compress_content'])
        if not decoded.decoded:
            raise QuoteSourceUnavailable('消息正文无法完整解码')
        return decoded.text

    def resolve(self, account, room, primary):
        if primary.get('status') != 'sent' or not primary.get('sent_at'):
            raise QuoteSourceUnavailable('原通知尚未取得桥接接受回执')
        stamp = int(datetime.fromisoformat(primary['sent_at']).timestamp())
        found, baseline = [], {}
        for name, snapshot in self._databases(account):
            with snapshot.read() as db:
                table, sender = self._table(db, room, account)
                if not table:
                    baseline[name] = 0
                    continue
                baseline[name] = db.execute(f'SELECT COALESCE(max(local_id),0) FROM "{table}"').fetchone()[0]
                rows = db.execute(f'''SELECT local_id,server_id,local_type,create_time,
                    CAST(message_content AS BLOB) AS message_content,CAST(compress_content AS BLOB) AS compress_content
                    FROM "{table}" WHERE real_sender_id=? AND local_type=1 AND create_time BETWEEN ? AND ?
                    ORDER BY local_id LIMIT 501''', (sender, stamp-120, stamp+120)).fetchall()
                if len(rows) > 500:
                    raise QuoteSourceUnavailable('原通知候选过多，停止自动选择')
                for row in rows:
                    parsed = parse_message_content(self._content(row), 1, 0, room.endswith('@chatroom'))
                    if parsed.content != primary['message']:
                        continue
                    # All IDs and time originate from this exact source row, never a receipt.
                    if not row['server_id'] or not 0 < row['local_id'] < 2**31 or not 0 < row['create_time'] < 2**31:
                        raise QuoteSourceUnavailable('原通知已出现但真实消息编号尚未就绪')
                    found.append({'account': account, 'room': room, 'database': name,
                                  'quotedMsgId': str(row['server_id']), 'quotedLocalId': row['local_id'],
                                  'createTime': row['create_time'], 'quotedType': 1,
                                  'quotedFrom': account, 'quotedChat': room, 'quotedContent': parsed.content})
        if len(found) != 1:
            raise QuoteSourceUnavailable('尚未唯一定位本次反馈的真实文字消息，暂缓图片引用')
        return {**found[0], 'baseline': baseline}

    def preflight(self, account):
        for _, snapshot in self._databases(account):
            with snapshot.read() as db:
                if not db.execute('SELECT 1 FROM Name2Id WHERE user_name=?', (account,)).fetchone():
                    raise QuoteSourceUnavailable('当前账号不在消息源身份表中')

    def verify(self, reference, submitted_at):
        matches = []
        stamp = int(datetime.fromisoformat(submitted_at).timestamp())
        for name, snapshot in self._databases(reference['account']):
            with snapshot.read() as db:
                table, sender = self._table(db, reference['room'], reference['account'])
                if not table:
                    continue
                rows = db.execute(f'''SELECT local_id,server_id,local_type,create_time,
                    CAST(message_content AS BLOB) AS message_content,CAST(compress_content AS BLOB) AS compress_content
                    FROM "{table}" WHERE real_sender_id=? AND local_type=3 AND local_id>?
                    AND create_time BETWEEN ? AND ? ORDER BY local_id LIMIT 501''',
                    (sender, reference['baseline'].get(name, 0), stamp-2, stamp+120)).fetchall()
                if len(rows) > 500:
                    return None
                for row in rows:
                    body = self._content(row)
                    prefix = GROUP_PREFIX.match(body)
                    if prefix:
                        if prefix.group(1) != reference['account']:
                            continue
                        body = prefix.group(2)
                    if '<!DOCTYPE' in body.upper():
                        continue
                    try:
                        xml = ET.fromstring(body)
                        ref = xml.find('./extcommoninfo/refermsg')
                        if (ref is None or xml.find('./img') is None
                                or int(ref.findtext('svrid', '0')) != int(reference['quotedMsgId'])
                                or int(ref.findtext('createtime', '0')) != reference['createTime']
                                or not row['server_id']):
                            continue
                    except (ET.ParseError, ValueError):
                        continue
                    matches.append({'database': name, 'local_id': row['local_id'],
                                    'server_id': str(row['server_id']), 'create_time': row['create_time'],
                                    'local_type': 3, 'reference_server_id': reference['quotedMsgId'],
                                    'reference_create_time': reference['createTime']})
        return matches[0] if len(matches) == 1 else None


def quote_payload(room, path, reference):
    if reference['room'] != room or reference['quotedFrom'] != reference['account'] or reference['quotedType'] != 1:
        raise QuoteSourceUnavailable('引用消息的会话或发送者不匹配')
    if (not isinstance(reference['quotedMsgId'], str) or not re.fullmatch(r'-?[1-9]\d*', reference['quotedMsgId'])
            or not -(2**63) <= int(reference['quotedMsgId']) < 2**63
            or type(reference['quotedLocalId']) is not int or not 0 < reference['quotedLocalId'] < 2**31
            or type(reference['createTime']) is not int or not 0 < reference['createTime'] < 2**31):
        raise QuoteSourceUnavailable('引用编号或时间格式不合法，未提交图片')
    return {'wxid': room, 'imagePath': str(Path(path).resolve()),
            **{key: reference[key] for key in ('quotedMsgId', 'quotedLocalId', 'createTime', 'quotedFrom',
                                              'quotedType', 'quotedContent', 'quotedChat')}}
