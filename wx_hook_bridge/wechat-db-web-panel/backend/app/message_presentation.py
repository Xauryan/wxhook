"""Bounded, lossless presentation of native WeChat message envelopes.

Only explicit navigation fields become links. Transport keys and raw XML never
become display text. References are decorations, not replacements for media.
"""
from __future__ import annotations

import html
import re
from urllib.parse import urlsplit

from .parsing import GROUP_PREFIX, _parse_xml, parse_message_content, split_local_type

PRESENTATION_VERSION = 3
MAX_DEPTH = 3
MAX_RECORDS = 100
MEDIA = {'image', 'sticker', 'voice', 'video', 'file'}
LABELS = {'image':'图片', 'sticker':'表情', 'voice':'语音', 'video':'视频', 'file':'文件',
          'link':'链接', 'mini_program':'小程序', 'game':'小游戏', 'finder_video':'视频号',
          'finder_live':'视频号直播', 'shared_video':'视频分享', 'music':'音乐分享',
          'chat_history':'合并聊天记录', 'note':'笔记', 'contact_card':'联系人名片',
          'location':'位置', 'call':'通话记录', 'redpacket':'红包', 'transfer':'转账',
          'announcement':'群公告', 'pat':'拍一拍', 'file_notice':'文件状态', 'location_notice':'位置共享',
          'friend_request':'好友请求', 'status':'状态消息', 'app':'应用消息', 'other':'特殊消息'}


def number(value, default=0):
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def identity(value):
    n = number(value)
    return str(n + (1 << 64) if n < 0 else n) if n else ''


def web_url(value):
    value = (value or '').strip()
    try:
        p = urlsplit(value)
        if p.scheme.lower() in {'http', 'https'} and p.hostname and not p.username and not p.password:
            return value
    except ValueError:
        pass
    return ''


def text(node, *paths):
    if node is None:
        return ''
    for path in paths:
        for found in node.xpath(path):
            value = ''.join(found.itertext()) if hasattr(found, 'itertext') else str(found)
            if value.strip():
                return value.strip()
    return ''


def plain(value):
    """Decode rich announcement markup as text, without executing HTML."""
    value = html.unescape(value or '')
    value = re.sub(r'<(?:script|style)\b[^>]*>.*?</(?:script|style)>', '', value, flags=re.S | re.I)
    value = re.sub(r'<(?:br\b[^>]*|/(?:p|div|li))>', '\n', value, flags=re.I)
    return re.sub(r'<[^>]+>', '', value).replace('\x00', '').strip()


def articles(root):
    readers = root.xpath('self::mmreader | ./mmreader | ./appmsg/mmreader')
    if not readers:
        return None
    result, seen = [], set()
    for reader in readers:
        for category in reader.findall('./category'):
            source = text(category, './name')
            cover = web_url(text(category, './topnew/cover'))
            for node in category:
                if node.tag not in {'item', 'newitem', 'topnew'}:
                    continue
                title = text(node, './title', './title_v2', './text_title')
                if not title:
                    continue
                url = web_url(text(node, './url', './longurl', './shorturl'))
                if (title, url) in seen:
                    continue
                seen.add((title, url))
                result.append({'title':title, 'url':url, 'cover_url':(cover if not result else '') or web_url(text(node, './cover')),
                               'description':text(node, './digest', './summary'),
                               'source':text(node, './sources/source/name') or source})
                if len(result) >= MAX_RECORDS:
                    return result
    return result


def system_text(root):
    notice = text(root, './/revokemsg/replacemsg', './/revokemsg/content', './/plain')
    if notice:
        return plain(notice)
    template = root.find('.//content_template')
    if template is not None:
        message = text(template, './template')
        names = {}
        for link in template.findall('./link_list/link'):
            members = [text(n, './nickname', './username') for n in link.findall('./memberlist/member')]
            separator = text(link, './separator') or '、'
            names[link.get('name', '')] = separator.join(x for x in members if x) or text(link, './plain', './title')
        if message:
            return re.sub(r'\$\{([^}]+)\}', lambda m:names.get(m.group(1)) or '相关成员', plain(message))
    top = root.find('.//mmchatroomtopmsg')
    if top is not None:
        # Keep unknown operation codes neutral; do not claim pin/unpin success.
        return '群置顶消息更新'
    return plain(text(root, './/content', './/text')) or '[系统通知]'


def records(root, depth):
    record = _parse_xml(text(root, './/recorditem'))
    if record is None:
        return [], False
    return record_items(record, depth)


def record_items(root, depth):
    if depth >= MAX_DEPTH:
        return [], True
    nodes = root.xpath('./datalist/dataitem | ./dataitem')
    if not nodes and root.tag != 'recordinfo':
        nodes = root.xpath('./recordinfo/datalist/dataitem')
    result = []
    kinds = {'1':'text','2':'image','3':'voice','4':'video','5':'link','6':'location','7':'music',
             '8':'file','10':'file','14':'chat_history','16':'contact_card','19':'mini_program','22':'quote'}
    truncated = len(nodes) > MAX_RECORDS
    for node in nodes[:MAX_RECORDS]:
        datatype = node.get('datatype', '')
        kind = kinds.get(datatype, 'other')
        title = text(node, './datatitle')
        body = text(node, './datadesc')
        label = LABELS.get(kind, '特殊消息')
        item = {'sender':text(node, './sourcename'), 'content':body or title or f'[{label}]',
                'type':datatype, 'kind':kind, 'title':title, 'file_name':title,
                'file_size':number(text(node, './datasize')), 'url':web_url(text(node, './weburlitem/link', './url')),
                'duration':number(text(node, './duration')), 'server_id':identity(text(node, './fromnewmsgid')),
                'media_hints':list(dict.fromkeys(x.lower() for x in (text(node,'./fullmd5'),text(node,'./thumbfullmd5')) if re.fullmatch(r'[a-fA-F0-9]{32}',x)))}
        if kind in MEDIA:
            item['resource_note'] = '正在检查此记录的本地附件'
        if kind == 'location':
            loc = node.find('./locitem')
            if loc is not None:
                item['content'] = text(loc, './label', './poiname') or '[位置]'
        if kind == 'chat_history':
            nested = node.find('./recordinfo')
            if nested is None:
                nested = _parse_xml(text(node, './recordxml', './recorditem'))
            children, cut = record_items(nested, depth + 1) if nested is not None else ([], False)
            item.update(children=children, truncated=cut)
        result.append(item)
    return result, truncated


def present_content(raw: str, local_type: int, group: bool, _depth=0) -> dict:
    base, packed_subtype = split_local_type(local_type)
    prefix = GROUP_PREFIX.match(raw) if group else None
    body = prefix.group(2) if prefix else raw
    parsed = parse_message_content(body, base, packed_subtype, False)
    value = {k:getattr(parsed,k) for k in ('category','summary','title','description','url','file_name','file_size',
             'reference_server_id','reference_from_username','reference_display_name')}
    value.update(content=body if base==1 else '', sender_prefix=prefix.group(1) if prefix else None,
                 base_type=base, local_type=local_type, app_type=parsed.app_type,
                 reference=None, media_hints=[], _presentation_version=PRESENTATION_VERSION)
    envelope = bool(re.match(r'^\s*(?:<\?xml[^>]*>\s*)?<(?:mmreader|msg|appmsg)\b',body) and re.search(r'<mmreader(?=[\s/>])',body))
    if base==1 and not envelope:
        value.update(category='text', summary=body[:180])
        return value
    root = _parse_xml(body)
    subtype = parsed.app_type if parsed.app_type is not None else packed_subtype
    news = articles(root) if root is not None else None
    if news or ((news is not None or (root is None and envelope)) and (base==1 or (base==49 and subtype==5))):
        first = (news or [{}])[0]
        value.update(category='public_articles', content='', articles=news or [],
                     title=first.get('title','公众号消息'), summary=first.get('title','[公众号消息]')[:180],
                     description=first.get('description',''),url=first.get('url',''),source_name=first.get('source',''))
        return value
    if base==1:
        value.update(category='text')
        return value
    if root is None:
        value.update(content=body if base in {50,51,10000,10002} and not body.lstrip().startswith('<') else '',
                     parse_error=bool(body.lstrip().startswith('<')))
        value['summary'] = value['content'][:180] or f'[{LABELS.get(value["category"],"特殊消息")}]'
        value['type_label'] = LABELS.get(value['category'], f'特殊消息（{base}）')
        return value
    app = root if root.tag=='appmsg' else root.find('./appmsg')
    value.update(title=text(app,'./title'), description=text(app,'./des'),
                 url=web_url(text(app,'./url')), file_name=text(app,'./appattach/filename','./title'),
                 cover_url=web_url(text(app,'./thumburl')), source_name=text(app,'./sourcedisplayname') or text(root,'./appinfo/appname'))
    kind = value['category']
    if base==49:
        kind = {3:'music',4:'shared_video',8:'sticker',17:'location_notice',24:'note',33:'mini_program',36:'mini_program',44:'mini_program',
                51:'finder_video',62:'pat',63:'finder_live',74:'file_notice',87:'announcement',109:'game',
                2000:'transfer',2001:'redpacket'}.get(subtype,kind)
    value['category'] = kind
    if kind in {'mini_program','game'}:
        value['mini_program'] = {'name':text(app,'./sourcedisplayname','./weappinfo/sharename') or value['source_name'],
                                 'page':text(app,'./weappinfo/pagepath'), 'username':text(app,'./weappinfo/username'),
                                 'app_id':text(app,'./weappinfo/appid')}
        value['source_name'] = value['mini_program']['name'] or LABELS[kind]
    elif kind in {'finder_video','finder_live'}:
        feed = app.find('./finderFeed') if kind=='finder_video' and app is not None else app.find('./finderLive') if app is not None else None
        value.update(title=text(feed,'./desc') or value['title'], source_name=text(feed,'./nickname') or value['source_name'],
                     cover_url=web_url(text(feed,'./mediaList/media/coverUrl','./mediaList/media/thumbUrl','./media/coverUrl','./media/thumbUrl')) or value['cover_url'],
                     duration=number(text(feed,'./mediaList/media/videoPlayDuration')))
    elif kind=='announcement':
        value.update(title='群公告',content=text(app,'./textannouncement') or plain(text(app,'./announcement')))
    elif kind=='pat':
        value['content'] = value['title'] or plain(text(app,'./patinfo/template')) or '[拍一拍]'
    elif kind in {'transfer','redpacket'}:
        pay = app.find('./wcpayinfo') if app is not None else None
        value['payment'] = {'kind':kind, 'amount':text(pay,'./feedesc'),
                            'remark':text(pay,'./pay_memo') if kind=='transfer' else text(pay,'./sendertitle'),
                            'status':value['description'], 'status_code':text(pay,'./paysubtype')}
    elif kind=='call':
        value['content'] = text(root,'.//VoIPBubbleMsg/msg','.//msg')
        value['duration'] = number(text(root,'.//VoIPBubbleMsg/duration','.//duration'))
        value['call'] = {'text':value['content'],'duration':value['duration'], 'room_type':text(root,'.//room_type')}
    elif kind=='system':
        value.update(content=system_text(root), recalled_server_id=text(root,'.//revokemsg/newmsgid','.//revokemsg/msgid'),
                     _recalled=bool(root.xpath('.//revokemsg')), _recall_time=number(text(root,'.//revokemsg/revoketime')))
    elif kind=='contact_card':
        card = root.find('./msg') if root.tag!='msg' else root
        value['card'] = {'name':(card.get('nickname','')if card is not None else '') or value['title'],
                         'username':card.get('username','')if card is not None else '',
                         'description':card.get('alias','')if card is not None else ''}
    elif kind=='location':
        loc = root.find('.//location')
        if loc is not None:
            value['location'] = {k:loc.get(k,'')for k in ('label','poiname','x','y')}
    elif kind in {'chat_history','note'}:
        value['records'],value['records_truncated'] = records(root,_depth)
    elif kind=='file_notice':
        value['content'] = '文件状态记录'
    elif kind in {'app','other','friend_request','status'}:
        # Explicit human fields only; no fallback to raw XML or incidental URLs.
        value['content'] = plain(text(app,'./content')) if app is not None else plain(text(root,'.//plain','.//text'))
    if kind in {'voice','video'}:
        node = root.find('.//voicemsg' if kind=='voice' else './/videomsg')
        if node is not None:
            value['duration'] = number(node.get('voicelength')) / 1000 if kind=='voice' else number(node.get('playlength'))
    # The reference accompanies any outer type, including images and stickers.
    refnode = app.find('./refermsg') if app is not None else None
    if refnode is None:
        refnode = root.find('./extcommoninfo/refermsg')
    if refnode is not None:
        content_node = refnode.find('./content')
        original = ''.join(content_node.itertext()) if content_node is not None else ''
        ref_type = number(text(refnode,'./type'),1)
        if _depth < MAX_DEPTH and original:
            ref = present_content(original,ref_type,False,_depth+1)
            ref_content = ref.get('content') or ref.get('title') or ref.get('summary')
        else:
            ref = {}; ref_content = '[引用层级已折叠]' if original else '[原消息待定位]'
        server = identity(text(refnode,'./svrid'))
        value.update(reference_server_id=server or None,reference={'server_id':server,'display_name':text(refnode,'./displayname'),
                     'sender':text(refnode,'./fromusr'),'content':ref_content or '[原消息不可用]',
                     'kind':ref.get('category'),'url':web_url(ref.get('url'))})
    if kind=='quote':
        value['content'] = value['title']
    hints = []
    for node in root.iter():
        for key,item in node.attrib.items():
            if 'md5' in key.lower() and re.fullmatch(r'[a-fA-F0-9]{32}',item):
                hints.append(item.lower())
        if isinstance(node.tag,str) and 'md5' in node.tag.lower() and re.fullmatch(r'[a-fA-F0-9]{32}',node.text or ''):
            hints.append(node.text.lower())
    value['media_hints'] = list(dict.fromkeys(hints))
    emoji = root.find('.//emoji')
    if emoji is not None:
        value['_media_key'] = emoji.get('aeskey','')
    value['type_label'] = (f'特殊消息（{base}）' if kind=='other' else f'应用消息（{base}/{subtype}）' if kind=='app' else LABELS.get(kind,'特殊消息'))
    value['summary'] = (value['content'] or value['title'] or value['description'] or f'[{value["type_label"]}]')[:180]
    return value
