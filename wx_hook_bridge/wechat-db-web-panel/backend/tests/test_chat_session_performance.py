import sqlite3
from contextlib import contextmanager

import pytest

from backend.app.chat_messages import ChatMessages
from backend.tests.test_chat_history import add, msg
from backend.tests.test_live_chat import service  # noqa: F401


def stats(store, account='self', session='peer'):
    with store.db() as db:
        row = db.execute('SELECT * FROM chat_session_stats WHERE account=? AND session=?', (account,session)).fetchone()
    return dict(row) if row else None


def test_legacy_backfill_preserves_bodies_and_account_scope(tmp_path):
    store = ChatMessages(tmp_path/'cache.sqlite')
    first, _ = add(store,msg(1,create_time=2000))
    second, _ = add(store,msg(2,create_time=1000))
    other, _ = add(store,msg(3),account='other')
    with store.db() as db:
        before = db.execute('SELECT id,data,revision FROM chat_messages ORDER BY id').fetchall()
        for name in ('chat_stats_insert','chat_stats_delete','chat_stats_move'):
            db.execute('DROP TRIGGER '+name)
        db.execute('DROP TABLE chat_session_stats')
    migrated = ChatMessages(store.path)
    with migrated.db() as db:
        after = db.execute('SELECT id,data,revision FROM chat_messages ORDER BY id').fetchall()
    assert [tuple(r) for r in before] == [tuple(r) for r in after]
    assert stats(migrated)['message_count'] == 2
    assert stats(migrated)['latest_id'] == first
    assert stats(migrated)['max_id'] == second
    assert stats(migrated,'other')['latest_id'] == other
    assert migrated.page('self','peer')['total'] == 2
    assert ChatMessages(store.path).page('self','peer')['total'] == 2


def test_alias_coalescing_corrects_latest_count_and_ceiling(tmp_path):
    store = ChatMessages(tmp_path/'cache.sqlite')
    pending, _ = add(store,msg(server_id='0',source='realtime'))
    ack, _ = add(store,msg(server_id='8',local_id=99))
    assert stats(store)['message_count'] == 2
    assert stats(store)['latest_id'] == ack
    merged, _ = add(store,msg(server_id='8',source='realtime'))
    assert merged == pending
    assert stats(store)['message_count'] == 1
    assert stats(store)['latest_id'] == pending
    assert stats(store)['max_id'] == pending
    assert store.page('self','peer')['total'] == 1


def test_counts_rollback_with_message_and_remove_empty_session(tmp_path):
    store = ChatMessages(tmp_path/'cache.sqlite')
    ident, _ = add(store,msg())
    with pytest.raises(RuntimeError), store.db() as db:
        store.upsert(db,'self',msg(2))
        raise RuntimeError('rollback fixture')
    assert stats(store)['message_count'] == 1
    with store.db() as db:
        db.execute('DELETE FROM chat_messages WHERE id=?',(ident,))
    assert stats(store) is None
    assert store.page('self','peer')['total'] == 0


def test_edit_without_new_message_keeps_count_and_updates_summary(service):
    with service.messages.db() as db:
        ident, _ = service.messages.upsert(db,'self_a',msg(summary='old'))
        service.messages.upsert(db,'self_a',msg(summary='edited',content='edited'))
    result = service.sessions('self_a','','all',0,80)
    item = next(i for i in result['items'] if i['username']=='peer')
    assert item['message_count'] == 1 and item['summary'] == 'edited'
    assert stats(service.messages,'self_a')['latest_id'] == ident


def test_metadata_update_moves_count_between_accounts(tmp_path):
    store = ChatMessages(tmp_path/'cache.sqlite')
    ident, _ = add(store,msg())
    with store.db() as db:
        db.execute('UPDATE chat_messages SET account=?,session=?,timestamp=? WHERE id=?',('other','other-peer',3000,ident))
    assert stats(store) is None
    moved = stats(store,'other','other-peer')
    assert moved['message_count'] == 1 and moved['last_timestamp'] == 3000
    assert store.page('self','peer')['total'] == 0


def test_foreground_reads_are_bounded_by_page_not_member_directory(service, monkeypatch):
    with service.messages.db() as db:
        service.messages.upsert(db,'self_a',msg(sender_username='author',reference={'sender':'quoted'}))
    with service.db() as db:
        db.executemany('INSERT OR REPLACE INTO live_names VALUES (?,?,?)',
            [('self_a','unused-'+str(i),'Unused') for i in range(6000)]+[('self_a','author','Author'),('self_a','quoted','Quoted')])
    original = service.db
    observed = []
    @contextmanager
    def counted_db():
        with original() as db:
            def row_factory(cursor, values):
                observed.append(values)
                return sqlite3.Row(cursor,values)
            db.row_factory = row_factory
            yield db
    monkeypatch.setattr(service,'db',counted_db)
    detail = service.detail('self_a','peer')
    assert detail['messages'][0]['sender_name'] == 'Author'
    assert detail['messages'][0]['reference']['display_name'] == 'Quoted'
    assert len(observed) < 30
    observed.clear()
    listed = service.sessions('self_a','','all',0,1)
    assert len(listed['items']) == 1
    assert len(observed) < 30


def test_summary_pagination_scope_and_same_timestamp_order(service):
    with service.messages.db() as db:
        service.messages.upsert(db,'self_a',msg(1,session_username='one@chatroom',summary='first'))
        service.messages.upsert(db,'self_a',msg(2,session_username='one@chatroom',summary='latest'))
        service.messages.upsert(db,'self_b',msg(3,session_username='private',summary='other account'))
    groups = service.sessions('self_a','','groups',0,1)
    assert groups['total'] == 1
    assert groups['items'][0]['summary'] == 'latest'
    assert groups['items'][0]['message_count'] == 2
    assert service.sessions('self_a','','groups',1,1)['items'] == []
    assert all(i['username'] != 'private' for i in service.sessions('self_a','','all',0,80)['items'])


def test_summary_body_query_seeks_message_primary_keys(service, monkeypatch):
    with service.messages.db() as db:
        service.messages.upsert(db,'self_a',msg())
        service.messages.upsert(db,'self_b',msg(2))
    original = service.db
    traced = []
    @contextmanager
    def traced_db():
        with original() as db:
            db.set_trace_callback(traced.append)
            yield db
    monkeypatch.setattr(service,'db',traced_db)
    listed = service.sessions('self_a','','all',0,80)
    assert any(item['message_count'] == 1 for item in listed['items'])
    bodies = [sql for sql in traced if sql.startswith('SELECT id,data FROM chat_messages ')]
    assert len(bodies) == 1
    with original() as db:
        plan = db.execute('EXPLAIN QUERY PLAN '+bodies[0]).fetchall()
    assert any('INTEGER PRIMARY KEY' in row[3] for row in plan)
    assert not any('chat_page' in row[3] for row in plan)
