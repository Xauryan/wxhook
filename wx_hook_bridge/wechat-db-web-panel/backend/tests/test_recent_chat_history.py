import asyncio
import hashlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.active_roster import ActiveRosterError
from backend.app.chat_history import ActiveHistory, prepare_snapshot
from backend.app.chat_messages import ChatMessages
from backend.app.sns_sync import SnsSnapshotSync
from backend.tests.test_sent_echo import snapshot, source_message, history, live  # noqa: F401


def test_external_self_and_incoming_messages_import_without_an_outbox(tmp_path, snapshot):
    store = history(tmp_path)
    source_message(snapshot, 1, author=1)
    source_message(snapshot, 2, author=2, body=b'peer reply')
    source_message(snapshot, 3, session='room@chatroom', body=b'self_a:\ngroup reply')
    result = store.apply_recent('self_a', snapshot)
    assert result['own_count'] == 2
    assert store.store.page('self_a','peer')['total'] == 2
    assert [m['is_self'] for m in store.store.page('self_a','peer')['messages']] == [True,False]
    assert store.store.page('self_a','room@chatroom')['messages'][0]['sender_username'] == 'self_a'
    assert store.store.state('self_a') == {}


def test_recent_read_is_bounded_and_replay_does_not_create_revisions(tmp_path, snapshot):
    store = history(tmp_path)
    for ident in range(1, 101):
        source_message(snapshot, ident, timestamp=1000+ident)
    store.apply_recent('self_a', snapshot, limit=3)
    first = store.store.page('self_a','peer')
    assert [m['local_id'] for m in first['messages']] == [98,99,100]
    revision = store.store.revision('self_a')
    store.apply_recent('self_a', {**snapshot,'read_started_at':2000}, limit=3)
    assert store.store.revision('self_a') == revision
    assert store.store.page('self_a','peer') == first


def test_recent_source_kinds_are_not_limited_to_outgoing_text(tmp_path, snapshot):
    store = history(tmp_path)
    source_message(snapshot, 1, kind=3, body=b'<msg><img /></msg>')
    source_message(snapshot, 2, kind=42, body=b'<msg username="peer" nickname="Peer"/>')
    store.apply_recent('self_a',snapshot)
    messages = store.store.page('self_a','peer')['messages']
    assert [m['category'] for m in messages] == ['image','contact_card']
    assert all(m['is_self'] for m in messages)


def test_recent_rejects_other_account_before_writing(tmp_path, snapshot):
    store = history(tmp_path)
    source_message(snapshot)
    with pytest.raises(ActiveRosterError):
        store.apply_recent('self_b', snapshot)
    assert store.store.page('self_a','peer')['total'] == 0
    assert store.store.page('self_b','peer')['total'] == 0


def test_old_archive_and_old_tail_cannot_replace_a_new_recent_body(tmp_path,snapshot):
    store = history(tmp_path)
    source_message(snapshot, body=b'new body')
    store.apply_recent('self_a',snapshot)
    message = dict(store.store.page('self_a','peer')['messages'][0])
    message.update(content='old body',summary='old body',_history_read_at=999)
    message.pop('_recent_read_at')
    with store.store.db() as db:
        store.store.upsert(db,'self_a',message)
    with sqlite3.connect(snapshot['databases']['message/message_0.db']) as db:
        table = 'Msg_'+hashlib.md5(b'peer').hexdigest()
        db.execute(f'UPDATE "{table}" SET message_content=?',(b'older tail',))
    store.apply_recent('self_a',{**snapshot,'read_started_at':998})
    assert store.store.page('self_a','peer')['messages'][0]['content'] == 'new body'


def test_recent_never_restores_recalled_body(tmp_path,snapshot):
    store = history(tmp_path)
    source_message(snapshot)
    store.apply_recent('self_a',snapshot)
    with store.store.db() as db:
        store.store.upsert(db,'self_a',{'session_username':'peer','sender_username':'peer',
            'server_id':'2','local_id':2,'create_time':1001,'source':'realtime',
            'category':'system','content':'recalled','recalled_server_id':'1'})
    store.apply_recent('self_a',{**snapshot,'read_started_at':1002})
    original = next(m for m in store.store.page('self_a','peer')['messages'] if m['server_id']=='1')
    assert original['_recalled'] and original['content']=='recalled'


def test_session_directory_limits_recent_tail_candidates(tmp_path,snapshot):
    store = history(tmp_path)
    directory = tmp_path/'sessions.sqlite'
    with sqlite3.connect(directory) as db:
        db.execute('CREATE TABLE SessionTable(username,last_timestamp)')
        db.executemany('INSERT INTO SessionTable VALUES (?,?)',[('peer',1000),('room@chatroom',999),('other',-90000)])
    native = {**snapshot,'databases':{**snapshot['databases'],'session/session.db':str(directory)}}
    source_message(native,1)
    source_message(native,2,session='room@chatroom')
    source_message(native,3,session='other')
    store.apply_recent('self_a',native,session_limit=1)
    assert store.store.page('self_a','peer')['total'] == 1
    assert store.store.page('self_a','room@chatroom')['total'] == 0
    assert store.store.page('self_a','other')['total'] == 0


def test_recent_task_runs_while_full_archive_import_is_pending(tmp_path,snapshot,monkeypatch):
    service,calls = live(tmp_path,snapshot,monkeypatch)
    source_message(snapshot)
    actual = ActiveHistory(SimpleNamespace(),service.messages)
    prepares = 0
    async def prepare_recent(account):
        nonlocal prepares
        prepares += 1
        return snapshot
    async def forbidden_prepare(account):
        pytest.fail('full archive is already running')
    service.history = SimpleNamespace(prepare=forbidden_prepare,prepare_recent=prepare_recent,apply_recent=actual.apply_recent)
    async def scenario():
        service.history_tasks['self_a'] = asyncio.create_task(asyncio.Event().wait())
        service.refresh_history('self_a')
        service.refresh_history('self_a')
        await asyncio.gather(*service.recent_tasks.values())
        assert not service.history_tasks['self_a'].done()
        assert service.messages.page('self_a','peer')['total'] == 1
        assert service.history_state('self_a')['recent_updated_at'] == 1001
        service.refresh_history('self_a')
        assert prepares == 1
        await service.close()
    asyncio.run(scenario())
    assert '/api/sendTextMsg' not in calls


def test_account_switch_during_recent_snapshot_does_not_import(tmp_path,snapshot,monkeypatch):
    service,calls = live(tmp_path,snapshot,monkeypatch)
    async def prepare_recent(account):
        async def switched(path,payload,timeout=8):
            return {'code':1,'data':{'wxid':'self_b'}}
        service.bridge = switched
        return snapshot
    service.history = SimpleNamespace(prepare_recent=prepare_recent,apply_recent=lambda *args:pytest.fail('switched account'))
    async def scenario():
        service.refresh_recent('self_a')
        await asyncio.gather(*service.recent_tasks.values())
        assert service.messages.page('self_a','peer')['total'] == 0
        assert service.history_state('self_a')['recent_error']
        await service.close()
    asyncio.run(scenario())


def test_shutdown_cancels_recent_snapshot(tmp_path,snapshot,monkeypatch):
    service,calls = live(tmp_path,snapshot,monkeypatch)
    async def scenario():
        started = asyncio.Event()
        async def prepare_recent(account):
            started.set()
            await asyncio.Event().wait()
        service.history = SimpleNamespace(prepare_recent=prepare_recent,apply_recent=lambda *args:pytest.fail('cancelled'))
        service.refresh_recent('self_a')
        await started.wait()
        await service.close()
        assert not service.recent_tasks
    asyncio.run(scenario())


def test_recent_snapshot_excludes_media_but_keeps_native_verification(tmp_path):
    root = tmp_path/'self_123'
    relatives = ['message/message_0.db','message/media_0.db','message/message_resource.db','session/session.db','hardlink/hardlink.db']
    for relative in relatives:
        path = root/'db_storage'/relative
        path.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE evidence(value)')
    contact = root/'db_storage/contact/contact.db'
    contact.parent.mkdir();contact.touch()
    cache = tmp_path/'cache'
    loader = SnsSnapshotSync(Path('unused'),tmp_path,cache/'unused')
    loader._load_decrypt_tool = lambda:SimpleNamespace(load_manifest=lambda _: {})
    verified = []
    binding = {'source':contact,'account':'self','pid':1}
    def verify(account,force=False):
        verified.append((account,force))
        return binding
    roster = SimpleNamespace(verify=verify,cache_root=cache,loader=loader,key_manifest=Path('unused'))
    before = {relative:(root/'db_storage'/relative).read_bytes() for relative in relatives}
    result = prepare_snapshot(roster,'self',recent=True)
    assert set(result['databases']) == {'message/message_0.db','session/session.db'}
    assert verified == [('self',True),('self',True)]
    assert before == {relative:(root/'db_storage'/relative).read_bytes() for relative in relatives}


def test_partial_snapshot_cannot_publish_complete_archive_state(tmp_path,snapshot):
    store = history(tmp_path)
    source_message(snapshot)
    with pytest.raises(ActiveRosterError):
        store.apply('self_a',{**snapshot,'partial':True})
    assert store.store.state('self_a') == {}
    assert store.store.page('self_a','peer')['total'] == 0
