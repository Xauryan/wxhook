"""Bounded, read-only Office content previews. Never extract or execute files."""
from __future__ import annotations

import posixpath
import re
from zipfile import BadZipFile, ZipFile

from lxml import etree

from .chat_media import MediaFailure

MAX_XML = 16 * 1024 * 1024
MAX_TOTAL = 48 * 1024 * 1024
MAX_ROWS, MAX_COLS, MAX_SECTIONS = 200, 30, 10


def office_preview(path, file_name=None):
    from pathlib import Path
    suffix = Path(file_name or path.name).suffix.lower()
    if suffix not in {'.xlsx', '.docx', '.pptx'} or path.stat().st_size > MAX_TOTAL:
        raise MediaFailure('unsupported')
    try:
        with ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 5000 or sum(item.file_size for item in entries) > MAX_TOTAL:
                raise MediaFailure('unsupported')

            def xml(name):
                if not name or name.startswith('/') or '..' in name.split('/'):
                    raise MediaFailure('unsupported')
                info = archive.getinfo(name)
                if info.file_size > MAX_XML:
                    raise MediaFailure('unsupported')
                root = etree.fromstring(archive.read(info), etree.XMLParser(resolve_entities=False, no_network=True))
                if root.getroottree().docinfo.doctype:
                    raise MediaFailure('unsupported')
                return root

            def texts(node):
                return ''.join(node.xpath('.//*[local-name()="t"]/text()'))[:4000]

            sections, truncated = [], False
            remaining = 1_000_000

            def bounded(value):
                nonlocal remaining, truncated
                result = value[:min(4000, remaining)]
                truncated |= len(result) < len(value)
                remaining -= len(result)
                return result
            if suffix == '.xlsx':
                shared = []
                if 'xl/sharedStrings.xml' in archive.namelist():
                    shared = [texts(node) for node in xml('xl/sharedStrings.xml')][:100000]
                relations = {n.get('Id'): n.get('Target', '') for n in xml('xl/_rels/workbook.xml.rels')
                             if n.get('TargetMode', '').lower() != 'external'}
                sheets = xml('xl/workbook.xml').xpath('//*[local-name()="sheet"]')
                truncated = len(sheets) > MAX_SECTIONS
                for sheet in sheets[:MAX_SECTIONS]:
                    rid = sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
                    target = relations.get(rid, '')
                    name = posixpath.normpath(target.lstrip('/') if target.startswith('/') else 'xl/' + target)
                    if not name.startswith('xl/'):
                        raise MediaFailure('unsupported')
                    rows = xml(name).xpath('//*[local-name()="sheetData"]/*[local-name()="row"]')
                    truncated |= len(rows) > MAX_ROWS
                    output = []
                    for row in rows[:MAX_ROWS]:
                        if not remaining:
                            truncated = True
                            break
                        cells = []
                        for cell in row:
                            match = re.match(r'^([A-Z]+)', cell.get('r', ''))
                            column = 0
                            if match:
                                for letter in match[1]:
                                    column = column * 26 + ord(letter) - 64
                                column -= 1
                            else:
                                column = len(cells)
                            if column >= MAX_COLS:
                                truncated = True
                                continue
                            value = ''.join(cell.xpath('./*[local-name()="v"]/text()'))
                            if cell.get('t') == 's':
                                try:
                                    index = int(value)
                                    value = shared[index] if 0 <= index < len(shared) else ''
                                except ValueError:
                                    value = ''
                            elif cell.get('t') == 'inlineStr':
                                value = texts(cell)
                            elif not value and cell.xpath('./*[local-name()="f"]'):
                                value = '[公式无缓存值]'
                            while len(cells) <= column:
                                cells.append('')
                            cells[column] = bounded(value)
                        output.append(cells)
                    sections.append({'title': sheet.get('name', 'Sheet'), 'rows': output})
            else:
                names = ['word/document.xml'] if suffix == '.docx' else sorted(
                    (n for n in archive.namelist() if re.fullmatch(r'ppt/slides/slide\d+\.xml', n)),
                    key=lambda name: int(re.search(r'slide(\d+)', name)[1]))
                truncated = len(names) > MAX_SECTIONS
                for index, name in enumerate(names[:MAX_SECTIONS]):
                    paragraphs = xml(name).xpath('//*[local-name()="p"]')
                    truncated |= len(paragraphs) > MAX_ROWS
                    sections.append({'title': '文档正文' if suffix == '.docx' else f'幻灯片 {index + 1}',
                                     'paragraphs': [bounded(texts(node)) for node in paragraphs[:MAX_ROWS] if texts(node)]})
            return {'kind': suffix[1:], 'sections': sections, 'truncated': truncated,
                    'detail': '只读内容预览，不执行公式、宏或外部链接；原始排版请下载查看。'}
    except (BadZipFile, KeyError, ValueError, OSError, RuntimeError, etree.XMLSyntaxError):
        raise MediaFailure('unsupported') from None
