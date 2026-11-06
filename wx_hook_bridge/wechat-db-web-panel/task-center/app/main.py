import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
from uuid import uuid4
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import text
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .auth import Auth
from .config import Settings
from .db import Database, Project
from .errors import Problem, require
from .limits import BodyLimitMiddleware, Limiter
from .schemas import AccountCredentials, Command, EditTask, GitIdentity, MemberState, TaskFields
from .service import Tasks

PREFIX = '/api/task-center/v1'


def create_app(settings=None):
    settings = settings or Settings.from_env()
    database = Database(settings.database_url)
    auth = Auth(database, settings)
    tasks = Tasks(database, settings, auth)
    limiter = Limiter()

    @asynccontextmanager
    async def lifespan(_):
        database.initialize(settings)
        yield
        database.engine.dispose()

    app = FastAPI(title='Collaboration Task Center', lifespan=lifespan)
    app.state.db, app.state.auth, app.state.tasks, app.state.settings = database, auth, tasks, settings
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[urlsplit(settings.origin).hostname])
    app.add_middleware(BodyLimitMiddleware)

    @app.middleware('http')
    async def headers(request, call_next):
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers.update({'X-Request-ID': request.state.request_id, 'Cache-Control': 'no-store',
            'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
            'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'"})
        return response

    @app.exception_handler(Problem)
    async def problem(request, exc):
        return JSONResponse({'code': exc.code, 'message': exc.message, 'request_id': request.state.request_id}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse({'code': 'invalid_request', 'message': '请求字段无效，请检查邮箱或填写内容。',
                             'request_id': request.state.request_id}, status_code=422)

    def session_token(request):
        return request.cookies.get(settings.cookie) or (
            request.cookies.get('tc_remote_session') if not settings.local_http else None)

    def actor(request: Request):
        if request.method not in ('GET', 'HEAD'):
            limiter.check(('write', hashlib.sha256((session_token(request) or '').encode()).hexdigest()))
        return auth.authenticate(session_token(request), write=request.method not in ('GET', 'HEAD'),
                                 origin=request.headers.get('origin'), csrf=request.headers.get('x-csrf-token'))

    def public_write(request):
        require(settings.allows_origin(request.headers.get('origin')), 'csrf_failed', '请求来源无效。', 403)
        limiter.check(('auth', request.client.host), count=20)

    def signed_in(token, request):
        response = JSONResponse({'ok': True})
        response.set_cookie(**settings.session_cookie(request.headers.get('origin')), value=token,
                            httponly=True, samesite='lax', max_age=7 * 86400)
        return response

    @app.get('/healthz')
    def health():
        return {'status': 'ok'}

    @app.get('/readyz')
    def ready():
        with database.transaction() as db:
            db.execute(text('SELECT 1'))
            require(db.get(Project, 1) is not None, 'not_initialized', '中心尚未初始化。', 503)
        return {'status': 'ready'}

    @app.post(PREFIX + '/auth/owner-session')
    def login(body: AccountCredentials, request: Request):
        public_write(request)
        limiter.check(('account', body.login), count=10)
        return signed_in(auth.login(body.login, body.password), request)

    @app.post(PREFIX + '/auth/identify')
    def identify(body: GitIdentity, request: Request):
        public_write(request)
        return signed_in(auth.identify(body.email), request)

    @app.get(PREFIX + '/me')
    def me(user=Depends(actor)):
        return {**user.public(), 'title': settings.title}

    @app.post(PREFIX + '/auth/logout')
    def logout(request: Request):
        auth.logout(session_token(request), request.headers.get('origin'), request.headers.get('x-csrf-token'))
        response = JSONResponse({'ok': True})
        response.delete_cookie(**settings.session_cookie(request.headers.get('origin')), httponly=True, samesite='lax')
        return response

    @app.get(PREFIX + '/members')
    def members(user=Depends(actor)):
        return tasks.members(user)

    @app.post(PREFIX + '/members/{user_id}/state')
    def member_state(user_id: str, body: MemberState, user=Depends(actor)):
        auth.set_enabled(user, user_id, body.enabled, body.reason)
        return {'ok': True}

    @app.get(PREFIX + '/tasks')
    def listing(month: str = Query('', pattern=r'^(\d{4}-(0[1-9]|1[0-2]))?$'),
                mine: str = Query('', pattern=r'^(lead|created|joined|outstanding)?$'), status: str = Query('', max_length=30),
                cursor: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100), user=Depends(actor)):
        return tasks.snapshot(user, month=month, mine=mine, status=status, cursor=cursor, limit=limit)

    @app.post(PREFIX + '/tasks')
    def create(body: TaskFields, user=Depends(actor), idempotency_key: str = Header('')):
        return tasks.create(user, body.model_dump(), idempotency_key)

    @app.get(PREFIX + '/tasks/{task_id}')
    def detail(task_id: str, user=Depends(actor)):
        return tasks.detail(user, task_id)

    @app.patch(PREFIX + '/tasks/{task_id}')
    def edit(task_id: str, body: EditTask, user=Depends(actor), idempotency_key: str = Header('')):
        return tasks.mutate(user, task_id, 'edit', body.model_dump(), idempotency_key)

    @app.post(PREFIX + '/tasks/{task_id}/actions/{action}')
    def command(task_id: str, action: str, body: Command, user=Depends(actor), idempotency_key: str = Header('')):
        require(action in {'publish', 'copy', 'cancel', 'reopen', 'claim', 'join', 'leave',
            'assign-owner', 'owner-transfer', 'accept-transfer', 'cancel-transfer', 'add-work',
            'update-work', 'comment', 'submit', 'withdraw', 'complete', 'request-changes'},
            'unknown_action', '不支持此操作。', 404)
        return tasks.mutate(user, task_id, action, body.model_dump(), idempotency_key)

    @app.get(PREFIX + '/tasks/{task_id}/activity')
    def activity(task_id: str, before: int = Query(0, ge=0), user=Depends(actor)):
        return tasks.activity(user, task_id, before)

    @app.get(PREFIX + '/events')
    async def events(request: Request, after: int = Query(0, ge=0), user=Depends(actor)):
        supplied = request.headers.get('last-event-id', '')
        cursor = int(supplied) if supplied.isdigit() else after

        async def stream():
            nonlocal cursor
            yield 'retry: 3000\n\n'
            while not await request.is_disconnected():
                try:
                    current = await asyncio.to_thread(auth.authenticate, session_token(request), touch=False)
                    with database.transaction() as db:
                        high_water = db.get(Project, 1).sequence
                    if cursor > high_water:
                        cursor = high_water
                        yield f'id: {cursor}\nevent: reset\ndata: {{}}\n\n'
                    rows = await asyncio.to_thread(tasks.events, current, cursor)
                except Problem as exc:
                    yield 'event: auth-error\ndata: ' + json.dumps({'message': exc.message}) + '\n\n'
                    return
                for row in rows:
                    cursor = row['id']
                    event = {'task_id': row['task_id'], 'action': row['action']}
                    yield f'id: {cursor}\nevent: change\ndata: {json.dumps(event)}\n\n'
                yield ': keepalive\n\n'
                await asyncio.sleep(1)
        return StreamingResponse(stream(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})

    return app
