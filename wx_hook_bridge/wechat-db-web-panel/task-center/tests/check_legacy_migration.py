"""One-shot upgrade rehearsal against an explicitly empty, disposable local database."""
import json
import os
from urllib.parse import urlsplit

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


if __name__ == '__main__':
    url = os.environ['TASK_CENTER_DATABASE_URL']
    target = urlsplit(url)
    assert target.hostname in ('127.0.0.1','localhost') and target.port == 55439
    assert target.path == '/task_center_migration_test'
    engine = create_engine(url)
    with engine.connect() as db:
        assert db.scalar(text("SELECT COUNT(*) FROM pg_tables WHERE schemaname='public'")) == 0
    config = Config('alembic.ini')
    command.upgrade(config,'fc531d1e9b61')
    original = {'members':{'legacy-user':{'active':True}},'work_items':[],'comments':[],
        'objective':'Keep original objective','criteria':'Keep original criteria','priority':'medium',
        'deadline':'2026-10-31T18:00:00+08:00','scope_revision':1,'transfer':None,
        'pulls':[{'number':12,'url':'https://github.com/example/private/pull/12','head':'old-sha'}],
        'submissions':[{'id':'legacy-result','state':'active','author':'legacy-user','summary':'Original delivery','at':100,'required':[12]}]}
    with engine.begin() as db:
        db.execute(text("INSERT INTO tc_users (id,github_id,login,name,disabled,access_until) VALUES ('legacy-user',123,'legacy','Historical member',false,0)"))
        db.execute(text('INSERT INTO tc_project (id,repository_id,owner_github_id,installation_id,sequence) VALUES (1,10,123,11,5)'))
        db.execute(text("INSERT INTO tc_tasks (id,number,title,month,status,lead_id,version,created_at,updated_at,data) VALUES ('legacy-task',1,'Keep task','2026-10','in_review','legacy-user',3,100,100,CAST(:data AS jsonb))"), {'data':json.dumps(original)})
        db.execute(text("INSERT INTO tc_events (id,task_id,actor,action,at,owner_only,detail) VALUES (5,'legacy-task','legacy-user','task.submit',100,false,'{}')"))
        db.execute(text("INSERT INTO tc_sessions (token_hash,user_id,csrf,expires,last_seen) VALUES ('legacy-token','legacy-user','old-csrf',2000000000,100)"))
    command.upgrade(config,'head')
    with engine.connect() as db:
        row = db.execute(text("SELECT * FROM tc_tasks WHERE id='legacy-task'")).mappings().one()
        assert row['status'] == 'in_review' and row['version'] == 3
        assert row['data']['submissions'] == original['submissions'] and row['data']['pulls'] == original['pulls']
        assert row['data']['deliveries'][0]['summary'] == 'Original delivery'
        assert row['data']['deliveries'][0]['pr_links'] == [original['pulls'][0]['url']]
        assert db.scalar(text('SELECT COUNT(*) FROM tc_events')) == 1
        assert db.scalar(text('SELECT owner_user_id FROM tc_project')) is None
        assert db.scalar(text('SELECT password_hash FROM tc_users')) is None
        assert db.scalar(text('SELECT COUNT(*) FROM tc_sessions')) == 1
    command.check(config)
    print('Legacy migration passed: task, source JSON, audit and identity retained; no automatic owner/account conversion.')
