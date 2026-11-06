"""Account-scoped, bounded repair of old presentation caches from native evidence."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from pathlib import Path

from .message_presentation import PRESENTATION_VERSION, present_content, identity
from .parsing import decode_wcdb_value

FIELDS = set(('category summary title description url file_name file_size reference_server_id reference_from_username '
              'reference_display_name content base_type local_type reference media_hints app_type articles source_name '
              'cover_url records records_truncated card location duration payment call mini_program type_label parse_error '
              'recalled_server_id _recalled _recall_time _media_key _presentation_version decode_error').split())


class PresentationRepair:
    def __init__(self, store):
        self.store = store

    def repair(self, account, messages):
        try:
            return self._repair(account, messages)
        except (OSError, sqlite3.DatabaseError, ValueError) as exc:
            logging.getLogger(__name__).warning('Message presentation repair deferred (%s)', type(exc).__name__)
            return 0

    def _repair(self, account, messages):
        candidates = [m for m in messages if m.get('_presentation_version',0)<PRESENTATION_VERSION and not m.get('_recalled')]
        if not candidates:
            return 0
        state = self.store.state(account)
        if state.get('account',account) != account:
            return 0
        sources = state.get('databases',{})
        cache_root = self.store.path.parent.resolve() / 'chat_sources'
        paths, conns, changes = {}, {}, []
        try:
            for message in candidates[:200]:
                session = message.get('session_username','')
                if not session or not identity(message.get('server_id')):
                    continue
                with self.store.db() as db:
                    row = db.execute('SELECT data FROM chat_messages WHERE account=? AND session=? AND id=?',
                                     (account,session,message['id'])).fetchone()
                if not row:
                    continue
                old_raw = row[0]; old = json.loads(old_raw)
                if old.get('_recalled') or old.get('_presentation_version',0)>=PRESENTATION_VERSION:
                    continue
                body, local_type = None, old.get('local_type',0)
                shard = old.get('source_shard')
                if shard in sources and re.fullmatch(r'message/(?:biz_)?message_\d+\.db',shard):
                    if shard not in paths:
                        source = Path(sources[shard]).resolve()
                        paths[shard] = []
                        if source.is_relative_to(cache_root):
                            prefix = Path(shard).stem + '-'
                            # Only siblings of this account's verified immutable snapshot.
                            recent = sorted(source.parent.glob(prefix+'*.sqlite'),key=lambda p:p.stat().st_mtime,reverse=True)[:2]
                            paths[shard] = list(dict.fromkeys([*recent,source]))
                    table = 'Msg_' + hashlib.md5(session.encode()).hexdigest()
                    for path in paths[shard]:
                        key = str(path)
                        try:
                            if key not in conns:
                                db = sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True,timeout=.2)
                                db.execute('PRAGMA query_only=ON');conns[key]=db
                            native = conns[key].execute(f'SELECT server_id,local_type,CAST(message_content AS BLOB),CAST(compress_content AS BLOB) FROM "{table}" WHERE local_id=?',(old.get('local_id'),)).fetchone()
                        except (OSError,sqlite3.DatabaseError):
                            continue
                        if native and identity(native[0])==identity(old.get('server_id')):
                            decoded = decode_wcdb_value(native[2]or native[3])
                            if not decoded.error:
                                body,local_type = decoded.text,native[1]
                            break
                if body is None:
                    # Exact, indexed original Hook event; never search raw bodies.
                    with self.store.db() as db:
                        aliases = db.execute("SELECT alias FROM chat_aliases WHERE account=? AND session=? AND alias>='event:' AND alias<'event;' AND message_id=? LIMIT 3",(account,session,message['id'])).fetchall()
                        for alias in aliases:
                            raw = db.execute('SELECT raw FROM chat_envelopes WHERE account=? AND event_id=? LIMIT 1',(account,alias[0][6:])).fetchone()
                            if not raw:
                                continue
                            event = json.loads(raw[0]); nested = event.get('payload')
                            flat = {**(nested if isinstance(nested,dict) else {}),**{k:v for k,v in event.items()if k!='payload'}}
                            if isinstance(flat.get('content'),str) and not flat.get('content_truncated'):
                                body=flat['content'];break
                if body is None:
                    continue
                fresh = present_content(body,int(local_type or 0),session.endswith('@chatroom'))
                fresh.pop('sender_prefix',None)
                fresh['decode_error'] = None
                merged = {k:v for k,v in old.items() if k not in FIELDS}
                merged.update(fresh)
                if old.get('category')!=fresh['category']:
                    merged.pop('_media',None)
                # An original row replaced by a recall is not resurrected.
                if fresh.get('_recalled') and not old.get('_recalled'):
                    continue
                encoded = json.dumps(merged,ensure_ascii=False,sort_keys=True,separators=(',',':'))
                changes.append((message['id'],session,old_raw,encoded))
            count = 0
            with self.store.db() as db:
                for mid,session,old_raw,encoded in changes:
                    updated = db.execute('UPDATE chat_messages SET data=? WHERE account=? AND id=? AND data=?',(encoded,account,mid,old_raw)).rowcount
                    if updated:
                        rev=db.execute('INSERT INTO chat_changes(account,session,message_id) VALUES (?,?,?)',(account,session,mid)).lastrowid
                        db.execute('UPDATE chat_messages SET revision=? WHERE account=? AND id=?',(rev,account,mid));count+=1
            return count
        finally:
            for db in conns.values():
                db.close()
