import copy
import pytest
from backend.app.delivery_notifications import failure_message
from backend.app.notification_text import feedback_recordings
from backend.tests.test_delivery_materials import sample, system


@pytest.mark.parametrize('state,expected', [('pending','待筛选'),('retry_pending','筛选失败，等待重试'),
    ('ai_unavailable','筛选失败，待处理'),('awaiting_transcripts','录音转写中')])
def test_deepseek_states_do_not_promise_processing_or_missing_side(state,expected):
    case=sample()
    case['notification_calls']=[]
    case['candidates']['items']=[case['candidates']['items'][0]]
    case['material_scope']={'policy':'business-relevance-v2-deepseek-auto','status':state,'calls':[],
                            'work_call_ids':[case['candidates']['items'][0]['id']]}
    message=failure_message(case,'')
    assert f'【外呼录音】{expected}' in message
    assert '【配送录音】暂未查到可确认的本单录音，继续复查' in message
    assert 'AI 自动处理中' not in message
    if state in {'ai_unavailable','retry_pending'}:
        assert '当前暂无可补充长图' in message and '就绪后补充' not in message


def test_one_notice_has_group_full_phone_and_two_distinct_exact_call_links():
    case = sample()
    case['notification_order'] = {'outbound_group_name':'呼叫中心四组','agent_name':'小周','customer_phone':'13800001234'}
    message = failure_message(case, 'https://youshenghe666.com/d/abcdefghijklmnop')
    assert '所属组：呼叫中心四组｜外呼员：小周' in message
    assert '客户电话：13800001234' in message and '客户尾号' not in message
    assert message.count('【外呼录音】') == 1 and message.count('【配送录音】') == 1
    assert 'https://youshenghe666.com/outbound/recordings/orders/10683?case=38&call=1' in message
    assert 'https://youshenghe666.com/outbound/recordings/orders/10683?case=38&call=2' in message
    assert '/d/' not in message
    assert message.count('https://') == 2


def test_missing_side_is_pending_never_reuses_other_side_or_mask_as_full_phone():
    case = sample()
    case['notification_calls'] = [case['notification_calls'][1]]
    message = failure_message(case, 'https://youshenghe666.com/d/abcdefghijklmnop')
    assert '【外呼录音】录音待确认' in message
    assert message.count('https://') == 1 and '&call=2' in message
    assert '客户电话：未记录' in message and '0900' not in message


def test_multiple_same_side_candidates_are_not_arbitrarily_reduced_to_first():
    case = sample()
    call = copy.deepcopy(case['candidates']['items'][0]);call.update(id=9,ec_waste_id='w9')
    ref = {**case['notification_calls'][0], 'id':9,'ec_waste_id':'w9'}
    case['candidates']['items'].append(call);case['notification_calls'].append(ref)
    assert set(feedback_recordings(case)) == {'delivery'}
    assert '【外呼录音】录音待确认' in failure_message(case, '')


@pytest.mark.asyncio
async def test_single_notice_with_both_links_then_two_images_without_caption(system):
    system.case['notification_order'] = {'outbound_group_name':'模拟组','agent_name':'模拟外呼','customer_phone':'13800001234'}
    for call_id in [1, 2]:
        await system.dialogues.generate(system.case, call_id)
    ids = [row['id'] for row in system.dialogues.listing(38)]
    await system.dialogues.send(38, ids, ['fixture-room@chatroom'], False, {},
                                'https://youshenghe666.com/d/abcdefghijklmnop', system.case)
    calls = system.bridge._post.call_args_list
    assert [call.args[0] for call in calls] == ['/api/sendTextMsg','/api/sendImageQuoteMsg','/api/sendImageQuoteMsg']
    assert calls[0].args[1]['msg'].count('https://') == 2
    assert '客户电话：13800001234' in calls[0].args[1]['msg']
    await system.worker.process_case(38)
    assert system.bridge._post.await_count == 3
    with system.store.connect() as db:
        assert db.execute('SELECT count(*) FROM dialogue_context_receipts').fetchone()[0] == 0
