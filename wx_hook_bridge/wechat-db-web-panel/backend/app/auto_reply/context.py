from datetime import datetime
import hashlib
import json
from zoneinfo import ZoneInfo

from fastapi import HTTPException


class ConversationContext:
    def __init__(self, live):
        self.live = live

    def message(self, account, room, ident):
        with self.live.db() as db:
            row = db.execute('SELECT * FROM chat_messages WHERE id=? AND account=? AND session=?',
                             (ident, account, room)).fetchone()
        if row is None:
            raise HTTPException(404, '该群消息不存在')
        return {**dict(row), 'message': json.loads(row['data'])}

    def from_event(self, account, room, event_id):
        with self.live.db() as db:
            row = db.execute('SELECT message_id FROM chat_aliases WHERE account=? AND session=? AND alias=?',
                             (account, room, 'event:' + event_id)).fetchone()
        return row[0] if row else None

    def load(self, account, room, ident, config, *, ask_prefix=False):
        anchor = self.message(account, room, ident)
        stamp = anchor['timestamp']
        with self.live.db() as db:
            same_second = '' if config.exclude_same_second else ' OR (timestamp=? AND id<?)'
            bound = [stamp] if config.exclude_same_second else [stamp, stamp, ident]
            rows = db.execute(f'''SELECT id,timestamp,data FROM chat_messages
                WHERE account=? AND session=? AND timestamp>=? AND (timestamp<?{same_second})
                ORDER BY timestamp DESC,id DESC LIMIT ?''',
                (account, room, stamp - config.context_minutes * 60, *bound, config.context_messages)).fetchall()
            room_row = db.execute('SELECT name FROM live_names WHERE account=? AND username=?', (account, room)).fetchone()
        speakers = {}

        def present(message, record_id, timestamp):
            sender = str(message.get('sender_username') or '')
            speakers.setdefault(sender, 'u' + str(len(speakers) + 1))
            category = message.get('category') or 'unknown'
            content = str(message.get('content') or message.get('summary') or '')
            if category not in {'text', 'quote'}:
                content = f'[{category}] ' + str(message.get('title') or message.get('summary') or '')[:150]
            reference = message.get('reference')
            quoted = None
            if isinstance(reference, dict):
                quoted = {'kind': reference.get('kind'), 'text': str(reference.get('content') or '')[:config.context_quote_chars]}
            return {'message_id': record_id, 'time': datetime.fromtimestamp(timestamp, ZoneInfo('Asia/Shanghai')).isoformat(),
                    'speaker': speakers[sender], 'category': category, 'text': content[:config.context_message_chars],
                    'quote': quoted, 'is_self': bool(message.get('is_self') or sender == account)}

        previous = [present(json.loads(row['data']), row['id'], row['timestamp']) for row in reversed(rows)]
        current = present(anchor['message'], ident, stamp)
        if ask_prefix:
            current['text'] = '/ask ' + current['text']
        context = {'group': room_row[0] if room_row else room, 'at': current['time'],
                   'policy': {'messages': config.context_messages, 'minutes': config.context_minutes,
                              'same_second': 'excluded' if config.exclude_same_second else 'earlier_ids',
                              'message_chars': config.context_message_chars, 'quote_chars': config.context_quote_chars},
                   'previous': previous, 'current': current}
        encoded = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        return context, hashlib.sha256(encoded.encode()).hexdigest()
