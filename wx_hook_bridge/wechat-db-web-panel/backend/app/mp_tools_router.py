from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import threading
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import settings
from .mp_export import (
    MPArticleExporter,
    MPExportError,
    allocate_export_directory,
)
from .mp_native_context import NativeArticleContextStore
from .mp_tokens import (
    BrowserEventLogTailer,
    MPTokenCaptureService,
    MPTokenStore,
)


MP_CACHE_ROOT = settings.cache_path.parent.resolve()
MP_TOKEN_PATH = MP_CACHE_ROOT / "mp_tokens.json"
MP_EXPORT_ROOT = MP_CACHE_ROOT / "mp_exports"
MP_BROWSER_EVENT_LOG = settings.repository_root / "bridge" / "control" / "browser_events.jsonl"
MP_MEMORY_SNAPSHOT_LIMIT = 1 * 1024 * 1024
MP_JSON_BODY_LIMIT = 4 * 1024 * 1024
_export_semaphore = threading.BoundedSemaphore(2)
logger = logging.getLogger(__name__)

token_store = MPTokenStore(MP_TOKEN_PATH)
token_capture = MPTokenCaptureService(token_store)
article_context_store = NativeArticleContextStore()
browser_event_tailer = BrowserEventLogTailer(
    MP_BROWSER_EVENT_LOG,
    token_capture,
    event_consumer=article_context_store.ingest,
)
article_exporter = MPArticleExporter(token_store)

router = APIRouter(prefix="/api/mp", tags=["official-account"])


def start_mp_services() -> None:
    """Start local background capture services (safe to call repeatedly)."""
    global article_exporter
    if getattr(article_exporter.client, "is_closed", False):
        article_exporter = MPArticleExporter(token_store)
    article_context_store.scan_recent_log(MP_BROWSER_EVENT_LOG)
    browser_event_tailer.start()


def stop_mp_services() -> None:
    """Stop capture and release the shared HTTP client."""
    browser_event_tailer.stop()
    article_exporter.close()


class MPExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=10, max_length=8192)
    formats: list[Literal["md", "markdown", "html", "pdf"]] = Field(
        default_factory=lambda: ["markdown", "html", "pdf"],
        min_length=1,
        max_length=3,
    )
    localize_resources: bool = True

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlsplit(value.strip())
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("url has an invalid authority") from exc
        if (
            parsed.scheme.casefold() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
        ):
            raise ValueError("url must use https")
        host = parsed.hostname.casefold().rstrip(".")
        if host != "mp.weixin.qq.com":
            raise ValueError("url must point to mp.weixin.qq.com")
        return value.strip()


def _require_loopback(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(status_code=403, detail="official-account API is loopback-only")


def _capture_response(payload: Any, source: str) -> dict[str, Any]:
    result = token_capture.ingest(payload, source=source)
    return {
        "captured": list(result.captured),
        "changed": list(result.changed),
        "source": result.source,
        "token": result.status,
    }


async def _read_bounded_memory_snapshot(request: Request) -> bytes:
    """Read one caller-provided memory slice without unbounded buffering."""

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MP_MEMORY_SNAPSHOT_LIMIT:
                raise HTTPException(
                    status_code=413, detail="memory snapshot is too large"
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid content-length") from exc

    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        if size + len(chunk) > MP_MEMORY_SNAPSHOT_LIMIT:
            raise HTTPException(status_code=413, detail="memory snapshot is too large")
        size += len(chunk)
        chunks.append(chunk)
    snapshot = b"".join(chunks)
    if not snapshot:
        raise HTTPException(status_code=422, detail="memory snapshot is empty")
    return snapshot


async def _read_bounded_json_object(request: Request) -> dict[str, Any]:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
    if media_type != "application/json":
        raise HTTPException(status_code=415, detail="request requires application/json")
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MP_JSON_BODY_LIMIT:
                raise HTTPException(status_code=413, detail="JSON request is too large")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid content-length") from exc
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        if size + len(chunk) > MP_JSON_BODY_LIMIT:
            raise HTTPException(status_code=413, detail="JSON request is too large")
        size += len(chunk)
        chunks.append(chunk)
    try:
        payload = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="request body must be JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="request body must be a JSON object")
    return payload


@router.get("/tokens")
@router.get("/tokens/status", include_in_schema=False)
def mp_token_status(request: Request) -> dict[str, Any]:
    _require_loopback(request)
    article_context_status = article_context_store.status()
    return {
        "token": token_store.status(),
        "sources": {
            "native_browser_event_log_configured": True,
            "native_browser_event_log_exists": MP_BROWSER_EVENT_LOG.is_file(),
            "native_article_context_available": article_context_status["available"],
            "native_article_context_count": article_context_status["count"],
            "native_article_context_captured_at": article_context_status["captured_at"],
            "cdp_ingest_path": "/api/mp/tokens/cdp",
            "memory_snapshot_path": "/api/mp/tokens/memory",
            "memory_snapshot_max_bytes": MP_MEMORY_SNAPSHOT_LIMIT,
        },
    }


@router.post("/tokens/ingest")
async def mp_token_ingest(
    request: Request,
    source: Annotated[str, Query(min_length=1, max_length=80)] = "browser-request",
) -> dict[str, Any]:
    _require_loopback(request)
    payload = await _read_bounded_json_object(request)
    return _capture_response(payload, source)


@router.post("/tokens/cdp")
async def mp_token_cdp_ingest(
    request: Request,
) -> dict[str, Any]:
    _require_loopback(request)
    event = await _read_bounded_json_object(request)
    result = token_capture.ingest_cdp_event(event)
    return {
        "captured": list(result.captured),
        "changed": list(result.changed),
        "source": result.source,
        "token": result.status,
    }


@router.post("/tokens/memory")
@router.post("/tokens/memory-snapshot", include_in_schema=False)
async def mp_token_memory_ingest(
    request: Request,
    source: Annotated[str, Query(min_length=1, max_length=80)] = "memory-snapshot",
) -> dict[str, Any]:
    """Ingest one bounded UTF-8/UTF-16 memory slice supplied by a local hook."""

    _require_loopback(request)
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
    if media_type not in {"application/octet-stream", "text/plain"}:
        raise HTTPException(
            status_code=415,
            detail="memory snapshot requires application/octet-stream or text/plain",
        )
    snapshot = await _read_bounded_memory_snapshot(request)
    result = token_capture.ingest_memory_snapshot(snapshot, source=source)
    return {
        "captured": list(result.captured),
        "changed": list(result.changed),
        "source": result.source,
        "token": result.status,
    }


@router.delete("/tokens")
def mp_token_clear(request: Request) -> dict[str, Any]:
    _require_loopback(request)
    token_store.clear()
    return {"cleared": True, "token": token_store.status()}


def _run_article_export(payload: MPExportRequest):
    with _export_semaphore:
        output_dir = allocate_export_directory(MP_EXPORT_ROOT, payload.url)
        try:
            return article_exporter.export(
                payload.url,
                output_dir=output_dir,
                formats=payload.formats,
                localize_resources=payload.localize_resources,
            )
        except Exception:
            shutil.rmtree(output_dir, ignore_errors=True)
            raise


@router.post("/exports")
@router.post("/export", include_in_schema=False)
@router.post("/article/export", include_in_schema=False)
async def mp_article_export(request: Request, payload: MPExportRequest) -> dict[str, Any]:
    _require_loopback(request)
    try:
        result = await asyncio.to_thread(_run_article_export, payload)
    except MPExportError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="article export failed") from exc
    except Exception as exc:
        logger.exception("unexpected official-account export failure")
        raise HTTPException(status_code=500, detail="article export failed") from exc
    response = result.as_dict()
    response.pop("output_dir", None)
    response["download_url"] = f"/api/mp/exports/{result.export_id}/backup.zip"
    response["files"] = {
        name: f"/api/mp/exports/{result.export_id}/{relative}"
        for name, relative in result.files.items()
    }
    return response


@router.get("/exports/{export_id}/{relative_path:path}")
def mp_article_export_file(
    request: Request,
    export_id: str,
    relative_path: str,
) -> FileResponse:
    _require_loopback(request)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", export_id):
        raise HTTPException(status_code=404, detail="export not found")
    export_root = (MP_EXPORT_ROOT / export_id).resolve()
    candidate = (export_root / relative_path).resolve()
    if (
        not export_root.is_relative_to(MP_EXPORT_ROOT.resolve())
        or not candidate.is_relative_to(export_root)
        or not candidate.is_file()
    ):
        raise HTTPException(status_code=404, detail="export file not found")
    return FileResponse(candidate, filename=candidate.name)
