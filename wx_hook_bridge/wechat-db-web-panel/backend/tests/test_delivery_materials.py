import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from backend.app.delivery_notifications import DeliveryNotificationStore, DeliveryNotificationService, failure_message
from backend.app.delivery_dialogues import DialogueService, image_identity
from backend.app.delivery_materials import DeliveryMaterialWorker, ready_calls
from backend.tests.quote_fixtures import fake_quote_source

ROOM = 'fixture-room@chatroom'
LINK = '/d/abcdefghijklmnop'


def sample():
    calls = [dict(id=n, ec_waste_id=f'w{n}', role=role, call_time=f'2026-09-20T17:{n:02d}:00+08:00',
                  call_duration=19, user_name='关联员工', delivery_transcript={'status':'succeeded', 'full_text':f'原文{n}',
                    'segments':[{'text':f'原文{n}', 'speaker_id':0, 'begin_time':0, 'end_time':1000}]})
             for n, role in [(1,'outbound'),(2,'delivery'),(3,'unverified')]]
    refs = [{'id':c['id'],'ec_waste_id':c['ec_waste_id'],'role':c['role'],
             'purpose':'outbound_sale' if c['role']=='outbound' else 'delivery_failure','source':'manual_business_confirmation'} for c in calls[:2]]
    return dict(id=38,order_id=10683,created_at='2026-09-20T17:54:12+08:00',status='open',closed_at=None,
        reported_reason='原始反馈',selected_calls=None,reporter={'verified':True,'user_id':86,'name':'已核实提交人',
         'source':'intention_customer_logs','audit_id':16179,'source_event_id':103,'submitted_at':'2026-09-20T17:54:12+08:00'},
        notification_calls=refs, material_scope={'policy':'business-relevance-v1','status':'selected','calls':refs},
        candidates={'order':{'customer_phone_masked':'***0900'},'items':calls})


@pytest.fixture
def system(tmp_path, monkeypatch):
    store = DeliveryNotificationStore(tmp_path/'notifications.sqlite')
    store.initialize();store.configure(False,[{'room_id':ROOM,'name':'测试群'}])
    with store.connect() as db:
        db.execute("UPDATE config SET failure_notifications_enabled=1,failure_activated_at='2026-09-20T17:45:00+08:00'")
    case = sample()
    async def fetch(path, params, **kwargs):
        return {'path':LINK} if path.endswith('/archive-link') else copy.deepcopy(case)
    bridge = SimpleNamespace(_post=AsyncMock(),close=AsyncMock())
    fetch_mock = AsyncMock(side_effect=fetch)
    notifications = DeliveryNotificationService(store,bridge,fetch_mock,send_interval=0)
    dialogues = DialogueService(store,tmp_path/'images',Path('unused-renderer'),bridge)
    fake_quote_source(dialogues)
    worker = DeliveryMaterialWorker(notifications,dialogues,fetch_mock);worker.initialize()
    async def generate(detail,call_id,regenerate=False):
        content,raw,version,image_id=image_identity(detail,call_id)
        with store.connect() as db:
            db.execute('''INSERT OR IGNORE INTO dialogue_images
                (id,case_id,call_id,version,call_time,title,status,context_json) VALUES(?,?,?,?,?,?,'ready',?)''',
                (image_id,detail['id'],call_id,version,content['call_time'],content['title'],
                 json.dumps({k:v for k,v in content.items() if k!='segments'},ensure_ascii=False)))
        (dialogues.root/(image_id+'.png')).write_bytes(b'fixture-png')
        return dialogues.listing(detail['id'])
    dialogues.generate=AsyncMock(side_effect=generate)
    monkeypatch.setattr('backend.app.delivery_dialogues.asyncio.sleep',AsyncMock())
    return SimpleNamespace(store=store,case=case,bridge=bridge,worker=worker,dialogues=dialogues,fetch=fetch_mock)


def primary(system,status='sent',body='已发送的原主消息，反馈人未记录'):
    with system.store.connect() as db:
        db.execute('''INSERT INTO deliveries(event_key,room_id,room_name,message,status,created_at)
            VALUES('delivery.failure:38',?,'测试群',?,?,?)''',(ROOM,body,status,'2026-09-20T17:55:00+08:00'))


@pytest.mark.asyncio
async def test_deepseek_v2_selected_materials_use_same_image_only_pipeline(system):
    primary(system)
    system.case['material_scope']['policy']='business-relevance-v2-deepseek-auto'
    for ref in system.case['notification_calls']:ref['source']='deepseek_relevance'
    await system.worker.process_case(38)
    assert [call.args[0] for call in system.bridge._post.call_args_list]==['/api/sendImageQuoteMsg','/api/sendImageQuoteMsg']


@pytest.mark.asyncio
async def test_ai_no_relevant_result_is_completed_without_human_selection_or_images(system):
    primary(system)
    system.case['material_scope']={'policy':'business-relevance-v2-deepseek-auto','status':'no_relevant','calls':[],
                                   'review_reasons':['仅有普通预约，无本次失败沟通']}
    system.case['notification_calls']=[]
    await system.worker.process_case(38)
    assert system.worker.status()['items'][0]['status']=='no_relevant'
    system.dialogues.generate.assert_not_awaited();system.bridge._post.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['missing_scope', 'wrong_policy', 'wrong_purpose', 'unselected', 'unknown_source'])
async def test_order_association_alone_never_generates_or_sends(system, invalid):
    primary(system)
    if invalid == 'missing_scope': system.case.pop('material_scope')
    if invalid == 'wrong_policy': system.case['material_scope']['policy'] = 'association-only'
    if invalid == 'wrong_purpose':
        for ref in system.case['notification_calls']: ref['purpose'] = 'unconfirmed'
    if invalid == 'unselected': system.case['material_scope']['calls'] = []
    if invalid == 'unknown_source':
        for ref in system.case['notification_calls']: ref['source'] = 'same_phone'
    await system.worker.process_case(38)
    system.dialogues.generate.assert_not_awaited()
    system.bridge._post.assert_not_awaited()


@pytest.mark.asyncio
async def test_each_business_side_follows_primary_with_images_only(system):
    primary(system)
    await system.worker.process_case(38)
    assert system.dialogues.generate.await_count==2
    sent=system.bridge._post.call_args_list
    assert [c.args[0] for c in sent]==['/api/sendImageQuoteMsg','/api/sendImageQuoteMsg']
    assert not any('【无法配送反馈】' in c.args[1].get('msg','') for c in sent)
    await system.worker.process_case(38)
    assert system.bridge._post.await_count==2 and system.dialogues.generate.await_count==2
    with system.store.connect() as db:
        assert db.execute("SELECT message FROM deliveries WHERE event_key='delivery.failure:38'").fetchone()[0]=='已发送的原主消息，反馈人未记录'
        assert db.execute('SELECT count(*) FROM dialogue_images WHERE call_id=3').fetchone()[0]==0


@pytest.mark.asyncio
@pytest.mark.parametrize('status',['queued','sending','uncertain'])
async def test_materials_never_precede_or_retry_uncertain_primary(system,status):
    primary(system,status)
    await system.worker.process_case(38)
    system.bridge._post.assert_not_awaited();system.dialogues.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_pre_activation_feedback_is_not_backfilled(system):
    primary(system);system.case['created_at']='2026-09-20T10:00:00+08:00'
    await system.worker.process_case(38)
    system.bridge._post.assert_not_awaited();system.dialogues.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_call_follows_later_without_replaying_first(system):
    primary(system)
    transcript=system.case['candidates']['items'][1]['delivery_transcript']
    transcript['status']='processing'
    await system.worker.process_case(38)
    assert system.bridge._post.await_count==1
    transcript['status']='succeeded'
    await system.worker.process_case(38)
    assert system.bridge._post.await_count==2
    await system.worker.process_case(38)
    assert system.bridge._post.await_count==2


@pytest.mark.asyncio
async def test_uncertain_image_stops_later_calls_and_revisions(system):
    primary(system)
    system.bridge._post.side_effect=[TimeoutError()]
    await system.worker.process_case(38)
    assert system.bridge._post.await_count==1
    system.case['candidates']['items'][0]['delivery_transcript']['segments'][0]['text']='更新后的原文'
    await system.worker.process_case(38)
    assert system.bridge._post.await_count==1
    assert system.worker.status()['items'][0]['status']=='blocked'


@pytest.mark.asyncio
async def test_deselection_during_generation_is_revalidated(system):
    primary(system)
    original=system.dialogues.generate.side_effect
    async def generate(case,call_id,regenerate=False):
        result=await original(case,call_id,regenerate)
        system.case['notification_calls']=[]
        return result
    system.dialogues.generate.side_effect=generate
    await system.worker.process_case(38)
    system.bridge._post.assert_not_awaited()


@pytest.mark.asyncio
async def test_image_failure_is_not_repeated_automatically(system):
    primary(system)
    _,_,version,image_id=image_identity(system.case,1)
    with system.store.connect() as db:
        db.execute("INSERT INTO dialogue_images(id,case_id,call_id,version,status) VALUES(?,38,1,?,'failed')",(image_id,version))
    system.case['candidates']['items'][1]['delivery_transcript']['status']='processing'
    await system.worker.process_case(38);await system.worker.process_case(38)
    system.dialogues.generate.assert_not_awaited();system.bridge._post.assert_not_awaited()
    assert system.worker.status()['items'][0]['status']=='image_failed'


@pytest.mark.asyncio
async def test_poll_uses_only_configured_rooms_with_accepted_primary(system):
    primary(system)
    await system.worker.poll_once()
    assert system.bridge._post.await_count==2
    await system.worker.poll_once()
    assert system.bridge._post.await_count==2


def test_reporter_requires_verified_audit_provenance():
    case=sample()
    assert '反馈人：已核实提交人' in failure_message(case,'https://fixture.invalid')
    case['reporter']['source']='current_assignee'
    assert '反馈人：未记录' in failure_message(case,'https://fixture.invalid')
