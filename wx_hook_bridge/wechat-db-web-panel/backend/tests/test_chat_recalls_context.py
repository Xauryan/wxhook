"""Recall navigation must read the target window, even outside the latest page."""
import json

import pytest
from fastapi import HTTPException

from backend.app.chat_messages import ChatMessages, present_content


def message(number, *, account='self', session='peer', content=None):
    return {'session_username': session, 'sender_username': 'sender',
            'server_id': str(9007199254740993000 + number), 'local_id': number,
            'create_time': 1000 + number, 'category': 'text',
            'content': content or f'context {number}', 'source': 'realtime'}


def revoke(target, *, timestamp=1200):
    xml = f'<sysmsg><revokemsg><newmsgid>{target}</newmsgid><replacemsg>消息已撤回</replacemsg></revokemsg></sysmsg>'
    return {**message(timestamp, content='notice'), **present_content(xml, 10002, False),
            'create_time': timestamp}


def test_old_recall_loads_both_sides_of_context_without_restoring_original(tmp_path):
    store = ChatMessages(tmp_path / 'chat.sqlite')
    target = message(30)['server_id']
    with store.db() as db:
        for number in range(1, 121):
            store.upsert(db, 'self', message(number))
        store.upsert(db, 'self', revoke(target))

    assert target not in {item['server_id'] for item in store.page('self', 'peer')['messages']}
    context = store.page('self', 'peer', around=target)
    rows = context['messages']
    index = next(i for i, item in enumerate(rows) if item['server_id'] == target)
    assert index > 0 and index < len(rows) - 1
    assert rows[index]['category'] == 'system'
    assert rows[index]['content'] == '消息已撤回'
    assert rows[index - 1]['content'] == 'context 29'
    assert rows[index + 1]['content'] == 'context 31'
    with store.db() as db:
        original = db.execute('SELECT original FROM chat_recalls WHERE account=? AND session=? AND server_id=?',
                              ('self', 'peer', target)).fetchone()[0]
    assert json.loads(original)['content'] == 'context 30'
    # An exact target from another account or conversation cannot be navigated.
    for account, session in [('other', 'peer'), ('self', 'different-peer')]:
        with pytest.raises(HTTPException) as error:
            store.page(account, session, around=target)
        assert error.value.status_code == 404


def test_uncaptured_original_locates_notice_context_by_scoped_row_id(tmp_path):
    store = ChatMessages(tmp_path / 'chat.sqlite')
    with store.db() as db:
        for number in range(1, 121):
            store.upsert(db, 'self', message(number))
        notice_id, _ = store.upsert(db, 'self', revoke('999999999999999999', timestamp=1030))

    assert str(notice_id) not in {item['id'] for item in store.page('self', 'peer')['messages']}
    rows = store.page('self', 'peer', around_id=notice_id)['messages']
    index = next(i for i, item in enumerate(rows) if item['id'] == str(notice_id))
    assert 0 < index < len(rows) - 1
    assert rows[index]['content'] == '消息已撤回'
    assert rows[index + 1]['content'] == 'context 31'
    for account, session in [('other', 'peer'), ('self', 'different-peer')]:
        with pytest.raises(HTTPException) as error:
            store.page(account, session, around_id=notice_id)
        assert error.value.status_code == 404
