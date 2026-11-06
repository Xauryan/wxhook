"""Account-bound, read-only group membership analysis and offline exports."""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import itertools
import json
import sqlite3
import sys
import tempfile
import time
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, Response

from .active_roster import ActiveWechatRoster, ActiveRosterError

TEMPLATE = Path(__file__).with_name('relationship_view.html')


def analyze(db: sqlite3.Connection, account: str, meta: dict) -> dict:
    db.row_factory = sqlite3.Row
    contacts = {r['username']: dict(r) for r in db.execute(
        'SELECT username,nick_name,remark,alias,local_type,delete_flag,is_in_chat_room,flag FROM contact')}
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    strangers = {r['username']: dict(r) for r in db.execute('SELECT username,nick_name,remark,alias FROM stranger')} if 'stranger' in tables else {}
    owners = dict(db.execute('SELECT username,owner FROM chat_room')) if 'chat_room' in tables else {}
    memberships: dict[str, set[str]] = defaultdict(set)
    unresolved = 0
    for room, member in db.execute('''SELECT r.username,u.username FROM chatroom_member m
        LEFT JOIN name2id r ON r.rowid=m.room_id LEFT JOIN name2id u ON u.rowid=m.member_id'''):
        if room is None or member is None:
            unresolved += 1
        else:
            memberships[room].add(member)
    if unresolved:
        raise ActiveRosterError('群成员 ID 关联不完整，无法生成可靠交集')

    def person(uid):
        c = contacts.get(uid) or strangers.get(uid) or {}
        name = c.get('remark') or c.get('nick_name') or c.get('alias') or uid
        return {'id': uid, 'name': name, 'nickname': c.get('nick_name') or '', 'remark': c.get('remark') or '',
                'friend': bool(c.get('local_type') == 1 and (c.get('flag') or 0) & 1 and not c.get('delete_flag') and not uid.endswith('@chatroom')),
                'name_known': name != uid}

    all_groups = []
    for uid,c in contacts.items():
        if not uid.endswith('@chatroom'):
            continue
        all_groups.append({'id':uid, 'name':c['remark'] or c['nick_name'] or c['alias'] or uid,
                           'nickname':c['nick_name'] or '', 'remark':c['remark'] or '',
                           'owner_id':owners.get(uid) or '', 'owner_name':person(owners[uid])['name'] if owners.get(uid) else '',
                           'current':not c['delete_flag'] and c['is_in_chat_room']==1,
                           'local_type':c['local_type'], 'is_in_chat_room':c['is_in_chat_room'],
                           'local_member_count':len(memberships[uid]), 'self_in_local_members':account in memberships[uid]})
    all_groups.sort(key=lambda g:(not g['current'],-g['local_member_count'],g['id']))
    groups=[dict(g) for g in all_groups if g['current']]
    ids=sorted(set().union(*(memberships[g['id']] for g in groups)) - {account}) if groups else []
    people=[person(uid) for uid in ids]
    index={uid:i for i,uid in enumerate(ids)}
    member_groups=defaultdict(list)
    for gi,g in enumerate(groups):
        g.update(index=gi, members=sorted(index[uid] for uid in memberships[g['id']] if uid!=account))
        g['peers']=len(g['members'])
        g['friend_count']=sum(people[i]['friend'] for i in g['members'])
        g['unnamed_count']=sum(not people[i]['name_known'] for i in g['members'])
        for pi in g['members']:
            member_groups[pi].append(gi)
    shared=defaultdict(list)
    for pi,p in enumerate(people):
        p['groups']=member_groups[pi]
        for a,b in itertools.combinations(p['groups'],2):
            shared[a,b].append(pi)
    edges=[]
    for (a,b),ps in shared.items():
        n=len(ps)
        union=groups[a]['peers']+groups[b]['peers']-n
        edges.append({'a':a,'b':b,'n':n,'j':n/union if union else 0,'shared':ps})
    edges.sort(key=lambda e:(-e['n'],-e['j'],e['a'],e['b']))
    degree=[0]*len(groups)
    for e in edges:
        degree[e['a']]+=1
        degree[e['b']]+=1
    for gi,g in enumerate(groups):
        g['shared_peers']=sum(len(people[i]['groups'])>1 for i in g['members'])
        g['linked_groups']=degree[gi]
    bridges=sorted((i for i,p in enumerate(people) if len(p['groups'])>1),key=lambda i:(-len(people[i]['groups']),people[i]['name'],people[i]['id']))
    stats={'groups':len(groups),'historical_groups':len(all_groups)-len(groups),
           'memberships_including_self':sum(g['local_member_count'] for g in groups),
           'unique_peers':len(people),'cross_group_peers':len(bridges),'edges':len(edges),
           'unnamed_peers':sum(not p['name_known'] for p in people),'unresolved_member_ids':unresolved,
           'groups_missing_self':sum(not g['self_in_local_members'] for g in groups),
           'self_excluded':True,'id_key':'WeChat username','members_complete_on_server':'unverified'}
    metadata={**meta,'account':account,'stats':stats,
              'definitions':{'current_groups':'username ends with @chatroom, delete_flag=0, is_in_chat_room=1; includes all local_type values',
                             'membership_join':'chatroom_member -> name2id.rowid -> username',
                             'intersection':'unique usernames present in both local member sets, excluding the active account',
                             'jaccard':'intersection / union, active account excluded',
                             'completeness':'Complete export of local records. Server membership completeness is unverified.'}}
    return {'meta':metadata,'groups':groups,'people':people,'edges':edges,'bridges':bridges,'all_groups':all_groups}


def read_active(roster: ActiveWechatRoster, account: str) -> dict:
    before=roster.verify(account,force=True)
    source=before['source']
    fingerprint=roster.loader._source_fingerprint(source)
    tool=roster.loader._load_decrypt_tool()
    manifest=tool.load_manifest(roster.key_manifest)
    root=roster.cache_root / 'relationship-analysis'
    root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='read-',dir=root) as directory:
        output=Path(directory)/'contact.sqlite'
        result=tool.decrypt_database(source,output,manifest,force=False,ignore_wal=True)
        wal=result['wal']
        roster.loader._validate_wal_status(wal)
        if wal.get('commit_frames'):
            roster.loader._apply_encrypted_wal(source,output,manifest,wal,tool)
        if fingerprint != roster.loader._source_fingerprint(source):
            raise ActiveRosterError('通讯录正在更新，请重新分析')
        db=sqlite3.connect(output.as_uri()+'?mode=ro&immutable=1',uri=True)
        try:
            db.execute('PRAGMA query_only=ON')
            if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':
                raise ActiveRosterError('当前通讯录完整性校验失败')
            acquired=datetime.now(timezone.utc)
            data=analyze(db,account,{'acquired_at':acquired.isoformat(),
                         'acquired_local':acquired.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S'),
                         'source':'active_contact_db','quick_check':'ok',
                         'snapshot_sha256':hashlib.sha256(output.read_bytes()).hexdigest(),
                         'wal_commit_frames':wal.get('commit_frames',0)})
        finally:
            db.close()
        if before!=roster.verify(account,force=True) or fingerprint!=roster.loader._source_fingerprint(source):
            raise ActiveRosterError('读取期间账号或通讯录已变化，请重新分析')
    return {'binding':{**before,'source':str(source)},'fingerprint':fingerprint,'data':data}


def render_html(data: dict) -> str:
    # A member nickname must never terminate the script tag or execute HTML.
    payload=json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    return TEMPLATE.read_text(encoding='utf-8').replace('__RELATIONSHIP_DATA__',payload)


def export_files(data: dict) -> dict[str,bytes]:
    groups,people,edges=data['groups'],data['people'],data['edges']
    def encode_csv(header,rows):
        stream=io.StringIO(newline='')
        writer=csv.writer(stream)
        writer.writerow(header)
        for row in rows:
            writer.writerow(["'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v for v in row])
        return stream.getvalue().encode('utf-8-sig')
    members=[]
    for g in groups:
        if g['self_in_local_members']:
            members.append([g['id'],g['name'],data['meta']['account'],'本人','','',False,True])
        for pi in g['members']:
            p=people[pi]
            members.append([g['id'],g['name'],p['id'],p['name'],p['nickname'],p['remark'],p['friend'],False])
    j=lambda x:json.dumps(x,ensure_ascii=False)
    return {
        '微信群关系图.html':render_html(data).encode('utf-8'),
        'wechat_groups.json':j(data).encode('utf-8'),
        '全部群.csv':encode_csv(['群ID','群名','当前已加入','本机成员数含本人','本人在成员表','群主ID','群主名称','local_type','is_in_chat_room'],
                             ([g['id'],g['name'],g['current'],g['local_member_count'],g['self_in_local_members'],g['owner_id'],g['owner_name'],g['local_type'],g['is_in_chat_room']] for g in data['all_groups'])),
        '群成员.csv':encode_csv(['群ID','群名','成员ID','显示名','昵称','备注','是否好友','是否本人'],members),
        '群交集.csv':encode_csv(['群A_ID','群A','群B_ID','群B','共同群友数不含本人','Jaccard','共同群友名称','共同群友ID'],
                             ([groups[e['a']]['id'],groups[e['a']]['name'],groups[e['b']]['id'],groups[e['b']]['name'],e['n'],round(e['j'],6),j([people[i]['name'] for i in e['shared']]),j([people[i]['id'] for i in e['shared']])] for e in edges)),
        '跨群群友.csv':encode_csv(['成员ID','显示名','共同加入群数','是否好友','群名称','群ID'],
                               ([people[i]['id'],people[i]['name'],len(people[i]['groups']),people[i]['friend'],j([groups[g]['name'] for g in people[i]['groups']]),j([groups[g]['id'] for g in people[i]['groups']])] for i in data['bridges'])),
        'source_manifest.json':json.dumps(data['meta'],ensure_ascii=False,indent=2).encode('utf-8'),
        '说明.txt':('当前群依据 is_in_chat_room=1 且未删除，包括所有 local_type。其他本地群记录保留在全部群.csv。\n'
                   '交集按微信 ID 去重，排除本人。成员通过 name2id.rowid 关联。\n'
                   '当前微信进程及账号已核验，源库及已提交 WAL 只读。完整性仅覆盖本机保存的成员数据，未验证服务器成员完整性。\n'
                   'CSV 为 UTF-8 BOM，疑似公式文本加前导单引号，JSON 保存原值。双击 HTML 可离线查看。\n').encode('utf-8-sig'),
    }


def export_zip(data: dict) -> bytes:
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,payload in export_files(data).items():
            archive.writestr(name,payload)
    return stream.getvalue()


class RelationshipAnalysis:
    def __init__(self,service):
        self.service=service
        self.lock=asyncio.Lock()
        self.cached=None
        self.cached_at=0.0

    async def read(self,account: str,refresh: bool=False) -> dict:
        await self.service.check_account(account,force=True)
        roster=self.service.roster
        if roster is None or not hasattr(roster,'verify_async'):
            raise HTTPException(503,'当前账号通讯录来源未配置')
        async with self.lock:
            try:
                binding=await roster.verify_async(account)
                binding={**binding,'source':str(binding['source'])}
                fingerprint=await asyncio.to_thread(roster.loader._source_fingerprint,Path(binding['source']))
                fingerprint=json.loads(json.dumps(fingerprint))
                cached=self.cached
                if not refresh and cached and time.monotonic()-self.cached_at<60 and cached['binding']==binding and cached['fingerprint']==fingerprint:
                    data=cached['data']
                else:
                    process=await asyncio.create_subprocess_exec(
                        sys.executable,'-m','backend.app.relationship_analysis',account,roster.bridge_url,
                        str(roster.repository_root),str(roster.key_manifest.resolve()),str(roster.cache_root.resolve()),
                        cwd=str(Path(__file__).resolve().parents[2]),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
                        creationflags=0x08000000 if sys.platform=='win32' else 0)
                    try:
                        stdout,_=await asyncio.wait_for(process.communicate(),90)
                    except BaseException:
                        if process.returncode is None:
                            process.kill()
                        await process.wait()
                        raise
                    if process.returncode or len(stdout)>64*1024*1024:
                        raise ActiveRosterError('当前微信通讯录读取失败，请重新分析')
                    result=json.loads(stdout)
                    if result['binding']!=binding or result['data']['meta']['account']!=account:
                        raise ActiveRosterError('读取期间微信账号或进程已切换')
                    data=result['data']
                    self.cached=result
                    self.cached_at=time.monotonic()
                await self.service.check_account(account,force=True)
                return data
            except (ActiveRosterError,OSError,ValueError,KeyError,asyncio.TimeoutError) as exc:
                raise HTTPException(503,'当前微信账号或通讯录核验失败，请恢复连接后重新分析') from exc


def create_relationship_router(service) -> APIRouter:
    router=APIRouter(prefix='/relationships')
    analysis=RelationshipAnalysis(service)
    headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}

    @router.get('')
    async def get_analysis(account: str=Query(min_length=1,max_length=240),refresh: bool=False):
        return Response(json.dumps(await analysis.read(account,refresh),ensure_ascii=False),media_type='application/json',headers=headers)

    @router.get('/view')
    async def view(account: str=Query(min_length=1,max_length=240),refresh: bool=False):
        data=await analysis.read(account,refresh)
        html=await asyncio.to_thread(render_html,data)
        return HTMLResponse(html,headers={**headers,'Content-Security-Policy':"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; base-uri 'none'; form-action 'none'"})

    @router.get('/export')
    async def export(account: str=Query(min_length=1,max_length=240)):
        data=await analysis.read(account)
        payload=await asyncio.to_thread(export_zip,data)
        await service.check_account(account,force=True)
        return Response(payload,media_type='application/zip',headers={**headers,'Content-Disposition':'attachment; filename="wechat-relationships.zip"'})
    return router


if __name__=='__main__':
    try:
        account,bridge,repository,manifest,cache=sys.argv[1:]
        reader=ActiveWechatRoster(bridge,Path(repository),Path(manifest),Path(cache))
        result=read_active(reader,account)
        sys.stdout.buffer.write(json.dumps(result,ensure_ascii=False).encode('utf-8'))
    except Exception:
        sys.stderr.write('Verified relationship analysis failed.\n')
        sys.exit(1)
