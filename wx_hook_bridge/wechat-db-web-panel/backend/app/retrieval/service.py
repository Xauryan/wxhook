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
        options = options or RetrievalConfig()
        result = RetrievalResult()
        started = time.monotonic()
        hits = []
        titles = list(dict.fromkeys(plan.wiki_titles))[:options.wiki_titles]
        queries = list(dict.fromkeys(plan.queries or [plan.question]))[:options.search_queries]
        if mode in {'bwiki', 'hybrid'} and plan.domain == 'identityv':
            for title in titles:
                try:
                    result.documents.append(await self.wiki_page(title, as_of, options.wiki_cache_seconds))
                    result.searches.append({'provider': 'bwiki', 'query': title, 'results': 1})
                except RetrievalError as error:
                    result.errors.append(str(error))
            for query in (titles or queries)[:options.search_queries]:
                try:
                    async def search_wiki(query=query):
                        return [asdict(hit) for hit in await self.wiki.search(query, count=options.wiki_search_count)]
                    found = await self._cached(['wiki-search', query, options.wiki_search_count], search_wiki, options.search_cache_seconds)
                    hits.extend(SearchHit(**row) for row in found)
                    result.searches.append({'provider': 'bwiki', 'query': query, 'results': len(found)})
                except RetrievalError as error:
                    result.errors.append(str(error))
        if mode in {'deepseek', 'hybrid'}:
            for query in queries:
                try:
                    # Date syntax also applies in live processing. It limits future-dated articles.
                    before = (as_of + timedelta(days=1)).date().isoformat()
                    bounded = query[:100] + ' before:' + before
                    async def search_deepseek(query=bounded):
                        return [asdict(hit) for hit in await self.deepseek.search(query,
                            original_question=plan.question[:128], count=options.deepseek_count)]
                    found = await self._cached(['deepseek-search', bounded, plan.question[:128], options.deepseek_count], search_deepseek, options.search_cache_seconds)
                    hits.extend(SearchHit(**row) for row in found)
                    result.searches.append({'provider': 'deepseek', 'query': bounded, 'results': len(found)})
                except (RetrievalError, RuntimeError) as error:
                    result.errors.append(str(error))
        if mode == 'gpt':
            try:
                async def search_gpt():
                    return [asdict(hit) for hit in await self.gpt.search(profile_id, plan.question,
                        as_of=as_of, expected_revision=revision, prompt=search_prompt)]
                found = await self._cached(['gpt-search', profile_id, revision, plan.question, as_of.isoformat(), search_prompt], search_gpt, options.search_cache_seconds)
                hits.extend(SearchHit(**row) for row in found)
                result.searches.append({'provider': 'gpt', 'query': plan.question, 'results': len(found)})
            except (RetrievalError, RuntimeError) as error:
                result.errors.append(str(error))
        seen = {(urlsplit(document.url).hostname or '') + urlsplit(document.url).path for document in result.documents}
        # Official sites and specialist wikis are read before other publishers.
        hits.sort(key=lambda hit: (source_kind(hit.url, hit.authority) not in {'official', 'community_wiki'}, -hit.score))
        for hit in hits:
            identity = (urlsplit(hit.url).hostname or '') + urlsplit(hit.url).path
            if identity in seen or len(result.documents) >= options.max_documents:
                continue
            seen.add(identity)
            if source_kind(hit.url, hit.authority) == 'unverified' and not options.allow_unverified_sources:
                continue
            try:
                result.documents.append(await self._fetch(hit, as_of, options))
            except RetrievalError as error:
                result.errors.append(str(error))
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
            for _, title in sorted(candidates, reverse=True):
                if title in known_titles or len(result.documents) >= options.max_wiki_documents:
                    continue
                known_titles.add(title)
                try:
                    result.documents.append(await self.wiki_page(title, as_of, options.wiki_cache_seconds))
                except RetrievalError as error:
                    result.errors.append(str(error))
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
