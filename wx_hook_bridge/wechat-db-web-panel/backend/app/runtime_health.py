"""Read-only liveness of the expected API worker, independent of SQLite or WeChat."""
import os

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix='/api/runtime')


@router.get('/health', include_in_schema=False)
async def health(request: Request):
    host = request.client.host if request.client else ''
    if host not in {'127.0.0.1', '::1', 'testclient', 'localhost'}:
        raise HTTPException(403, 'Runtime health is loopback-only')
    return {'ok': True, 'instance': os.getenv('JQR_API_INSTANCE', ''), 'pid': os.getpid()}
