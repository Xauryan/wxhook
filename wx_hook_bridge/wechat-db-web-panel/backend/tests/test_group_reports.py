import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.group_reports.analysis import DEFAULT_PROMPTS, ReportAnalyzer
from backend.app.group_reports.models import RuleConfig, RuleEdit, TIMEZONE, next_run, scheduled_window
from backend.app.group_reports.service import GroupReportService, command_window
from backend.app.group_reports.source import GroupMessageSource
from backend.app.group_reports.store import ReportStore
from backend.app.group_reports.router import create_group_report_router
from backend.app.ai.service import AIError


def config(**values):
    return RuleConfig(ai_profile_id='model', prompt_topics=DEFAULT_PROMPTS['topics'],
                      prompt_titles=DEFAULT_PROMPTS['titles'], prompt_quotes=DEFAULT_PROMPTS['quotes'], **values)


def test_cross_day_window_and_catchup_use_scheduled_boundary():
    now = datetime(2026, 11, 6, 1, 0, tzinfo=TIMEZONE)
    start, end = scheduled_window(now, '01:00', 24)
    assert start == datetime(2026, 11, 5, 1, 0, tzinfo=TIMEZONE)
    assert end == now
    assert scheduled_window(now + timedelta(hours=8), '01:00', 24) == (start, end)
    assert datetime.fromtimestamp(next_run(now, '01:00'), TIMEZONE).day == 7
    assert command_window('群日报 最近3小时', now, config()) == (now - timedelta(hours=3), now)
    assert command_window('群日报 昨天', now, config()) == (start, end)
    assert command_window('群日报 最近999小时', now, config()) is None


def test_jobs_idempotency_and_restart_preserve_uncertain_sends(tmp_path):
    store = ReportStore(tmp_path)
    edit = RuleEdit(account='self', room='room@chatroom', revision=0, config=config())
    rule = store.save_rule(edit, 'Fixture', 1000)
    job = store.enqueue(rule, 100, 200, 'manual', 'request', True)
    assert store.enqueue(rule, 100, 200, 'manual', 'request', True)['id'] == job['id']
    with pytest.raises(Exception):
        store.enqueue(rule, 100, 201, 'manual', 'request', True)
    store.update(job['id'], 'sending', 'sending')
    store.reserve_delivery(job['id'], 0)
    store.recover()
    assert store.job(job['id'])['status'] == 'unknown'
    assert store.reserve_delivery(job['id'], 0) == 'unknown'


class FakeLive:
    def __init__(self, root):
        self.path = root / 'live.sqlite'
        self.sent = []
        with self.db() as db:
            db.executescript('CREATE TABLE live_messages(sequence INTEGER PRIMARY KEY,account TEXT,event_id TEXT,message TEXT); CREATE TABLE live_names(account TEXT,username TEXT,name TEXT);')
            db.execute('INSERT INTO live_names VALUES (?,?,?)', ('self', 'alice', 'Alice'))
        self.media = SimpleNamespace(avatar=lambda *args: (_ for _ in ()).throw(ValueError()))

    @contextmanager
    def db(self):
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            yield db

    async def check_account(self, account, force=False):
        if account != 'self':
            raise ValueError('wrong account')

    async def send(self, payload):
        self.sent.append(payload)
        return {'status': 'accepted', 'detail': 'fixture accepted'}


def test_source_window_is_half_open_and_deduplicates_shards(tmp_path):
    live = FakeLive(tmp_path)
    source = GroupMessageSource(live)
    room = 'room@chatroom'
    table = 'Msg_' + hashlib.md5(room.encode()).hexdigest()
    database = tmp_path / 'messages.sqlite'
    with sqlite3.connect(database) as db:
        db.executescript(f'CREATE TABLE Name2Id(user_name TEXT); INSERT INTO Name2Id VALUES ("alice"); CREATE TABLE {table}(local_id INTEGER,server_id INTEGER,local_type INTEGER,sort_seq INTEGER,real_sender_id INTEGER,create_time INTEGER,message_content TEXT,compress_content BLOB);')
        db.executemany(f'INSERT INTO {table} VALUES (?,?,1,0,1,?,?,NULL)', [(1, 101, 99, 'before'), (2, 102, 100, 'first'), (3, 103, 199, 'last'), (4, 104, 200, 'after')])
    snapshot = {'account': 'self', 'read_started_at': 210, 'verified_at': 211,
                'databases': {'message/message_0.db': str(database), 'message/message_1.db': str(database)}}
    result = source._read('self', room, 'Fixture', 100, 200, 100, snapshot)
    assert result['totalMessages'] == 2
    assert [row['content'] for row in result['messages']] == ['first', 'last']
    assert result['totalParticipants'] == 1


def test_scheduler_catches_up_only_latest_window(tmp_path):
    live = FakeLive(tmp_path)
    service = GroupReportService(live, None, tmp_path / 'reports')
    rule = config(enabled=True)
    service.store.save_rule(RuleEdit(account='self', room='room@chatroom', revision=0, config=rule), 'Fixture', 1)
    now = datetime(2026, 11, 6, 9, tzinfo=TIMEZONE)
    asyncio.run(service.tick_schedule('self', now))
    asyncio.run(service.tick_schedule('self', now))
    jobs = service.store.jobs('self')
    assert len(jobs) == 1
    assert datetime.fromtimestamp(jobs[0]['end'], TIMEZONE) == now.replace(hour=1)


def test_command_allowlist_and_deduplication(tmp_path):
    live = FakeLive(tmp_path)
    async def members(*args):
        return [{'id': 'admin', 'name': 'Admin'}]
    service = GroupReportService(live, None, tmp_path / 'reports', source=SimpleNamespace(members=members))
    rule = config(manual_enabled=True, admin_ids=['admin'])
    service.store.save_rule(RuleEdit(account='self', room='room@chatroom', revision=0, config=rule), 'Fixture', 1)
    now = datetime.now(TIMEZONE) + timedelta(seconds=1)
    with live.db() as db:
        for sequence, sender in [(1, 'stranger'), (2, 'admin')]:
            message = {'session_username': 'room@chatroom', 'base_type': 1, 'content': '群日报',
                       'sender_username': sender, 'create_time': now.timestamp()}
            db.execute('INSERT INTO live_messages VALUES (?,?,?,?)', (sequence, 'self', str(sequence), json.dumps(message)))
    asyncio.run(service.tick_commands('self', now))
    assert len(service.store.jobs('self')) == 1
    asyncio.run(service.tick_commands('self', now))
    assert len(service.store.jobs('self')) == 1


def test_send_resumes_accepted_parts_and_never_replays_unknown(tmp_path):
    live = FakeLive(tmp_path)
    async def verify(*args):
        return {}, {}
    service = GroupReportService(live, None, tmp_path / 'reports', source=SimpleNamespace(verify_group=verify))
    rule = service.store.save_rule(RuleEdit(account='self', room='room@chatroom', revision=0, config=config(output_format='text')), 'Fixture', 1)
    job = service.store.enqueue(rule, 100, 200, 'manual', 'request', False)
    directory = service.directory(job['id'])
    directory.mkdir()
    (directory / 'report.txt').write_text('x' * 2000)
    service.store.update(job['id'], 'ready', 'ready')
    assert asyncio.run(service.send(job['id']))['status'] == 'accepted'
    assert len(live.sent) == 2
    assert asyncio.run(service.send(job['id']))['status'] == 'accepted'
    assert len(live.sent) == 2


def analysis_source():
    return {'groupName': 'Fixture', 'timeRange': '2026-11-05 01:00 至 2026-11-06 01:00',
            'messages': [{'id': f'm{index}', 'user_id': 'u1', 'sender': 'Alice', 'at': '11-05 10:00',
                          'content': 'x' * 1500} for index in range(6)],
            'userStats': [{'userId': 'u1', 'nickname': 'Alice', 'messageCount': 6, 'avgChars': 1500,
                           'emojiRatio': 0, 'nightRatio': 0, 'replyRatio': 0}]}


def test_analysis_uses_shared_ai_and_merges_chunks():
    purposes = []
    async def complete(profile_id, messages, *, purpose, expected_revision):
        assert profile_id == 'model'
        assert expected_revision == 3
        purposes.append(purpose)
        if purpose.endswith('quotes'):
            result = [{'content': 'invented', 'sender': 'Alice', 'reason': 'invalid'},
                      {'content': 'x' * 1500, 'sender': 'Alice', 'reason': 'fixture'}]
        elif purpose.endswith('titles'):
            result = [{'name': 'model name', 'id': 'u1', 'title': '龙王', 'reason': 'fixture'},
                      {'name': 'unknown', 'id': 'u99', 'title': '虚构', 'reason': 'fixture'}]
        else:
            result = [{'topic': 'fixture', 'contributors': ['Alice'], 'detail': 'fixture summary'}]
        return SimpleNamespace(text=json.dumps(result), usage={'total_tokens': 10})
    ai = SimpleNamespace(store=SimpleNamespace(get=lambda _: (SimpleNamespace(context_chars=7000, model='fixture'), '', 3)),
                         complete=complete)
    async def progress(_):
        pass
    result = asyncio.run(ReportAnalyzer(ai).analyze(analysis_source(), config(), progress))
    assert purposes.count('group_report.topics') > 1
    assert 'group_report.merge' in purposes
    assert result['userTitles'][0]['name'] == 'Alice'
    assert len(result['userTitles']) == 1
    assert len(result['goldenQuotes']) == 1
    assert result['goldenQuotes'][0]['message_id'] == 'm0'
    assert result['usage']['total_tokens'] == 10 * len(purposes)


def test_oversized_merge_fails_without_dropping_topics_or_looping():
    async def complete(*args, **kwargs):
        return SimpleNamespace(text=json.dumps([{'topic': 'large', 'detail': 'x' * 6000}]), usage={})
    ai = SimpleNamespace(store=SimpleNamespace(get=lambda _: (SimpleNamespace(context_chars=7000, model='fixture'), '', 1)),
                         complete=complete)
    async def progress(_):
        pass
    with pytest.raises(AIError, match='合并上下文上限'):
        asyncio.run(ReportAnalyzer(ai).analyze(analysis_source(), config(max_quotes=0), progress))


def test_manual_api_generates_preview_and_records_model_failure(tmp_path):
    live = FakeLive(tmp_path)
    report = {'groupName': 'Fixture', 'timeRange': 'fixture window', 'totalMessages': 20,
              'totalParticipants': 2, 'totalChars': 200, 'mostActivePeriod': '10:00–11:00',
              'analyzedMessages': 20, 'topics': [], 'userTitles': [], 'goldenQuotes': []}
    async def verify(*args):
        return {'name': 'Fixture'}, {}
    async def collect(*args):
        return report
    async def analyze(*args):
        return report
    async def render(value, rule, directory):
        assert value == report
        (directory / 'report.png').write_bytes(b'fixture image')
        (directory / 'report.pdf').write_bytes(b'fixture pdf')
    ai = SimpleNamespace(store=SimpleNamespace(get=lambda _: None))
    service = GroupReportService(live, ai, tmp_path / 'reports',
        source=SimpleNamespace(verify_group=verify, collect=collect),
        analyzer=SimpleNamespace(analyze=analyze), renderer=render)
    app = FastAPI()
    app.include_router(create_group_report_router(service))
    with TestClient(app) as client:
        saved = client.post('/api/group-reports/rules', json=RuleEdit(
            account='self', room='room@chatroom', revision=0, config=config()).model_dump())
        assert saved.status_code == 200
        payload = {'account': 'self', 'room': 'room@chatroom', 'request_id': 'a' * 32,
                   'start': '2026-01-01T01:00:00+08:00', 'end': '2026-01-02T01:00:00+08:00', 'send': False}
        created = client.post('/api/group-reports/jobs', json=payload)
        assert created.status_code == 200
        ident = created.json()['id']
        assert client.post('/api/group-reports/jobs', json=payload).json()['id'] == ident
        asyncio.run(service.generate(service.store.job(ident)))
        detail = client.get(f'/api/group-reports/jobs/{ident}').json()
        assert detail['job']['status'] == 'ready'
        assert detail['report'] == report
        assert client.get(f'/api/group-reports/jobs/{ident}/files/report.txt').status_code == 200
        assert not live.sent

        async def fail(*args):
            raise AIError('模型请求超时')
        service.analyzer = SimpleNamespace(analyze=fail)
        failed = client.post('/api/group-reports/jobs', json={**payload, 'request_id': 'b' * 32}).json()
        asyncio.run(service.generate(service.store.job(failed['id'])))
        assert service.store.job(failed['id'])['status'] == 'failed'
        assert '超时' in service.store.job(failed['id'])['detail']


def test_render_failure_reason_reaches_the_job(tmp_path, monkeypatch):
    import subprocess
    from backend.app.group_reports import service as module
    def missing_browser(*args, **kwargs):
        return SimpleNamespace(returncode=1, stdout=b'', stderr=(
            "browserType.launch: Executable doesn't exist at C:\ms-playwright\chrome.exe\n"
            "\u2554\u2550\u2550\u2557\n\u2551 Looks like Playwright was just installed \u2551\n").encode())
    monkeypatch.setattr(subprocess, 'run', missing_browser)
    service = GroupReportService(FakeLive(tmp_path), SimpleNamespace(), tmp_path / 'reports')
    with pytest.raises(module.RenderError, match='Playwright'):
        asyncio.run(service.render({}, config(), tmp_path))
    def no_node(*args, **kwargs):
        raise FileNotFoundError('node')
    monkeypatch.setattr(subprocess, 'run', no_node)
    with pytest.raises(module.RenderError, match='Node'):
        asyncio.run(service.render({}, config(), tmp_path))
