import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.app.chat_history import ActiveHistory, HistoryResources, prepare_snapshot, readonly
from backend.app.chat_messages import ChatMessages, present_content
from backend.app.chat_media import ChatMedia, MediaFailure, safe_path
from backend.app.sns_sync import SnsSnapshotSync


def msg(number=1, **changes):
    return {'session_username':'peer', 'sender_username':'peer', 'server_id':str(number),
            'local_id':number, 'create_time':1000, 'content':f'message {number}',
            'category':'text', 'is_self':False, 'source':'history', **changes}


@pytest.fixture
def store(tmp_path):
    return ChatMessages(tmp_path/'live.sqlite')


def add(store, value, account='self', event=''):
    with store.db() as db:
        return store.upsert(db,account,value,event)


def test_large_history_import_allows_live_writes_and_keeps_newer_body(store, tmp_path, monkeypatch):
    import hashlib
    source = tmp_path / 'message.sqlite'
    table = 'Msg_' + hashlib.md5(b'peer').hexdigest()
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE Name2Id(user_name TEXT)')
        db.execute("INSERT INTO Name2Id VALUES ('peer')")
        db.execute(f'CREATE TABLE "{table}" (local_id,server_id,local_type,sort_seq,real_sender_id,create_time,message_content,compress_content)')
        db.executemany(f'INSERT INTO "{table}" VALUES (?,?,1,?,1,1000,?,NULL)',
                       [(i, i, i, f'history {i}'.encode()) for i in range(1, 506)])
    original = store.upsert
    seen = 0
    def upsert(db, account, message, event_id=''):
        nonlocal seen
        seen += 1
        if seen == 501:
            # A separate writer fails immediately if history holds the lock.
            with sqlite3.connect(store.path, timeout=0) as live:
                live.row_factory = sqlite3.Row
                original(live, account, msg(501, source='realtime',
                         content='new realtime edit', _live_seen_at=2), 'new-live-event')
        return original(db, account, message, event_id)
    monkeypatch.setattr(store, 'upsert', upsert)
    history = ActiveHistory(SimpleNamespace(), store)
    result = history.apply('self', {'account':'self', 'root':str(tmp_path),
        'databases':{'message/message_0.db':str(source)}, 'fingerprint':'new-snapshot',
        'verified_at':2, 'read_started_at':1})
    assert result['message_count'] == 505
    page = store.page('self', 'peer', around='501')
    assert next(m for m in page['messages'] if m['server_id'] == '501')['content'] == 'new realtime edit'


def test_history_resource_index_excludes_external_symlinks(tmp_path):
    root = tmp_path / 'account'
    media = root / 'msg'
    media.mkdir(parents=True)
    (media / 'local.bin').write_bytes(b'local')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'private.bin').write_bytes(b'outside')
    try:
        (media / 'escaped.bin').symlink_to(outside / 'private.bin')
        (media / 'escaped-directory').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('symlink creation is unavailable')
    resources = HistoryResources(root, {})
    assert resources.files['local.bin'] == [media / 'local.bin']
    assert 'escaped.bin' not in resources.files
    assert 'private.bin' not in resources.files


def test_server_id_deduplicates_different_sources_and_local_ids(store):
    first,_=add(store,msg())
    second,_=add(store,msg(local_id=444,content='updated',source='realtime'),'self','hook-1')
    assert first==second
    page=store.page('self','peer')
    assert len(page['messages'])==1 and page['messages'][0]['content']=='updated'
    add(store,msg())
    assert store.page('self','peer')['messages'][0]['content']=='updated'


def test_pending_send_backwrite_retains_row_identity_and_delta(store):
    ident,_=add(store,msg(server_id='0',source='realtime'),'self','pending')
    rev=store.revision('self')
    second,_=add(store,msg(server_id='9007199254740993123',source='realtime'),'self','sent')
    assert ident==second
    delta=store.changes('self','peer',rev)
    assert len(delta['messages'])==1
    assert delta['messages'][0]['server_id']=='9007199254740993123'


def test_late_alias_resolution_keeps_earliest_visible_identity(store):
    pending,_=add(store,msg(server_id='0',source='realtime'))
    ack,_=add(store,msg(server_id='8',local_id=99))
    revision=store.revision('self')
    merged,_=add(store,msg(server_id='8',source='realtime'))
    assert merged==pending
    assert store.page('self','peer')['total']==1
    assert str(ack) in store.changes('self','peer',revision)['deleted']


def test_same_text_different_server_ids_never_collapses(store):
    add(store,msg(content='same'))
    add(store,msg(server_id='2',content='same'))
    assert store.page('self','peer')['total']==2


def test_duplicate_no_change_does_not_advance_revision(store):
    add(store,msg())
    before=store.revision('self')
    assert add(store,msg())[1] is False
    assert store.revision('self')==before


def test_empty_delta_advances_to_account_watermark_without_leaking_other_sessions(store):
    add(store, msg())
    revision = store.revision('self')
    add(store, msg(number=2, session_username='other'))
    delta = store.changes('self', 'peer', revision)
    assert delta['messages'] == []
    assert delta['revision'] == store.revision('self')
    assert delta['revision'] > revision


def test_delta_watermark_does_not_skip_paginated_changes(store):
    for number in range(1, 6):
        add(store, msg(number))
    first = store.changes('self', 'peer', 0, limit=2)
    second = store.changes('self', 'peer', first['revision'], limit=2)
    third = store.changes('self', 'peer', second['revision'], limit=2)
    assert first['has_more'] and second['has_more'] and not third['has_more']
    assert len({message['id'] for page in (first, second, third) for message in page['messages']}) == 5


def test_authoritative_snapshot_updates_old_hook_but_not_newer_hook(store):
    add(store,msg(source='realtime',_live_seen_at=200,content='hook'))
    add(store,msg(_history_read_at=199,content='stale'))
    assert store.page('self','peer')['messages'][0]['content']=='hook'
    add(store,msg(_history_read_at=201,content='database update'))
    assert store.page('self','peer')['messages'][0]['content']=='database update'


def test_local_ids_in_different_shards_are_not_false_duplicates(store):
    add(store,msg(server_id='0',source_shard='message_0.db'))
    add(store,msg(server_id='0',source_shard='message_1.db'))
    assert store.page('self','peer')['total']==2


def test_recall_updates_original_and_cannot_be_restored_by_baseline(store):
    add(store,msg())
    revision=store.revision('self')
    add(store,msg(2,category='system',content='recalled',recalled_server_id='1'))
    assert len(store.changes('self','peer',revision)['messages'])==2
    add(store,msg(content='old body'))
    assert store.page('self','peer')['messages'][0]['content']=='recalled'


def test_page_boundary_stays_exhausted_after_reconnect_and_reopens_for_backfill(store):
    add(store,msg())
    ident=store.page('self','peer')['messages'][0]['id']
    assert store.boundary('self','peer',ident) is None
    add(store,msg(2,create_time=999))
    assert store.boundary('self','peer',ident)


def test_signed_and_unsigned_server_ids_have_same_identity(store):
    add(store,msg(server_id='-1'))
    add(store,msg(server_id=str(2**64-1),local_id=999))
    assert store.page('self','peer')['total']==1


def test_cursor_same_second_no_duplicates_or_gaps_on_insertion(store):
    for i in range(1,151): add(store,msg(i))
    first=store.page('self','peer',17)
    add(store,msg(151))
    cursor=first['next_cursor'];seen=[m['id'] for m in first['messages']]
    while cursor:
        page=store.page('self','peer',17,cursor)
        seen.extend(m['id'] for m in page['messages']);cursor=page['next_cursor']
    assert len(seen)==len(set(seen))==150


def test_cursor_scoped_to_account_session_and_malformed(store):
    add(store,msg());add(store,msg(2))
    cursor=store.page('self','peer',1)['next_cursor']
    for account,session,value in [('other','peer',cursor),('self','other',cursor),('self','peer','not-a-cursor'),('self','peer','e30')]:
        with pytest.raises(HTTPException) as error:store.page(account,session,1,value)
        assert error.value.status_code==422


def test_accounts_and_sessions_with_same_ids_are_isolated(store):
    add(store,msg(),'self');add(store,msg(content='other account'),'other')
    add(store,msg(session_username='room@chatroom'))
    assert store.page('self','peer')['total']==1
    assert store.page('other','peer')['messages'][0]['content']=='other account'
    assert len(store.changes('self','room@chatroom',0)['messages'])==1


def test_reconnect_changes_are_paginated_and_include_updates(store):
    for i in range(1,14):add(store,msg(i))
    cursor=0;seen=[]
    while True:
        delta=store.changes('self','peer',cursor,4);seen.extend(delta['messages']);cursor=delta['revision']
        if not delta['has_more']:break
    assert len(seen)==13
    add(store,msg(1,source='realtime',content='edited'))
    assert store.changes('self','peer',cursor)['messages'][0]['content']=='edited'


def test_locate_quote_only_within_same_account_session(store):
    for i in range(1,31):add(store,msg(i,create_time=i+1000))
    page=store.page('self','peer',10,around='15')
    assert '15' in [m['server_id'] for m in page['messages']]
    with pytest.raises(HTTPException):store.page('other','peer',10,around='15')


def test_body_is_lossless_and_reference_not_truncated():
    text='  literal <tag> &amp; \n'+'x'*150000+'\n end  '
    assert present_content(text,1,False)['content']==text
    assert present_content('member:\n'+text,1,True)['content']==text
    quote='<msg><appmsg><type>57</type><title><![CDATA[reply\nfull]]></title><refermsg><type>1</type><svrid>999999999999999999</svrid><displayname>A</displayname><content><![CDATA['+text+']]></content></refermsg></appmsg></msg>'
    result=present_content(quote,49,False)
    assert result['content']=='reply\nfull'
    assert result['reference']['content']==text


@pytest.mark.parametrize('kind,body,field',[
    (42,'<msg nickname="Name" username="peer"/>','card'),
    (48,'<msg><location label="Place" x="1" y="2"/></msg>','location'),
    (10000,'<sysmsg><revokemsg><newmsgid>20</newmsgid><replacemsg>Recalled</replacemsg></revokemsg></sysmsg>','content'),
    (10000,'<sysmsg><revokemsg><content>Recalled message</content><revoketime>1</revoketime></revokemsg></sysmsg>','content'),
    (49,'<msg><appmsg><type>19</type><recorditem><![CDATA[<recordinfo><datalist><dataitem><sourcename>Name</sourcename><datadesc>Full text</datadesc></dataitem></datalist></recordinfo>]]></recorditem></appmsg></msg>','records'),
])
def test_structured_types_do_not_expose_raw_markup(kind,body,field):
    result=present_content(body,kind,False)
    assert result[field]
    assert '<msg' not in result['content']


@pytest.mark.parametrize('relative',['../outside.txt','sub/../../outside','C:\\secret.txt','\\\\server\\file','/etc/passwd','file.txt:stream','..\\outside.txt'])
def test_media_paths_reject_traversal_and_ads(tmp_path,relative):
    with pytest.raises(MediaFailure):safe_path(tmp_path,relative)


def test_media_missing_and_not_downloaded_are_distinct(store,tmp_path):
    media=ChatMedia(store,tmp_path,'http://127.0.0.1:19088')
    for state in ['missing','not_downloaded']:
        ident,_=add(store,msg(1 if state=='missing' else 2,category='image',_media=[{'kind':'image','state':state}]))
        result=media.describe('self',ident)['items'][0]
        assert result['state']==state and result['url'] is None


def test_media_identity_scoped_and_failure_not_claimed_available(store,tmp_path,monkeypatch):
    root=tmp_path/'source';root.mkdir();(root/'broken.dat').write_bytes(b'invalid')
    with store.db() as db:db.execute('INSERT INTO chat_history_state VALUES (?,?)',('self',json.dumps({'root':str(root)})))
    ident,_=add(store,msg(category='image',_media=[{'kind':'image','state':'local','path':'broken.dat'}]))
    media=ChatMedia(store,tmp_path,'http://127.0.0.1:19088')
    assert media.describe('self',ident)['items'][0]['state']=='unsupported'
    with pytest.raises(HTTPException):media.describe('other',ident)
    (root/'broken.dat').unlink()
    assert media.describe('self',ident)['items'][0]['state']=='missing'


@pytest.mark.parametrize('state',['decrypt_failed','conversion_failed','unsupported','decode_limited'])
def test_conversion_failures_never_expose_success_url(store,tmp_path,monkeypatch,state):
    root=tmp_path/'source';root.mkdir();(root/'media').write_bytes(b'payload')
    with store.db() as db:db.execute('INSERT INTO chat_history_state VALUES (?,?)',('self',json.dumps({'root':str(root)})))
    ident,_=add(store,msg(category='image',_media=[{'kind':'image','state':'local','path':'media'}]))
    media=ChatMedia(store,tmp_path,'http://127.0.0.1:19088')
    def fail(*args):raise MediaFailure(state)
    monkeypatch.setattr(media,'_convert',fail)
    result=media.describe('self',ident)['items'][0]
    assert result['state']==state and result['url'] is None


def test_media_token_rejects_changed_index(store,tmp_path):
    ident,_=add(store,msg(category='image',_media=[{'kind':'image','state':'not_downloaded'}]))
    media=ChatMedia(store,tmp_path,'http://127.0.0.1:19088')
    with pytest.raises(HTTPException) as error:media.resolve('self',ident,0,'0'*24)
    assert error.value.status_code==409


def test_video_extension_cannot_turn_a_playlist_into_a_local_path_reader(store,tmp_path,monkeypatch):
    source=tmp_path/'evil.mp4';source.write_bytes(b'#EXTM3U\nfile:///outside/private.wav')
    media=ChatMedia(store,tmp_path,'http://127.0.0.1:19088')
    def forbidden(*args,**kwargs):raise AssertionError('converter must not be invoked')
    monkeypatch.setattr(media,'_ffmpeg',forbidden)
    with pytest.raises(MediaFailure) as error:media._convert(source,tmp_path/'output',{'kind':'video'},'self')
    assert error.value.state=='unsupported'


def test_plain_active_wal_snapshot_includes_commits_without_writing_source(tmp_path):
    root=tmp_path/'self_123';source=root/'db_storage/message/message_0.db';source.parent.mkdir(parents=True)
    contact=root/'db_storage/contact/contact.db';contact.parent.mkdir();contact.touch()
    writer=sqlite3.connect(source);writer.execute('PRAGMA journal_mode=WAL');writer.execute('CREATE TABLE evidence(value)');writer.commit()
    writer.execute('INSERT INTO evidence VALUES (42)');writer.commit()
    cache=tmp_path/'cache';cache.mkdir()
    loader=SnsSnapshotSync(Path('unused'),tmp_path,cache/'unused')
    loader._load_decrypt_tool=lambda: SimpleNamespace(load_manifest=lambda _: {})
    binding={'source':contact,'account':'self','pid':1}
    roster=SimpleNamespace(verify=lambda *a,**k: binding,cache_root=cache,loader=loader,key_manifest=Path('unused'))
    before={p.name:p.read_bytes() for p in [source,Path(str(source)+'-wal')]}
    result=prepare_snapshot(roster,'self')
    with readonly(result['databases']['message/message_0.db']) as db:assert db.execute('SELECT value FROM evidence').fetchone()[0]==42
    assert before=={p.name:p.read_bytes() for p in [source,Path(str(source)+'-wal')]}
    writer.close()


def test_switch_during_snapshot_rejected(tmp_path):
    root=tmp_path/'self_123';source=root/'db_storage/message/message_0.db';source.parent.mkdir(parents=True)
    db=sqlite3.connect(source);db.execute('CREATE TABLE evidence(value)');db.close()
    loader=SnsSnapshotSync(Path('unused'),tmp_path,tmp_path/'cache'/'unused')
    loader._load_decrypt_tool=lambda: SimpleNamespace(load_manifest=lambda _: {})
    calls=iter([{'source':root/'db_storage/contact/contact.db','pid':1},{'source':root/'db_storage/contact/contact.db','pid':2}])
    roster=SimpleNamespace(verify=lambda *a,**k:next(calls),cache_root=tmp_path/'cache',loader=loader,key_manifest=Path('unused'))
    with pytest.raises(RuntimeError,match='切换'):prepare_snapshot(roster,'self')


def test_encrypted_snapshot_uses_stable_copy_while_source_receives_new_messages(tmp_path):
    import shutil
    root = tmp_path / 'self_123'
    source = root / 'db_storage/message/message_0.db'
    source.parent.mkdir(parents=True)
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE evidence(value)')
        db.execute('INSERT INTO evidence VALUES (42)')
    contact = root / 'db_storage/contact/contact.db'
    contact.parent.mkdir()
    contact.touch()
    loader = SnsSnapshotSync(Path('unused'), tmp_path, tmp_path / 'cache/unused')
    loader._is_plain_sqlite = lambda _: False
    inputs = []
    def decrypt(copied, output, manifest, **kwargs):
        inputs.append(copied)
        assert copied != source and copied.read_bytes() == source.read_bytes()
        # Simulate an independent WeChat write after the encrypted copy was
        # captured. Decryption must continue using the earlier coherent copy.
        with sqlite3.connect(source) as writer:
            writer.execute('INSERT INTO evidence VALUES (99)')
        shutil.copyfile(copied, output)
        return {'wal': {'exists':False}}
    loader._load_decrypt_tool = lambda: SimpleNamespace(load_manifest=lambda _: {}, decrypt_database=decrypt)
    binding = {'source':contact, 'account':'self', 'pid':1}
    roster = SimpleNamespace(verify=lambda *a, **k: binding, cache_root=tmp_path / 'cache',
                             loader=loader, key_manifest=Path('unused'))
    result = prepare_snapshot(roster, 'self')
    with readonly(result['databases']['message/message_0.db']) as db:
        assert db.execute('SELECT value FROM evidence').fetchall()[0][0] == 42
        assert db.execute('SELECT count(*) FROM evidence').fetchone()[0] == 1
    assert len(inputs) == 1 and not inputs[0].exists()


def test_encrypted_snapshot_rejects_source_changes_during_copy(tmp_path, monkeypatch):
    import shutil
    root = tmp_path / 'self_123'
    source = root / 'db_storage/message/message_0.db'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'encrypted fixture')
    contact = root / 'db_storage/contact/contact.db'
    contact.parent.mkdir()
    contact.touch()
    loader = SnsSnapshotSync(Path('unused'), tmp_path, tmp_path / 'cache/unused')
    loader._is_plain_sqlite = lambda _: False
    def forbidden(*args, **kwargs):
        raise AssertionError('an inconsistent copy must never be decrypted')
    loader._load_decrypt_tool = lambda: SimpleNamespace(load_manifest=lambda _: {}, decrypt_database=forbidden)
    copy = shutil.copyfile
    def copy_then_external_write(input_path, output_path):
        result = copy(input_path, output_path)
        with source.open('ab') as writer:
            writer.write(b'new message')
        return result
    monkeypatch.setattr(shutil, 'copyfile', copy_then_external_write)
    binding = {'source':contact, 'account':'self', 'pid':1}
    roster = SimpleNamespace(verify=lambda *a, **k: binding, cache_root=tmp_path / 'cache',
                             loader=loader, key_manifest=Path('unused'))
    with pytest.raises(RuntimeError, match='复制期间'):
        prepare_snapshot(roster, 'self')
    assert not list((tmp_path / 'cache').rglob('*.encrypted'))
    assert not list((tmp_path / 'cache').rglob('*.sqlite'))
