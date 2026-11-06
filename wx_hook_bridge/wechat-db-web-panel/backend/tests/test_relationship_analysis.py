import asyncio
import io
import json
import sqlite3
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app.active_roster import ActiveRosterError
from backend.app.relationship_analysis import analyze, create_relationship_router, export_zip, render_html


@pytest.fixture
def source():
    db=sqlite3.connect(':memory:')
    db.executescript('''
        CREATE TABLE contact(id INTEGER,username TEXT,nick_name TEXT,remark TEXT,alias TEXT,local_type INTEGER,delete_flag INTEGER,is_in_chat_room INTEGER,flag INTEGER);
        CREATE TABLE name2id(username TEXT);
        CREATE TABLE chatroom_member(room_id INTEGER,member_id INTEGER);
        CREATE TABLE chat_room(username TEXT,owner TEXT);
        INSERT INTO contact VALUES
        (91,'self','本人','','',1,0,0,1),
        (92,'alice','同名','','',1,0,0,1),
        (93,'bob','同名','','',3,0,0,0),
        (94,'carl','=1+2','','',1,0,0,1),
        (95,'a@chatroom','A','','',1,0,1,3),
        (96,'b@chatroom','B','','',2,0,1,2),
        (97,'empty@chatroom','空群','','',2,0,1,2),
        (98,'left@chatroom','已退','','',2,0,2,2),
        (99,'deleted@chatroom','已删','','',2,1,1,2);
        INSERT INTO name2id VALUES('self'),('alice'),('bob'),('carl'),('a@chatroom'),('b@chatroom'),('left@chatroom');
        INSERT INTO chatroom_member VALUES(5,1),(5,2),(5,2),(5,3),(6,1),(6,2),(6,4),(7,1),(7,2);
        INSERT INTO chat_room VALUES('a@chatroom','self');
    ''')
    yield db
    db.close()


def test_group_membership_uses_name2id_not_contact_id_and_includes_saved_groups(source):
    result=analyze(source,'self',{})
    assert result['meta']['stats']['groups']==3
    assert result['meta']['stats']['historical_groups']==2
    assert result['meta']['stats']['memberships_including_self']==6
    assert {g['id'] for g in result['groups']}=={'a@chatroom','b@chatroom','empty@chatroom'}
    assert result['meta']['stats']['groups_missing_self']==1
    assert result['meta']['stats']['unique_peers']==3
    # Distinct IDs with the same name remain distinct; duplicate rows deduplicate.
    assert len([p for p in result['people'] if p['name']=='同名'])==2
    assert len(result['edges'])==1
    edge=result['edges'][0]
    assert edge['n']==1 and edge['j']==pytest.approx(1/3)
    assert result['people'][edge['shared'][0]]['id']=='alice'
    assert all(p['id']!='self' for p in result['people'])


def test_unresolved_member_ids_fail_closed(source):
    source.execute('INSERT INTO chatroom_member VALUES(5,2000)')
    with pytest.raises(ActiveRosterError,match='ID'):
        analyze(source,'self',{})


def test_empty_account_roster_returns_no_invented_intersections(source):
    source.execute('UPDATE contact SET is_in_chat_room=0 WHERE username LIKE ?',['%@chatroom'])
    result=analyze(source,'self',{})
    assert result['groups']==result['people']==result['edges']==[]
    assert result['meta']['stats']['unique_peers']==0


def test_html_and_csv_escaping_and_zip_readback(source):
    source.execute('UPDATE contact SET nick_name=? WHERE username=?',['</script><script>alert(1)</script>','alice'])
    data=analyze(source,'self',{'acquired_local':'fixture'})
    html=render_html(data)
    assert '</script><script>alert(1)</script>' not in html
    assert '\\u003c/script>' in html
    with zipfile.ZipFile(io.BytesIO(export_zip(data))) as archive:
        assert archive.testzip() is None
        exported=json.loads(archive.read('wechat_groups.json'))
        assert exported['meta']['account']=='self'
        assert "'=1+2" in archive.read('群成员.csv').decode('utf-8-sig')
        assert len(exported['edges'])==1


def test_api_rejects_other_account_before_any_roster_access():
    calls=[]
    async def check(account,force=False):
        calls.append(account)
        if account!='self':raise HTTPException(409,'账号已切换')
    service=SimpleNamespace(check_account=check,roster=None)
    app=FastAPI()
    app.include_router(create_relationship_router(service))
    with TestClient(app) as client:
        for path in ('/relationships','/relationships/view','/relationships/export'):
            assert client.get(path,params={'account':'other'}).status_code==409
        assert client.get('/relationships',params={'account':'self'}).status_code==503
    assert calls==['other','other','other','self']


def test_cached_result_is_rechecked_and_cannot_survive_account_switch(source):
    from backend.app.relationship_analysis import RelationshipAnalysis
    switched=False
    async def check(account,force=False):
        if switched:raise HTTPException(409,'账号已切换')
    async def verify(account):
        return {'account':account,'source':'source','pid':123,'created_at':1.0}
    loader=SimpleNamespace(_source_fingerprint=lambda p:('source',1,None))
    service=SimpleNamespace(check_account=check,roster=SimpleNamespace(verify_async=verify,loader=loader))
    analysis=RelationshipAnalysis(service)
    data=analyze(source,'self',{})
    analysis.cached={'binding':{'account':'self','source':'source','pid':123,'created_at':1.0},'fingerprint':['source',1,None],'data':data}
    import time
    analysis.cached_at=time.monotonic()
    assert asyncio.run(analysis.read('self'))==data
    switched=True
    with pytest.raises(HTTPException) as error:
        asyncio.run(analysis.read('self'))
    assert error.value.status_code==409
