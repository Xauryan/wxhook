from fastapi import APIRouter, Depends

from ..local_requests import require_local
from .models import ReplayRequest, ReplyRuleEdit


def create_reply_router(service):
    router = APIRouter(prefix='/api/group-replies', tags=['group replies'], dependencies=[Depends(require_local)])

    @router.get('')
    async def status():
        return await service.status()

    @router.get('/groups')
    async def groups(account: str):
        return {'items': await service.groups.groups(account)}

    @router.post('/rules')
    async def save_rule(payload: ReplyRuleEdit):
        return await service.save_rule(payload)

    @router.get('/faq')
    async def faq(account: str, room: str):
        await service.live.check_account(account, force=True)
        return {'items': service.faq.list(account, room)}

    @router.delete('/faq/{ident}')
    async def delete_faq(ident: str, account: str):
        await service.live.check_account(account, force=True)
        return service.faq.delete(account, ident)

    @router.get('/cases')
    async def cases(account: str, room: str):
        await service.live.check_account(account, force=True)
        return {'items': service.cases(account, room)}

    @router.post('/replays')
    async def replay(payload: ReplayRequest):
        return await service.replay(payload)

    @router.get('/replays/{ident}')
    async def batch(ident: str):
        result = service.store.batch(ident)
        await service.live.check_account(result['account'], force=True)
        return result

    @router.post('/replays/{ident}/cancel')
    async def cancel(ident: str):
        result = service.store.batch(ident)
        await service.live.check_account(result['account'], force=True)
        service.store.cancel(ident)
        return service.store.batch(ident)

    @router.get('/runs/{ident}')
    async def run(ident: str):
        result = service.store.run(ident)
        await service.live.check_account(result['account'], force=True)
        return result

    return router
