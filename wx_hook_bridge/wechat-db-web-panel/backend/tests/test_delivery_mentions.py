import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.delivery_mentions import DeliveryMentions, MentionSelectionError, TEAMS
from backend.app.delivery_dialogues import DialogueService
from backend.tests.test_delivery_notifications import service, GROUP
from backend.tests.test_delivery_call_images import setup_bundle
from backend.tests.test_delivery_materials import system, ROOM
from backend.tests.quote_fixtures import ACCEPTED


def install(store, bridge):
    policy = DeliveryMentions(store, bridge)
    roster = [{'wxid': 'wxid_a', 'group_nickname': '甲'}, {'wxid': 'legacy_b', 'nickname': '乙'}]
    async def request(path, payload):
        if path == '/api/userInfo': return {'wxid': 'wxid_fixture'}
        if path == '/api/getMemberFromChatRoom': return {'ok': True, 'members': roster, 'count': len(roster)}
        return ACCEPTED
    bridge._post.side_effect = request
    policy.initialize()
    return policy, roster


async def select(policy, room, team='3', ids=None):
    cfg = policy.configuration()
    return await policy.save(cfg['revision'], 'wxid_fixture', [{'room_id': room, 'team_id': team, 'wxids': ['wxid_a', 'legacy_b'] if ids is None else ids}])


@pytest.mark.asyncio
async def test_default_empty_never_mentions_or_fetches_members(service):
    service.store.configure(True, [GROUP])
    policy, _ = install(service.store, service.bridge)
    assert policy.configuration()['selections'] == []
    for team in TEAMS.values():
        plan = await policy.prepare(GROUP['room_id'], 'call', {'outbound_group_name': team}, 'original')
        assert plan['message'] == 'original' and not plan['members']
    service.bridge._post.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('team', list(TEAMS))
async def test_each_team_has_independent_real_multi_member_mentions(service, team):
    service.store.configure(True, [GROUP]); policy, _ = install(service.store, service.bridge)
    await select(policy, GROUP['room_id'], team)
    plan = await policy.prepare(GROUP['room_id'], 'call', {'outbound_group_name': TEAMS[team]}, 'original')
    assert plan['message'] == '@甲 @乙 original'
    await policy.send(GROUP['room_id'], plan)
    assert service.bridge._post.call_args.args == ('/api/sendAtText', {'chatRoomId': GROUP['room_id'], 'wxids': 'wxid_a,legacy_b', 'nickname': '甲 @乙', 'msg': 'original'})
    other = next(key for key in TEAMS if key != team)
    assert not (await policy.prepare(GROUP['room_id'], 'call', {'outbound_group_name': TEAMS[other]}, 'original'))['members']


@pytest.mark.asyncio
@pytest.mark.parametrize('ids', [['notify@all'], ['wxid_a,legacy_b'], ['outsider'], ['wxid_a', 'wxid_a']])
async def test_invalid_members_are_not_saved(service, ids):
    service.store.configure(True, [GROUP]); policy, _ = install(service.store, service.bridge)
    with pytest.raises(MentionSelectionError): await select(policy, GROUP['room_id'], ids=ids)
    assert policy.configuration()['selections'] == []


@pytest.mark.asyncio
async def test_wrong_room_stale_revision_and_account_do_not_overwrite(service):
    service.store.configure(True, [GROUP]); policy, _ = install(service.store, service.bridge)
    with pytest.raises(MentionSelectionError): await select(policy, 'other@chatroom')
    await select(policy, GROUP['room_id'])
    before = policy.configuration()
    with pytest.raises(MentionSelectionError): await policy.save(0, 'wxid_fixture', [])
    with pytest.raises(MentionSelectionError): await policy.save(before['revision'], 'wxid_other', [{'room_id': GROUP['room_id'], 'team_id': '3', 'wxids': ['wxid_a']}])
    assert policy.configuration() == before


@pytest.mark.asyncio
async def test_saving_does_not_change_notifications_and_clear_works_offline(service):
    service.store.configure(True, [GROUP]); policy, _ = install(service.store, service.bridge)
    service.store.enqueue('existing', [GROUP], 'original')
    cfg = service.store.configuration(); rows = service.store.recent()
    await select(policy, GROUP['room_id'])
    assert service.store.configuration() == cfg and service.store.recent() == rows
    service.bridge._post.side_effect = RuntimeError('offline')
    result = await policy.save(policy.configuration()['revision'], None, [])
    assert result['selections'] == []


@pytest.mark.asyncio
async def test_departed_member_never_matches_another_person_with_same_name(service):
    service.store.configure(True, [GROUP]); policy, roster = install(service.store, service.bridge)
    await select(policy, GROUP['room_id'], ids=['wxid_a'])
    roster[0]['wxid'] = 'wxid_new_person'
    with pytest.raises(MentionSelectionError):
        await policy.prepare(GROUP['room_id'], 'call', {'outbound_group_name': TEAMS['3']}, 'original')


@pytest.mark.asyncio
async def test_partial_roster_and_group_identity_conflict_fail_closed(service):
    service.store.configure(True, [GROUP]); policy, _ = install(service.store, service.bridge)
    await select(policy, GROUP['room_id'])
    with pytest.raises(MentionSelectionError):
        await policy.prepare(GROUP['room_id'], 'call', {'outbound_group_name': TEAMS['3'], 'outbound_group_id': 2}, 'original')
    service.bridge._post.side_effect = None
    service.bridge._post.return_value = {'wxid': 'wxid_fixture', 'members': [], 'count': 10}
    with pytest.raises(MentionSelectionError): await policy.members(GROUP['room_id'])


@pytest.mark.asyncio
async def test_call_mentions_are_frozen_in_exact_quoted_text(service, tmp_path):
    data = setup_bundle(service, tmp_path); data['call']['outbound_group_name'] = TEAMS['3']
    with service.store.connect() as db:
        db.execute('UPDATE call_reports SET payload=?', (json.dumps(data),))
    policy, _ = install(service.store, service.bridge); service.call_quotes.mentions = policy
    await select(policy, GROUP['room_id'])
    await service.drain(); await service.drain()
    sends = [call for call in service.bridge._post.call_args_list if call.args[0].startswith('/api/send')]
    assert [c.args[0] for c in sends] == ['/api/sendAtText', '/api/sendImageQuoteMsg']
    service.bridge.send_text.assert_not_awaited()
    with service.store.connect() as db:
        message = db.execute('SELECT message FROM deliveries').fetchone()[0]
        assert message.startswith('@甲 @乙 ')
        assert db.execute('SELECT message FROM delivery_mention_receipts').fetchone()[0] == message
        assert sends[1].args[1]['quotedContent'] == message
    await policy.save(policy.configuration()['revision'], None, [])
    await service.drain()
    assert len([c for c in service.bridge._post.call_args_list if c.args[0].startswith('/api/send')]) == 2


@pytest.mark.asyncio
async def test_feedback_mentions_and_image_reference_use_same_frozen_primary(system):
    system.case['notification_order'] = {'outbound_group_name': TEAMS['3']}
    policy, _ = install(system.store, system.bridge); system.dialogues.mentions = policy
    await select(policy, ROOM)
    system.dialogues.resolve_feedback_reference = DialogueService.resolve_feedback_reference.__get__(system.dialogues)
    def resolve(account, room, primary):
        assert primary['message'].startswith('@甲 @乙 ')
        return {'account': account, 'room': room, 'quotedMsgId': '9007199254740993', 'quotedLocalId': 17,
                'createTime': 1789975235, 'quotedFrom': account, 'quotedType': 1,
                'quotedContent': primary['message'], 'quotedChat': room, 'baseline': {}}
    system.dialogues.quote_source.resolve = resolve
    await system.dialogues.generate(system.case, 1)
    iid = system.dialogues.listing(38)[0]['id']
    await system.dialogues.send(38, [iid], [ROOM], False, {}, 'https://fixture.invalid', system.case)
    sends = [c for c in system.bridge._post.call_args_list if c.args[0].startswith('/api/send')]
    assert [c.args[0] for c in sends] == ['/api/sendAtText', '/api/sendImageQuoteMsg']
    assert sends[1].args[1]['quotedContent'].startswith('@甲 @乙 ')


def test_local_endpoints_expose_four_teams_and_save_without_sending(service, monkeypatch):
    from backend.app import delivery_management_router as router
    service.store.configure(True, [GROUP]); policy, _ = install(service.store, service.bridge)
    monkeypatch.setattr(router, 'notification_service', service); monkeypatch.setattr(router, 'mention_service', policy)
    app = FastAPI(); app.include_router(router.router)
    with TestClient(app) as client:
        cfg = client.get('/api/delivery-management/notifications/mentions').json()
        assert [t['name'] for t in cfg['teams']] == list(TEAMS.values())
        member = client.get('/api/delivery-management/notifications/mention-members', params={'room_id': GROUP['room_id']})
        assert member.status_code == 200
        response = client.post('/api/delivery-management/notifications/mentions', json={'revision': 0, 'account': 'wxid_fixture',
            'selections': [{'room_id': GROUP['room_id'], 'team_id': '3', 'wxids': ['wxid_a']}]})
        assert response.status_code == 200
        denied = client.post('/api/delivery-management/notifications/mentions', headers={'origin': 'https://untrusted.invalid'}, json={'revision': 1, 'selections': []})
        assert denied.status_code == 403
    assert not any(c.args[0].startswith('/api/send') for c in service.bridge._post.call_args_list)
