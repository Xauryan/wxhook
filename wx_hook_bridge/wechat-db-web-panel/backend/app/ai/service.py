import asyncio
import json
import time
from urllib.parse import urlsplit
from urllib.request import getproxies, proxy_bypass

import httpx

from .models import ChatMessage, Completion
from .store import AIStore
from .errors import AIError


class OpenAICompatibleProvider:
    """Transport adapter. It has no knowledge of WeChat or report prompts."""

    def __init__(self, transport=None):
        self.transport = transport

    async def request(self, client, url, body, headers):
        if not url.endswith('/responses'):
            return await client.post(url, json=body, headers=headers)
        async with client.stream('POST', url, json={**body, 'stream': True}, headers=headers) as response:
            if response.status_code != 200 or 'text/event-stream' not in response.headers.get('content-type', ''):
                await response.aread()
                return response
            size = 0
            event_lines = []
            async for line in response.aiter_lines():
                size += len(line)
                if size > max(8 * 1024 * 1024, body.get('max_output_tokens', 4096) * 1024):
                    raise AIError('模型响应超过大小限制')
                if line.startswith('data:'):
                    event_lines.append(line[5:].lstrip())
                if line or not event_lines:
                    continue
                try:
                    event = json.loads('\n'.join(event_lines))
                except ValueError:
                    raise AIError('模型流式响应格式无效') from None
                event_lines = []
                if event.get('type') in {'response.completed', 'response.incomplete'}:
                    return httpx.Response(200, json=event['response'])
                if event.get('type') in {'response.failed', 'error'}:
                    raise AIError('模型服务返回生成失败')
            raise httpx.RemoteProtocolError('Responses stream ended before completion')

    def prepare_request(self, config, key, messages, json_output):
        body = {'model': config.model, 'messages': [message.model_dump() for message in messages],
                'stream': False, config.token_parameter: config.max_output_tokens}
        if config.temperature is not None:
            body['temperature'] = config.temperature
        if json_output and config.json_mode:
            body['response_format'] = {'type': 'json_object'}
        endpoint = '/chat/completions'
        if config.protocol == 'responses':
            endpoint = '/responses'
            body = {'model': config.model, 'store': False, 'max_output_tokens': config.max_output_tokens,
                    'instructions': '\n\n'.join(message.content for message in messages if message.role == 'system'),
                    'input': [message.model_dump() for message in messages if message.role != 'system']}
            if config.temperature is not None:
                body['temperature'] = config.temperature
            if json_output and config.json_mode:
                body['text'] = {'format': {'type': 'json_object'}}
            if config.reasoning_effort is not None:
                body['reasoning'] = {'effort': config.reasoning_effort}
        headers = {'Authorization': f'Bearer {key}'} if key else {}
        if config.protocol == 'anthropic':
            endpoint = '/messages' if config.base_url.endswith('/v1') else '/v1/messages'
            body = {'model': config.model, 'max_tokens': config.max_output_tokens,
                    'system': '\n\n'.join(message.content for message in messages if message.role == 'system'),
                    'messages': [message.model_dump() for message in messages if message.role != 'system']}
            if config.temperature is not None:
                body['temperature'] = config.temperature
            headers = {'anthropic-version': '2023-06-01'}
            if key:
                headers['x-api-key'] = key
        if config.model.startswith(('gpt-6-astra', 'gpt-6.1-sol')):
            body.pop('temperature', None)
        return endpoint, body, headers

    async def complete(self, config, key, messages, *, json_output=False):
        endpoint, body, headers = self.prepare_request(config, key, messages, json_output)
        host = urlsplit(config.base_url).hostname
        proxy = None
        if self.transport is None and host not in {'localhost', '127.0.0.1', '::1'} and not proxy_bypass(host):
            proxy = getproxies().get('https')
        async with httpx.AsyncClient(timeout=config.timeout_seconds, follow_redirects=False,
                                     transport=self.transport, proxy=proxy, trust_env=False) as client:
            for attempt in range(3):
                try:
                    response = await self.request(client, config.base_url + endpoint, body, headers)
                except httpx.TimeoutException:
                    raise AIError('模型请求超时，请检查服务或增加超时时间') from None
                except httpx.HTTPError:
                    if attempt < 2:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    raise AIError('模型接口连接失败，请检查接口地址与网络') from None
                if response.status_code in {429, 502, 503, 504} and attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                if response.status_code != 200:
                    raise AIError(f'模型接口返回 HTTP {response.status_code}，请检查模型、密钥与接口配置')
                break
        if len(response.content) > 4 * 1024 * 1024 + config.max_output_tokens * 32:
            raise AIError('模型响应超过大小限制')
        try:
            payload = response.json()
            if config.protocol == 'anthropic':
                if payload.get('stop_reason') == 'max_tokens':
                    raise AIError('模型输出被长度限制截断，请增加输出上限')
                content = ''.join(part.get('text', '') for part in payload.get('content', [])
                                  if part.get('type') == 'text')
                if not content.strip():
                    raise ValueError()
                raw_usage = payload.get('usage') or {}
                usage = {'prompt_tokens': sum(raw_usage.get(name, 0) for name in
                         ['input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens']),
                         'completion_tokens': raw_usage.get('output_tokens', 0)}
                usage['total_tokens'] = sum(usage.values())
                return content, usage
            if config.protocol == 'responses':
                if payload.get('status') == 'incomplete':
                    raise AIError('模型输出被长度限制截断，请增加输出上限')
                texts = [''.join(part.get('text', '') for part in item.get('content', [])
                                  if part.get('type') == 'output_text')
                         for item in payload.get('output', []) if item.get('type') == 'message']
                content = next((value for value in reversed(texts) if value.strip()), '')
                if not content:
                    raise ValueError()
                raw_usage = payload.get('usage') or {}
                usage = {target: raw_usage[source] for source, target in
                         [('input_tokens', 'prompt_tokens'), ('output_tokens', 'completion_tokens'), ('total_tokens', 'total_tokens')]
                         if type(raw_usage.get(source)) is int}
                return content, usage
            choice = payload['choices'][0]
            content = choice['message']['content']
            if choice.get('finish_reason') == 'length':
                raise AIError('模型输出被长度限制截断，请增加输出上限')
            if not isinstance(content, str) or not content.strip():
                raise ValueError()
            usage = {name: value for name, value in (payload.get('usage') or {}).items()
                     if name in {'prompt_tokens', 'completion_tokens', 'total_tokens'} and type(value) is int}
            return content, usage
        except (ValueError, KeyError, IndexError, TypeError):
            raise AIError('模型响应格式无效，请核对所选协议与接口地址') from None


class DeepSeekProvider(OpenAICompatibleProvider):
    """DeepSeek adapter with native thinking controls and shared credentials."""
    def prepare_request(self, config, key, messages, json_output):
        endpoint, body, headers = super().prepare_request(config, key, messages, json_output)
        effort = config.reasoning_effort
        if config.protocol == 'chat_completions':
            body.pop(config.token_parameter, None)
            body['max_tokens'] = config.max_output_tokens
            if effort is not None:
                body['thinking'] = {'type': 'disabled' if effort == 'none' else 'enabled'}
                if effort != 'none':
                    body['reasoning_effort'] = effort
        elif config.protocol == 'anthropic' and effort is not None:
            body['thinking'] = {'type': 'disabled' if effort == 'none' else 'enabled'}
            if effort != 'none':
                body['output_config'] = {'effort': effort}
        if effort != 'none':
            body.pop('temperature', None)
        return endpoint, body, headers


class AIService:
    def __init__(self, store: AIStore, provider=None):
        self.store = store
        self.provider = provider
        self.providers = {'deepseek': DeepSeekProvider(), 'default': OpenAICompatibleProvider()}
        self._slots = asyncio.Semaphore(3)

    async def complete(self, profile_id: str, messages: list[ChatMessage], *, purpose: str,
                       json_output=False, expected_revision: int | None = None,
                       max_output_tokens: int | None = None) -> Completion:
        config, key, revision = await asyncio.to_thread(self.store.get, profile_id)
        if config.decision_only:
            raise AIError('该连接用于决策分类，请选择能够生成文本的回答模型')
        if expected_revision is not None and revision != expected_revision:
            raise AIError('模型配置在任务期间发生变化，请重新生成')
        if config.context_chars is not None and sum(len(message.content) for message in messages) > config.context_chars:
            raise AIError('输入超出当前模型配置的上下文字符上限')
        if max_output_tokens is not None:
            if not 128 <= max_output_tokens <= 1048576:
                raise AIError('输出长度设置无效')
            config = config.model_copy(update={'max_output_tokens': min(max_output_tokens, config.max_output_tokens)})
        started = time.monotonic()
        usage, status = {}, 'failed'
        try:
            async with self._slots:
                provider = self.provider or self.providers.get(config.provider, self.providers['default'])
                text, usage = await provider.complete(config, key, messages, json_output=json_output)
            status = 'completed'
            return Completion(text=text, model=config.model, usage=usage,
                              elapsed_ms=round((time.monotonic() - started) * 1000))
        finally:
            await asyncio.to_thread(self.store.record, profile_id, purpose, config.model, status,
                                    usage, round((time.monotonic() - started) * 1000))
