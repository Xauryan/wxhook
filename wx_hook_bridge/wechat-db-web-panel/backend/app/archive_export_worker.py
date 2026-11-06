"""Isolated streaming exporter. No imports of main, LiveChat or native plugins."""
from __future__ import annotations

import csv
from datetime import datetime
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import zipfile

import psutil

from .analysis_snapshots import atomic_json, read_json
from .archive_exports import ExportRequest
from .archive_insights import ArchiveInsights
from .archive_local import content_hash
from .config import Settings
from .contact_data import in_directory
from .repository import WeChatRepository
from .snapshot_cache import plain_path


MESSAGE_COLUMNS = ['message_key', 'session_username', 'session_name', 'server_id', 'sender_username',
                   'sender_name', 'is_self', 'create_time', 'local_date', 'category', 'content', 'summary', 'file_name', 'url']
CONTACT_COLUMNS = ['username', 'display_name', 'alias', 'remark', 'nick_name', 'local_type', 'verify_flag', 'delete_flag', 'is_group', 'is_system']
CHAT_TYPES = {'text': 0, 'image': 1, 'voice': 2, 'video': 3, 'file': 4, 'emoji': 5, 'sticker': 5, 'link': 7,
              'location': 8, 'red_packet': 20, 'transfer': 21, 'poke': 22, 'call': 23,
              'app': 24, 'quote': 25, 'merged_forward': 26, 'contact': 27, 'system': 80, 'recall': 81}


class Cancelled(Exception):
    pass


def json_text(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def csv_cell(value):
    text = '' if value is None else str(value)
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else text


class ExportWorker:
    def __init__(self, directory):
        self.directory = Path(directory).absolute()
        if not plain_path(self.directory) or not plain_path(self.directory.parent):
            raise ValueError('invalid export directory')
        self.request = read_json(self.directory / 'request.json')
        self.payload = ExportRequest(**self.request['payload'])
        self.state = read_json(self.directory / 'state.json')
        self.last_write = 0
        self.truncated = 0
        self.edit_count = 0
        self.edits = None
        self.flags = None
        self.insights = None
        self.last_scope_check = 0

    def check(self):
        if (self.directory / 'cancel.json').exists():
            raise Cancelled()
        if self.insights and time.monotonic()-self.last_scope_check > 2:
            self.last_scope_check = time.monotonic()
            self.insights.check(self.payload.account, self.payload.scope)

    def interrupt_query(self):
        try:
            self.check()
            return 0
        except Exception:
            return 1

    def progress(self, force=False, **values):
        self.check()
        self.state.update(values, updated_at=time.time())
        if force or time.monotonic() - self.last_write >= 0.5:
            atomic_json(self.directory / 'state.json', self.state)
            self.last_write = time.monotonic()

    def records(self, db, session=None):
        p = self.payload
        if p.source == 'contacts':
            cursor = db.execute('SELECT '+','.join(CONTACT_COLUMNS)+' FROM contacts ORDER BY username')
            for raw in cursor:
                row = dict(raw)
                row.update(self.flags.get(row['username'], {}) if self.flags is not None else {})
                kind = p.contact_kind
                friend = in_directory(row)
                official = row['username'].startswith('gh_')
                if (kind == 'friends' and not friend or kind == 'groups' and not row['is_group']
                    or kind == 'official' and not official or kind == 'history' and
                    (friend or row['is_group'] or official or row['is_system'])):
                    continue
                yield row
        else:
            where, args = ['session_username=?'], [session['username']]
            if p.start_date:
                where.append('local_date>=?')
                args.append(p.start_date)
            if p.end_date:
                where.append('local_date<=?')
                args.append(p.end_date)
            if p.direction != 'all':
                where.append('is_self=?')
                args.append(int(p.direction == 'sent'))
            for raw in db.execute('SELECT '+','.join(MESSAGE_COLUMNS)+' FROM messages WHERE '+' AND '.join(where)+' ORDER BY create_time,sort_seq,message_key', args):
                row = dict(raw)
                if row['server_id'] is not None:
                    row['server_id'] = str(row['server_id'])
                if self.edits:
                    edited = self.edits.execute('SELECT text,note FROM local_edits WHERE account=? AND scope=? AND message_key=? AND original_hash=?',
                        (p.account,p.scope,row['message_key'],content_hash(row['content']))).fetchone()
                    row['original_content'] = row['content']
                    row['local_note'] = edited[1] if edited else ''
                    if edited and edited[0] is not None:
                        row['content'] = edited[0]
                        self.edit_count += 1
                    if edited:
                        with (self.directory / 'local-edits.jsonl').open('a', encoding='utf-8') as copies:
                            copies.write(json_text(row)+'\n')
                yield row

    def chat_message(self, row):
        sender = self.payload.account if row['is_self'] else row['sender_username'] or 'unknown'
        value = {'sender': sender, 'accountName': row['sender_name'] or sender,
                 'timestamp': row['create_time'], 'type': CHAT_TYPES.get(row['category'], 99),
                 'content': row['content'] or row['summary'], 'platformMessageId': row['message_key']}
        return value

    def write(self, path, records, columns, session):
        fmt = self.payload.format
        count = 0
        completed_before = self.state['rows']
        workbook = sheet = None
        stream = None
        if fmt == 'xlsx':
            from openpyxl import Workbook
            from openpyxl.cell import WriteOnlyCell
            workbook = Workbook(write_only=True)
            sheet = workbook.create_sheet('记录1')
            sheet.freeze_panes = 'A2'
            sheet.append(columns)
        else:
            stream = path.open('w', encoding='utf-8-sig' if fmt == 'csv' else 'utf-8', newline='')
        try:
            if fmt == 'csv':
                writer = csv.writer(stream)
                writer.writerow(columns)
            elif fmt == 'json':
                stream.write('[')
            elif fmt == 'html':
                stream.write('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'"><title>微信本地导出</title><style>body{font:14px system-ui;margin:24px}table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:8px;white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>微信本地导出</h1><table><thead><tr>')
                stream.write(''.join('<th>'+html.escape(c)+'</th>' for c in columns)+'</tr></thead><tbody>')
            elif fmt == 'pgsql':
                table = 'wechat_messages_archive' if self.payload.source == 'messages' else 'wechat_contacts_archive'
                stream.write('-- Local archive. All values are text; run only in a chosen destination.\nSET standard_conforming_strings = on;\nCREATE TABLE IF NOT EXISTS '+table+' ('+', '.join('"'+c+'" TEXT' for c in columns)+');\n')
            elif fmt in {'chatlab', 'chatlab-jsonl'}:
                header = {'chatlab': {'version': '0.0.2', 'exportedAt': int(time.time()), 'generator': 'jqr-local-archive'},
                          'meta': {'name': session['display_name'], 'platform': 'wechat', 'type': 'group' if session['is_group'] else 'private', 'ownerId': self.payload.account}}
                if session['is_group']:
                    header['meta']['groupId'] = session['username']
                if fmt == 'chatlab-jsonl':
                    stream.write(json_text({'_type': 'header', **header})+'\n')
                else:
                    # Members are collected in a separate bounded SQL query before streaming messages.
                    stream.write(json_text(header)[:-1]+',"members":'+json_text(session['members'])+',"messages":[')
            for row in records:
                if count % 500 == 0:
                    self.progress(rows=completed_before+count, message='正在流式导出本地记录。')
                values = [row.get(c) for c in columns]
                if fmt == 'csv':
                    writer.writerow([csv_cell(value) for value in values])
                elif fmt == 'json':
                    stream.write((',' if count else '')+json_text(row))
                elif fmt == 'html':
                    stream.write('<tr>'+''.join('<td>'+html.escape('' if v is None else str(v))+'</td>' for v in values)+'</tr>\n')
                elif fmt in {'txt', 'markdown'}:
                    # Each entry carries its identity and all fields, including filenames and links.
                    text = '\n'.join(c+': '+('' if v is None else str(v)) for c,v in zip(columns, values))
                    if fmt == 'markdown':
                        fence = '`' * max(3, max((len(run) for run in re.findall(r'`+', text)), default=0)+1)
                        stream.write('\n'+fence+'text\n'+html.escape(text, quote=False)+'\n'+fence+'\n')
                    else:
                        stream.write(text+'\n\n')
                elif fmt == 'pgsql':
                    literal = lambda v: 'NULL' if v is None else "'"+str(v).replace('\x00', '\\0').replace("'", "''")+"'"
                    stream.write('INSERT INTO '+table+' ('+','.join('"'+c+'"' for c in columns)+') VALUES ('+','.join(literal(v) for v in values)+');\n')
                elif fmt == 'xlsx':
                    if count and count % 1048575 == 0:
                        sheet = workbook.create_sheet('记录'+str(count//1048575+1))
                        sheet.freeze_panes = 'A2'
                        sheet.append(columns)
                    cells = []
                    overflow = False
                    for value in values:
                        text = '' if value is None else str(value)
                        clean = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '�', text)
                        if len(clean) > 32767:
                            clean = clean[:32730]+' [完整内容见 full-text.jsonl]'
                            overflow = True
                        if clean != text:
                            overflow = True
                        cell = WriteOnlyCell(sheet, value=clean)
                        cell.data_type = 's'
                        cells.append(cell)
                    sheet.append(cells)
                    if overflow:
                        with (self.directory / 'full-text.jsonl').open('a', encoding='utf-8') as full:
                            full.write(json_text(row)+'\n')
                        self.truncated += 1
                elif fmt == 'chatlab':
                    stream.write((',' if count else '')+json_text(self.chat_message(row)))
                elif fmt == 'chatlab-jsonl':
                    stream.write(json_text({'_type': 'message', **self.chat_message(row)})+'\n')
                count += 1
            if fmt == 'json':
                stream.write(']')
            elif fmt == 'html':
                stream.write('</tbody></table>')
            elif fmt == 'chatlab':
                stream.write(']}')
            elif fmt == 'xlsx':
                workbook.save(path)
        finally:
            if stream:
                stream.close()
            if workbook:
                workbook.close()
        self.state['rows'] = completed_before+count
        self.progress(force=True)
        return count

    def run(self):
        settings = self.request['settings']
        settings = Settings(**{k: Path(v) if k in {'panel_root','repository_root','database_root','report_path','cache_path','fts_extension','weixin_dll'} else v for k,v in settings.items()})
        insights = ArchiveInsights(WeChatRepository(settings))
        self.insights = insights
        insights.check(self.payload.account, self.payload.scope)
        self.progress(force=True, pid=os.getpid(), process_started=psutil.Process().create_time())
        if shutil.disk_usage(self.directory).free < 256 * 1024 * 1024:
            raise ValueError('insufficient space')
        path = Path(self.request['cache'])
        if not path.is_relative_to(settings.cache_path.parent) or not plain_path(path):
            raise ValueError('invalid input cache')
        files = []
        local_path = settings.cache_path.parent / 'archive-local.sqlite'
        if self.payload.use_local_edits and local_path.exists():
            self.edits = sqlite3.connect(local_path.as_uri()+'?mode=ro', uri=True)
            self.edits.execute('BEGIN')
        if self.payload.source == 'contacts':
            native = insights.repository.database('contact/contact.db')
            if native.exists():
                with sqlite3.connect(native.as_uri()+'?mode=ro', uri=True) as source:
                    source.row_factory = sqlite3.Row
                    self.flags = {r['username']: dict(r) for r in source.execute('SELECT username,flag,local_type,delete_flag FROM contact')}
            if self.payload.contact_kind in {'friends', 'history'} and self.flags is None:
                raise ValueError('contact directory flags unavailable')
        with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            db.set_progress_handler(self.interrupt_query, 20000)
            if self.payload.source == 'contacts':
                sessions = [None]
                columns = CONTACT_COLUMNS+(['flag'] if self.flags is not None else [])
            else:
                columns = MESSAGE_COLUMNS+(['original_content','local_note'] if self.payload.use_local_edits else [])
                if self.payload.all_sessions:
                    sessions = [dict(r) for r in db.execute('SELECT username,display_name,is_group FROM sessions WHERE is_system=0 ORDER BY username')]
                else:
                    markers = ','.join('?' for _ in self.payload.sessions)
                    sessions = [dict(r) for r in db.execute(f'SELECT username,display_name,is_group FROM sessions WHERE username IN ({markers}) ORDER BY username', self.payload.sessions)]
                    if len(sessions) != len(set(self.payload.sessions)):
                        raise ValueError('unknown session')
            suffix = {'markdown':'md','chatlab':'json','chatlab-jsonl':'jsonl'}.get(self.payload.format, self.payload.format)
            for i, session in enumerate(sessions):
                self.check()
                if session and self.payload.format == 'chatlab':
                    members = {self.payload.account: self.payload.account}
                    for row in db.execute('SELECT sender_username,MAX(sender_name) FROM messages WHERE session_username=? GROUP BY sender_username', (session['username'],)):
                        members[row[0] or 'unknown'] = row[1] or row[0] or 'unknown'
                    session['members'] = [{'platformId': k, 'accountName': v} for k,v in members.items()]
                output = self.directory / f'records-{i+1:05}.{suffix}'
                count = self.write(output, self.records(db, session), columns, session)
                # ChatLab requires at least one message; don't publish an invalid empty conversation.
                if not count and self.payload.format.startswith('chatlab'):
                    output.unlink()
                    continue
                files.append({'file': output.name, 'session': session['username'] if session else None, 'rows': count})
                self.progress(force=True, completed_files=i+1, total_files=len(sessions))
        self.check()
        if self.edits:
            self.edits.close()
        insights.check(self.payload.account, self.payload.scope)
        for item in files:
            digest = hashlib.sha256()
            with (self.directory / item['file']).open('rb') as source:
                while chunk := source.read(1024*1024):
                    self.progress(message='正在校验并打包导出文件。')
                    digest.update(chunk)
            item['sha256'] = digest.hexdigest()
        manifest = {**self.request['identity'], 'exported_at': datetime.now().astimezone().isoformat(),
                    'filters': self.payload.model_dump(exclude={'request_id'}), 'rows': self.state['rows'], 'files': files,
                    'xlsx_adjusted_rows': self.truncated, 'local_edits_applied': self.edit_count, 'contact_flags_known': self.flags is not None, 'notes': [
                        '来自单一只读分析库事务；微信原始数据库未修改。',
                        '媒体仅导出消息内容、文件名及链接，不包含媒体二进制；未生成可用媒体的声明。',
                        '联系人分类采用快照标记，历史联系人候选不等同于已删好友；不代表当前微信通讯录。',
                        'CSV 对公式起始字符加单引号；XLSX 全部按文本保存；超长或非法字符原文保留在 full-text.jsonl。']}
        atomic_json(self.directory / 'manifest.json', manifest)
        pending = self.directory / 'archive.pending.zip'
        with zipfile.ZipFile(pending, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True) as bundle:
            for name in [item['file'] for item in files]+['manifest.json']+(['full-text.jsonl'] if self.truncated else [])+(['local-edits.jsonl'] if (self.directory / 'local-edits.jsonl').exists() else []):
                with (self.directory / name).open('rb') as source, bundle.open(name, 'w', force_zip64=True) as target:
                    while chunk := source.read(1024*1024):
                        self.progress(message='正在打包导出文件。')
                        target.write(chunk)
        self.check()
        insights.check(self.payload.account, self.payload.scope)
        from .analysis_snapshots import job_lock
        with job_lock(settings.cache_path.parent / 'analysis-snapshots'):
            insights.check(self.payload.account, self.payload.scope)
            os.replace(pending, self.directory / 'archive.zip')
            self.progress(force=True, status='succeeded', finished_at=time.time(), message='导出完成，可下载 ZIP 与来源清单。')


def main(directory):
    worker = ExportWorker(directory)
    try:
        worker.run()
    except Exception as exc:
        cancelled = isinstance(exc, Cancelled) or (worker.directory / 'cancel.json').exists()
        worker.state.update(status='cancelled' if cancelled else 'failed', updated_at=time.time(), finished_at=time.time(),
                            error_type=type(exc).__name__, message='任务已取消。' if cancelled else '导出失败，未发布下载文件。请检查空间和快照后重试。')
        # Failed/cancelled jobs have no published artifact, even after a final-state write failure.
        (worker.directory / 'archive.zip').unlink(missing_ok=True)
        atomic_json(worker.directory / 'state.json', worker.state)
    finally:
        if worker.edits:
            worker.edits.close()
        for file in worker.directory.iterdir():
            if (re.fullmatch(r'records-\d{5}\.[a-z0-9]+', file.name) or file.name in {'archive.pending.zip','full-text.jsonl','local-edits.jsonl'}) and file.is_file() and plain_path(file):
                file.unlink(missing_ok=True)


if __name__ == '__main__':
    if sys.argv[1] == '--launch':
        subprocess.Popen([sys.executable, '-m', 'backend.app.archive_export_worker', sys.argv[2]],
            cwd=str(Path(__file__).resolve().parents[2]), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000 if os.name == 'nt' else 0)
    else:
        main(sys.argv[1])
