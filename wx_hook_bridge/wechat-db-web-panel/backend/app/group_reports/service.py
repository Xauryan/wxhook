import asyncio
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import subprocess
import time
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import HTTPException
import httpx

from ..ai.service import AIError
from ..analysis_snapshots import atomic_json
from ..friend_automation import _AutomationLeaderLock
from ..live_chat import SendText
from ..node_runtime import failure_tail, node_executable
from .analysis import DEFAULT_PROMPTS, ReportAnalyzer, text_report
from .models import RuleConfig, RunRequest, TIMEZONE, next_run, scheduled_window
from .source import GroupMessageSource
from .store import ReportStore


class RenderError(RuntimeError):
    pass


def command_window(text, now, rule):
    text = text.strip()
    if text in {'群日报', '/群日报'}:
        return now - timedelta(hours=rule.window_hours), now
    if text in {'群日报 昨天', '/群日报 昨天'}:
        return scheduled_window(now, rule.schedule_time, rule.window_hours)
    match = re.fullmatch(r'/?群日报 最近(\d{1,3})小时', text)
    if match and 1 <= int(match[1]) <= 168:
        return now - timedelta(hours=int(match[1])), now
    match = re.fullmatch(r'/?群日报 (\d{4}-\d\d-\d\d \d\d:\d\d) 至 (\d{4}-\d\d-\d\d \d\d:\d\d)', text)
    if match:
        try:
            return tuple(datetime.strptime(value, '%Y-%m-%d %H:%M').replace(tzinfo=TIMEZONE) for value in match.groups())
        except ValueError:
            return None
    return None


class GroupReportService:
    def __init__(self, live, ai, root, *, source=None, analyzer=None, renderer=None):
        self.live, self.ai = live, ai
        self.store = ReportStore(root)
        self.source = source or GroupMessageSource(live)
        self.analyzer = analyzer or ReportAnalyzer(ai)
        self.renderer = renderer or self.render
        self._leader = _AutomationLeaderLock(Path(root) / '.worker.lock')
        self._task = None
        self._job_task = None
        self._cursor = 0
        self._send_lock = asyncio.Lock()
        self.error = None

    async def status(self):
        profile = await self.live.profile(force=True)
        account = profile.get('username') or ''
        return {'account': account, 'profile': profile,
                'rules': self.store.rules(account), 'jobs': self.store.jobs(account),
                'prompts': DEFAULT_PROMPTS, 'timezone': 'Asia/Shanghai',
                'running': bool(self._task and not self._task.done()), 'error': self.error}

    async def save_rule(self, edit):
        group, _ = await self.source.verify_group(edit.account, edit.room)
        await asyncio.to_thread(self.ai.store.get, edit.config.ai_profile_id)
        if edit.config.manual_enabled:
            members = {row['id'] for row in await self.source.members(edit.account, edit.room)}
            if not set(edit.config.admin_ids).issubset(members):
                raise HTTPException(422, '指令管理员须属于当前群成员名单')
        return self.store.save_rule(edit, group['name'], next_run(datetime.now(TIMEZONE), edit.config.schedule_time))

    async def create(self, payload: RunRequest):
        await self.source.verify_group(payload.account, payload.room)
        rule = self.store.get_rule(payload.account, payload.room)
        if rule is None:
            raise HTTPException(409, '请先保存此群的日报配置')
        await asyncio.to_thread(self.ai.store.get, rule['config']['ai_profile_id'])
        return self.public_job(self.store.enqueue(rule, payload.start.timestamp(), payload.end.timestamp(),
                               'manual', 'manual:' + payload.request_id, payload.send))

    @staticmethod
    def public_job(job):
        return {key: value for key, value in job.items() if key not in {'config', 'request_key'}}

    def directory(self, ident):
        if not re.fullmatch(r'[a-f0-9]{32}', ident):
            raise HTTPException(404, '报告不存在')
        return self.store.root / ident

    async def detail(self, ident):
        job = self.store.job(ident)
        await self.live.check_account(job['account'], force=True)
        path = self.directory(ident) / 'report.json'
        report = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None
        return {'job': self.public_job(job), 'report': report}

    async def tick_schedule(self, account, now):
        for rule in self.store.rules(account):
            config = RuleConfig.model_validate(rule['config'])
            if not config.enabled or rule['next_run'] > now.timestamp():
                continue
            start, end = scheduled_window(now, config.schedule_time, config.window_hours)
            self.store.enqueue(rule, start.timestamp(), end.timestamp(), 'scheduled',
                               f'scheduled:{account}:{rule["room"]}:{int(end.timestamp())}', True,
                               next_at=next_run(now, config.schedule_time))

    async def tick_commands(self, account, now):
        with self.live.db() as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM live_messages WHERE sequence>? ORDER BY sequence LIMIT 100', (self._cursor,))]
        for row in rows:
            self._cursor = row['sequence']
            if row['account'] != account or not row['message']:
                continue
            message = json.loads(row['message'])
            room = message.get('session_username', '')
            if not room.endswith('@chatroom') or message.get('base_type') != 1:
                continue
            text = str(message.get('content') or '')
            if not text.strip().startswith(('群日报', '/群日报')):
                continue
            rule = self.store.get_rule(account, room)
            if rule is None:
                continue
            config = RuleConfig.model_validate(rule['config'])
            if (not config.manual_enabled or message.get('sender_username') not in config.admin_ids
                    or message.get('create_time', 0) < max(now.timestamp() - 120, rule['updated_at'])
                    or message.get('create_time', 0) > now.timestamp() + 30):
                continue
            window = command_window(text, now, config)
            if window is None:
                continue
            # Revalidate current membership before accepting a command.
            members = {member['id'] for member in await self.source.members(account, room)}
            if message.get('sender_username') not in members:
                continue
            try:
                request = RunRequest(account=account, room=room, request_id=uuid5(NAMESPACE_URL, row['event_id']).hex,
                                     start=window[0], end=window[1], send=True)
            except ValueError:
                continue
            with self.store.db() as db:
                recent = db.execute("SELECT 1 FROM report_jobs WHERE account=? AND room=? AND trigger='command' AND created_at>?", (account, room, now.timestamp() - 300)).fetchone()
            if recent:
                continue
            self.store.enqueue(rule, request.start.timestamp(), request.end.timestamp(), 'command',
                               f'command:{account}:{row["event_id"]}', True)

    async def generate(self, job):
        ident, account, room = job['id'], job['account'], job['room']
        rule = RuleConfig.model_validate_json(job['config'])
        try:
            self.store.update(ident, 'preparing', '读取当前账号群聊快照')
            source = await self.source.collect(account, room, job['start'], job['end'], rule.max_messages)
            if source['totalMessages'] < rule.min_messages:
                self.store.update(ident, 'skipped', f'该时段消息 {source["totalMessages"]} 条，低于配置的 {rule.min_messages} 条')
                return

            async def progress(detail):
                self.store.update(ident, 'analyzing', detail)

            report = await self.analyzer.analyze(source, rule, progress)
            directory = self.directory(ident)
            directory.mkdir(exist_ok=True)
            atomic_json(directory / 'report.json', report)
            (directory / 'report.txt').write_text(text_report(report), encoding='utf-8')
            self.store.update(ident, 'rendering', '渲染报告')
            await self.renderer(report, rule, directory)
            self.store.update(ident, 'ready', '报告已生成，可预览或发送')
            if job['auto_send']:
                await self.send(ident, explicit=False)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            detail = str(error) if isinstance(error, (AIError, HTTPException, RenderError)) else '报告生成失败，请检查模型、微信连接及任务配置'
            current = self.store.job(ident)['status']
            if current != 'accepted':
                self.store.update(ident, 'unknown' if current in {'sending', 'unknown'} else 'failed', detail[:300])

    async def render(self, report, rule, directory):
        atomic_json(directory / 'render.json', {'report': report, 'skin': rule.skin, 'theme': rule.theme})
        script = Path(__file__).resolve().parents[3] / 'scripts/render-group-report.cjs'
        try:
            result = await asyncio.to_thread(subprocess.run,
                [node_executable(), str(script), str(directory / 'render.json'), str(directory)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        except FileNotFoundError:
            raise RenderError('报告渲染失败：找不到 Node.js，请安装 Node 或设置 WXHOOK_NODE 环境变量') from None
        except subprocess.TimeoutExpired:
            raise RenderError('报告渲染失败：渲染超过 120 秒未完成') from None
        if result.returncode or not (directory / 'report.png').is_file() or not (directory / 'report.pdf').is_file():
            reason = failure_tail(result)
            if "Executable doesn't exist" in reason:
                reason = '缺少 Playwright 浏览器，请在 frontend-next 下运行 npx playwright install chromium-headless-shell'
            raise RenderError('报告渲染失败：' + reason)
        (directory / 'render.json').unlink(missing_ok=True)

    async def send(self, ident, *, explicit=True):
        async with self._send_lock:
            job = self.store.job(ident)
            if job['status'] in {'accepted', 'unknown', 'sending'}:
                return self.public_job(job)
            if job['status'] != 'ready':
                raise HTTPException(409, '请等待报告生成完成')
            await self.source.verify_group(job['account'], job['room'])
            current = self.store.get_rule(job['account'], job['room'])
            rule = RuleConfig.model_validate_json(job['config'])
            if not explicit and (not current or current['revision'] != job['rule_revision'] or
                (job['trigger'] == 'scheduled' and not current['config']['enabled']) or
                (job['trigger'] == 'command' and not current['config']['manual_enabled'])):
                self.store.update(ident, 'ready', '配置已变化，请预览后手动发送')
                with self.store.db() as db:
                    db.execute('UPDATE report_jobs SET auto_send=0 WHERE id=?', (ident,))
                return self.public_job(self.store.job(ident))
            directory = self.directory(ident)
            if rule.output_format == 'text':
                text = (directory / 'report.txt').read_text(encoding='utf-8')
                parts = [text[index:index + 1800] for index in range(0, len(text), 1800)]
            else:
                parts = [str((directory / ('report.png' if rule.output_format == 'image' else 'report.pdf')).resolve())]
            self.store.update(ident, 'sending', '正在提交给微信')
            for index, value in enumerate(parts):
                previous = self.store.reserve_delivery(ident, index)
                if previous == 'accepted':
                    continue
                if previous != 'new':
                    self.store.update(ident, 'unknown', '已有发送结果待核对，请先查看微信群')
                    return self.public_job(self.store.job(ident))
                status, detail = 'unknown', '发送结果待核对，请先查看微信群'
                try:
                    await self.live.check_account(job['account'], force=True)
                    if rule.output_format == 'text':
                        receipt = await self.live.send(SendText(account=job['account'], username=job['room'], text=value,
                            request_id=UUID(str(uuid5(NAMESPACE_URL, f'group-report:{ident}:{index}')))))
                        status, detail = receipt['status'], receipt['detail']
                    else:
                        endpoint, field = ('/api/sendImagesMsg', 'imagePath') if rule.output_format == 'image' else ('/api/sendFileMsg', 'filePath')
                        response = await self.live.bridge(endpoint, {'wxid': job['room'], field: value}, timeout=45)
                        status = 'accepted' if response.get('code') == 1 else 'failed'
                        detail = '微信已受理报告请求' if status == 'accepted' else '微信返回发送失败'
                except (httpx.HTTPError, ValueError, HTTPException):
                    pass
                self.store.delivery_result(ident, index, status, detail)
                if status != 'accepted':
                    self.store.update(ident, 'unknown', detail)
                    return self.public_job(self.store.job(ident))
            self.store.update(ident, 'accepted', '微信已受理报告，实际送达以群内消息为准')
            return self.public_job(self.store.job(ident))

    async def start(self):
        if not self._leader.acquire():
            self.error = '已有群日报执行器运行'
            return
        self.store.recover()
        with self.live.db() as db:
            self._cursor = db.execute('SELECT coalesce(max(sequence),0) FROM live_messages').fetchone()[0]

        async def loop():
            while True:
                try:
                    with self.store.db() as db:
                        configured = db.execute('SELECT 1 FROM report_rules LIMIT 1').fetchone()
                    if not configured:
                        await asyncio.sleep(5)
                        continue
                    profile = await self.live.profile(force=True)
                    account = profile.get('username') or ''
                    if account:
                        now = datetime.now(TIMEZONE)
                        await self.tick_schedule(account, now)
                        await self.tick_commands(account, now)
                        if self._job_task is None or self._job_task.done():
                            if self._job_task and not self._job_task.cancelled():
                                self._job_task.exception()
                            with self.store.db() as db:
                                row = db.execute("SELECT * FROM report_jobs WHERE account=? AND (status='queued' OR (status='ready' AND auto_send=1)) ORDER BY created_at LIMIT 1", (account,)).fetchone()
                            if row:
                                self._job_task = asyncio.create_task(self.generate(dict(row)) if row['status'] == 'queued' else self.send(row['id'], explicit=False))
                        self.error = None
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.error = '当前微信或群日报执行状态暂不可用，将继续检查'
                await asyncio.sleep(5)
        self._task = asyncio.create_task(loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._job_task and not self._job_task.done():
            self._job_task.cancel()
            try:
                await self._job_task
            except asyncio.CancelledError:
                pass
        self._leader.release()
