from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .friend_automation import FriendAutomation, FriendAutomationError
from .delivery_management_router import (
    router as delivery_management_router, start_delivery_notifications, stop_delivery_notifications,
)
from .intention_notifications_router import (
    router as intention_notifications_router,
    start_intention_notification_service,
    stop_intention_notification_service,
)
from .repository import WeChatRepository
from .realtime import REALTIME_BODY_LIMIT
from .live_chat import LiveChat, create_live_router
from .active_roster import ActiveWechatRoster
from .runtime_health import router as runtime_health_router
from .integrations import Integrations, create_integrations_router
from .analysis_snapshots import AnalysisSnapshots, create_snapshot_router
from .archive_insights import ArchiveInsights, create_insights_router
from .archive_exports import ArchiveExports, create_exports_router
from .archive_local import ArchiveLocal, create_local_router
from .mp_router import router as mp_router
from .mp_tools_router import start_mp_services, stop_mp_services
from .sns_backup import router as sns_router, service as sns_service
from .sns_sync import SnsSnapshotSyncError
from .ai.service import AIService, AIError
from .ai.decisions import DecisionService
from .ai.store import AIStore
from .ai.router import create_ai_router
from .ai.tool_store import ToolStore
from .retrieval.providers import DeepSeekSearchFetch, GPTWebSearch
from .retrieval.service import RetrievalCache, Retriever
from .auto_reply.engine import ReplyEngine
from .auto_reply.service import GroupReplyService
from .auto_reply.router import create_reply_router
from .group_reports.service import GroupReportService
from .group_reports.router import create_group_report_router


repository = WeChatRepository()
live_chat = LiveChat(
    repository.settings.cache_path.with_name('live_chat.sqlite'),
    repository.settings.friend_automation_bridge_url,
    repository.settings.database_root.parents[2] / 'bridge' / 'control' / 'realtime_relay_status.json',
    roster=ActiveWechatRoster(
        repository.settings.friend_automation_bridge_url,
        repository.settings.repository_root,
        repository.settings.report_path.parent / 'keys_active.json',
        repository.settings.cache_path.parent,
    ),
)
logger = logging.getLogger(__name__)
integrations = Integrations(
    live_chat, repository.settings.cache_path.with_name('integrations.sqlite'),
    available=os.getenv('WECHAT_CHAT_ONLY') == '1',
)
analysis_snapshots = AnalysisSnapshots(repository.settings, live_chat)
archive_insights = ArchiveInsights(repository)
archive_exports = ArchiveExports(archive_insights, live_chat)
archive_local = ArchiveLocal(archive_insights, live_chat)
ai_service = AIService(AIStore(repository.settings.cache_path.parent / 'ai' / 'settings.sqlite'))
ai_tools = ToolStore(ai_service.store)
deepseek_tools = DeepSeekSearchFetch(ai_tools)
retriever = Retriever(deepseek_tools, GPTWebSearch(ai_service.store),
                      RetrievalCache(repository.settings.cache_path.parent / 'retrieval' / 'cache.sqlite'))
group_replies = GroupReplyService(live_chat, ai_service, ReplyEngine(ai_service, retriever, DecisionService(ai_service)),
    repository.settings.cache_path.parent / 'group-replies',
    repository.settings.repository_root.parent / '.local' / 'faq-analysis' / 'pil-faq-candidates-20261106.json')
group_reports = GroupReportService(live_chat, ai_service, repository.settings.cache_path.parent / 'group-reports')
live_chat.notification_observer = archive_local.observe
REALTIME_ALLOWED_ORIGINS = {
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:4173",
    "http://localhost:4173",
}
_realtime_wakeup: asyncio.Event | None = None
_realtime_stop: asyncio.Event | None = None
_realtime_generation = 0
_friend_automation: FriendAutomation | None = None
_sns_sync_stop: asyncio.Event | None = None
try:
    SNS_SYNC_INTERVAL_SECONDS = max(
        2.0, float(os.getenv("WECHAT_SNS_SYNC_INTERVAL_SECONDS", "5"))
    )
except ValueError:
    SNS_SYNC_INTERVAL_SECONDS = 5.0


class FriendAutomationToggle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


async def _realtime_reconcile_loop() -> None:
    assert _realtime_wakeup is not None and _realtime_stop is not None
    while not _realtime_stop.is_set():
        signaled = False
        try:
            await asyncio.wait_for(_realtime_wakeup.wait(), timeout=1.0)
            signaled = True
        except TimeoutError:
            pass
        if _realtime_stop.is_set():
            break
        if signaled:
            # Use a trailing debounce so a relay burst is durably ACKed before
            # analytics work starts competing for CPU and disk.
            generation = _realtime_generation
            debounce_deadline = time.monotonic() + 2.0
            while not _realtime_stop.is_set():
                await asyncio.sleep(0.05)
                if (
                    generation == _realtime_generation
                    or time.monotonic() >= debounce_deadline
                ):
                    break
                generation = _realtime_generation
        _realtime_wakeup.clear()
        try:
            await asyncio.to_thread(repository.replay_realtime)
        except (OSError, sqlite3.DatabaseError, SnsSnapshotSyncError, ValueError) as exc:
            logger.error("realtime reconciliation failed: %s", exc)


def _signal_realtime_reconcile() -> None:
    global _realtime_generation
    _realtime_generation += 1
    if _realtime_wakeup is not None:
        _realtime_wakeup.set()


async def _sns_sync_loop() -> None:
    assert _sns_sync_stop is not None
    while not _sns_sync_stop.is_set():
        try:
            await asyncio.to_thread(sns_service.refresh_snapshot)
        except Exception as exc:
            logger.warning("SNS snapshot refresh failed: %s", exc)
        try:
            await asyncio.wait_for(
                _sns_sync_stop.wait(), timeout=SNS_SYNC_INTERVAL_SECONDS
            )
        except TimeoutError:
            pass


@asynccontextmanager
async def application_lifespan(_: FastAPI):
    global _friend_automation, _realtime_generation, _realtime_stop, _realtime_wakeup, _sns_sync_stop
    if os.getenv('WECHAT_CHAT_ONLY') == '1':
        # Replay real bridge envelopes only, never historical snapshot messages.
        with sqlite3.connect(f'{repository.realtime.path.as_uri()}?mode=ro', uri=True) as ledger:
            for row in ledger.execute('SELECT payload_json FROM realtime_events ORDER BY sequence'):
                try:
                    live_chat.ingest(json.loads(row[0]))
                except (ValueError, TypeError) as exc:
                    logger.warning('live chat retained an unparseable legacy event: %s', type(exc).__name__)
        await integrations.start()
        try:
            yield
        finally:
            await integrations.stop()
            await live_chat.close()
        return
    if not repository.configured:
        yield
        return
    try:
        await asyncio.to_thread(repository.ensure_cache)
    except (OSError, RuntimeError, sqlite3.DatabaseError, ValueError):
        # Keep diagnostics and independent live-chat routes available.
        yield
        return
    await asyncio.to_thread(repository.replay_realtime)
    try:
        await asyncio.to_thread(sns_service.refresh_snapshot)
    except (OSError, sqlite3.DatabaseError, SnsSnapshotSyncError, ValueError) as exc:
        logger.warning("initial SNS snapshot refresh failed: %s", exc)
    start_mp_services()
    await start_intention_notification_service()
    await start_delivery_notifications()
    _realtime_wakeup = asyncio.Event()
    _realtime_stop = asyncio.Event()
    _realtime_generation = 0
    task = asyncio.create_task(_realtime_reconcile_loop())
    _sns_sync_stop = asyncio.Event()
    sns_sync_task = asyncio.create_task(_sns_sync_loop())
    _friend_automation = FriendAutomation(repository, repository.settings)
    try:
        await _friend_automation.start()
        yield
    finally:
        await live_chat.close()
        await _friend_automation.stop()
        _friend_automation = None
        await stop_intention_notification_service()
        await stop_delivery_notifications()
        stop_mp_services()
        _realtime_stop.set()
        _realtime_wakeup.set()
        _sns_sync_stop.set()
        await task
        await sns_sync_task
        _realtime_stop = None
        _realtime_wakeup = None
        _sns_sync_stop = None


@asynccontextmanager
async def lifespan(application: FastAPI):
    await analysis_snapshots.start()
    await archive_exports.start()
    await group_reports.start()
    await group_replies.start()
    try:
        async with application_lifespan(application):
            yield
    finally:
        await group_replies.stop()
        await group_reports.stop()
        await archive_exports.close()
        await analysis_snapshots.close()


app = FastAPI(
    title="Local WeChat Database Panel API",
    version="1.0.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:4173",
        "http://localhost:4173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["127.0.0.1", "localhost", "testserver"],
)
@app.middleware("http")
async def require_analysis_snapshot(request: Request, call_next):
    path = request.url.path
    analysis_roots = (
        "/api/profile", "/api/overview", "/api/contacts", "/api/chat-list",
        "/api/media", "/api/contributions", "/api/conversations",
        "/api/messages", "/api/search", "/api/fts", "/api/avatars", "/api/resources",
    )
    if any(path == root or path.startswith(root + "/") for root in analysis_roots):
        await asyncio.to_thread(repository.reload_configuration_if_available)
        if not repository.configured:
            return JSONResponse(repository.health(), status_code=503)
    return await call_next(request)


app.include_router(runtime_health_router)
app.include_router(mp_router)
app.include_router(sns_router)
app.include_router(intention_notifications_router)
app.include_router(delivery_management_router)
app.include_router(create_live_router(live_chat))
app.include_router(create_integrations_router(integrations))
app.include_router(create_snapshot_router(analysis_snapshots))
app.include_router(create_insights_router(archive_insights))
app.include_router(create_exports_router(archive_exports))
app.include_router(create_local_router(archive_local))
@app.exception_handler(AIError)
async def ai_failure(request: Request, error: AIError):
    return JSONResponse({'detail': str(error)}, status_code=error.status_code)


app.include_router(create_ai_router(ai_service, ai_tools, deepseek_tools))
app.include_router(create_reply_router(group_replies))
app.include_router(create_group_report_router(group_reports))


@app.get("/api/health")
def health() -> dict:
    repository.reload_configuration_if_available()
    return repository.health()


def _loopback_request(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "testclient", "localhost"}:
        raise HTTPException(status_code=403, detail="realtime ingest is loopback-only")


def _validate_realtime_headers(request: Request) -> None:
    content_type = request.headers.get("content-type", "")
    media_type = content_type.split(";", 1)[0].strip().casefold()
    if media_type != "application/json":
        raise HTTPException(
            status_code=415, detail="realtime ingest requires application/json"
        )
    origin = request.headers.get("origin")
    if not origin:
        return
    normalized_origin = origin.strip().casefold().rstrip("/")
    if normalized_origin not in REALTIME_ALLOWED_ORIGINS:
        raise HTTPException(status_code=403, detail="realtime origin is not local")


async def _ingest_realtime(request: Request) -> JSONResponse:
    _loopback_request(request)
    _validate_realtime_headers(request)
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > REALTIME_BODY_LIMIT:
                raise HTTPException(status_code=413, detail="realtime event is too large")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid content-length") from exc
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > REALTIME_BODY_LIMIT:
            raise HTTPException(status_code=413, detail="realtime event is too large")
        chunks.append(chunk)
    body = b"".join(chunks)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="request body must be JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="event body must be a JSON object")
    try:
        if os.getenv('WECHAT_CHAT_ONLY') == '1':
            result = await asyncio.to_thread(live_chat.ingest, payload)
            return JSONResponse(result, status_code=200 if result['duplicate'] else 201)
        result = await asyncio.to_thread(repository.accept_realtime, payload)
        await asyncio.to_thread(live_chat.ingest, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    status_code = 200 if result.get("duplicate") else 201
    if result.get("status") == "pending":
        _signal_realtime_reconcile()
    return JSONResponse(result, status_code=status_code)


@app.post("/api/realtime/ingest")
async def realtime_ingest(request: Request) -> JSONResponse:
    return await _ingest_realtime(request)


@app.post("/api/bridge/messages")
async def bridge_message_compat(request: Request) -> JSONResponse:
    """Compatibility path used by older bridge builds."""
    return await _ingest_realtime(request)


@app.get("/api/realtime/status")
def realtime_status() -> dict:
    return repository.realtime_status()


def _friend_automation_service() -> FriendAutomation:
    if _friend_automation is None:
        raise HTTPException(status_code=503, detail="friend automation is starting")
    return _friend_automation


@app.get("/api/friend-automation/status")
def friend_automation_status(request: Request) -> dict:
    _loopback_request(request)
    return _friend_automation_service().status()


@app.post("/api/friend-automation")
async def configure_friend_automation(
    request: Request, payload: FriendAutomationToggle
) -> dict:
    _loopback_request(request)
    try:
        return await _friend_automation_service().set_enabled(payload.enabled)
    except FriendAutomationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _sse_frame(event_name: str, sequence: int | None, data: dict[str, object]) -> str:
    lines = [f"event: {event_name}"]
    if sequence is not None:
        lines.insert(0, f"id: {sequence}")
    lines.append(f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}")
    return "\n".join(lines) + "\n\n"


@app.get("/api/realtime/events")
async def realtime_events(
    request: Request,
    after: Annotated[int, Query(ge=0)] = 0,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    once: Annotated[bool, Query()] = False,
) -> StreamingResponse:
    _loopback_request(request)
    cursor = after
    if last_event_id:
        try:
            cursor = max(cursor, int(last_event_id))
        except ValueError:
            pass

    async def stream():
        repository.realtime.subscriber_opened()
        heartbeat_at = time.monotonic() + 15
        try:
            status = repository.realtime_status()
            oldest = int(status.get("oldest_sequence") or 0)
            newest = int(status.get("newest_sequence") or 0)
            cursor_is_stale = bool(cursor and oldest and cursor < oldest - 1)
            cursor_is_ahead = bool(cursor > newest)
            if cursor_is_stale or cursor_is_ahead:
                reason = (
                    "requested cursor is ahead of the published event ledger"
                    if cursor_is_ahead
                    else "requested cursor is outside retained event ledger"
                )
                yield _sse_frame(
                    "realtime.reset",
                    newest or None,
                    {
                        "event_id": "realtime-reset",
                        "event_type": "realtime.reset",
                        "cursor": newest,
                        "reason": reason,
                        "payload": {
                            "cursor": newest,
                            "reason": reason,
                        },
                    },
                )
                current = newest
            else:
                current = cursor
            while True:
                if await request.is_disconnected():
                    break
                rows = await asyncio.to_thread(
                    repository.realtime_events_since, current, 256
                )
                if rows:
                    for row in rows:
                        sequence = int(row["sequence"])
                        yield _sse_frame(
                            "realtime.message",
                            sequence,
                            {
                                "sequence": sequence,
                                "cursor": sequence,
                                "event_id": row["event_id"],
                                "event_type": row["event_type"],
                                "source": row["source"],
                                "status": row["status"],
                                "quality": row["quality"],
                                "payload": row["payload"],
                                "error": row.get("error"),
                            },
                        )
                        current = sequence
                    heartbeat_at = time.monotonic() + 15
                    continue
                if once:
                    yield ": realtime idle\n\n"
                    break
                if time.monotonic() >= heartbeat_at:
                    yield ": keepalive\n\n"
                    heartbeat_at = time.monotonic() + 15
                await asyncio.sleep(0.25)
        finally:
            repository.realtime.subscriber_closed()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/profile")
def profile() -> dict:
    return repository.profile()


@app.get("/api/overview")
def overview(days: Annotated[int, Query(ge=1, le=366)] = 30) -> dict:
    return repository.overview(days)


@app.get("/api/contacts")
def contacts(
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str, Query(max_length=200)] = "",
) -> dict:
    return repository.contacts(limit=limit, offset=offset, query=q)


@app.get("/api/contacts/graph")
def contacts_graph(limit: Annotated[int, Query(ge=2, le=100)] = 30) -> dict:
    return repository.contact_graph(limit)


@app.get("/api/chat-list")
def chat_list(
    limit: Annotated[int, Query(ge=1, le=500)] = 120,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str, Query(max_length=200)] = "",
    scope: Annotated[str, Query(pattern=r"^(all|groups|contacts)$")] = "all",
) -> dict:
    return repository.chat_sessions(
        limit=limit,
        offset=offset,
        query=q,
        scope=scope,
    )


@app.get("/api/chat-list/{username}")
def chat_list_detail(
    username: str,
    limit: Annotated[int, Query(ge=20, le=500)] = 180,
    year: Annotated[int | None, Query(ge=1970, le=2200)] = None,
    date: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
) -> dict:
    result = repository.chat_session(
        username,
        limit=limit,
        year=year,
        selected_date=date,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return result


@app.get("/api/media")
def media(limit: Annotated[int, Query(ge=1, le=200)] = 24) -> dict:
    return repository.media(limit)


@app.get("/api/contributions")
def contributions(
    year: Annotated[int | None, Query(ge=1970, le=2200)] = None,
    date: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
) -> dict:
    return repository.contributions(year=year, selected_date=date)


@app.get("/api/conversations")
def conversations(
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    start_date: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
    end_date: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
    session: Annotated[str | None, Query(max_length=200)] = None,
    category: Annotated[str | None, Query(max_length=40)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    hour: Annotated[int | None, Query(ge=0, le=23)] = None,
    date: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
    message: Annotated[str | None, Query(max_length=64)] = None,
    sort: Annotated[str, Query(pattern=r"^(asc|desc)$")] = "desc",
    order: Annotated[str | None, Query(pattern=r"^(asc|desc)$")] = None,
) -> dict:
    if date:
        start_date = date
        end_date = date
    return repository.conversations(
        limit=limit,
        offset=offset,
        start_date=start_date,
        end_date=end_date,
        session=session,
        category=category,
        query=q,
        hour=hour,
        message_id=message,
        sort=order or sort,
    )


@app.get("/api/conversations/export")
def conversations_export(
    format: Annotated[str, Query(pattern=r"^(csv|json|html)$")] = "csv",
    start_date: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
    end_date: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
    session: Annotated[str | None, Query(max_length=200)] = None,
    category: Annotated[str | None, Query(max_length=40)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    hour: Annotated[int | None, Query(ge=0, le=23)] = None,
    date: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
    order: Annotated[str, Query(pattern=r"^(asc|desc)$")] = "desc",
) -> Response:
    if date:
        start_date = date
        end_date = date
    payload, media_type, filename = repository.export_conversations(
        format_name=format,
        start_date=start_date,
        end_date=end_date,
        session=session,
        category=category,
        query=q,
        hour=hour,
        order=order,
    )
    return Response(
        content=payload,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/conversations/{episode_id}")
def conversation_episode(episode_id: str) -> dict:
    result = repository.episode_messages(episode_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    return result


@app.get("/api/messages/{message_id}")
def message(message_id: str) -> dict:
    result = repository.message(message_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return result


@app.get("/api/search")
def search(
    q: Annotated[str, Query(min_length=1, max_length=500)],
    scope: Annotated[str, Query(pattern=r"^(all|messages|contacts|favorites)$")] = "all",
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> dict:
    return repository.search(q, scope=scope, limit=limit)


@app.get("/api/fts/validate")
def validate_fts() -> dict:
    return repository.validate_fts()


@app.get("/api/avatars/{username}")
def avatar(username: str) -> Response:
    payload, media_type, etag = repository.avatar(username)
    return Response(
        content=payload,
        media_type=media_type,
        headers={
            "Cache-Control": "public, max-age=3600",
            "ETag": f'"{etag}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/api/resources/{kind}/{rowid}")
def resource(kind: str, rowid: int, download: bool = False) -> FileResponse:
    result = repository.resource(kind, rowid)
    if result is None:
        raise HTTPException(status_code=404, detail="Resource is unavailable")
    path, media_type, file_name = result
    return FileResponse(
        path,
        media_type=media_type,
        filename=file_name if download or kind == "file" else None,
        content_disposition_type="attachment" if download or kind == "file" else "inline",
        headers={
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/api/settings")
def local_settings() -> dict:
    return {
        "database_root": str(repository.database_root),
        "cache_path": str(repository.settings.cache_path),
        "timezone": repository.settings.timezone,
        "episode_gap_seconds": repository.settings.episode_gap_seconds,
        "response_max_seconds": repository.settings.response_max_seconds,
        "friend_automation_enabled": (
            _friend_automation.status()["enabled"] if _friend_automation else False
        ),
        "friend_automation_keyword": repository.settings.friend_automation_keyword,
        "friend_automation_group_capacity": (
            repository.settings.friend_automation_group_capacity
        ),
        "listen_policy": "127.0.0.1 only",
        "source_read_only": True,
        "telemetry": False,
        "snapshot_time": repository.snapshot_time,
    }
