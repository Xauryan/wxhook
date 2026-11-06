from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..local_requests import require_local
from .models import RuleEdit, RunRequest


def create_group_report_router(service):
    router = APIRouter(prefix='/api/group-reports', tags=['group reports'], dependencies=[Depends(require_local)])

    @router.get('')
    async def status():
        return await service.status()

    @router.get('/groups')
    async def groups(account: str):
        return {'items': await service.source.groups(account)}

    @router.get('/members')
    async def members(account: str, room: str):
        return {'items': await service.source.members(account, room)}

    @router.post('/rules')
    async def save_rule(payload: RuleEdit):
        return await service.save_rule(payload)

    @router.post('/jobs')
    async def create_job(payload: RunRequest):
        return await service.create(payload)

    @router.get('/jobs/{ident}')
    async def detail(ident: str):
        return await service.detail(ident)

    @router.post('/jobs/{ident}/send')
    async def send(ident: str):
        return await service.send(ident)

    @router.get('/jobs/{ident}/files/{filename}')
    async def artifact(ident: str, filename: str, download: bool = False):
        types = {'report.png': 'image/png', 'report.pdf': 'application/pdf', 'report.txt': 'text/plain; charset=utf-8'}
        if filename not in types:
            raise HTTPException(404, '文件不存在')
        await service.detail(ident)
        path = service.directory(ident) / filename
        if not path.is_file():
            raise HTTPException(404, '报告文件尚未生成')
        return FileResponse(path, media_type=types[filename], filename=filename if download else None,
                            headers={'Cache-Control': 'no-store'})

    return router
