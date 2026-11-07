import asyncio
import time
from urllib.request import getproxies

import httpx

from ..ai.service import AIError, OpenAICompatibleProvider
from .config import SEARCH_PROMPT
from .models import RetrievalError, SearchHit, public_url


def client_options(transport):
    return {'trust_env': False, 'follow_redirects': False, 'transport': transport,
            'proxy': None if transport else getproxies().get('https')}


class DeepSeekSearchFetch:
    """DeepSeek provider Search / Fetch capability using its shared AI profile."""
    base_url = 'https://api.deepseek.com/v1/tools'

    def __init__(self, credentials, *, transport=None):
        self.credentials = credentials
        self.transport = transport
        self._slots = asyncio.Semaphore(3)

    async def _post(self, action, payload):
        ident, config, key, base_url, _ = await asyncio.to_thread(self.credentials.connection)
        started = time.monotonic()
        status = 'failed'
        try:
            async with self._slots, httpx.AsyncClient(timeout=config.timeout_seconds, **client_options(self.transport)) as client:
                for attempt in range(3):
                    try:
                        response = await client.post(base_url + '/' + action, json=payload,
                                                     headers={'Authorization': 'Bearer ' + key})
                    except httpx.HTTPError:
                        if attempt < 2:
                            await asyncio.sleep(2 ** attempt)
                            continue
                        raise RetrievalError('DeepSeek ' + action + ' 连接失败') from None
                    if response.status_code in {408, 429, 500, 502, 503, 504} and attempt < 2:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    if response.status_code != 200:
                        raise RetrievalError(f'DeepSeek {action} 返回 HTTP {response.status_code}')
                    if len(response.content) > 12 * 1024 * 1024:
                        raise RetrievalError('DeepSeek 返回内容超过大小限制')
                    try:
                        data = response.json()
                        if not isinstance(data, dict):
                            raise ValueError()
                    except ValueError:
                        raise RetrievalError('DeepSeek 返回格式无效') from None
                    status = 'completed'
                    return data
        finally:
            models = getattr(self.credentials, 'models', None)
            if models:
                await asyncio.to_thread(models.record, ident, action, config.model, status,
                                        {}, round((time.monotonic() - started) * 1000))

    async def search(self, query, *, original_question=None, count=6, need_content=False):
        if not isinstance(query, str) or not query.strip() or len(query) > 128:
            raise RetrievalError('搜索词须为 1 至 128 个字符')
        if type(count) is not int or not 1 <= count <= 50:
            raise RetrievalError('搜索条数须为 1 至 50')
        body = {'query': query, 'count': count, 'need_content': bool(need_content)}
        if original_question:
            if not isinstance(original_question, str) or len(original_question) > 128:
                raise RetrievalError('原始问题须为 1 至 128 个字符')
            body['ori_question'] = original_question
        data = await self._post('search', body)
        if not isinstance(data.get('results'), list):
            raise RetrievalError('DeepSeek 搜索结果格式无效')
        result = []
        for row in data['results'][:count]:
            try:
                result.append(SearchHit(title=str(row['title']), url=public_url(row['url']),
                    snippet=str(row.get('snippet') or ''), summary=str(row.get('summary') or ''),
                    content=str(row.get('content') or ''), authority=int(row.get('authority', -1)),
                    score=float(row.get('rerank_score', 0)), published_at=row.get('date')))
            except (KeyError, TypeError, ValueError, RetrievalError):
                continue
        return result

    async def fetch(self, url):
        url = public_url(url)
        data = await self._post('fetch', {'url': url})
        if not isinstance(data.get('content'), str) or not data['content'].strip():
            raise RetrievalError('DeepSeek Fetch 未返回可用正文')
        return str(data.get('title') or ''), data['content']


class GPTWebSearch:
    """Hosted Responses web_search, using an existing shared model profile."""
    def __init__(self, models, *, transport=None):
        self.models = models
        self.transport = transport

    async def search(self, profile_id, query, *, as_of, expected_revision=None, prompt=None):
        config, key, revision = await asyncio.to_thread(self.models.get, profile_id)
        if expected_revision is not None and revision != expected_revision:
            raise RetrievalError('模型配置在检索期间发生变化')
        started = time.monotonic()
        usage, status = {}, 'failed'
        try:
            body = {'model': config.model, 'store': False, 'max_output_tokens': config.max_output_tokens,
                'tools': [{'type': 'web_search'}], 'tool_choice': 'required',
                'include': ['web_search_call.action.sources'],
                'input': (prompt or SEARCH_PROMPT).replace('{at}', as_of.isoformat()).replace('{question}', query)}
            if getattr(config, 'reasoning_effort', None) is not None:
                body['reasoning'] = {'effort': config.reasoning_effort}
            async with httpx.AsyncClient(timeout=config.timeout_seconds, **client_options(self.transport)) as client:
                try:
                    response = await OpenAICompatibleProvider().request(client, config.base_url + '/responses', body,
                                                  {'Authorization': 'Bearer ' + key} if key else {})
                    if response.status_code != 200:
                        raise RetrievalError(f'GPT 搜索接口返回 HTTP {response.status_code}')
                    data = response.json()
                except (httpx.HTTPError, ValueError, AIError):
                    raise RetrievalError('GPT 搜索接口暂不可用') from None
            output = data.get('output', [])
            calls = [row for row in output if row.get('type') == 'web_search_call']
            if not calls:
                raise RetrievalError('当前 GPT 接口未返回真实 web_search 调用记录')
            hits = {}
            for call in calls:
                for source in call.get('action', {}).get('sources', []):
                    url = source.get('url')
                    if url:
                        try:
                            hits[url] = SearchHit(str(source.get('title') or url), public_url(url))
                        except RetrievalError:
                            pass
            for row in output:
                for part in row.get('content', []):
                    for note in part.get('annotations', []):
                        if note.get('type') == 'url_citation' and note.get('url'):
                            try:
                                url = public_url(note['url'])
                                hits[url] = SearchHit(str(note.get('title') or url), url)
                            except RetrievalError:
                                pass
            usage = {key: value for key, value in (data.get('usage') or {}).items() if type(value) is int}
            status = 'completed'
            return list(hits.values())[:10]
        finally:
            await asyncio.to_thread(self.models.record, profile_id, 'web_search', config.model, status,
                                    usage, round((time.monotonic() - started) * 1000))
