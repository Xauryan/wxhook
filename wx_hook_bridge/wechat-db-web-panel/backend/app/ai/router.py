import asyncio

from fastapi import APIRouter, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from ..local_requests import require_local
from .models import ChatMessage, ModelBatch, ModelEdit, ProfileEdit, ProviderEdit
from .capabilities import MODEL_CAPABILITIES
from .service import AIError
from .tool_store import ToolEdit
from .decisions import DecisionService


class AIRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def protected(request):
            try:
                return await handler(request)
            except RequestValidationError as error:
                # FastAPI's default validation response includes the submitted input.
                messages = []
                for item in error.errors():
                    field = '.'.join(str(part) for part in item['loc'] if part != 'body')
                    if item['type'] == 'value_error':
                        message = str(item.get('ctx', {}).get('error', '配置值无效'))
                    else:
                        message = '格式或取值范围无效'
                    messages.append((field + ' ' + message).strip())
                return JSONResponse({'detail': '；'.join(messages)}, status_code=422)
        return protected


def create_ai_router(service, tool_store=None, search_tools=None):
    router = APIRouter(prefix='/api/ai', tags=['AI'], dependencies=[Depends(require_local)], route_class=AIRoute)

    @router.get('/profiles')
    async def profiles():
        return {**await asyncio.to_thread(service.store.list), 'catalog': MODEL_CAPABILITIES}

    decisions = DecisionService(service)

    @router.delete('/profiles/{ident}')
    async def delete(ident: str, revision: int):
        return await asyncio.to_thread(service.store.delete, ident, revision)

    @router.post('/profiles/{ident}/test-decision')
    async def test_decision(ident: str):
        try:
            result = await decisions.choose(ident, '这个技能冷却多少秒？', '判断是否在询问信息。',
                                            {'question': '寻求信息或帮助', 'other': '普通陈述或闲聊'})
        except AIError as error:
            raise HTTPException(502, str(error)) from None
        return {'ok': True, **result}

    @router.post('/profiles')
    async def create(payload: ProfileEdit):
        return await asyncio.to_thread(service.store.save, None, payload)

    @router.put('/profiles/{ident}')
    async def update(ident: str, payload: ProfileEdit):
        return await asyncio.to_thread(service.store.save, ident, payload)

    @router.post('/providers')
    async def create_provider(payload: ProviderEdit):
        return await asyncio.to_thread(service.store.save_provider, None, payload)

    @router.put('/providers/{ident}')
    async def update_provider(ident: str, payload: ProviderEdit):
        return await asyncio.to_thread(service.store.save_provider, ident, payload)

    @router.delete('/providers/{ident}')
    async def delete_provider(ident: str, revision: int):
        return await asyncio.to_thread(service.store.delete_provider, ident, revision)

    @router.post('/providers/{ident}/discover-models')
    async def discover_models(ident: str):
        try:
            return await service.list_models(ident)
        except AIError as error:
            raise HTTPException(error.status_code, str(error)) from None

    @router.post('/providers/{ident}/models')
    async def add_models(ident: str, payload: ModelBatch):
        return {'items': await asyncio.to_thread(service.store.add_models, ident, payload.models)}

    @router.post('/models')
    async def create_model(payload: ModelEdit):
        return await asyncio.to_thread(service.store.save_model, None, payload)

    @router.put('/models/{ident}')
    async def update_model(ident: str, payload: ModelEdit):
        return await asyncio.to_thread(service.store.save_model, ident, payload)

    @router.post('/profiles/{ident}/test')
    async def test(ident: str):
        try:
            config, _, _ = await asyncio.to_thread(service.store.get, ident)
            if config.decision_only:
                return await test_decision(ident)
            response = await service.complete(ident, [ChatMessage(role='user', content='Reply with OK.')], purpose='connection_test')
        except AIError as error:
            raise HTTPException(502, str(error)) from None
        return {'ok': True, 'model': response.model, 'usage': response.usage, 'elapsed_ms': response.elapsed_ms}

    @router.post('/profiles/{ident}/test-search')
    async def test_profile_search(ident: str):
        from .tool_store import ToolStore
        from ..retrieval.providers import DeepSeekSearchFetch
        from ..retrieval.models import RetrievalError
        adapter = DeepSeekSearchFetch(ToolStore(service.store, ident))
        try:
            results = await adapter.search('DeepSeek Search Fetch API 官方文档', count=1)
            title, content = await adapter.fetch('https://api-docs.deepseek.com/guides/search/')
        except (RetrievalError, AIError) as error:
            raise HTTPException(502, str(error)) from None
        return {'ok': True, 'search_results': len(results), 'fetch_title': title, 'fetch_chars': len(content)}

    if tool_store is not None:
        @router.get('/tools/deepseek')
        async def tool_status():
            return await asyncio.to_thread(tool_store.status)

        @router.put('/tools/deepseek')
        async def save_tool(payload: ToolEdit):
            return await asyncio.to_thread(tool_store.save, payload)

        @router.post('/tools/deepseek/test')
        async def test_tools():
            from ..retrieval.models import RetrievalError
            try:
                results = await search_tools.search('DeepSeek Search Fetch API 官方文档', count=1)
                title, content = await search_tools.fetch('https://api-docs.deepseek.com/guides/search/')
            except RetrievalError as error:
                raise HTTPException(502, str(error)) from None
            return {'ok': True, 'search_results': len(results), 'fetch_title': title, 'fetch_chars': len(content)}

    return router
