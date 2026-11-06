"""Local snapshot reports. SQL runs against one read transaction, never native DBs."""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

from fastapi import APIRouter, HTTPException, Query


class ArchiveInsights:
    def __init__(self, repository, *, isolate=True):
        self.repository = repository
        self.isolate = isolate
        self._memo = {}
        self._lock = threading.Lock()
        self._process_lock = threading.Lock()

    def isolated(self, method, arguments):
        """Keep large SQLite scans outside the live API's process/GIL/native runtime."""
        settings = self.repository.settings
        if not self.isolate:
            return getattr(self, method)(**arguments)
        from .analysis_snapshots import atomic_json, read_json
        from .snapshot_cache import plain_path
        identity = self.identity()
        key = hashlib.sha256(json.dumps([identity['scope'],method,arguments], sort_keys=True).encode()).hexdigest()
        root = settings.cache_path.parent / 'archive-reports'
        root.mkdir(parents=True, exist_ok=True)
        if not plain_path(root):
            raise HTTPException(503, '报告缓存目录不可用。')
        result_path = root / (key+'.result.json')
        with self._process_lock:
            if result_path.is_file() and plain_path(result_path) and time.time()-result_path.stat().st_mtime < 120:
                result = read_json(result_path)
            else:
                values = {k: str(v) if isinstance(v,Path) else v for k,v in asdict(settings).items()}
                request_path = root / (key+'.request.json')
                atomic_json(request_path, {'settings':values,'method':method,'arguments':arguments,'identity':identity})
                try:
                    process = subprocess.run([sys.executable,'-m','backend.app.archive_report_worker',str(request_path)],
                        cwd=str(settings.panel_root),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                        creationflags=0x08000000 if sys.platform=='win32' else 0,timeout=180)
                except (OSError,subprocess.TimeoutExpired):
                    raise HTTPException(503, '统计进程暂不可用，请稍后重新统计。') from None
                finally:
                    request_path.unlink(missing_ok=True)
                if process.returncode or not result_path.is_file() or not plain_path(result_path):
                    raise HTTPException(503, '统计未完成，请检查快照后重新统计。')
                result = read_json(result_path)
            self.check(identity['account'],identity['scope'])
            if result.get('account') != identity['account'] or result.get('scope') != identity['scope']:
                raise HTTPException(409, '统计结果的账号或快照已变化。')
            # Cache files contain derived statistics only, capped to recent requests.
            for stale in sorted(root.glob('*.result.json'),key=lambda p:p.stat().st_mtime,reverse=True)[48:]:
                if plain_path(stale):
                    stale.unlink(missing_ok=True)
            return result

    def identity(self):
        repo = self.repository
        repo.reload_configuration_if_available()
        if not repo.configured:
            raise HTTPException(503, '请先生成解密快照。')
        value = {'account': repo.account, 'snapshot_time': repo.snapshot_time,
                 'database_root': str(repo.database_root)}
        value['scope'] = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:24]
        return value

    def check(self, account, scope):
        value = self.identity()
        if value['account'] != account or value['scope'] != scope:
            raise HTTPException(409, '账号或快照已变化，请重新读取。')
        return value

    def catalog(self, q='', offset=0, limit=100):
        identity = self.identity()
        with self.repository.cache() as db:
            db.execute('BEGIN')
            span = db.execute('SELECT MIN(local_date), MAX(local_date) FROM messages').fetchone()
            years = list(range(int(span[1][:4]), int(span[0][:4])-1, -1)) if span[0] else []
            # instr treats search text literally, including SQL LIKE wildcards.
            params = (q.casefold(), q.casefold())
            where = 'is_system=0 AND (instr(lower(display_name),?)>0 OR instr(lower(username),?)>0)'
            total = db.execute(f'SELECT count(*) FROM sessions WHERE {where}', params).fetchone()[0]
            sessions = [dict(row) for row in db.execute(
                f'SELECT username,display_name,is_group,message_count FROM sessions WHERE {where} '
                'ORDER BY message_count DESC,username LIMIT ? OFFSET ?', (*params, limit, offset))]
        self.check(identity['account'], identity['scope'])
        return {**identity, 'years': years, 'sessions': sessions, 'total_sessions': total}

    def report(self, account, scope, kind, year=0, session=''):
        identity = self.check(account, scope)
        if kind not in {'annual', 'dual', 'group', 'footprint'} or year and not 1900 <= year <= 2100:
            raise HTTPException(422, '报告类型或年份无效。')
        key = (scope, kind, year, session)
        with self._lock:
            cached = self._memo.get(key)
        if cached and time.monotonic() - cached[0] < 120:
            return cached[1]
        where, args = ['1=1'], []
        if year:
            where += ['local_date>=?', 'local_date<?']
            args += [f'{year:04}-01-01', f'{year+1:04}-01-01']
        if kind == 'footprint':
            where.append('is_self=1')
        if kind in {'dual', 'group'}:
            where.append('session_username=?')
            args.append(session)
        clause = ' AND '.join(where)
        with self.repository.cache() as db:
            db.execute('BEGIN')
            selected = None
            if kind in {'dual', 'group'}:
                selected = db.execute('SELECT username,display_name,is_group FROM sessions WHERE username=? AND is_system=0', (session,)).fetchone()
                if not selected or bool(selected['is_group']) != (kind == 'group'):
                    raise HTTPException(422, '请选择对应类型的会话。')
            def rows(sql, extra=()):
                return [dict(row) for row in db.execute(sql.format(where=clause), (*args, *extra))]
            totals = rows('''SELECT count(*) message_count, COALESCE(sum(is_self),0) sent_count,
                count(DISTINCT local_date) active_days,count(DISTINCT session_username) session_count,
                count(DISTINCT CASE WHEN sender_username IS NOT NULL AND sender_username!='' THEN sender_username END) observed_speakers,
                COALESCE(sum(CASE WHEN category='text' THEN length(content) ELSE 0 END),0) text_characters,
                min(local_date) first_date,max(local_date) last_date FROM messages WHERE {where}''')[0]
            totals['received_count'] = totals['message_count'] - totals['sent_count']
            daily = rows('SELECT local_date label,count(*) count,sum(is_self) sent FROM messages WHERE {where} GROUP BY local_date ORDER BY local_date')
            monthly = rows("SELECT substr(local_date,1,7) label,count(*) count,sum(is_self) sent FROM messages WHERE {where} GROUP BY 1 ORDER BY 1")
            hourly = rows('SELECT local_hour label,count(*) count FROM messages WHERE {where} GROUP BY local_hour ORDER BY local_hour')
            categories = rows('SELECT category label,count(*) count FROM messages WHERE {where} GROUP BY category ORDER BY count DESC')
            ranking = rows('''SELECT session_username username,MAX(session_name) name,count(*) count,sum(is_self) sent
                FROM messages WHERE {where} GROUP BY session_username ORDER BY count DESC,username LIMIT 20''')
            speakers = rows('''SELECT COALESCE(NULLIF(sender_username,''),'unknown') username,
                MAX(COALESCE(NULLIF(sender_name,''),sender_username,'身份未知')) name,count(*) count
                FROM messages WHERE {where} GROUP BY 1 ORDER BY count DESC,username LIMIT 30''')
            # Explicitly sampled and literal repeated phrases; no inferred sentiment or relationships.
            sample_index = 'messages_session_time' if selected else 'messages_time'
            sample = rows("SELECT content FROM messages INDEXED BY "+sample_index+" WHERE {where} AND category='text' AND length(content) BETWEEN 2 AND 40 ORDER BY create_time DESC,sort_seq DESC LIMIT 20000")
            phrases = [{'text': text, 'count': count} for text, count in Counter(r['content'].strip() for r in sample).most_common(15) if text and count > 1]
            first = []
            if selected:
                first = [dict(r) for r in db.execute('SELECT message_key,local_date,sender_name,summary FROM messages WHERE session_username=? ORDER BY create_time,sort_seq,message_key LIMIT 3', (session,))]
        best, run, previous, start = {'days': 0, 'start': None, 'end': None}, 0, None, None
        for row in daily:
            current = date.fromisoformat(row['label'])
            if previous is None or current != previous + timedelta(days=1):
                run, start = 0, row['label']
            run += 1
            if run > best['days']:
                best = {'days': run, 'start': start, 'end': row['label']}
            previous = current
        result = {**identity, 'kind': kind, 'year': year, 'session': dict(selected) if selected else None,
                  'totals': totals, 'daily': daily, 'monthly': monthly, 'hourly': hourly,
                  'categories': categories, 'ranking': ranking, 'speakers': speakers,
                  'peak_day': max(daily, key=lambda r: r['count']) if daily else None,
                  'longest_streak': best, 'first_messages': first, 'phrases': phrases,
                  'phrase_sample_count': len(sample), 'phrase_sample_limit': 20000,
                  'notes': ['统计来自已解密快照，不代表微信当前全部数据。',
                            '字数按文本字符统计；发言人数是记录中可识别的发送者，不代表群成员总数。',
                            '高频短句仅统计最近 20000 条长度为 2–40 字符的文本；首次记录指本地留存最早记录。']}
        self.check(account, scope)
        with self._lock:
            if len(self._memo) >= 24:
                self._memo.pop(next(iter(self._memo)))
            self._memo[key] = (time.monotonic(), result)
        return result


def create_insights_router(service):
    router = APIRouter(prefix='/api/archive', tags=['local archive'])

    @router.get('/catalog')
    def catalog(q: str = Query('', max_length=100), offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200)):
        return service.isolated('catalog', dict(q=q, offset=offset, limit=limit))

    @router.get('/report')
    def report(account: str, scope: str, kind: str = 'annual', year: int = 0, session: str = ''):
        service.check(account, scope)
        if kind not in {'annual','dual','group','footprint'} or year and not 1900 <= year <= 2100:
            raise HTTPException(422, '报告类型或年份无效。')
        return service.isolated('report', dict(account=account, scope=scope, kind=kind, year=year, session=session))

    return router
