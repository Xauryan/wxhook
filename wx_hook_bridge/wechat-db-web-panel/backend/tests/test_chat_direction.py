import json
from zoneinfo import ZoneInfo

import pytest

from backend.app.live_chat import LiveChat
from backend.app.realtime import normalize_event


def group_event(sender='custom-author', account='self', event_id='group-quote', **changes):
    return {
        'event_id': event_id, 'source': 'wx_hook_bridge', 'schema_version': 1,
        'message_id': '9007199254740993123', 'local_id': '123',
        'message_type': 49, 'message_subtype': 57, 'timestamp_unix': 1000,
        'direction': 'unknown', 'is_incoming': False, 'is_outgoing': False,
        'sender_id': '', 'senderWxId': '', 'self_id': account, 'fromWxId': account,
        'self_identity_known': True, 'peer_id': 'group@chatroom', 'room_id': 'group@chatroom',
        'raw_from': 'group@chatroom', 'raw_to': account,
        'content': sender + ':\n<msg><appmsg><title>reply text</title><type>57</type>'
            '<refermsg><fromusr>group@chatroom</fromusr><chatusr>quoted-author</chatusr>'
            '<displayname>Quoted author</displayname><type>1</type><svrid>42</svrid>'
            '<content>original text</content></refermsg></appmsg></msg>',
        **changes,
    }


@pytest.mark.parametrize('sender', ['custom-author', 'fengnanking', 'lihao-278992708', 'wxid_group_member'])
def test_unknown_native_direction_uses_actual_group_author(sender):
    value = normalize_event(group_event(sender), 'self', ZoneInfo('Asia/Shanghai'))
    assert value['event_type'] == 'message.received'
    assert value['normalized']['sender_username'] == sender
    assert value['normalized']['is_self'] == 0
    assert value['normalized']['reference_server_id'] == 42


def test_own_group_reply_remains_outgoing_even_when_flags_are_unknown():
    value = normalize_event(group_event('self'), 'self', ZoneInfo('Asia/Shanghai'))
    assert value['normalized']['sender_username'] == 'self'
    assert value['normalized']['is_self'] == 1
    assert value['event_type'] == 'message.sent'


def test_compatibility_account_is_not_used_as_an_unknown_group_author():
    value = normalize_event(group_event(content='<msg><appmsg><title>reply</title><type>57</type></appmsg></msg>'),
                            'self', ZoneInfo('Asia/Shanghai'))
    assert value['normalized']['sender_username'] == 'group@chatroom'
    assert value['normalized']['is_self'] == 0


def test_group_prefix_overrides_conflicting_legacy_send_flag():
    value = normalize_event(group_event(is_outgoing=True, direction='outgoing'), 'self', ZoneInfo('Asia/Shanghai'))
    assert value['normalized']['sender_username'] == 'custom-author'
    assert value['normalized']['is_self'] == 0


@pytest.mark.parametrize('outgoing', [False, True])
def test_private_route_without_sender_keeps_the_correct_direction(outgoing):
    payload = group_event(peer_id='peer', room_id='', content='private text', message_type=1,
        message_subtype=0, direction='outgoing' if outgoing else 'incoming',
        is_outgoing=outgoing, is_incoming=not outgoing,
        raw_from='self' if outgoing else 'peer', raw_to='peer' if outgoing else 'self')
    value = normalize_event(payload, 'self', ZoneInfo('Asia/Shanghai'))['normalized']
    assert value['sender_username'] == ('self' if outgoing else 'peer')
    assert bool(value['is_self']) is outgoing


def test_restart_repairs_old_direction_without_replacing_edited_or_recalled_body(tmp_path):
    path = tmp_path / 'chat.sqlite'
    service = LiveChat(path, 'http://127.0.0.1:19088', tmp_path / 'relay.json')
    payload = group_event()
    service.ingest(payload)
    with service.db() as db:
        row = db.execute('SELECT id,data FROM chat_messages').fetchone()
        body = json.loads(row['data'])
        body.update(is_self=1, content='kept recall placeholder', category='system', _recalled=True)
        db.execute('UPDATE chat_messages SET data=? WHERE id=?', (json.dumps(body), row['id']))
    before = service.messages.revision('self')
    restored = LiveChat(path, service.bridge_url, service.relay_path)
    messages = restored.detail('self', 'group@chatroom')['messages']
    assert len(messages) == 1
    assert messages[0]['id'] == str(row['id'])
    assert messages[0]['sender_username'] == 'custom-author'
    assert messages[0]['is_self'] is False
    assert messages[0]['content'] == 'kept recall placeholder'
    assert messages[0]['category'] == 'system'
    assert restored.messages.revision('self') > before
    again = LiveChat(path, service.bridge_url, service.relay_path)
    assert again.messages.revision('self') == restored.messages.revision('self')


def test_unknown_author_does_not_overwrite_a_verified_historical_author(tmp_path):
    service = LiveChat(tmp_path / 'chat.sqlite', 'http://127.0.0.1:19088', tmp_path / 'relay.json')
    payload = group_event(content='<msg><appmsg><title>reply</title><type>57</type></appmsg></msg>')
    service.ingest(payload)
    with service.db() as db:
        row = db.execute('SELECT id,data FROM chat_messages').fetchone()
        body = json.loads(row['data'])
        body.update(sender_username='verified-member', is_self=False)
        db.execute('UPDATE chat_messages SET data=? WHERE id=?', (json.dumps(body), row['id']))
    restored = LiveChat(service.path, service.bridge_url, service.relay_path)
    assert restored.detail('self', 'group@chatroom')['messages'][0]['sender_username'] == 'verified-member'
