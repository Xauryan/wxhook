import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import html
import json
import logging
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import parse_qs, urlsplit

import jieba

from .config import RetrievalConfig
from .models import Document, RetrievalError, RetrievalResult, SearchHit, parsed_date

jieba.setLogLevel(logging.ERROR)

OFFICIAL_DOMAINS = (
    'id5.163.com', 'ivl.163.com', 'identityvgame.com', 'dwrg.163.com',
    'ds.163.com', 'game.163.com', 'gm.163.com', 'netease.com',
    'ielts.neea.cn', 'toefl.neea.cn', 'neea.edu.cn', 'ielts.org', 'ielts.com.cn',
    'ets.org', 'britishcouncil.cn', 'pku.edu.cn', 'tsinghua.edu.cn',
    'support.microsoft.com', 'learn.microsoft.com', 'support.apple.com',
    'help.bilibili.com', 'bilibili.com/blackboard', 'kf.qq.com',
    'developers.openai.com', 'platform.openai.com', 'help.openai.com',
    'api-docs.deepseek.com', 'docs.python.org', 'developer.mozilla.org',
    'ak.hypergryph.com', 'ak.mihoyo.com', 'ys.mihoyo.com', 'sr.mihoyo.com',
    'deltaforce.qq.com', 'df.qq.com',
)


def source_kind(url, authority=-1):
    host = (urlsplit(url).hostname or '').lower()
    if any(host == domain or host.endswith('.' + domain) for domain in OFFICIAL_DOMAINS):
        return 'official'
    if host == 'wiki.biligame.com' or host == 'wikiwiki.jp':
        return 'community_wiki'
    if host.endswith(('.gov.cn', '.edu.cn')):
        return 'official'
    return 'publisher' if authority >= 3 else 'unverified'


class RetrievalCache:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS retrieval_cache (key TEXT PRIMARY KEY,payload TEXT NOT NULL,expires REAL NOT NULL)')

    @staticmethod
    def key(parts):
        return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def get(self, key):
        with sqlite3.connect(self.path, timeout=10) as db:
            row = db.execute('SELECT payload FROM retrieval_cache WHERE key=? AND expires>?', (key, time.time())).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key, payload, ttl):
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute('INSERT OR REPLACE INTO retrieval_cache VALUES (?,?,?)',
                       (key, json.dumps(payload, ensure_ascii=False), time.time() + ttl))
            db.execute('DELETE FROM retrieval_cache WHERE expires<?', (time.time(),))


class Retriever:
    def __init__(self, wiki, deepseek, gpt, cache):
        self.wiki, self.deepseek, self.gpt, self.cache = wiki, deepseek, gpt, cache

    async def _cached(self, key, producer, ttl=900):
        if ttl <= 0:
            return await producer()
        key = self.cache.key(key)
        found = await asyncio.to_thread(self.cache.get, key)
        if found is not None:
            return found
        result = await producer()
        await asyncio.to_thread(self.cache.put, key, result, ttl)
        return result

    async def wiki_page(self, title, as_of, ttl=86400):
        async def load():
            return asdict(await self.wiki.page(title, as_of))
        data = await self._cached(['wiki-revision-v2', title, as_of.isoformat()], load, ttl)
        return Document(**data)

    async def _wiki_hit(self, hit, as_of, options):
        title = self.wiki.title_from_url(hit.url)
        if not title:
            page_id = parse_qs(urlsplit(hit.url).query).get('curid', [''])[0]
            if page_id.isdigit():
                data = await self.wiki._get({'action': 'query', 'pageids': page_id})
                page = next(iter(data.get('query', {}).get('pages', {}).values()), {})
                title = page.get('title')
        if not title:
            raise RetrievalError('BWIKI 搜索结果没有可读取的条目')
        return await self.wiki_page(title, as_of, options.wiki_cache_seconds)

    async def _fetch(self, hit, as_of, options):
        if (urlsplit(hit.url).hostname or '') == 'wiki.biligame.com' and '/dwrg/' in urlsplit(hit.url).path:
            return await self._wiki_hit(hit, as_of, options)
        published = parsed_date(hit.published_at)
        if published and published.tzinfo and published > as_of:
            raise RetrievalError('检索结果发布时间晚于该消息')
        async def load():
            title, content = await self.deepseek.fetch(hit.url)
            return {'title': title or hit.title, 'content': content}
        page = await self._cached(['fetch', hit.url], load, options.page_cache_seconds)
        return Document(page['title'], hit.url, page['content'], 'deepseek-fetch',
                        source_kind(hit.url, hit.authority), hit.published_at, authority=hit.authority)

    async def retrieve(self, plan, mode, profile_id, revision, as_of, *, options=None, search_prompt=None):
        """Search every source at once, then read the best pages at once, all within one time budget."""
        options = options or RetrievalConfig()
        result = RetrievalResult()
        started = time.monotonic()
        titles = list(dict.fromkeys(plan.wiki_titles))[:options.wiki_titles]
        queries = list(dict.fromkeys(plan.queries or [plan.question]))[:options.search_queries]
        use_wiki = mode in {'bwiki', 'hybrid'} and plan.domain == 'identityv'
        # Wiki search results change slowly; keep them longer than general web searches.
        wiki_search_ttl = max(options.search_cache_seconds, 6 * 3600) if options.search_cache_seconds else 0

        async def run(jobs, share):
            """Run (label, coroutine) jobs concurrently until `share` of the budget; return finished (label, value)."""
            tasks = {asyncio.ensure_future(job): label for label, job in jobs}
            if not tasks:
                return []
            until = started + options.time_budget_seconds * share
            done, pending = await asyncio.wait(tasks, timeout=max(0, until - time.monotonic()))
            for task in pending:
                task.cancel()
            if pending:
                result.errors.append(f'检索超过 {options.time_budget_seconds} 秒，{len(pending)} 项未完成，已用现有资料')
            finished = []
            for task, label in tasks.items():
                if task not in done:
                    continue
                error = task.exception()
                if error is None:
                    finished.append((label, task.result()))
                elif isinstance(error, (RetrievalError, RuntimeError)):
                    result.errors.append(str(error))
                else:
                    raise error
            return finished

        async def search_wiki(query):
            async def load():
                return [asdict(hit) for hit in await self.wiki.search(query, count=options.wiki_search_count)]
            return await self._cached(['wiki-search', query, options.wiki_search_count], load, wiki_search_ttl)

        async def search_deepseek(query):
            # Date syntax also applies in live processing. It limits future-dated articles.
            bounded = query[:100] + ' before:' + (as_of + timedelta(days=1)).date().isoformat()
            async def load():
                return [asdict(hit) for hit in await self.deepseek.search(bounded,
                    original_question=plan.question[:128], count=options.deepseek_count)]
            return bounded, await self._cached(['deepseek-search', bounded, plan.question[:128], options.deepseek_count],
                                               load, options.search_cache_seconds)

        async def search_gpt():
            async def load():
                return [asdict(hit) for hit in await self.gpt.search(profile_id, plan.question,
                    as_of=as_of, expected_revision=revision, prompt=search_prompt)]
            return await self._cached(['gpt-search', profile_id, revision, plan.question, as_of.isoformat(), search_prompt],
                                      load, options.search_cache_seconds)

        jobs = []
        if use_wiki:
            jobs += [(('page', title), self.wiki_page(title, as_of, options.wiki_cache_seconds)) for title in titles]
            jobs += [(('bwiki', query), search_wiki(query)) for query in (titles or queries)[:options.search_queries]]
        if mode in {'deepseek', 'hybrid'}:
            jobs += [(('deepseek', query), search_deepseek(query)) for query in queries]
        if mode == 'gpt':
            jobs.append((('gpt', plan.question), search_gpt()))
        hits = []
        # A stuck source must not starve page reading, so searches get only part of the budget.
        for (kind, query), value in await run(jobs, .5):
            if kind == 'page':
                result.documents.append(value)
                result.searches.append({'provider': 'bwiki', 'query': query, 'results': 1})
            elif kind == 'deepseek':
                bounded, found = value
                hits.extend(SearchHit(**row) for row in found)
                result.searches.append({'provider': 'deepseek', 'query': bounded, 'results': len(found)})
            else:
                hits.extend(SearchHit(**row) for row in value)
                result.searches.append({'provider': kind, 'query': query, 'results': len(value)})

        seen = {(urlsplit(document.url).hostname or '') + urlsplit(document.url).path for document in result.documents}
        # Official sites and specialist wikis are read before other publishers.
        hits.sort(key=lambda hit: (source_kind(hit.url, hit.authority) not in {'official', 'community_wiki'}, -hit.score))
        chosen = []
        for hit in hits:
            identity = (urlsplit(hit.url).hostname or '') + urlsplit(hit.url).path
            if identity in seen or len(result.documents) + len(chosen) >= options.max_documents:
                continue
            seen.add(identity)
            if source_kind(hit.url, hit.authority) == 'unverified' and not options.allow_unverified_sources:
                continue
            chosen.append(hit)
        fetched = dict(await run([(index, self._fetch(hit, as_of, options)) for index, hit in enumerate(chosen)], .9))
        result.documents.extend(fetched[index] for index in sorted(fetched))

        if mode in {'bwiki', 'hybrid'}:
            # Character pages name skill subpages. Read relevant subpages with the same time bound.
            candidates = []
            for document in list(result.documents):
                if document.provider != 'bwiki':
                    continue
                skills = re.findall(r'\|(?:外在特质\d*|[012]阶技能\d*)名称=([^\n|{}]+)', document.content)
                for skill in skills:
                    skill = skill.strip()
                    score = sum(char in plan.question for char in skill)
                    candidates.append((score, skill))
            known_titles = {document.title for document in result.documents}
            subpages = []
            for _, title in sorted(candidates, reverse=True):
                if title in known_titles or len(result.documents) + len(subpages) >= options.max_wiki_documents:
                    continue
                known_titles.add(title)
                subpages.append(title)
            found = dict(await run([(index, self.wiki_page(title, as_of, options.wiki_cache_seconds))
                                    for index, title in enumerate(subpages)], 1))
            result.documents.extend(found[index] for index in sorted(found))
        result.elapsed_ms = round((time.monotonic() - started) * 1000)
        return result


STOP_WORDS = {'第五人格', '第五', '人格', '什么', '怎么', '如何', '是否', '可以', '这个', '那个',
              '哪些', '为什么', '时候', '现在', '游戏', '一个', '需要', '当前', '官方', '问题', '真的'}


def evidence_chunks(result, plan, *, limit=None, options=None):
    options = options or RetrievalConfig()
    limit = limit or options.evidence_limit
    chunk_chars = options.chunk_chars
    terms = {word.strip().lower() for word in jieba.lcut(' '.join([plan.question, *plan.queries, *plan.wiki_titles]))
             if len(word.strip()) >= 2 and word not in STOP_WORDS}
    terms.update(title.lower() for title in plan.wiki_titles)
    candidates = []
    seen_documents = set()
    for document in result.documents:
        if document.url in seen_documents:
            continue
        seen_documents.add(document.url)
        text = html.unescape(re.sub('<[^>]+>', ' ', document.content))
        text = re.sub(r'\[\[(?:文件|File|分类|Category):[^\]]*\]\]', '', text, flags=re.I)
        chunks = []
        current = ''
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if len(current) + len(line) > chunk_chars:
                if current:
                    chunks.append(current)
                current = ''
            for offset in range(0, len(line), chunk_chars):
                piece = line[offset:offset + chunk_chars]
                if len(piece) == chunk_chars:
                    chunks.append(piece)
                else:
                    current += piece + '\n'
        if current:
            chunks.append(current)
        ranked = []
        for index, chunk in enumerate(chunks):
            lower = chunk.lower()
            score = sum(min(lower.count(term), 4) * min(len(term), 6) for term in terms)
            if not score:
                continue
            ranked.append((score, index, chunk))
        for score, index, chunk in sorted(ranked, reverse=True)[:options.chunks_per_document]:
            candidates.append((score, document, index, chunk))
    selected = sorted(candidates, key=lambda item: item[0], reverse=True)[:limit]
    return [{'id': f'S{index + 1}', 'title': document.title, 'url': document.url,
             'source_kind': document.source_kind, 'published_at': document.published_at,
             'revision': document.revision, 'revision_as_of': document.revision_as_of, 'text': chunk, 'score': score}
            for index, (score, document, _, chunk) in enumerate(selected)]
