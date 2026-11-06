import json
import hashlib
import sqlite3
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.app.delivery_image_quotes import ImageQuoteSource, QuoteSourceUnavailable, ImageQuoteRejected, quote_payload
from backend.app.intention_notifications import WeChatNotificationBridge
from backend.tests.test_delivery_materials import system, primary, ROOM

ACCOUNT = 'wxid_fixture'
STAMP = 1789975235
SID = 9007199254740993
ROOM_NAME = 'fixture-room@chatroom'
TABLE = 'Msg_' + hashlib.md5(ROOM_NAME.encode()).hexdigest()


@pytest.fixture
def messages(tmp_path):
    root = tmp_path/(ACCOUNT+'_abcd')/'db_storage'
    (root/'message').mkdir(parents=True)
    path = root/'message/message_0.db'
    db = sqlite3.connect(path)
    db.executescript(f'''CREATE TABLE Name2Id(user_name TEXT PRIMARY KEY,is_session INTEGER);
        CREATE TABLE "{TABLE}"(local_id INTEGER PRIMARY KEY,server_id INTEGER,local_type INTEGER,
            real_sender_id INTEGER,create_time INTEGER,message_content BLOB,compress_content BLOB);''')
    db.executemany('INSERT INTO Name2Id VALUES(?,1)', [(ROOM_NAME,), (ACCOUNT,), ('other_sender',)])
    db.execute(f'INSERT INTO "{TABLE}" VALUES(1,?,1,2,?,?,NULL)', (SID, STAMP, 'exact primary'))
    db.commit(); db.close()
    manifest = tmp_path/'keys.json'; manifest.write_text('{}')
    config = tmp_path/'source.json'
    config.write_text(json.dumps({'account': ACCOUNT, 'source_root': str(root), 'key_manifest': str(manifest)}))
    source = ImageQuoteSource(config, tmp_path, tmp_path/'snapshots')
    receipt = {'status': 'sent', 'sent_at': datetime.fromtimestamp(STAMP+1, timezone.utc).isoformat(), 'message': 'exact primary'}
    return source, path, receipt


def change(path, sql, params=()):
    db = sqlite3.connect(path)
    try:
        db.execute(sql, params); db.commit()
    finally:
        db.close()


def test_reference_is_one_real_row_and_large_id_is_string(messages):
    source, path, receipt = messages
    source.preflight(ACCOUNT)
    ref = source.resolve(ACCOUNT, ROOM_NAME, receipt)
    assert (ref['quotedMsgId'], ref['quotedLocalId'], ref['createTime']) == (str(SID), 1, STAMP)
    assert quote_payload(ROOM_NAME, path, ref)['quotedMsgId'] == str(SID)
    with pytest.raises(QuoteSourceUnavailable):
        quote_payload(ROOM_NAME, path, {**ref, 'quotedMsgId': SID})


@pytest.mark.parametrize('kind', ['account', 'room', 'sender', 'content', 'zero_id', 'ambiguous', 'stale', 'wrong_type'])
def test_reference_never_guesses(messages, kind):
    source, path, receipt = messages
    account, room = ACCOUNT, ROOM_NAME
    if kind == 'account': account = 'wxid_other'
    elif kind == 'room': room = 'other-room@chatroom'
    elif kind == 'content': receipt['message'] = 'similar but different'
    elif kind == 'sender': change(path, f'UPDATE "{TABLE}" SET real_sender_id=3')
    elif kind == 'zero_id': change(path, f'UPDATE "{TABLE}" SET server_id=0')
    elif kind == 'stale': change(path, f'UPDATE "{TABLE}" SET create_time=create_time-3600')
    elif kind == 'wrong_type': change(path, f'UPDATE "{TABLE}" SET local_type=49')
    else: change(path, f'INSERT INTO "{TABLE}" SELECT 2,server_id+1,local_type,real_sender_id,create_time,message_content,NULL FROM "{TABLE}"')
    with pytest.raises(QuoteSourceUnavailable): source.resolve(account, room, receipt)


@pytest.mark.parametrize('kind', ['valid', 'group_prefix', 'plain_image', 'wrong_id', 'wrong_time', 'wrong_type', 'old_image', 'ambiguous'])
def test_only_new_native_image_reference_is_evidence(messages, kind):
    source, path, receipt = messages
    ref = source.resolve(ACCOUNT, ROOM_NAME, receipt)
    sid = SID+1 if kind == 'wrong_id' else SID
    stamp = STAMP+1 if kind == 'wrong_time' else STAMP
    xml = f'<msg><img/><extcommoninfo><refermsg><svrid>{sid}</svrid><createtime>{stamp}</createtime></refermsg></extcommoninfo></msg>'
    if kind == 'plain_image': xml = '<msg><img/></msg>'
    if kind == 'group_prefix': xml = ACCOUNT + ':\n' + xml
    change(path, f'INSERT INTO "{TABLE}" VALUES(2,?, ?,2,?,?,NULL)', (SID+10, 49 if kind == 'wrong_type' else 3, STAMP+2, xml))
    if kind == 'old_image': ref['baseline']['message_0.db'] = 2
    if kind == 'ambiguous': change(path, f'INSERT INTO "{TABLE}" SELECT 3,server_id+1,local_type,real_sender_id,create_time,message_content,NULL FROM "{TABLE}" WHERE local_id=2')
    proof = source.verify(ref, receipt['sent_at'])
    assert bool(proof) == (kind in {'valid', 'group_prefix'})
    if proof:
        assert proof['local_type'] == 3 and proof['reference_server_id'] == str(SID)


@pytest.mark.asyncio
async def test_no_reference_defers_images_without_resending_primary(system):
    system.dialogues.resolve_feedback_reference.side_effect = QuoteSourceUnavailable('waiting')
    for cid in [1, 2]: await system.dialogues.generate(system.case, cid)
    ids = [r['id'] for r in system.dialogues.listing(38)]
    result = await system.dialogues.send(38, ids, [ROOM], False, {}, 'https://fixture.invalid', system.case)
    assert result['waiting_reference'] is True
    assert [c.args[0] for c in system.bridge._post.call_args_list] == ['/api/sendTextMsg']
    await system.dialogues.send(38, ids, [ROOM], False, {}, 'https://fixture.invalid', system.case)
    assert system.bridge._post.await_count == 1
    with system.store.connect() as db:
        assert db.execute('SELECT count(*) FROM dialogue_receipts').fetchone()[0] == 0


@pytest.mark.asyncio
async def test_unavailable_account_source_prevents_even_primary_send(system):
    system.dialogues.preflight_quote_source.side_effect = QuoteSourceUnavailable('wrong account')
    await system.dialogues.generate(system.case, 1)
    iid = system.dialogues.listing(38)[0]['id']
    result = await system.dialogues.send(38, [iid], [ROOM], False, {}, 'https://fixture.invalid', system.case)
    assert result['waiting_reference']
    system.bridge._post.assert_not_awaited()
    assert not system.store.recent()


@pytest.mark.asyncio
async def test_accepted_is_not_verified_and_second_image_waits(system):
    primary(system)
    system.dialogues.quote_source.verify = lambda *args: None
    await system.worker.process_case(38)
    assert system.bridge._post.await_count == 1
    assert system.bridge._post.call_args.args[0] == '/api/sendImageQuoteMsg'
    assert system.dialogues.pending_quote_evidence(38, ROOM)[0]['status'] == 'awaiting_evidence'
    await system.worker.process_case(38)
    assert system.bridge._post.await_count == 1
    system.dialogues.quote_source.verify = lambda *args: {'local_type': 3, 'server_id': '42'}
    await system.worker.process_case(38)
    assert system.bridge._post.await_count == 2
    assert not system.dialogues.pending_quote_evidence(38, ROOM)


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [ImageQuoteRejected('rejected'), TimeoutError('unknown')])
async def test_quote_failure_never_falls_back_or_retries(system, error):
    primary(system)
    system.bridge._post.side_effect = error
    await system.worker.process_case(38)
    await system.worker.process_case(38)
    assert system.bridge._post.await_count == 1
    assert system.bridge._post.call_args.args[0] == '/api/sendImageQuoteMsg'


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [{'code': -1, 'msg': 'validation: bad'}, {'code': -1, 'msg': 'failed', 'data': {'stage': 'send-rejected'}}])
async def test_bridge_distinguishes_definite_quote_rejection(body):
    bridge = WeChatNotificationBridge('http://127.0.0.1:19088')
    bridge._client = httpx.AsyncClient(base_url=bridge.base_url, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)))
    try:
        with pytest.raises(ImageQuoteRejected): await bridge._post('/api/sendImageQuoteMsg', {})
    finally: await bridge.close()
