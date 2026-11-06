"""Local-only read proxy. Delivery data never enters intention notification queues."""

import os
import asyncio
import json
import sqlite3
import re
import base64
from datetime import date

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from uuid import UUID

from .config import settings
from fastapi.responses import FileResponse
from .delivery_dialogues import DialogueService
from .delivery_materials import DeliveryMaterialWorker
from .delivery_image_quotes import ImageQuoteSource
from .delivery_mentions import DeliveryMentions, MentionSelectionError
from .delivery_notifications import DeliveryNotificationService, DeliveryNotificationStore
from .delivery_call_images import bundle_message
from .intention_notifications import WeChatNotificationBridge, IntentionNotificationError

router = APIRouter(prefix="/api/delivery-management", tags=["配送管理"])
REMOTE = "https://youshenghe666.com/api/v1/delivery-management/consumer"
_remote_client_instance = None


def remote_client():
    global _remote_client_instance
    if _remote_client_instance is None or getattr(_remote_client_instance,'is_closed',False):
        _remote_client_instance=httpx.AsyncClient(timeout=httpx.Timeout(12,connect=5),follow_redirects=False,trust_env=False,
                                                limits=httpx.Limits(max_connections=10,max_keepalive_connections=5,keepalive_expiry=60))
    return _remote_client_instance


def integration_token():
    token = os.getenv("YSH_DELIVERY_MANAGEMENT_TOKEN", "").strip() or os.getenv("YSH_ROBOT_DISPATCH_TOKEN", "").strip()
    if token:
        return token
    path = settings.cache_path.with_name("intention_notifications.sqlite")
    if not path.exists():
        return ""
    # Reuse connection credentials only, without initializing or modifying the existing queue.
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
            row = connection.execute("SELECT value FROM intention_notification_meta WHERE key='api_token'").fetchone()
            return str(row[0]).strip() if row else ""
    except sqlite3.Error:
        return ""


def require_local(request):
    if not request.client or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(403, "配送管理仅允许本机访问")
    origin = request.headers.get("origin")
    if request.method != "GET" and origin and origin not in {"http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:4173", "http://localhost:4173"}:
        raise HTTPException(403, "不允许的配置请求来源")


async def fetch_remote(path, params, *, method="GET", payload=None):
    token = integration_token()
    if not token:
        raise HTTPException(503, "尚未配置服务器对接令牌")
    try:
        client=remote_client()
        for attempt in range(2 if method=='GET' else 1):
            headers={"Authorization": f"Bearer {token}"}
            try:
                if method == "POST":
                    result = await client.post(REMOTE + path, json=payload, headers=headers)
                else:
                    result = await client.get(REMOTE + path, params={k:v for k,v in params.items() if v is not None}, headers=headers)
            except httpx.RequestError:
                if method!='GET' or attempt:
                    raise
                await asyncio.sleep(0.3)
                continue
            result.raise_for_status()
            return result.json()
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code in {400,404,409,422,503}:
            try:
                detail=exc.response.json().get('detail')
            except ValueError:
                detail=None
            if isinstance(detail,str):
                raise HTTPException(code,detail) from exc
        raise HTTPException(502, f"配送管理服务器接口返回 {code}，请检查接口部署及凭证") from exc
    except (httpx.RequestError, ValueError) as exc:
        raise HTTPException(502, "配送管理服务器暂时不可用，请重试") from exc


async def remote_get(request, path, params):
    require_local(request)
    return await fetch_remote(path, params)


image_quote_source = ImageQuoteSource(settings.cache_path.with_name('delivery_image_quote_source.json'),
                                     settings.repository_root, settings.cache_path.parent/'delivery_quote_messages')
notification_service = DeliveryNotificationService(
    DeliveryNotificationStore(settings.cache_path.with_name("delivery_notifications.sqlite")),
    WeChatNotificationBridge(settings.friend_automation_bridge_url), fetch_remote, quote_source=image_quote_source,
)
mention_service = DeliveryMentions(notification_service.store, notification_service.bridge)
notification_service.call_quotes.mentions = mention_service
dialogue_service = DialogueService(notification_service.store, settings.cache_path.parent / 'delivery_dialogues',
                                  settings.panel_root / 'scripts' / 'render-delivery-dialogue.cjs', notification_service.bridge,
                                  settings.cache_path, image_quote_source, mentions=mention_service)
material_worker = DeliveryMaterialWorker(notification_service, dialogue_service, fetch_remote)


async def start_delivery_notifications():
    mention_service.initialize()
    dialogue_service.recover()
    if os.getenv("PYTEST_CURRENT_TEST") or os.getenv("YSH_DELIVERY_NOTIFICATIONS_AUTOSTART", "1") == "0":
        notification_service.store.initialize()
        return
    await notification_service.start()
    # The worker admits only server-selected business-relevant materials.
    await material_worker.start()


async def stop_delivery_notifications():
    global _remote_client_instance
    notification_service.stop_event.set()
    await material_worker.stop()
    await notification_service.stop()
    if _remote_client_instance is not None:
        await _remote_client_instance.aclose()
        _remote_client_instance=None


class TargetGroupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    room_id: str = Field(min_length=10, max_length=256, pattern=r"^[^\s]+@chatroom$")
    name: str = Field(min_length=1, max_length=128)


class DeliveryPushConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    target_groups: list[TargetGroupInput] = Field(max_length=50)
    scheduled_enabled: bool = False
    reminder_enabled: bool = False
    result_notifications_enabled: bool = False
    failure_notifications_enabled: bool = False
    stage_times: list[str] = Field(default_factory=lambda: ['12:00','16:00','18:30','21:00'], min_length=1,max_length=8)
    first_call_minutes: int = Field(60,ge=5,le=1440)


class OverviewPushInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: date
    request_id: UUID


class MentionTeamInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    room_id: str = Field(min_length=10, max_length=256, pattern=r'^[^\s]+@chatroom$')
    team_id: str = Field(pattern=r'^[2345]$')
    wxids: list[str] = Field(default_factory=list, max_length=50)


class MentionConfigInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    account: str | None = Field(default=None, max_length=256)
    selections: list[MentionTeamInput] = Field(default_factory=list, max_length=200)


@router.get('/notifications/mentions')
async def mention_configuration(request: Request):
    require_local(request)
    return mention_service.configuration()


@router.get('/notifications/mention-members')
async def mention_members(request: Request, room_id: str = Query(min_length=10, max_length=256)):
    require_local(request)
    try:
        return await mention_service.members(room_id)
    except MentionSelectionError as exc:
        raise HTTPException(422, str(exc)) from exc
    except IntentionNotificationError as exc:
        raise HTTPException(503, '群成员读取失败，请确认微信和桥接在线') from exc


@router.post('/notifications/mentions')
async def save_mention_configuration(request: Request, payload: MentionConfigInput):
    require_local(request)
    try:
        async with notification_service.lock:
            return await mention_service.save(payload.revision, payload.account, [r.model_dump() for r in payload.selections])
    except MentionSelectionError as exc:
        raise HTTPException(422, str(exc)) from exc
    except IntentionNotificationError as exc:
        raise HTTPException(503, '@ 设置未保存：群成员查询暂不可用') from exc


@router.get("/notifications")
async def notification_status(request: Request):
    require_local(request)
    return {**notification_service.status(), 'materials': material_worker.status()}


@router.get('/notifications/stages/{report_key}')
async def stage_report(request:Request,report_key:str):
    require_local(request)
    notification_service.store.initialize()
    report=notification_service.store.stage(report_key)
    if not report:
        raise HTTPException(404,'阶段报表不存在')
    with notification_service.store.connect() as db:
        message=db.execute('SELECT message FROM deliveries WHERE event_key=? LIMIT 1',(report_key,)).fetchone()
    return {**report,'payload':json.loads(report['payload']) if report['payload'] else None,
            'message':message[0] if message else None}


@router.get('/calls/{call_id}/report-preview')
async def call_report_preview(request: Request, call_id: int, day: date):
    require_local(request)
    payload = await fetch_remote(f'/calls/{call_id}/monthly-report', {'day': day.isoformat()})
    path = await notification_service.call_images.render('preview:' + payload['call']['event_key'], payload)
    return {'message': bundle_message(payload), 'snapshot_at': payload['snapshot_at'],
            'image_data_url': 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode('ascii'),
            'bridge_called': False, 'validity_note': payload.get('validity_note')}


@router.get('/notifications/calls/{event_key}/image')
async def call_report_image(request: Request, event_key: str):
    require_local(request)
    payload = notification_service.store.call_report(event_key)
    if not payload:
        raise HTTPException(404, '该通话没有图文快照；历史通知不补发')
    path = await notification_service.call_images.render(event_key, payload)
    return FileResponse(path, media_type='image/png', headers={'Cache-Control': 'no-store'})


@router.post('/notifications/calls/{event_key}/retry-render')
async def retry_call_render(request: Request, event_key: str):
    require_local(request)
    async with notification_service.lock:
        store = notification_service.store
        groups = store.configuration()['target_groups']
        if not store.call_report(event_key):
            raise HTTPException(404, '通话图文快照不存在')
        with store.connect() as db:
            for group in groups:
                db.execute("""UPDATE deliveries SET status='queued',error=NULL,render_attempts=0,next_render_at=NULL
                    WHERE event_key=? AND room_id=? AND media_type='call_bundle' AND status='render_failed'
                    AND text_status IS NULL""", (event_key, group['room_id']))
    return notification_service.status()


@router.get('/notifications/stages/{report_key}/image')
async def stage_report_image(request: Request, report_key: str):
    require_local(request)
    notification_service.store.initialize()
    report = notification_service.store.stage(report_key)
    if not report or report['media_type'] != 'image' or not report['payload']:
        raise HTTPException(404, '该记录不是图片报表；历史文字快照保持原样')
    try:
        path = await notification_service.stage_images.render(report_key, json.loads(report['payload']))
    except Exception as exc:
        raise HTTPException(503, '报表图片暂时无法生成；未发送文字替代') from exc
    return FileResponse(path, media_type='image/png', headers={'Cache-Control':'no-store'})


@router.post('/notifications/stages/{report_key}/retry-render')
async def retry_stage_render(request: Request, report_key: str):
    require_local(request)
    async with notification_service.lock:
        store = notification_service.store
        store.initialize()
        report = store.stage(report_key)
        if not report or report['media_type'] != 'image':
            raise HTTPException(404, '图片阶段报表不存在')
        groups = store.configuration()['target_groups']
        if not groups:
            raise HTTPException(422, '请先配置配送目标群')
        with store.connect() as db:
            for group in groups:
                db.execute("""UPDATE deliveries SET status='queued',error=NULL,render_attempts=0,next_render_at=NULL
                    WHERE event_key=? AND room_id=? AND media_type='image' AND status='render_failed'""",(report_key,group['room_id']))
    return notification_service.status()


@router.get("/groups")
async def notification_groups(request: Request):
    require_local(request)
    try:
        groups = await notification_service.bridge.groups()
        return {"items": [{"room_id": g.room_id, "name": g.name} for g in groups]}
    except IntentionNotificationError as exc:
        raise HTTPException(503, "微信群列表读取失败，请确认微信及桥接服务在线") from exc


@router.post("/notifications/config")
async def configure_notifications(request: Request, payload: DeliveryPushConfig):
    require_local(request)
    if len({g.room_id for g in payload.target_groups}) != len(payload.target_groups):
        raise HTTPException(422, "请勿重复选择目标群")
    notification_service.store.initialize()
    old=notification_service.store.configuration()
    effective={**old,**payload.model_dump(exclude_unset=True)}
    if any(effective[key] for key in ('enabled','scheduled_enabled','reminder_enabled','result_notifications_enabled','failure_notifications_enabled')) and not integration_token():
        raise HTTPException(503, "尚未配置服务器对接令牌")
    try:
        policy=payload.model_dump(exclude_unset=True,exclude={'enabled','target_groups'})
        await notification_service.configure(payload.enabled, [g.model_dump() for g in payload.target_groups], **policy)
    except (ValueError, IntentionNotificationError) as exc:
        raise HTTPException(422, "群设置未保存：" + str(exc)) from exc
    return notification_service.status()


@router.post("/notifications/send-overview")
async def send_overview(request: Request, payload: OverviewPushInput):
    require_local(request)
    if notification_service.task is None or notification_service.task.done():
        raise HTTPException(503, "配送发送服务未运行，请重启本地服务后重试")
    notification_service.store.initialize()
    try:
        return await notification_service.send_overview(payload.day, str(payload.request_id))
    except (ValueError, IntentionNotificationError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/overview")
async def overview(request: Request, day: date | None = None):
    return await remote_get(request, "/overview", {"day": day})


@router.get("/calls")
async def calls(request: Request, day: date | None = None, before_id: int | None = Query(None, ge=1),
                limit: int = Query(100, ge=1, le=200)):
    return await remote_get(request, "/calls", {"day": day, "before_id": before_id, "limit": limit})


@router.get("/failures")
async def failures(request: Request, before_id: int | None = Query(None, ge=1),
                   limit: int = Query(100, ge=1, le=200)):
    return await remote_get(request, "/failures", {"before_id": before_id, "limit": limit})


@router.get("/orders/{order_id}/evidence")
async def evidence(request: Request, order_id: int, before_id: int | None = Query(None, ge=1),
                   limit: int = Query(100, ge=1, le=200)):
    return await remote_get(request, f"/orders/{order_id}/evidence", {"before_id": before_id, "limit": limit})


@router.get('/workflow/status')
async def workflow_status(request:Request):
    return await remote_get(request,'/workflow/status',{})


@router.post('/workflow/settings')
async def workflow_settings(request:Request,payload:dict):
    require_local(request)
    return await fetch_remote('/workflow/settings',{},method='POST',payload=payload)


@router.get('/cases')
async def cases(request:Request,before_id:int|None=Query(None,ge=1)):
    return await remote_get(request,'/cases',{'before_id':before_id,'limit':50})


@router.get('/cases/owners')
async def owners(request:Request):
    return await remote_get(request,'/cases/owners',{})


@router.get('/cases/{case_id}')
async def case_detail(request:Request,case_id:int):
    return await remote_get(request,f'/cases/{case_id}',{})


class DialogueGenerateInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    call_id: int = Field(gt=0)
    regenerate: bool = False


class DialogueSendInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    image_ids: list[str] = Field(min_length=1, max_length=200)
    room_ids: list[str] = Field(min_length=1, max_length=50)
    simulate: bool = True
    references: dict[str, str] = Field(default_factory=dict, max_length=50)


@router.get('/dialogues/references')
async def dialogue_references(request: Request, room_id: str, search: str = Query('', max_length=100)):
    require_local(request)
    try:
        rows = dialogue_service.quote_messages(room_id, search)
        return {'items': [{k: r[k] for k in ('message_key', 'sender_name', 'content', 'create_time')} for r in rows]}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get('/cases/{case_id}/dialogues')
async def dialogue_list(request: Request, case_id: int):
    require_local(request)
    return {'items': dialogue_service.listing(case_id)}


@router.post('/cases/{case_id}/dialogues/generate')
async def dialogue_generate(request: Request, case_id: int, payload: DialogueGenerateInput):
    require_local(request)
    case = await fetch_remote(f'/cases/{case_id}', {})
    try:
        return {'items': await dialogue_service.generate(case, payload.call_id, payload.regenerate)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get('/dialogues/{image_id}/image')
async def dialogue_image(request: Request, image_id: str, download: bool = False):
    require_local(request)
    try:
        row, path = dialogue_service.image(image_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path, media_type='image/png', filename=f"{row['title']}-{row['call_id']}.png" if download else None,
                        headers={'Cache-Control': 'no-store'})


@router.post('/cases/{case_id}/dialogues/send')
async def dialogue_send(request: Request, case_id: int, payload: DialogueSendInput):
    require_local(request)
    try:
        link = await get_archive_link(case_id)
        if any(payload.references.values()):
            raise ValueError('不再搜索引用历史消息；以本次无法配送反馈为主消息')
        case = await fetch_remote(f'/cases/{case_id}', {})
        if not payload.simulate:
            from .delivery_materials import ready_calls
            allowed = {call['id'] for call in ready_calls(case)}
            for image_id in payload.image_ids:
                row, _ = dialogue_service.image(image_id)
                if row['call_id'] not in allowed:
                    with notification_service.store.connect() as db:
                        already_sent = all((receipt := dialogue_service.receipt(db, 'dialogue_receipts', row, room))
                                           and receipt['status'] == 'bridge_accepted' for room in payload.room_ids)
                    if not already_sent:
                        raise ValueError('该录音尚未确认为外呼成单或本次失败沟通，请先在录音筛选中确认业务用途')
        return await dialogue_service.send(case_id, payload.image_ids, payload.room_ids, payload.simulate, {}, link['url'], case)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


async def get_archive_link(case_id):
    result = await fetch_remote(f'/cases/{case_id}/archive-link', {}, method='POST', payload={})
    path = result.get('path', '')
    if not re.fullmatch(r'/d/[A-Za-z0-9_-]{16}', path):
        raise HTTPException(502, '服务器档案链接无效，未发送群消息')
    return {'url': 'https://youshenghe666.com' + path, 'requires_login': True}


@router.post('/cases/{case_id}/archive-link')
async def archive_link(request: Request, case_id: int):
    require_local(request)
    return await get_archive_link(case_id)


@router.post('/orders/{order_id}/analysis')
async def create_analysis(request:Request,order_id:int):
    require_local(request)
    return await fetch_remote(f'/orders/{order_id}/analysis',{},method='POST',payload={})


@router.post('/cases/{case_id}/update')
async def case_update(request:Request,case_id:int,payload:dict):
    require_local(request)
    return await fetch_remote(f'/cases/{case_id}/update',{},method='POST',payload=payload)


@router.post('/cases/{case_id}/analyze')
async def case_analyze(request:Request,case_id:int,payload:dict):
    require_local(request)
    return await fetch_remote(f'/cases/{case_id}/analyze',{},method='POST',payload=payload)


@router.get('/orders/{order_id}/history')
async def order_history(request:Request,order_id:int):
    return await remote_get(request,f'/orders/{order_id}/history',{})


@router.get('/alerts')
async def alerts(request:Request,first_call_minutes:int=Query(60,ge=5,le=1440)):
    return await remote_get(request,'/alerts',{'first_call_minutes':first_call_minutes})
