"""Favorite XML presentation and bounded candidates; no filename guesses become trusted assets."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from .chat_messages import integer, web_url
from .parsing import _parse_xml, decode_wcdb_value

CATEGORIES = {1: 'text', 2: 'image', 3: 'voice', 4: 'video', 5: 'link', 6: 'location', 7: 'music', 8: 'file', 14: 'chat_history', 18: 'note'}
LABELS = {'text': '文字', 'image': '图片', 'voice': '语音', 'video': '视频', 'link': '链接', 'location': '位置', 'music': '音乐', 'file': '文件', 'chat_history': '聊天记录', 'note': '笔记', 'other': '其他收藏'}
HEX = re.compile(r'^[a-fA-F0-9]{32}$')


def xml_text(node, *paths):
    for path in paths:
        found = node.find(path)
        if found is not None:
            value = ''.join(found.itertext()).strip()
            if value:
                return value
    return ''


class FavoriteFiles:
    def __init__(self, root):
        self.root = root.resolve()
        self.files = {'original': [], 'thumbnail': []}
        self.limited = False
        visited = set()
        for variant, folder in [('original', 'data'), ('thumbnail', 'mid')]:
            base = self.root / 'business/favorite' / folder
            if not base.is_dir() or not base.resolve().is_relative_to(self.root):
                continue
            for directory, dirs, filenames in os.walk(base, followlinks=False):
                resolved = Path(directory).resolve()
                if resolved in visited or not resolved.is_relative_to(base.resolve()):
                    dirs[:] = []
                    continue
                visited.add(resolved)
                dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink() and (Path(directory) / d).resolve().is_relative_to(base.resolve())]
                for name in filenames:
                    path = Path(directory) / name
                    try:
                        if path.is_symlink() or not path.resolve().is_relative_to(base.resolve()):
                            continue
                        stat = path.stat()
                        self.files[variant].append((path.relative_to(self.root).as_posix(), stat.st_size))
                    except OSError:
                        continue
                    if sum(len(values) for values in self.files.values()) >= 20000:
                        self.limited = True
                        return

    def spec(self, kind, group, variant, digest, size, name):
        digest = digest.lower() if HEX.fullmatch(digest or '') else ''
        # V2 containers add a small framing/padding overhead. Identity is always
        # established by the full plaintext digest later, never by size alone.
        candidates = sorted(path for path, length in self.files[variant] if not size or size <= length <= size + 64)
        state = 'local' if digest and candidates else 'not_downloaded'
        if not digest or len(candidates) > 32:
            state, candidates = 'unavailable', []
        return {'kind': kind, 'group': group, 'variant': variant, 'expected_md5': digest,
                'size': size or None, 'name': name, 'candidates': candidates[:32], 'state': state}


def parse_favorite(row, files, account=''):
    category = CATEGORIES.get(integer(row.get('type')), 'other')
    value = {'id': str(row['local_id']), 'category': category, 'type_label': LABELS[category],
             'title': '', 'content': '', 'description': '', 'url': '', 'attachments': [], '_media': [],
             'source_username': row.get('fromusr') or '', 'source_name_hint': '',
             'updated_at': integer(row.get('update_time')), 'tags': [], 'parse_error': False}
    decoded = decode_wcdb_value(row.get('content'))
    text = decoded.text
    root = _parse_xml(text) if len(text) <= 2 * 1024 * 1024 else None
    if root is None or root.tag != 'favitem' or root.getroottree().docinfo.doctype:
        if category == 'text' and '<' not in text and not decoded.error:
            value['content'] = text[:120000]
        else:
            value['parse_error'] = True
    else:
        sender = xml_text(root, './source/fromusr') or row.get('fromusr') or ''
        recipient = xml_text(root, './source/tousr')
        source = row.get('realchatname') or xml_text(root, './source/realchatname') or (
            recipient if account and sender == account and recipient else sender)
        value.update(title=xml_text(root, './title'), description=xml_text(root, './desc'),
                     url=web_url(xml_text(root, './weburlitem/link', './source/link', './url')),
                     source_username=source,
                     source_name_hint=xml_text(root, './source/sourcename'))
        blocks = [value['description']] if value['description'] else []
        for node in root.findall('./datalist/dataitem')[:100]:
            datatype = integer(node.get('datatype'))
            title, content = xml_text(node, './datatitle'), xml_text(node, './datadesc')
            if datatype == 1 and content:
                blocks.append(content)
            kind = CATEGORIES.get(datatype)
            if kind not in {'image', 'video', 'voice', 'file'}:
                continue
            group = len(value['attachments'])
            name = title or f'{LABELS[kind]}-{value["id"]}'
            fmt = xml_text(node, './datafmt')
            if not Path(name).suffix and re.fullmatch('[a-zA-Z0-9]{1,8}', fmt):
                name += '.' + fmt
            size = max(0, integer(xml_text(node, './fullsize', './datasize')))
            duration = max(0, integer(xml_text(node, './duration')))
            value['attachments'].append({'group': group, 'kind': kind, 'name': name, 'size': size or None, 'duration': duration or None})
            value['_media'].append(files.spec(kind, group, 'original', xml_text(node, './fullmd5'), size, name))
            thumb = xml_text(node, './thumbfullmd5')
            if thumb and kind in {'image', 'video'}:
                value['_media'].append(files.spec('image', group, 'thumbnail', thumb, integer(xml_text(node, './thumbfullsize', './thumbsize')), name))
        value['content'] = '\n\n'.join(blocks)[:120000]
        value['content_limited'] = len('\n\n'.join(blocks)) > 120000 or len(root.findall('./datalist/dataitem')) > 100
        location = root.find('./locitem')
        if location is not None:
            value['description'] = xml_text(location, './poiname', './label')
    value['title'] = value['title'] or (value['content'][:80] if category in {'text', 'note'} else '')
    value['summary'] = value['title'] or value['content'][:180] or f'[{LABELS[category]}]'
    value['revision'] = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]
    return value
