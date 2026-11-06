from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select
from app.db import Project, User
from tests.conftest import P, ORIGIN


def test_email_only_entry_and_case_normalization(env):
    app, owner, alice, _, users = env
    assert alice.post(P+'/auth/identify',json={'email':'  ALICE@EXAMPLE.TEST  '}).status_code == 200
    me = alice.get(P+'/me').json()
    assert me['id'] == users[1]['id'] and me['name'] == 'alice@example.test'
    assert not me['owner']
    with app.state.db.transaction() as db:
        assert db.get(User,me['id']).password_hash is None
    assert owner.post(P+'/invites',json={}).status_code == 404
    assert alice.post(P+'/auth/login',json={}).status_code == 404
    assert alice.post(P+'/auth/accept-invite',json={}).status_code == 404


@pytest.mark.parametrize('email',['','not-email','a@','a@domain','a..b@example.com','<script>@example.com','a@exam ple.com'])
def test_invalid_email(env,email):
    _, _, alice, _, _ = env
    assert alice.post(P+'/auth/identify',json={'email':email}).status_code == 422


def test_self_declared_email_never_grants_owner(env):
    app, owner, alice, _, users = env
    assert alice.post(P+'/auth/identify',json={'email':'owner@example.test'}).status_code == 200
    assert alice.get(P+'/me').json()['owner'] is False
    with app.state.db.transaction() as db:
        db.get(User,users[0]['id']).git_email = 'actual-owner@example.test'
    assert alice.post(P+'/auth/identify',json={'email':'actual-owner@example.test'}).status_code == 403
    assert owner.get(P+'/me').json()['owner'] is True


def test_email_concurrency_reuses_one_identity(env):
    app, _, _, _, _ = env
    with ThreadPoolExecutor(2) as pool:
        tokens = list(pool.map(app.state.auth.identify,['new@example.test','NEW@EXAMPLE.TEST']))
    assert tokens[0] != tokens[1]
    actors = [app.state.auth.authenticate(t) for t in tokens]
    assert actors[0].id == actors[1].id
    assert not any(a.owner for a in actors)
    with app.state.db.transaction() as db:
        assert len(db.scalars(select(User).where(User.git_email == 'new@example.test')).all()) == 1


def test_source_check_and_extra_role_rejected(env):
    _, _, alice, _, _ = env
    assert alice.post(P+'/auth/identify',json={'email':'test@example.test'},headers={'Origin':'https://evil.test'}).status_code == 403
    assert alice.post(P+'/auth/identify',json={'email':'test@example.test','owner':True},headers={'Origin':ORIGIN}).status_code == 422


def test_no_implicit_owner_when_not_initialized(env):
    app, _, _, _, _ = env
    with app.state.db.transaction() as db:
        db.get(Project,1).owner_user_id = None
    actor = app.state.auth.authenticate(app.state.auth.identify('first@example.test'))
    assert not actor.owner
