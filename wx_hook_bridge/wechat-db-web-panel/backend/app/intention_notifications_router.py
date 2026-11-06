from __future__ import annotations

import asyncio
import os
import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .config import settings
from .intention_notifications import (
    DEFAULT_REST_BASE_URL,
    DEFAULT_WSS_URL,
    IntentionNotificationError,
    IntentionNotificationService,
    IntentionNotificationStore,
    RobotDispatchRemote,
    WeChatNotificationBridge,
)

router = APIRouter(prefix="/api/intention-notifications", tags=["意向单通知"])


class TargetGroupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    room_id: Annotated[str, Field(min_length=10, max_length=256)]
    name: Annotated[str, Field(min_length=1, max_length=128)]


class NotificationConfigInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    consumer_id: Annotated[
        str,
        Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"),
    ]
    target_groups: Annotated[list[TargetGroupInput], Field(max_length=50)]
    api_token: Annotated[str | None, Field(max_length=4096)] = None
    startup_policy: Literal["new_only", "include_pending"] | None = None


service = IntentionNotificationService(
    IntentionNotificationStore(
        settings.cache_path.with_name("intention_notifications.sqlite")
    ),
    WeChatNotificationBridge(settings.friend_automation_bridge_url),
    RobotDispatchRemote(
        os.getenv("YSH_ROBOT_DISPATCH_REST_URL", DEFAULT_REST_BASE_URL)
    ),
    wss_url=os.getenv("YSH_ROBOT_DISPATCH_WSS_URL", DEFAULT_WSS_URL),
    seed_token=os.getenv("YSH_ROBOT_DISPATCH_TOKEN", ""),
)


def _require_loopback(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(status_code=403, detail="意向单通知配置仅允许本机访问")


async def start_intention_notification_service() -> None:
    autostart = os.getenv("YSH_ROBOT_DISPATCH_AUTOSTART", "1").strip().casefold()
    if os.getenv("PYTEST_CURRENT_TEST") or autostart in {"0", "false", "no", "off"}:
        await asyncio.to_thread(service.store.initialize, service.seed_token)
        return
    await service.start()


async def stop_intention_notification_service() -> None:
    await service.stop()


@router.get("")
async def intention_notification_status(request: Request) -> dict:
    _require_loopback(request)
    try:
        return await service.status()
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/groups")
async def intention_notification_groups(request: Request) -> dict:
    _require_loopback(request)
    try:
        groups = await service.available_groups()
    except IntentionNotificationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"items": groups, "total": len(groups)}


@router.post("/config")
async def configure_intention_notifications(
    request: Request, payload: NotificationConfigInput
) -> dict:
    _require_loopback(request)
    try:
        await service.configure(
            enabled=payload.enabled,
            consumer_id=payload.consumer_id,
            target_groups=[group.model_dump() for group in payload.target_groups],
            api_token=payload.api_token,
            startup_policy=payload.startup_policy,
        )
    except (ValueError, OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await service.status()
