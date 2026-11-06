from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db import Event, Project, Session, Task, User, now
from app.auth import digest
from app.errors import Problem
from tests.conftest import P, PASSWORD, login

FIELDS = dict(title='Shared task', month='2026-10', objective='A useful feature', criteria='Owner checks the result', deadline='2026-10-31T18:00:00+08:00')
LINK = 'https://github.com/example/private/pull/12'


def create(client):
    response = client.post(P + '/tasks', json=FIELDS, headers={'Idempotency-Key':str(uuid4())})
    assert response.status_code == 200, response.text
    return response.json()


def cmd(client, task, action, expected=200, key=None, **body):
    response = client.post(P + f"/tasks/{task['id']}/actions/{action}", json={'expected_version':task['version'], **body},
                           headers={'Idempotency-Key':key or str(uuid4())})
    assert response.status_code == expected, response.text
    return response.json()


def working(env):
    _, owner, alice, _, _ = env
    return cmd(alice, cmd(owner, create(owner), 'publish'), 'claim')


def test_simple_manual_loop_without_network(env):
    app, owner, alice, bob, users = env
    task = cmd(bob, working(env), 'join')
    task = cmd(alice, task, 'add-work', target_id=users[2]['id'], title='Check feature')
    task = cmd(bob, task, 'update-work', item_id=task['work_items'][0]['id'], work_state='done')
    assert task['status'] == 'in_progress'
    cmd(bob, task, 'submit', text='not lead', pr_links=[LINK], expected=403)
    task = cmd(alice, task, 'submit', text='Delivered feature', pr_links=[LINK])
    assert task['status'] == 'in_review'
    cmd(alice, task, 'complete', expected=403)
    task = cmd(owner, task, 'request-changes', reason='Please add tests')
    assert task['status'] == 'changes_requested' and task['deliveries'][0]['reason'] == 'Please add tests'
    task = cmd(alice, task, 'submit', text='Tests added', pr_links=[LINK])
    task = cmd(owner, task, 'complete', reason='Checked in Git')
    assert task['status'] == 'done'
    assert task['deliveries'][-1]['reviewed_by'] == users[0]['id']
    assert len(task['deliveries']) == 2
    with app.state.db.transaction() as db:
        assert any(e.action == 'task.complete' for e in db.scalars(select(Event)))


def test_github_routes_and_config_are_gone(env):
    app, owner, _, _, _ = env
    paths = app.openapi()['paths']
    assert not any('github' in path or 'webhook' in path for path in paths)
    assert owner.post(P + '/webhooks/github', json={}).status_code == 404
    task = working(env)
    cmd(owner, task, 'sync', expected=404)
    cmd(owner, task, 'bind-pr', expected=404)
    assert not hasattr(app.state, 'worker')


def test_disable_enable_retains_identity_and_revokes_sessions(env):
    app, owner, alice, _, users = env
    task = working(env)
    assert owner.post(P + '/members/' + users[1]['id'] + '/state', json={'enabled':False,'reason':'Pause'}).status_code == 200
    assert alice.get(P + '/me').status_code == 401
    assert owner.get(P + '/tasks/' + task['id']).json()['lead_unavailable'] is True
    assert owner.post(P + '/members/' + users[1]['id'] + '/state', json={'enabled':True,'reason':'Return'}).status_code == 200
    assert alice.get(P + '/me').status_code == 401
    assert login(alice,'alice')['id'] == users[1]['id']
    with app.state.db.transaction() as db:
        assert db.get(Task,task['id']).lead_id == users[1]['id']


def test_owner_protection_and_password_hash(env):
    app, owner, alice, _, users = env
    assert owner.post(P + '/members/' + users[0]['id'] + '/state', json={'enabled':False,'reason':'no'}).status_code == 422
    assert alice.post(P + '/members/' + users[0]['id'] + '/state', json={'enabled':False,'reason':'no'}).status_code == 403
    with app.state.db.transaction() as db:
        user = db.get(User,users[0]['id'])
        assert user.password_hash.startswith('scrypt$') and PASSWORD not in user.password_hash
        assert db.get(Project,1).owner_user_id == user.id
    with pytest.raises(Problem):
        app.state.auth.bootstrap('hacker','Hacker',PASSWORD)


def test_atomic_claim_idempotent_submit_and_stale_review(env):
    _, owner, alice, bob, users = env
    task = cmd(owner,create(owner),'publish')
    def race(client):
        return client.post(P + f"/tasks/{task['id']}/actions/claim",json={'expected_version':task['version']},headers={'Idempotency-Key':str(uuid4())})
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(race,[alice,bob]))
    assert sorted(r.status_code for r in responses) == [200,409]
    task = next(r.json() for r in responses if r.status_code == 200)
    lead = alice if task['lead_id'] == users[1]['id'] else bob
    old = task
    task = cmd(lead,old,'submit',key='same',text='Done',pr_links=[LINK])
    assert cmd(lead,old,'submit',key='same',text='Done',pr_links=[LINK]) == task
    cmd(lead,old,'submit',key='same',text='Changed',pr_links=[LINK],expected=409)
    withdrawn = cmd(lead,task,'withdraw',reason='Correcting')
    cmd(owner,task,'complete',expected=409)
    assert withdrawn['status'] == 'in_progress'


@pytest.mark.parametrize('link',['javascript:alert(1)','http://github.com/a/b/pull/1','https://secret@github.com/a/b/pull/1','https://github.com/a/b/pull/0','https://github.com/a/b/pull/1?token=secret'])
def test_links_validated_without_fetching(env,link):
    _, _, alice, _, _ = env
    cmd(alice,working(env),'submit',text='Done',pr_links=[link],expected=422)


def test_submission_requires_both_fields_and_manual_confirmation(env):
    _, owner, alice, _, _ = env
    task = working(env)
    cmd(alice,task,'submit',text='Done',expected=422)
    cmd(alice,task,'submit',pr_links=[LINK],expected=422)
    cmd(owner,task,'complete',expected=409)
    task = cmd(alice,task,'submit',text='Done',pr_links=[LINK,LINK])
    assert task['deliveries'][0]['pr_links'] == [LINK]
    assert owner.get(P + '/tasks/' + task['id']).json()['status'] == 'in_review'


def test_month_copy_transfer_and_history(env):
    _, owner, alice, bob, users = env
    task = cmd(bob,working(env),'join')
    task = cmd(alice,task,'owner-transfer',target_id=users[2]['id'])
    assert task['lead_id'] == users[1]['id']
    task = cmd(bob,task,'accept-transfer')
    assert task['lead_id'] == users[2]['id']
    cmd(bob,task,'leave',expected=422)
    copy = cmd(owner,task,'copy',month='2026-11')
    assert not copy['members'] and not copy['deliveries'] and copy['status'] == 'draft'
    assert alice.get(P + '/tasks?month=2026-11').json()['items'] == []


def test_private_draft_and_audit_visibility(env):
    app, owner, alice, _, _ = env
    draft = create(owner)
    assert alice.get(P + '/tasks/' + draft['id']).status_code == 404
    actor = app.state.auth.authenticate(alice.cookies.get('tc_session'))
    assert not any(e['task_id'] == draft['id'] for e in app.state.tasks.events(actor))
    assert owner.patch(P + '/tasks/' + draft['id'],json={'status':'done'}).status_code == 422


def test_session_idle_logout_and_body_limit(env):
    app, owner, alice, _, _ = env
    with app.state.db.transaction() as db:
        db.get(Session,digest(alice.cookies.get('tc_session'))).last_seen = now()-86401
    assert alice.get(P + '/me').status_code == 401
    assert owner.post(P + '/tasks', content='x'*(128*1024+1)).status_code == 413
    assert owner.post(P + '/auth/logout').status_code == 200
    assert owner.get(P + '/me').status_code == 401


def test_reopen_preserves_review_history(env):
    _, owner, alice, _, users = env
    task = cmd(alice,working(env),'submit',text='Done',pr_links=[LINK])
    task = cmd(owner,task,'complete')
    task = cmd(owner,task,'reopen',reason='New work')
    assert task['deliveries'][0]['state'] == 'accepted'
    assert task['deliveries'][0]['reviewed_by'] == users[0]['id']
    cmd(owner,task,'complete',expected=409)
