import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from backend.app.chat_file_preview import office_preview
from backend.app.chat_media import ChatMedia, MediaFailure
from backend.app.chat_messages import ChatMessages, present_content
from backend.app.live_chat import LiveChat, create_live_router


def entry(tag='item', title='First', url='https://example.com/article?a=1&amp;b=2'):
    return f'<{tag}><title><![CDATA[{title}]]></title><url>{url}</url><cover>https://example.com/thumb.jpg</cover><digest>Summary</digest><sources><source><name>Publisher</name></source></sources></{tag}>'


@pytest.mark.parametrize('local_type,wrap', [(1, '{}'), (49, '<msg><appmsg><type>5</type>{}</appmsg></msg>'), (1, '<appmsg>{}</appmsg>')])
def test_public_article_variants(local_type, wrap):
    body = '<mmreader><category><name>News</name><topnew><cover>https://example.com/large.jpg</cover></topnew>' + entry() + entry('newitem', 'Second') + entry() + '</category></mmreader>'
    result = present_content(wrap.format(body), local_type, False)
    assert result['category'] == 'public_articles'
    assert result['content'] == ''
    assert result['summary'] == 'First'
    assert [a['title'] for a in result['articles']] == ['First', 'Second']
    assert result['articles'][0]['cover_url'].endswith('large.jpg')
    assert result['articles'][0]['url'].endswith('a=1&b=2')
    assert result['articles'][1]['description'] == 'Summary'
    assert result['source_name'] == 'Publisher'


def test_public_source_prefix_and_invalid_envelope():
    body = 'member:\n<mmreader><category>' + entry() + '</category></mmreader>'
    result = present_content(body, 1, True)
    assert result['sender_prefix'] == 'member'
    assert result['articles'][0]['title'] == 'First'
    empty = present_content('<mmreader><category><newitem>', 1, False)
    assert empty['category'] == 'public_articles' and empty['content'] == ''
    assert empty['summary'] == '[公众号消息]'


@pytest.mark.parametrize('body', ['literal <mmreader> example', '<b>hello</b> [憨笑]', '  a\n[unknown]\n&amp; '])
def test_plain_text_remains_lossless(body):
    result = present_content(body, 1, False)
    assert result['category'] == 'text' and result['content'] == body


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'data:text/html,test', 'file:///etc/passwd', 'https://user:password@example.com', 'http://[invalid'])
def test_article_and_link_urls_reject_unsafe_protocols(url):
    result = present_content('<mmreader><category>' + entry(url=url) + '</category></mmreader>', 1, False)
    assert result['articles'][0]['url'] == ''
    result = present_content(f'<msg><appmsg><type>5</type><title>Link</title><url>{url}</url><thumburl>{url}</thumburl></appmsg></msg>', 49, False)
    assert result['url'] == '' and result['cover_url'] == ''


def setup_media(tmp_path, specs, category='image'):
    store = ChatMessages(tmp_path / 'ledger.sqlite')
    root = tmp_path / 'source'
    root.mkdir()
    with store.db() as db:
        db.execute('INSERT INTO chat_history_state VALUES (?,?)', ('self', json.dumps({'root': str(root)})))
        ident, _ = store.upsert(db, 'self', {'session_username': 'peer', 'sender_username': 'peer', 'server_id': '1',
            'local_id': 1, 'create_time': 1000, 'is_self': False, 'category': category, '_media': specs})
    return store, root, ident, ChatMedia(store, tmp_path / 'cache', 'http://127.0.0.1:1')


def test_broken_thumbnail_falls_back_to_valid_original(tmp_path):
    specs = [{'kind': 'image', 'state': 'local', 'path': name, 'variant': variant}
             for name, variant in [('missing.png', 'thumbnail'), ('original.png', 'original')]]
    _, root, ident, media = setup_media(tmp_path, specs)
    Image.new('RGB', (2, 2), 'green').save(root / 'original.png')
    result = media.describe('self', ident)
    assert result['preferred'] == 1
    assert result['items'][0]['state'] == 'missing' and result['items'][0]['url'] is None
    assert result['items'][1]['state'] == 'available'
    with pytest.raises(HTTPException):
        media.describe('other', ident)


def test_video_poster_does_not_become_playable_video(tmp_path):
    specs = [{'kind': 'image', 'state': 'local', 'path': 'poster.png', 'variant': 'thumbnail'},
             {'kind': 'video', 'state': 'missing'}]
    _, root, ident, media = setup_media(tmp_path, specs, 'video')
    Image.new('RGB', (2, 2)).save(root / 'poster.png')
    result = media.describe('self', ident)
    assert result['preferred'] == 1 and result['items'][1]['state'] == 'missing'


@pytest.mark.parametrize('filename,payload,mime', [
    ('doc.pdf', b'%PDF-1.4\n', 'application/pdf'),
    ('notes.txt', b'<script>alert(1)</script>', 'text/plain'),
    ('notes.html', b'<script>alert(1)</script>', 'application/octet-stream'),
    ('fake.pdf', b'<html>fake</html>', 'application/octet-stream'),
    ('notes.txt', b'\x00binary', 'application/octet-stream'),
])
def test_file_preview_mime_is_not_guessed_from_untrusted_extension(tmp_path, filename, payload, mime):
    source = tmp_path / filename
    source.write_bytes(payload)
    assert ChatMedia.file_mime(source) == mime


def test_utf8_text_header_can_end_mid_character(tmp_path):
    path = tmp_path / 'notes.txt'
    path.write_text('中' * 3000, encoding='utf-8')
    assert ChatMedia.file_mime(path) == 'text/plain'


def xlsx(path, external=False, dangerous=False):
    with ZipFile(path, 'w') as archive:
        archive.writestr('xl/workbook.xml', '<workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet 1" r:id="rId1"/></sheets></workbook>')
        mode = ' TargetMode="External"' if external else ''
        archive.writestr('xl/_rels/workbook.xml.rels', f'<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"{mode}/></Relationships>')
        archive.writestr('xl/sharedStrings.xml', '<sst><si><t>Fixture text</t></si></sst>')
        body = '<worksheet><sheetData><row><c r="A1" t="s"><v>0</v></c><c r="C1"><v>42</v></c><c r="D1"><f>1+1</f></c></row></sheetData></worksheet>'
        archive.writestr('xl/worksheets/sheet1.xml', '<!DOCTYPE foo [<!ENTITY x SYSTEM "file:///secret">]>' + body if dangerous else body)


def test_xlsx_preview_preserves_empty_columns_without_executing_formula(tmp_path):
    path = tmp_path / 'test.xlsx'
    xlsx(path)
    result = office_preview(path)
    assert result['sections'][0]['rows'] == [['Fixture text', '', '42', '[公式无缓存值]']]
    assert not result['truncated']


@pytest.mark.parametrize('option', ['external', 'dangerous'])
def test_office_external_references_and_dtd_are_rejected(tmp_path, option):
    path = tmp_path / 'test.xlsx'
    xlsx(path, **{option: True})
    with pytest.raises(MediaFailure):
        office_preview(path)


@pytest.mark.parametrize('suffix,name', [('.docx', 'word/document.xml'), ('.pptx', 'ppt/slides/slide1.xml')])
def test_office_document_content_preview(tmp_path, suffix, name):
    path = tmp_path / ('test' + suffix)
    with ZipFile(path, 'w') as archive:
        archive.writestr(name, '<root><p><r><t>hello &lt;script&gt;</t></r></p></root>')
    assert office_preview(path)['sections'][0]['paragraphs'] == ['hello <script>']


def test_file_routes_keep_account_and_token_checks(tmp_path):
    service = LiveChat(tmp_path / 'live.sqlite', 'http://127.0.0.1:1', tmp_path / 'relay.json')
    async def check(account, force=False):
        if account != 'self':
            raise HTTPException(409, 'account changed')
        return {}
    service.check_account = check
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'note.txt').write_text('fixture', encoding='utf-8')
    spec = {'kind': 'file', 'state': 'local', 'path': 'note.txt', 'name': 'note.txt'}
    with service.messages.db() as db:
        db.execute('INSERT INTO chat_history_state VALUES (?,?)', ('self', json.dumps({'root': str(root)})))
        ident, _ = service.messages.upsert(db, 'self', {'session_username': 'peer', 'sender_username': 'peer', 'server_id': '1', 'local_id': 1, 'create_time': 1, 'category': 'file', '_media': [spec]})
    app = FastAPI()
    app.include_router(create_live_router(service))
    client = TestClient(app)
    url = service.media.asset_url('self', ident, 0, spec)
    response = client.get(url)
    assert response.status_code == 200 and response.text == 'fixture'
    assert response.headers['content-type'].startswith('text/plain')
    assert response.headers['content-disposition'].startswith('inline')
    assert 'sandbox' in response.headers['content-security-policy']
    assert client.get(url + '&download=true').headers['content-disposition'].startswith('attachment')
    assert client.get(url.replace('account=self', 'account=other')).status_code == 409
    assert client.get(url.replace(service.media.spec_key(spec), '0' * 24)).status_code == 409


@pytest.mark.parametrize('local_type', [49, (5 << 32) | 49])
def test_article_share_metadata_is_not_an_empty_mmreader_envelope(local_type):
    body = '''<msg><appmsg><type>5</type><title>Shared article</title>
        <url>https://example.com/article</url><sourcedisplayname>Publisher</sourcedisplayname>
        <mmreadershare><itemshowtype>0</itemshowtype></mmreadershare></appmsg></msg>'''
    result = present_content(body, local_type, False)
    assert result['category'] == 'link'
    assert result['title'] == 'Shared article'
    assert result['url'] == 'https://example.com/article'
    assert result['source_name'] == 'Publisher'
    assert 'articles' not in result


def test_mmreader_quoted_inside_content_does_not_override_outer_message():
    body = '''<msg><appmsg><type>57</type><title>Current reply</title>
        <refermsg><type>1</type><displayname>Author</displayname>
        <content><![CDATA[<mmreader><category><item><title>Original article</title></item></category></mmreader>]]></content>
        </refermsg></appmsg></msg>'''
    result = present_content(body, (57 << 32) | 49, False)
    assert result['category'] == 'quote'
    assert result['content'] == 'Current reply'
    assert result['reference']['display_name'] == 'Author'
    assert result['reference']['content'] == 'Original article'
    assert 'articles' not in result


@pytest.mark.parametrize('body', [
    '<mmreadershare>literal message</mmreadershare>',
    '<msg><![CDATA[<mmreader>literal markup</mmreader>]]></msg>',
])
def test_mmreader_prefix_or_cdata_does_not_consume_literal_text(body):
    result = present_content(body, 1, False)
    assert result['category'] == 'text'
    assert result['content'] == body
