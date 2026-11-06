import html
import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app.chat_messages import ChatMessages, present_content
from backend.app.chat_presentation_repair import PresentationRepair
from backend.app.message_presentation import PRESENTATION_VERSION
from backend.app.parsing import parse_message_content
from backend.app.live_chat import LiveChat, create_live_router


def app(kind, fields=''):
    return f'<msg><appmsg><type>{kind}</type>{fields}</appmsg></msg>'


@pytest.mark.parametrize('base,tag', [(3,'img'),(47,'emoji')])
def test_media_reference_is_a_decoration_not_a_replacement(base,tag):
    body=f'<msg><{tag} md5="'+('a'*32)+'"/><extcommoninfo><refermsg><svrid>123</svrid></refermsg></extcommoninfo></msg>'
    parsed=parse_message_content(body,base,0,False)
    result=present_content(body,base,False)
    assert parsed.category==result['category']==('image'if base==3 else 'sticker')
    assert result['reference']['server_id']=='123'
    assert result['media_hints']==['a'*32]


def test_quoted_application_is_recursively_parsed_without_executing_markup():
    original=app(5,'<title>Original &lt;script&gt;</title><url>https://example.com/article</url>')
    result=present_content(app(57,f'<title>Reply</title><refermsg><type>49</type><content>{html.escape(original)}</content><svrid>999</svrid></refermsg>'),49,False)
    assert result['content']=='Reply'
    assert result['reference']['content']=='Original <script>'
    assert result['reference']['kind']=='link'
    assert result['reference']['url']=='https://example.com/article'


def test_quote_depth_is_bounded():
    body='end'
    for _ in range(10):
        body=app(57,f'<title>Reply</title><refermsg><type>49</type><content>{html.escape(body)}</content></refermsg>')
    assert present_content(body,49,False)['category']=='quote'


@pytest.mark.parametrize('sub,expected', [(33,'mini_program'),(36,'mini_program'),(44,'mini_program'),(109,'game'),(51,'finder_video'),(63,'finder_live'),(62,'pat'),(74,'file_notice'),(4,'shared_video'),(3,'music'),(8,'sticker'),(17,'location_notice'),(24,'note')])
def test_application_subtypes_get_their_own_presentation(sub,expected):
    result=present_content(app(sub,'<title>Visible title</title><weappinfo><pagepath>pages/example</pagepath></weappinfo>'),(sub<<32)|49,False)
    assert result['category']==expected
    assert result['title']=='Visible title'
    assert result['_presentation_version']==PRESENTATION_VERSION
    if expected in {'mini_program','game'}:
        assert result['mini_program']['page']=='pages/example'


def test_empty_mmreader_does_not_consume_a_game_card():
    result=present_content(app(109,'<title>Game</title><mmreader><category><topnew><title/></topnew></category></mmreader><wxgamecard/>'),49,False)
    assert result['category']=='game' and result['title']=='Game'
    assert 'articles'not in result


def test_announcement_prefers_plain_text_and_html_fallback_is_inert():
    result=present_content(app(87,'<textannouncement>First\nSecond &lt;b&gt;literal&lt;/b&gt;</textannouncement><announcement>ignored</announcement>'),49,False)
    assert result['category']=='announcement'
    assert result['content']=='First\nSecond <b>literal</b>'
    result=present_content(app(87,'<announcement><![CDATA[<div>Hello</div><script>alert(1)</script><p>World</p>]]></announcement>'),49,False)
    assert result['content']=='Hello\nWorld'


@pytest.mark.parametrize('kind,category',[(2000,'transfer'),(2001,'redpacket')])
def test_payment_fields_preserve_source_text_without_inventing_status(kind,category):
    result=present_content(app(kind,'<title>Payment</title><des>Source status</des><wcpayinfo><feedesc>￥12.34</feedesc><pay_memo>Memo</pay_memo><sendertitle>Greeting</sendertitle><paysubtype>3</paysubtype></wcpayinfo>'),49,False)
    assert result['category']==category
    assert result['payment']['amount']=='￥12.34'
    assert result['payment']['status']=='Source status'
    assert result['payment']['remark']==('Memo'if kind==2000 else 'Greeting')


def test_call_and_system_template_use_actual_human_fields():
    result=present_content('<voipmsg><VoIPBubbleMsg><msg>已取消</msg><duration>0</duration></VoIPBubbleMsg></voipmsg>',50,False)
    assert result['call']['text']=='已取消'
    assert result['call']['duration']==0
    result=present_content('<sysmsg type="sysmsgtemplate"><sysmsgtemplate><content_template><plain/><template>${actor}邀请${members}加入群聊</template><link_list><link name="actor"><memberlist><member><nickname>A</nickname></member></memberlist></link><link name="members"><memberlist><member><nickname>B</nickname></member><member><nickname>C</nickname></member></memberlist></link></link_list></content_template></sysmsgtemplate></sysmsg>',10000,False)
    assert result['content']=='A邀请B、C加入群聊'


def test_nested_records_preserve_attachment_type_name_and_order():
    record='<recordinfo><datalist><dataitem datatype="2"><sourcename>A</sourcename><fullmd5>'+('b'*32)+'</fullmd5></dataitem><dataitem datatype="8"><datatitle>doc.pdf</datatitle><datasize>42</datasize></dataitem><dataitem datatype="14"><recordinfo><datalist><dataitem datatype="1"><datadesc>nested</datadesc></dataitem></datalist></recordinfo></dataitem></datalist></recordinfo>'
    result=present_content(app(19,'<recorditem>'+html.escape(record)+'</recorditem>'),49,False)
    assert [r['kind']for r in result['records']]==['image','file','chat_history']
    assert result['records'][0]['content']=='[图片]'
    assert result['records'][1]['file_name']=='doc.pdf'
    assert result['records'][2]['children'][0]['content']=='nested'


def test_unknown_type_has_explicit_code_without_rendering_xml_or_incidental_url():
    result=present_content('<msg xmlns="http://example.com/internal"/>',66,False)
    assert result['type_label']=='特殊消息（66）'
    assert not result['content'] and not result['url']


def ledger(tmp_path,body,old=None):
    store=ChatMessages(tmp_path/'ledger.sqlite')
    value={'session_username':'peer','sender_username':'peer','server_id':'123','local_id':1,'create_time':1000,'is_self':False,'local_type':49,**(old or {'category':'app','content':''})}
    with store.db()as db:
        mid,_=store.upsert(db,'self',value,'event-one')
        db.execute('INSERT INTO chat_envelopes VALUES (?,?,?,?)',('self','event-one','digest',json.dumps({'self_id':'self','content':body})))
    return store,mid


def test_lazy_repair_is_account_scoped_idempotent_and_emits_revision(tmp_path):
    store,mid=ledger(tmp_path,app(87,'<textannouncement>Restored notice</textannouncement>'))
    before=store.revision('self')
    rows=store.page('self','peer')['messages']
    assert PresentationRepair(store).repair('other',rows)==0
    assert PresentationRepair(store).repair('self',rows)==1
    fixed=store.page('self','peer')['messages'][0]
    assert fixed['id']==str(mid)and fixed['category']=='announcement'and fixed['content']=='Restored notice'
    assert fixed['revision']>before
    assert PresentationRepair(store).repair('self',[fixed])==0


def test_repair_cannot_restore_a_recalled_message(tmp_path):
    store,mid=ledger(tmp_path,app(87,'<textannouncement>Old</textannouncement>'),{'category':'system','content':'消息已撤回','_recalled':True})
    assert PresentationRepair(store).repair('self',store.page('self','peer')['messages'])==0
    assert store.page('self','peer')['messages'][0]['content']=='消息已撤回'


def test_new_presentation_removes_stale_fields(tmp_path):
    store,mid=ledger(tmp_path,app(5,'<title>Link</title>'),{'category':'public_articles','articles':[]})
    fixed={**store.page('self','peer')['messages'][0],**present_content(app(5,'<title>Link</title>'),49,False)}
    fixed.pop('articles',None)
    with store.db()as db:store.upsert(db,'self',fixed)
    result=store.page('self','peer')['messages'][0]
    assert result['category']=='link'and'articles'not in result


def test_record_media_routes_validate_account_position_and_asset_token(tmp_path):
    service=LiveChat(tmp_path/'live.sqlite','http://127.0.0.1:1',tmp_path/'relay.json')
    async def check(account,force=False):
        if account!='self':raise HTTPException(409,'account changed')
        return {}
    service.check_account=check
    root=tmp_path/'source';root.mkdir();(root/'note.txt').write_text('local fixture',encoding='utf8')
    with service.messages.db()as db:
        db.execute('INSERT INTO chat_history_state VALUES (?,?)',('self',json.dumps({'root':str(root)})))
        mid,_=service.messages.upsert(db,'self',{'session_username':'peer','sender_username':'peer','server_id':'123','create_time':1,'category':'chat_history','records':[{'kind':'file','file_name':'note.txt','media_hints':[]}]})
    original=service.media.specs
    spec={'kind':'file','path':'note.txt','state':'local','name':'note.txt'}
    def specs(account,ident,refresh=False,record=None):
        original(account,ident,refresh,record)
        return [spec]if record=='0'else []
    service.media.specs=specs
    app_=FastAPI();app_.include_router(create_live_router(service));client=TestClient(app_)
    info=client.get(f'/api/chat/messages/{mid}/media?account=self&record=0')
    assert info.status_code==200
    url=info.json()['items'][0]['url'];assert 'record=0'in url
    assert client.get(url).text=='local fixture'
    assert client.get(url.replace('account=self','account=other')).status_code==409
    assert client.get(url.replace('record=0','record=1')).status_code==404
    assert client.get(url.replace(service.media.spec_key(spec),'0'*24)).status_code==409
    assert client.get(f'/api/chat/messages/{mid}/media?account=self&record=../0').status_code==422


def test_repair_defers_missing_snapshots_without_breaking_cached_page(tmp_path):
    store,mid=ledger(tmp_path,app(5,'<title>Link</title>'))
    repair=PresentationRepair(store)
    def fail(account):
        raise OSError('snapshot unavailable')
    store.state=fail
    assert repair.repair('self',store.page('self','peer')['messages'])==0
    assert store.page('self','peer')['messages'][0]['id']==str(mid)


def test_repair_uses_compare_and_swap_when_a_recall_arrives_during_parsing(tmp_path,monkeypatch):
    import backend.app.chat_presentation_repair as module
    store,mid=ledger(tmp_path,app(5,'<title>Original</title>'))
    original=module.present_content
    def parse(*args):
        with store.db()as db:
            old=json.loads(db.execute('SELECT data FROM chat_messages WHERE id=?',(mid,)).fetchone()[0])
            old.update(category='system',content='消息已撤回',_recalled=True)
            db.execute('UPDATE chat_messages SET data=? WHERE id=?',(json.dumps(old),mid))
        return original(*args)
    monkeypatch.setattr(module,'present_content',parse)
    assert PresentationRepair(store).repair('self',store.page('self','peer')['messages'])==0
    assert store.page('self','peer')['messages'][0]['content']=='消息已撤回'


def test_forwarded_voice_never_borrows_an_unrelated_zero_server_blob(tmp_path,monkeypatch):
    import backend.app.chat_media as module
    store,mid=ledger(tmp_path,'unused',{'category':'chat_history','records':[{'kind':'voice','content':'[语音]'}]})
    media=module.ChatMedia(store,tmp_path/'cache','http://127.0.0.1:1')
    def must_not_lookup(*args):
        raise AssertionError('forwarded voice has no verified source identity')
    monkeypatch.setattr(module,'MessageResources',must_not_lookup)
    assert media.specs('self',mid,record='0')==[{'kind':'voice','state':'not_downloaded'}]
