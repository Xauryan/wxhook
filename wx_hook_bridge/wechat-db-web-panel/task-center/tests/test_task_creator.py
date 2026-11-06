from uuid import uuid4

from tests.conftest import P
from tests.test_center import FIELDS, LINK, cmd, create


def edit(client, task, title, expected=200):
    response = client.patch(P + '/tasks/' + task['id'], json={
        **FIELDS, 'title': title, 'expected_version': task['version'],
    }, headers={'Idempotency-Key': str(uuid4())})
    assert response.status_code == expected, response.text
    return response.json()


def test_each_member_manages_own_draft_and_published_scope(env):
    app, owner, alice, bob, users = env
    for creator, other, user in ((alice, bob, users[1]), (bob, alice, users[2])):
        task = create(creator)
        assert task['created_by'] == user['id']
        assert task['lead_id'] is None and not task['members']
        assert creator.get(P + '/tasks/' + task['id']).status_code == 200
        assert owner.get(P + '/tasks/' + task['id']).status_code == 200
        assert task['id'] in [t['id'] for t in creator.get(P + '/tasks?mine=created').json()['items']]
        assert task['id'] in [t['id'] for t in creator.get(P + '/tasks?month=2026-10&status=draft').json()['items']]
        assert task['id'] not in [t['id'] for t in other.get(P + '/tasks').json()['items']]
        assert other.get(P + '/tasks/' + task['id']).status_code == 404
        assert other.get(P + '/tasks/' + task['id'] + '/activity').status_code == 404
        cmd(other, task, 'publish', expected=404)
        edit(other, task, 'Forbidden', expected=404)

        task = edit(creator, task, 'My updated draft')
        assert [e['action'] for e in creator.get(P + '/tasks/' + task['id'] + '/activity').json()['items']] == ['task.edit', 'task.created']
        creator_actor = app.state.auth.authenticate(creator.cookies.get('tc_session'))
        other_actor = app.state.auth.authenticate(other.cookies.get('tc_session'))
        assert any(e['task_id'] == task['id'] for e in app.state.tasks.events(creator_actor))
        assert not any(e['task_id'] == task['id'] for e in app.state.tasks.events(other_actor))

        task = cmd(creator, task, 'publish')
        assert other.get(P + '/tasks/' + task['id']).status_code == 200
        task = cmd(other, task, 'claim')
        # Being lead or collaborator never grants control over someone else's scope.
        edit(other, task, 'Lead cannot change scope', expected=403)
        cmd(other, task, 'publish', expected=403)
        task = cmd(creator, task, 'join')
        task = edit(creator, task, 'Creator updated published task')
        task = edit(owner, task, 'Owner can manage every task')
        assert task['created_by'] == user['id'] and task['lead_id'] != user['id']
        assert not any(e['task_id'] == task['id'] and e['action'] == 'task.created'
                       for e in app.state.tasks.events(other_actor))


def test_creator_cannot_review_results_or_manage_members(env):
    _, owner, alice, bob, users = env
    task = cmd(alice, create(alice), 'publish')
    task = cmd(bob, task, 'claim')
    cmd(alice, task, 'submit', text='Not lead', pr_links=[LINK], expected=403)
    task = cmd(bob, task, 'submit', text='Result', pr_links=[LINK])
    edit(alice, task, 'Locked during review', expected=422)
    for peer in (alice, bob):
        cmd(peer, task, 'complete', expected=403)
        cmd(peer, task, 'request-changes', reason='Not owner', expected=403)
        cmd(peer, task, 'cancel', reason='Not owner', expected=403)
        assert peer.post(P + '/members/' + users[2]['id'] + '/state',
                         json={'enabled': False, 'reason': 'Not owner'}).status_code == 403
    task = cmd(owner, task, 'request-changes', reason='Revise')
    task = edit(alice, task, 'Creator can edit after return')
    task = cmd(bob, task, 'submit', text='Revised', pr_links=[LINK])
    assert cmd(owner, task, 'complete')['status'] == 'done'


def test_creator_is_server_owned_and_creation_retry_is_stable(env):
    _, owner, alice, _, users = env
    key = str(uuid4())
    first = alice.post(P + '/tasks', json=FIELDS, headers={'Idempotency-Key': key})
    retry = alice.post(P + '/tasks', json=FIELDS, headers={'Idempotency-Key': key})
    assert first.status_code == retry.status_code == 200
    assert first.json() == retry.json()
    task = first.json()
    for client, method, path, payload in (
        (alice, 'post', '/tasks', {**FIELDS, 'created_by': users[0]['id']}),
        (owner, 'patch', '/tasks/' + task['id'], {**FIELDS, 'expected_version': 1, 'created_by': users[0]['id']}),
    ):
        assert getattr(client, method)(P + path, json=payload, headers={'Idempotency-Key': str(uuid4())}).status_code == 422
    copied = cmd(owner, task, 'copy', month='2026-11')
    assert copied['created_by'] == users[0]['id'] and copied['lead_id'] is None
