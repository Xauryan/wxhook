import copy

import pytest

from backend.app.delivery_image_quotes import QuoteSourceUnavailable, ImageQuoteRejected
from backend.tests.test_delivery_notifications import service, GROUP
from backend.tests.test_delivery_call_images import setup_bundle


def state(service):
    with service.store.connect() as db:
        return dict(db.execute('SELECT * FROM call_quote_receipts LIMIT 1').fetchone())


def due(service):
    with service.store.connect() as db:
        db.execute("UPDATE call_quote_receipts SET next_check_at='2000-01-01T00:00:00+08:00'")


@pytest.mark.asyncio
async def test_call_statistics_image_quotes_exact_text_with_string_id(service, tmp_path):
    setup_bundle(service, tmp_path)
    await service.drain()
    assert service.bridge._post.call_args.args[0] == '/api/sendImageQuoteMsg'
    sent = service.bridge._post.call_args.args[1]
    assert sent['quotedMsgId'] == '9007199254740993'
    assert sent['quotedType'] == 1 and sent['quotedContent'] == service.bridge.send_text.call_args.args[1]
    assert state(service)['status'] == 'local_verified'


@pytest.mark.asyncio
async def test_source_unavailable_sends_nothing_and_can_resume(service, tmp_path):
    setup_bundle(service, tmp_path)
    service.call_quotes.source.preflight.side_effect = QuoteSourceUnavailable('missing')
    await service.drain()
    service.bridge.send_text.assert_not_awaited(); service.bridge._post.assert_not_awaited()
    assert state(service)['status'] == 'waiting_source'
    assert service.store.recent()[0]['status'] == 'quote_waiting'
    service.call_quotes.source.preflight.side_effect = None
    due(service); await service.drain()
    assert service.bridge.send_text.await_count == service.bridge._post.await_count == 1


@pytest.mark.asyncio
async def test_text_id_delay_and_restart_never_repeat_text(service, tmp_path):
    setup_bundle(service, tmp_path)
    original = service.call_quotes.source.resolve.side_effect
    service.call_quotes.source.resolve.side_effect = QuoteSourceUnavailable('not ingested')
    await service.drain(); service.store.recover(); due(service); await service.drain()
    assert service.bridge.send_text.await_count == 1
    service.bridge._post.assert_not_awaited()
    assert state(service)['status'] == 'waiting_reference'
    service.call_quotes.source.resolve.side_effect = original
    due(service); await service.drain(); due(service); await service.drain()
    assert service.bridge.send_text.await_count == service.bridge._post.await_count == 1


@pytest.mark.asyncio
async def test_bridge_acceptance_is_separate_from_database_evidence(service, tmp_path):
    setup_bundle(service, tmp_path)
    service.call_quotes.source.verify.return_value = None
    await service.drain()
    assert service.store.recent()[0]['status'] == 'sent'
    assert state(service)['status'] == 'awaiting_evidence'
    due(service); service.store.recover(); await service.drain()
    service.call_quotes.source.verify.return_value = {'local_type': 3, 'server_id': '9007199254740995'}
    due(service); await service.drain()
    assert state(service)['status'] == 'local_verified'
    assert service.bridge.send_text.await_count == service.bridge._post.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('error,expected', [(ImageQuoteRejected('no'), 'rejected'), (TimeoutError(), 'uncertain')])
async def test_no_plain_image_fallback_or_retry(service, tmp_path, error, expected):
    setup_bundle(service, tmp_path)
    service.bridge._post.side_effect = error
    await service.drain(); due(service); service.store.recover(); await service.drain()
    assert service.store.recent()[0]['status'] == expected
    assert service.bridge._post.await_count == 1
    assert service.bridge._post.call_args.args[0] == '/api/sendImageQuoteMsg'


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['text_sending', 'image_sending'])
async def test_crash_during_submission_is_not_resumed(service, tmp_path, phase):
    setup_bundle(service, tmp_path)
    service.call_quotes.source.resolve.side_effect = QuoteSourceUnavailable('not ingested')
    await service.drain()
    with service.store.connect() as db:
        db.execute("UPDATE deliveries SET status='sending'")
        db.execute('UPDATE call_quote_receipts SET status=?', (phase,))
    service.bridge.send_text.reset_mock()
    service.store.recover(); due(service); await service.drain()
    assert state(service)['status'] == 'uncertain'
    service.bridge.send_text.assert_not_awaited(); service.bridge._post.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['disabled', 'group', 'account', 'image'])
async def test_delayed_image_rechecks_context_without_resending_text(service, tmp_path, change):
    setup_bundle(service, tmp_path)
    original = service.call_quotes.source.resolve.side_effect
    service.call_quotes.source.resolve.side_effect = QuoteSourceUnavailable('not ingested')
    await service.drain()
    service.call_quotes.source.resolve.side_effect = original
    if change == 'disabled': service.store.configure(False, [GROUP])
    elif change == 'group': service.store.configure(True, [{'room_id': 'different@chatroom', 'name': 'Different'}])
    elif change == 'account': service.call_quotes.account.return_value = 'wxid_changed'
    else: (tmp_path/'call.png').write_bytes(b'changed image')
    due(service); await service.drain()
    assert service.bridge.send_text.await_count == 1
    service.bridge._post.assert_not_awaited()


@pytest.mark.asyncio
async def test_waiting_call_does_not_block_next_call(service, tmp_path):
    data = setup_bundle(service, tmp_path)
    other = copy.deepcopy(data); other['call'].update(id=102, event_key='delivery.call:next', order_id=2)
    service.store.enqueue_call(other, [GROUP])
    original = service.call_quotes.source.resolve.side_effect
    def resolve(account, room, primary):
        if '订单 #1\n' in primary['message']:
            raise QuoteSourceUnavailable('waiting')
        return original(account, room, primary)
    service.call_quotes.source.resolve.side_effect = resolve
    await service.drain()
    assert service.bridge.send_text.await_count == 2
    assert service.bridge._post.await_count == 1
    assert '订单 #2\n' in service.bridge._post.call_args.args[1]['quotedContent']


@pytest.mark.asyncio
async def test_completed_old_bundle_is_not_upgraded(service, tmp_path):
    setup_bundle(service, tmp_path)
    with service.store.connect() as db:
        db.execute("UPDATE deliveries SET status='sent',text_status='sent'")
    await service.drain()
    service.bridge.send_text.assert_not_awaited(); service.bridge._post.assert_not_awaited()
    with service.store.connect() as db:
        assert db.execute('SELECT count(*) FROM call_quote_receipts').fetchone()[0] == 0
