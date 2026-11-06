from contextlib import contextmanager
from dataclasses import asdict
import io
import json
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace
import zipfile

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from openpyxl import load_workbook
import pytest

from backend.app.analysis_snapshots import atomic_json, read_json
from backend.app.archive_insights import ArchiveInsights, create_insights_router
from backend.app.archive_exports import ArchiveExports, ExportRequest, ExportAutomation, create_exports_router
from backend.app.archive_local import ArchiveLocal, LocalEdit, NotificationConfig, create_local_router
from backend.app import archive_export_worker as worker
from backend.app.config import Settings
from backend.app.repository import WeChatRepository


def insert(db, table, values):
    for col in db.execute(f'PRAGMA table_info({table})').fetchall():
        if col[3] and col[4] is None and col[1] not in values:
            values[col[1]] = 0 if col[2] in {'INTEGER', 'REAL'} else ''
    db.execute(f'INSERT INTO {table} ({",".join(values)}) VALUES ({",".join("?" for _ in values)})', list(values.values()))


@pytest.fixture
def services(tmp_path, monkeypatch):
    settings = Settings(panel_root=tmp_path/'panel', repository_root=tmp_path,
        database_root=tmp_path/'source', report_path=tmp_path/'source/report.json',
        cache_path=tmp_path/'panel/.cache/analytics.sqlite', fts_extension=tmp_path/'unused.dll', weixin_dll=tmp_path/'unused.dll')
    settings.cache_path.parent.mkdir(parents=True)
    with sqlite3.connect(settings.cache_path) as db:
        WeChatRepository._create_cache_schema(None, db)
        for username, group in [('friend',0),('group@chatroom',1)]:
            insert(db,'sessions',dict(username=username,display_name=username,is_group=group,message_count=4))
            insert(db,'contacts',dict(username=username,display_name=username,local_type=2 if group else 1,is_group=group,delete_flag=0))
        for index, (day, session, own, body) in enumerate([
            ('2025-12-31','friend',1,'old'),('2026-01-01','friend',0,'你好'),('2026-01-02','friend',1,'你好'),
            ('2026-01-04','friend',0,'=1+1'),('2026-01-04','group@chatroom',0,'<script>alert(1)</script>'),
            ('2026-01-04','group@chatroom',1,"O'Reilly"),('2026-02-01','group@chatroom',0,'长'*33000)]):
            insert(db,'messages',dict(message_key=f'm{index}',session_username=session,session_name=session,local_id=index,
                server_id=9223372036854775800+index,sender_username='self' if own else 'friend',sender_name='自己' if own else '朋友',
                is_self=own,create_time=1767200000+index,local_date=day,local_hour=10,category='text',content=body,summary=body[:40]))
    native = settings.database_root/'contact/contact.db'
    native.parent.mkdir(parents=True)
    with sqlite3.connect(native) as db:
        db.executescript("CREATE TABLE contact(username,flag,local_type,delete_flag); INSERT INTO contact VALUES ('friend',1,1,0),('group@chatroom',0,2,0)")
    class Repo:
        account='self'
        snapshot_time='2026-02-01T00:00:00Z'
        database_root=settings.database_root
        configured=True
        def reload_configuration_if_available(self): pass
        def ensure_cache(self): return settings.cache_path
        def database(self, relative): return settings.database_root/relative
        @contextmanager
        def cache(self):
            db=sqlite3.connect(settings.cache_path.as_uri()+'?mode=ro',uri=True)
            db.row_factory=sqlite3.Row
            try: yield db
            finally: db.close()
    repo=Repo()
    repo.settings=settings
    class Live:
        account='self'
        async def profile(self, force=False): return {'username':self.account}
    live=Live()
    insights=ArchiveInsights(repo,isolate=False)
    exports=ArchiveExports(insights,live)
    local=ArchiveLocal(insights,live)
    monkeypatch.setattr(worker,'WeChatRepository',lambda settings:repo)
    return SimpleNamespace(repo=repo,insights=insights,exports=exports,local=local,live=live,scope=insights.identity()['scope'])


def test_reports_math_filters_and_snapshot_fence(services):
    s=services
    annual=s.insights.report('self',s.scope,'annual',2026)
    assert annual['totals']['message_count']==6
    assert annual['totals']['sent_count']==2
    assert annual['totals']['received_count']==4
    assert annual['longest_streak']=={'days':2,'start':'2026-01-01','end':'2026-01-02'}
    assert annual['peak_day']['label']=='2026-01-04'
    assert annual['phrases'][0]=={'text':'你好','count':2}
    assert s.insights.report('self',s.scope,'dual',2026,'friend')['totals']['message_count']==3
    assert len(s.insights.report('self',s.scope,'dual',2026,'friend')['first_messages'])==3
    assert s.insights.report('self',s.scope,'group',2026,'group@chatroom')['totals']['observed_speakers']==2
    assert s.insights.report('self',s.scope,'footprint',0)['totals']['message_count']==3
    with pytest.raises(HTTPException): s.insights.report('self',s.scope,'group',2026,'friend')
    s.repo.snapshot_time='new'
    with pytest.raises(HTTPException) as error: s.insights.report('self',s.scope,'annual',2026)
    assert error.value.status_code==409


def run_export(s, fmt, **extra):
    from uuid import uuid4
    payload=ExportRequest(account='self',scope=s.scope,request_id=uuid4().hex,format=fmt,sessions=['friend','group@chatroom'],**extra)
    directory=s.exports.root/payload.request_id
    directory.mkdir(parents=True)
    atomic_json(directory/'request.json', {'payload':payload.model_dump(),'identity':s.insights.identity(),
        'cache':str(s.repo.settings.cache_path),'settings':{k:str(v) if isinstance(v,Path) else v for k,v in asdict(s.repo.settings).items()}})
    atomic_json(directory/'state.json',dict(id=payload.request_id,account='self',scope=s.scope,source=payload.source,format=fmt,
        status='running',rows=0,started_at=time.time(),updated_at=time.time()))
    worker.main(directory)
    state=read_json(directory/'state.json')
    assert state['status']=='succeeded',state
    return directory,zipfile.ZipFile(directory/'archive.zip')


@pytest.mark.parametrize('fmt',['json','csv','html','txt','markdown','xlsx','pgsql','chatlab','chatlab-jsonl'])
def test_exports_roundtrip_and_safety(services,fmt):
    s=services
    before=s.repo.settings.cache_path.read_bytes()
    directory,bundle=run_export(s,fmt)
    with bundle:
        manifest=json.loads(bundle.read('manifest.json'))
        assert manifest['rows']==7
        assert len(manifest['files'])==2
        assert all(len(f['sha256'])==64 for f in manifest['files'])
        outputs=[bundle.read(f['file']) for f in manifest['files']]
        merged=b'\n'.join(outputs)
        if fmt=='json':
            rows=[r for output in outputs for r in json.loads(output)]
            assert len(rows)==7
            assert rows[0]['server_id']=='9223372036854775800'
        if fmt=='csv': assert b"'=1+1" in merged
        if fmt=='html': assert b'<script>' not in merged and b'&lt;script&gt;' in merged
        if fmt=='markdown': assert b'<script>' not in merged
        if fmt=='pgsql': assert b"O''Reilly" in merged
        if fmt=='chatlab':
            for output in outputs:
                value=json.loads(output)
                assert value['chatlab']['version']=='0.0.2'
                members={r['platformId'] for r in value['members']}
                assert all(r['sender'] in members for r in value['messages'])
        if fmt=='chatlab-jsonl':
            assert all(json.loads(output.splitlines()[0])['_type']=='header' for output in outputs)
        if fmt=='xlsx':
            values=[]
            for output in outputs:
                book=load_workbook(io.BytesIO(output),read_only=True)
                values += [c.value for row in book.active for c in row]
                assert all(c.data_type!='f' for row in book.active for c in row)
                book.close()
            assert '=1+1' in values
            assert manifest['xlsx_adjusted_rows']==1
            assert len(json.loads(bundle.read('full-text.jsonl'))['content'])==33000
    assert before==s.repo.settings.cache_path.read_bytes()
    assert not list(directory.glob('records-*'))
    assert s.exports.download(directory.name,'self',s.scope).exists()
    s.repo.account='other'
    with pytest.raises(HTTPException):s.exports.download(directory.name,'self',s.scope)


def test_contact_friend_export_uses_native_flags(services):
    _,bundle=run_export(services,'json',source='contacts',contact_kind='friends')
    with bundle:
        manifest=json.loads(bundle.read('manifest.json'))
        rows=json.loads(bundle.read(manifest['files'][0]['file']))
        assert [r['username'] for r in rows]==['friend']
        assert rows[0]['flag']==1


def test_local_edit_cas_originals_and_export_optin(services):
    s=services
    row=s.local.preview('self',s.scope,'friend')['messages'][0]
    payload=LocalEdit(account='self',scope=s.scope,message_key=row['message_key'],original_hash=row['original_hash'],revision=0,text='本地修订',note='测试')
    assert s.local.edit(payload)=={'revision':1}
    with pytest.raises(HTTPException):s.local.edit(payload)
    _,bundle=run_export(s,'json',use_local_edits=True)
    with bundle:
        manifest=json.loads(bundle.read('manifest.json'))
        rows=[r for f in manifest['files'] for r in json.loads(bundle.read(f['file']))]
        edited=next(r for r in rows if r['message_key']==row['message_key'])
        assert edited['content']=='本地修订' and edited['original_content']==row['content']
        assert manifest['local_edits_applied']==1
    with s.repo.cache() as db:
        assert db.execute('SELECT content FROM messages WHERE message_key=?',(row['message_key'],)).fetchone()[0]==row['content']


@pytest.mark.asyncio
async def test_notification_optin_dedup_filters_and_account(services):
    s=services
    assert s.local.enabled() is False
    message={'is_self':0,'create_time':time.time(),'category':'text','session_username':'friend','content':'private'}
    s.local.observe('self','e1',message)
    assert not (await s.local.poll('self',0))['events']
    value=NotificationConfig(account='self',revision=0,enabled=True,mode='allowlist',sessions=['friend'])
    await s.local.configure(value)
    assert s.local.enabled() is True
    with pytest.raises(HTTPException):await s.local.configure(value)
    s.local.observe('self','e1',message)
    s.local.observe('self','e1',message)
    s.local.observe('self','e2',{**message,'is_self':1})
    s.local.observe('self','e3',{**message,'create_time':time.time()-300})
    s.local.observe('self','e4',{**message,'session_username':'blocked'})
    assert not (await s.local.poll('self',None))['events']
    rows=(await s.local.poll('self',0))['events']
    assert len(rows)==1 and rows[0]['body']=='收到一条新消息'
    s.live.account='other'
    with pytest.raises(HTTPException):await s.local.poll('self',0)


@pytest.mark.asyncio
async def test_automatic_export_account_due_and_disable_gates(services,monkeypatch):
    s=services
    payload=ExportRequest(account='self',scope=s.scope,request_id='a'*32,sessions=['friend'])
    config=ExportAutomation(revision=0,enabled=True,interval_minutes=60,template=payload)
    await s.exports.configure(config)
    created=[]
    monkeypatch.setattr(s.exports,'create',lambda *args:created.append(args))
    await s.exports.tick()
    assert not created
    saved=s.exports.config();saved['last_attempt_at']=0
    atomic_json(s.exports.root/'automation.json',saved)
    s.live.account='other'
    await s.exports.tick();assert not created
    s.live.account='self'
    await s.exports.tick();assert len(created)==1 and created[0][1]=='scheduled'
    with pytest.raises(HTTPException):await s.exports.configure(config)


def test_routes_reject_remote_writes_bad_selection_and_cross_account(services,monkeypatch):
    s=services
    app=FastAPI()
    app.include_router(create_insights_router(s.insights));app.include_router(create_exports_router(s.exports));app.include_router(create_local_router(s.local))
    client=TestClient(app)
    assert client.get('/api/archive/notifications/enabled').json()=={'enabled':False}
    assert client.get('/api/archive/catalog').json()['total_sessions']==2
    assert client.get('/api/archive/report',params=dict(account='other',scope=s.scope)).status_code==409
    body=dict(account='self',scope=s.scope,request_id='a'*32,sessions=['friend'])
    assert client.post('/api/archive/exports',json=body,headers={'Origin':'https://evil.test'}).status_code==403
    assert client.post('/api/archive/exports',json={**body,'sessions':[]}).status_code==422
    assert client.post('/api/archive/exports',json={**body,'start_date':'2026-04-20','end_date':'2026-04-01'}).status_code==422
    assert client.get('/api/archive/exports/bad/download',params=dict(account='self',scope=s.scope)).status_code==404


def test_cancelled_worker_never_publishes(services,monkeypatch):
    s=services
    original=worker.ExportWorker.run
    def cancel(self):
        atomic_json(self.directory/'cancel.json',{})
        original(self)
    monkeypatch.setattr(worker.ExportWorker,'run',cancel)
    with pytest.raises(AssertionError):run_export(s,'json')
    states=[read_json(p) for p in s.exports.root.glob('*/state.json')]
    assert states[0]['status']=='cancelled'
    assert not list(s.exports.root.glob('*/archive.zip'))


def test_live_observer_is_after_commit_and_does_not_repeat_or_break_ack(tmp_path):
    from backend.app.live_chat import LiveChat
    service=LiveChat(tmp_path/'live.sqlite','http://127.0.0.1:19088',tmp_path/'relay.json')
    calls=[]
    def observe(*values):
        # A separate connection proves that the chat transaction was committed first.
        with service.messages.db() as db:
            assert db.execute('SELECT count(*) FROM chat_envelopes').fetchone()[0] > 0
        calls.append(values)
        raise RuntimeError('notification failure')
    service.notification_observer=observe
    payload=dict(event_id='fresh1',self_id='self',message_id='10',local_id='1',message_type=1,
        timestamp_unix=int(time.time()),is_incoming=True,is_outgoing=False,direction='incoming',
        sender_id='friend',peer_id='friend',raw_from='friend',raw_to='self',content='new message')
    assert service.ingest(payload)['status']=='applied'
    assert service.ingest(payload)['duplicate'] is True
    assert len(calls)==1


def test_job_request_id_is_idempotent_and_overlapping_exports_are_rejected(services,monkeypatch):
    s=services
    launches=[]
    monkeypatch.setattr('backend.app.archive_exports.subprocess.Popen',lambda *args,**kwargs: launches.append(args) or SimpleNamespace(poll=lambda:0))
    request=ExportRequest(account='self',scope=s.scope,request_id='a'*32,sessions=['friend'])
    first=s.exports.create(request)
    assert s.exports.create(request)==first and len(launches)==1
    with pytest.raises(HTTPException):s.exports.create(request.model_copy(update={'format':'csv'}))
    with pytest.raises(HTTPException):s.exports.create(request.model_copy(update={'request_id':'b'*32}))
    assert s.exports.cancel(first['id'],'self',s.scope)['cancel_requested'] is True
    assert (s.exports.directory(first['id'])/'cancel.json').exists()


def test_worker_scope_change_fails_before_publication(services,monkeypatch):
    s=services
    original=worker.ExportWorker.write
    def write(self,*args):
        count=original(self,*args)
        s.repo.snapshot_time='changed'
        return count
    monkeypatch.setattr(worker.ExportWorker,'write',write)
    with pytest.raises(AssertionError):run_export(s,'json')
    states=[read_json(p) for p in s.exports.root.glob('*/state.json')]
    assert states[0]['status']=='failed'
    assert not list(s.exports.root.glob('*/archive.zip'))


def test_disabling_automation_revokes_a_pending_schedule(services):
    s=services
    request=ExportRequest(account='self',scope=s.scope,request_id='c'*32,sessions=['friend'])
    assert s.exports.create(request,'scheduled',0) is None
    assert not (s.exports.root/request.request_id).exists()


@pytest.mark.asyncio
async def test_disconnected_native_bridge_returns_explicit_retryable_error(services,monkeypatch):
    import httpx
    async def disconnected(**kwargs):raise httpx.ConnectError('unavailable')
    monkeypatch.setattr(services.live,'profile',disconnected)
    with pytest.raises(HTTPException) as error:await services.local.poll('self',0)
    assert error.value.status_code==503


def test_report_process_results_are_cached_and_scope_checked(services,monkeypatch):
    s=services
    s.insights.isolate=True
    runs=[]
    def run(command,**kwargs):
        request_path=Path(command[-1]);request=read_json(request_path)
        result=s.insights.report(**request['arguments'])
        atomic_json(request_path.with_name(request_path.name.replace('.request.json','.result.json')),result)
        runs.append(command)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr('backend.app.archive_insights.subprocess.run',run)
    arguments=dict(account='self',scope=s.scope,kind='annual',year=2026,session='')
    first=s.insights.isolated('report',arguments)
    assert first==s.insights.isolated('report',arguments)
    assert len(runs)==1
    assert not list((s.exports.root.parent/'archive-reports').glob('*.request.json'))
    s.repo.snapshot_time='different'
    with pytest.raises(HTTPException):s.insights.isolated('report',arguments)
