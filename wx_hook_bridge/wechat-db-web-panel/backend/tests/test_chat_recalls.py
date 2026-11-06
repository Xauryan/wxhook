import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import chat_recalls
from backend.app.live_chat import LiveChat, create_live_router


@pytest.fixture
def service(tmp_path):
    chat = LiveChat(tmp_path / 'live.sqlite', 'http://127.0.0.1:19088', tmp_path / 'relay.json')
    async def bridge(*args, **kwargs):
        return {'code': 1, 'data': {'wxid': 'self'}}
    chat.bridge = bridge
    with chat.db() as db:
        db.execute("INSERT INTO live_names VALUES ('self','peer','测试联系人')")
        db.execute("INSERT INTO live_names VALUES ('self','room@chatroom','测试群')")
    return chat


def original(account='self', session='peer', number='9007199254740993123', text='原文\n第二行🙂', **extra):
    return {'event_id': f'original-{account}-{session}-{number}', 'selfWxId': account,
        'peerWxId': session, 'senderWxId': 'peer', 'msgId': number, 'localId': '123',
        'type': 1, 'timestamp': 1000, 'direction': 'incoming', 'content': text, **extra}


def notice(account='self', session='peer', target='9007199254740993123', event_id='notice', timestamp=1010, **extra):
    return {'event_id': event_id, 'selfWxId': account, 'peerWxId': session, 'senderWxId': session,
        'msgId': 'notice-' + event_id, 'type': 10000, 'timestamp': timestamp, 'direction': 'incoming',
        'content': f'<sysmsg type="revokemsg"><revokemsg><session>{session}</session><newmsgid>{target}</newmsgid><replacemsg><![CDATA[对方撤回了一条消息]]></replacemsg></revokemsg></sysmsg>', **extra}


def records(service, account='self', **kwargs):
    with service.db() as db:
        return chat_recalls.page(db, account, **kwargs)


def test_keeps_full_original_after_chat_placeholder_and_history_refresh(service):
    service.ingest(original())
    service.ingest(notice())
    before = records(service)
    saved = before['items'][0]
    assert saved['original']['content'] == '原文\n第二行🙂'
    assert saved['sender_name'] == '测试联系人'
    assert saved['server_id'] == '9007199254740993123'
    assert saved['jump_server_id'] == saved['server_id']
    assert service.detail('self', 'peer')['messages'][0]['content'] == '对方撤回了一条消息'
    with service.messages.db() as db:
        service.messages.upsert(db, 'self', {'session_username': 'peer', 'sender_username': 'peer',
            'server_id': saved['server_id'], 'local_id': 123, 'create_time': 1000,
            'content': 'a later history body', 'category': 'text', 'source': 'history'})
    assert records(service)['items'][0]['original']['content'] == '原文\n第二行🙂'
    assert records(service)['revision'] == before['revision']
    assert service.detail('self', 'peer')['messages'][0]['content'] == '对方撤回了一条消息'


def test_notice_before_original_is_enriched_without_restoring_chat_body(service):
    service.ingest(notice())
    missing = records(service)['items'][0]
    assert missing['original'] is None
    assert missing['jump_message_id']
    page = service.detail('self', 'peer', around_id=int(missing['jump_message_id']))
    assert any(item['id'] == missing['jump_message_id'] for item in page['messages'])
    service.ingest(original())
    saved = records(service)['items'][0]
    assert saved['original']['content'] == '原文\n第二行🙂'
    assert saved['jump_server_id']
    assert service.detail('self', 'peer', around=saved['server_id'])['messages'][0]['category'] == 'system'


def test_in_place_recall_keeps_pre_recall_content(service):
    service.ingest(original())
    service.ingest(notice(msgId='9007199254740993123'))
    saved = records(service)['items'][0]
    assert saved['original']['content'] == '原文\n第二行🙂'
    assert service.detail('self', 'peer')['messages'][0]['category'] == 'system'


def test_duplicate_hooks_and_restart_preserve_one_record(service):
    service.ingest(original())
    service.ingest(notice())
    service.ingest(notice(event_id='second-hook'))
    before = records(service)
    assert before['total'] == 1
    rebuilt = LiveChat(service.path, service.bridge_url, service.relay_path)
    after = records(rebuilt)
    assert after['total'] == 1
    assert after['revision'] == before['revision']
    assert after['items'][0]['original']['content'] == '原文\n第二行🙂'


def test_backfills_old_raw_cache_without_resurrecting_original(service):
    service.ingest(original())
    service.ingest(notice())
    with service.db() as db:
        db.execute('DELETE FROM chat_recalls')
        db.execute('DELETE FROM chat_recall_changes')
    # Simulate an existing pre-feature cache whose chat row is already replaced.
    rebuilt = LiveChat(service.path, service.bridge_url, service.relay_path)
    saved = records(rebuilt)['items'][0]
    assert saved['original']['content'] == '原文\n第二行🙂'
    assert rebuilt.detail('self', 'peer')['messages'][0]['category'] == 'system'
    revision = records(rebuilt)['revision']
    again = LiveChat(service.path, service.bridge_url, service.relay_path)
    assert records(again)['revision'] == revision


def test_same_server_id_is_scoped_by_account_and_conversation(service):
    for account, session in [('self', 'peer'), ('other', 'peer'), ('self', 'room@chatroom')]:
        service.ingest(original(account, session, text=account + ':' + session))
        service.ingest(notice(account, session, event_id=account + ':' + session))
    assert records(service)['total'] == 2
    assert records(service, 'other')['total'] == 1
    assert records(service, 'other')['items'][0]['original']['content'] == 'other:peer'


def test_pagination_search_and_no_literal_text_false_positive(service):
    service.ingest(original(text='literal <revokemsg> markup', number='99'))
    assert records(service)['total'] == 0
    for number in range(1, 5):
        service.ingest(original(number=str(number), text=f'content {number}'))
        service.ingest(notice(target=str(number), event_id=str(number)))
    first = records(service, limit=2)
    second = records(service, limit=2, before=int(first['next_cursor']))
    assert len({item['id'] for item in first['items'] + second['items']}) == 4
    assert second['next_cursor'] is None
    assert records(service, q='content 3')['total'] == 1
    assert records(service, q='测试联系人')['total'] == 4


def test_search_excludes_internal_message_hashes(service):
    service.ingest(original(number='1', text='D4 memory'))
    service.ingest(notice(target='1', event_id='first'))
    service.ingest(original(number='2', text='N100'))
    service.ingest(notice(target='2', event_id='second'))
    with service.db() as db:
        db.execute("UPDATE chat_recalls SET original=json_set(original,'$.message_key','random-d4-hash') WHERE server_id='2'")
    result = records(service, q='D4')
    assert result['total'] == 1
    assert result['items'][0]['server_id'] == '1'


def test_signed_id_alias_and_unknown_native_timestamp(service):
    service.ingest(original(number='-2'))
    service.ingest(notice(target=str((1 << 64) - 2), timestamp=0))
    saved = records(service)['items'][0]
    assert saved['original']['content'] == '原文\n第二行🙂'
    assert saved['recalled_at'] is None
    assert saved['observed_at'] > 1000


@pytest.mark.parametrize('message_type', [10000, 10002])
def test_legacy_native_recall_without_timestamp_is_applied_and_deduplicated(service, message_type):
    payload = notice(timestamp='0', type=message_type)
    del payload['event_id']
    payload['msgId'] = 'revoke_real_9007199254740993123_0'
    first = service.ingest(payload)
    second = service.ingest(payload)
    assert first['status'] == 'applied'
    assert second['event_id'] == first['event_id']
    assert second['duplicate']
    saved = records(service)
    assert saved['total'] == 1
    assert saved['items'][0]['recalled_at'] is None
    assert saved['items'][0]['jump_message_id']


def test_invalid_or_literal_recall_xml_does_not_create_a_record(service):
    service.ingest(original(number='10', text='<revokemsg>text, not a system event'))
    service.ingest(original(number='11', type=10000, text='<sysmsg><revokemsg>', timestamp=0))
    assert records(service)['total'] == 0


def test_group_sender_and_nontext_summary_are_retained(service):
    service.ingest(original(session='room@chatroom', type=49,
        text='peer:\n<msg><appmsg><title>附件.xlsx</title><type>6</type></appmsg></msg>'))
    service.ingest(notice(session='room@chatroom'))
    saved = records(service)['items'][0]
    assert saved['original']['file_name'] == '附件.xlsx'
    assert saved['original']['sender_username'] == 'peer'
    assert saved['session']['is_group']


def test_api_account_guard_input_validation_and_recall_sse(service):
    service.ingest(original())
    service.ingest(notice())
    app = FastAPI()
    app.include_router(create_live_router(service))
    with TestClient(app) as client:
        result = client.get('/api/chat/recalls', params={'account': 'self'})
        assert result.status_code == 200
        assert result.json()['total'] == 1
        assert client.get('/api/chat/recalls', params={'account': 'other'}).status_code == 409
        assert client.get('/api/chat/recalls', params={'account': 'self', 'before': 0}).status_code == 422
        assert client.get('/api/chat/sessions/peer', params={'account': 'self', 'around_id': result.json()['items'][0]['id'], 'around': 'bad'}).status_code == 422
        response = client.get('/api/chat/events', params={'account': 'self', 'once': True})
        assert 'event: chat.recalls' in response.text
        assert '"account": "self"' in response.text
        assert 'other' not in response.text
