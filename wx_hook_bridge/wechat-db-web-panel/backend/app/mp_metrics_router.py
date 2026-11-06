from __future__ import annotations

import asyncio
import copy
import inspect
import json
from collections import deque
from threading import Lock
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .mp_metrics import (
    MAX_HTML_BYTES,
    MAX_JSON_BYTES,
    ArticleAuth,
    WeChatArticleUrlError,
    WeChatMetricsAuthError,
    WeChatMetricsError,
    WeChatMetricsUpstreamError,
    collect_article_metrics,
    extract_article_context,
    redact_metric_detail,
    sanitize_article_url,
)


router = APIRouter(prefix="/api/mp", tags=["official-account"])

# Keep batch collection bounded: every item may issue several upstream requests.
MAX_BATCH_URLS = 50
MAX_BATCH_CONCURRENCY = 8
DEFAULT_BATCH_CONCURRENCY = 4
MAX_HISTORY_ITEMS = 1_000
MAX_METRICS_BODY_BYTES = 18 * 1024 * 1024
MAX_BATCH_BODY_BYTES = 1 * 1024 * 1024

_batch_history: deque[dict[str, Any]] = deque(maxlen=MAX_HISTORY_ITEMS)
_batch_history_lock = Lock()


class ArticleAuthInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(max_length=2048)
    uin: str = Field(max_length=128)
    pass_ticket: str = Field(max_length=4096)
    appmsg_token: str | None = Field(default=None, max_length=4096)
    wxtoken: str = Field(default="777", max_length=128)
    device_type: str = Field(default="WindowsWechat", max_length=128)
    client_version: str = Field(default="63090b13", max_length=128)
    cookie: str | None = Field(default=None, max_length=16 * 1024)
    user_agent: str | None = Field(default=None, max_length=2048)


class ArticleMetricsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    url: str | None = Field(default=None, max_length=4096)
    html: str | None = None
    appmsgext: Any = None
    comments: Any = None
    tokens: ArticleAuthInput | None = None
    fetch_missing: bool | None = None
    include_comments: bool = True
    comment_limit: int = Field(default=100, ge=1, le=100)
    max_comment_pages: int = Field(default=3, ge=1, le=10)

    @field_validator("url")
    @classmethod
    def validate_url_size(cls, value: str | None) -> str | None:
        if value is not None and len(value.encode("utf-8", errors="ignore")) > 4096:
            raise ValueError("article URL is too large")
        return value


class ArticleMetricsBatchRequest(BaseModel):
    """Request body for bounded competitor-monitoring collection."""

    model_config = ConfigDict(extra="forbid")

    urls: list[str] = Field(min_length=1, max_length=MAX_BATCH_URLS)
    tokens: ArticleAuthInput | None = None
    fetch_missing: bool | None = None
    include_comments: bool = True
    concurrency: int = Field(
        default=DEFAULT_BATCH_CONCURRENCY,
        ge=1,
        le=MAX_BATCH_CONCURRENCY,
    )
    comment_limit: int = Field(default=100, ge=1, le=100)
    max_comment_pages: int = Field(default=3, ge=1, le=10)

    @field_validator("urls")
    @classmethod
    def validate_url_sizes(cls, values: list[str]) -> list[str]:
        oversized = next(
            (
                index
                for index, value in enumerate(values)
                if len(str(value).encode("utf-8", errors="ignore")) > 4096
            ),
            None,
        )
        if oversized is not None:
            raise ValueError(f"article URL at index {oversized} is too large")
        return values


def _require_loopback(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(status_code=403, detail="official-account API is loopback-only")


def _json_size(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bytes):
        return len(value)
    if isinstance(value, str):
        return len(value.encode("utf-8", errors="ignore"))
    try:
        return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return MAX_JSON_BYTES + 1


async def _read_bounded_json_object(
    request: Request,
    *,
    limit: int,
) -> dict[str, Any]:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].casefold()
    if media_type != "application/json" and not media_type.endswith("+json"):
        raise HTTPException(status_code=415, detail="request requires application/json")
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > limit:
                raise HTTPException(status_code=413, detail="request body is too large")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid content-length") from exc
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise HTTPException(status_code=413, detail="request body is too large")
        body.extend(chunk)
    try:
        payload = json.loads(bytes(body).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="request body must be JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="request body must be a JSON object")
    return payload


async def _validated_request(
    request: Request,
    model: type[ArticleMetricsRequest] | type[ArticleMetricsBatchRequest],
    *,
    limit: int,
) -> ArticleMetricsRequest | ArticleMetricsBatchRequest:
    payload = await _read_bounded_json_object(request, limit=limit)
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        # Validation errors can echo rejected inputs, including session values.
        raise HTTPException(status_code=422, detail="invalid request payload") from exc


def _stored_token_values() -> dict[str, str]:
    """Read the shared in-memory token store without coupling module imports."""
    try:
        from .mp_tools_router import token_store
    except ImportError:
        return {}
    return token_store.current_values()


def _stored_article_context(url: str) -> dict[str, str] | None:
    """Resolve a recent Native short-link identity without importing raw events."""

    try:
        from .mp_tools_router import article_context_store
    except ImportError:
        return None
    try:
        return article_context_store.resolve(url)
    except (TypeError, ValueError):
        return None


def _public_article_url(url: str) -> str:
    """Return a URL safe to put in API responses and history records."""
    try:
        context = extract_article_context(url)
        if context.url:
            return context.url[:4096]
    except Exception:  # malformed input is reported by the item itself
        pass
    return sanitize_article_url(str(url).strip())[:4096]


def _redact_error_detail(value: Any) -> str:
    return redact_metric_detail(value)


def _batch_error(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, HTTPException):
        status_code = int(exc.status_code)
        code = "http_error"
    elif isinstance(exc, WeChatMetricsAuthError):
        status_code, code = 401, "auth_error"
    elif isinstance(exc, WeChatMetricsUpstreamError):
        status_code, code = 502, "upstream_error"
    elif isinstance(exc, (WeChatArticleUrlError, WeChatMetricsError, ValueError)):
        status_code, code = 422, "metrics_error"
    else:
        status_code, code = 500, "collection_error"
    return {
        "code": code,
        "status": status_code,
        "status_code": status_code,
        "detail": _redact_error_detail(exc),
    }


def _batch_token_data(payload: ArticleMetricsBatchRequest, fetch_missing: bool) -> dict[str, str] | None:
    if payload.tokens:
        token_data = payload.tokens.model_dump(exclude_none=True)
        if not token_data.get("user_agent"):
            token_data.pop("user_agent", None)
        return token_data
    if fetch_missing:
        return _stored_token_values()
    return None


async def _collect_batch_item(
    index: int,
    url: str,
    payload: ArticleMetricsBatchRequest,
    auth_data: dict[str, str] | None,
    fetch_missing: bool,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    safe_url = ""
    async with semaphore:
        try:
            raw_url = str(url or "").strip()
            if not raw_url:
                raise WeChatArticleUrlError("article URL is empty")
            if len(raw_url.encode("utf-8", errors="ignore")) > 4096:
                raise WeChatArticleUrlError("article URL is too large")
            safe_url = _public_article_url(raw_url)
            if fetch_missing:
                auth = ArticleAuth.from_url(raw_url, auth_data)
            else:
                auth = ArticleAuth.from_mapping(auth_data) if auth_data else None
            result = await asyncio.to_thread(
                collect_article_metrics,
                url=raw_url,
                auth=auth,
                context_hint=_stored_article_context(raw_url),
                fetch_missing=fetch_missing,
                include_comments=payload.include_comments,
                comment_limit=payload.comment_limit,
                max_comment_pages=payload.max_comment_pages,
            )
            if inspect.isawaitable(result):
                result = await result
            data = result.as_dict() if hasattr(result, "as_dict") else dict(result)
            # `collect_article_metrics` already strips sensitive query values;
            # retain the input-safe URL when a custom collector omits it.
            data["url"] = _public_article_url(str(data.get("url") or url))
            return {
                "index": index,
                "url": data["url"],
                "ok": True,
                "data": data,
            }
        except Exception as exc:  # isolate one bad article from the batch
            if not safe_url:
                safe_url = _public_article_url(str(url or "")[:4096])
            error = _batch_error(exc)
            return {
                "index": index,
                "url": safe_url,
                "ok": False,
                "data": None,
                "error": error,
            }


def _record_batch_history(items: list[dict[str, Any]]) -> None:
    successful = [item for item in items if item.get("ok") and item.get("data")]
    if not successful:
        return
    # Add newest observations first while preserving input order within a batch.
    with _batch_history_lock:
        for item in reversed(successful):
            entry = copy.deepcopy(item["data"])
            entry.pop("comments", None)
            entry["batch_index"] = item.get("index")
            _batch_history.appendleft(entry)


@router.post("/articles/metrics/batch")
@router.post("/metrics/batch", include_in_schema=False)
async def article_metrics_batch(
    local_request: Request,
) -> dict[str, Any]:
    """Collect a bounded set of article snapshots without fail-fast behavior."""
    _require_loopback(local_request)
    parsed_payload = await _validated_request(
        local_request,
        ArticleMetricsBatchRequest,
        limit=MAX_BATCH_BODY_BYTES,
    )
    assert isinstance(parsed_payload, ArticleMetricsBatchRequest)
    payload = parsed_payload
    # A URL-only batch is intended for monitoring, so live fetching is enabled
    # unless the caller explicitly asks for intercepted data only.
    fetch_missing = payload.fetch_missing
    if fetch_missing is None:
        fetch_missing = True
    try:
        auth_data = _batch_token_data(payload, fetch_missing)
    except Exception as exc:
        # Keep malformed shared credentials item-scoped as well. The same error
        # is returned for each URL instead of rejecting the whole batch.
        auth_data = None
        auth_error = exc
    else:
        auth_error = None

    semaphore = asyncio.Semaphore(payload.concurrency)

    async def collect(index: int, url: str) -> dict[str, Any]:
        if auth_error is not None:
            safe_url = _public_article_url(url)
            error = _batch_error(auth_error)
            return {
                "index": index,
                "url": safe_url,
                "ok": False,
                "data": None,
                "error": error,
            }
        return await _collect_batch_item(
            index,
            url,
            payload,
            auth_data,
            fetch_missing,
            semaphore,
        )

    items = await asyncio.gather(
        *(collect(index, url) for index, url in enumerate(payload.urls))
    )
    item_list = list(items)
    _record_batch_history(item_list)
    succeeded = sum(1 for item in item_list if item.get("ok"))
    failed = len(item_list) - succeeded
    return {
        "ok": failed == 0,
        "total": len(item_list),
        "succeeded": succeeded,
        "failed": failed,
        "concurrency": payload.concurrency,
        "items": item_list,
    }


@router.get("/articles/history")
def article_metrics_history(
    local_request: Request,
    limit: int = Query(default=100, ge=1, le=MAX_HISTORY_ITEMS),
    url: str | None = Query(default=None, max_length=4096),
) -> dict[str, Any]:
    """Return recent in-memory batch snapshots, newest first."""
    _require_loopback(local_request)
    safe_filter = _public_article_url(url) if url else None
    with _batch_history_lock:
        entries = list(_batch_history)
    if safe_filter:
        entries = [entry for entry in entries if entry.get("url") == safe_filter]
    entries = entries[:limit]
    return {
        "items": entries,
        "total": len(entries),
        "count": len(entries),
        "limit": limit,
    }


@router.post("/articles/metrics")
@router.post("/metrics", include_in_schema=False)
async def article_metrics(
    local_request: Request,
) -> dict[str, Any]:
    _require_loopback(local_request)
    parsed_payload = await _validated_request(
        local_request,
        ArticleMetricsRequest,
        limit=MAX_METRICS_BODY_BYTES,
    )
    assert isinstance(parsed_payload, ArticleMetricsRequest)
    payload = parsed_payload
    if payload.html and len(payload.html.encode("utf-8", errors="ignore")) > MAX_HTML_BYTES:
        raise HTTPException(status_code=413, detail="article HTML is too large")
    if _json_size(payload.appmsgext) > MAX_JSON_BYTES or _json_size(payload.comments) > MAX_JSON_BYTES:
        raise HTTPException(status_code=413, detail="intercepted response is too large")

    try:
        fetch_missing = payload.fetch_missing
        if fetch_missing is None:
            fetch_missing = bool(
                payload.url and payload.html is None and payload.appmsgext is None
            )
        if payload.tokens:
            token_data = payload.tokens.model_dump(exclude_none=True)
            if not token_data.get("user_agent"):
                token_data.pop("user_agent", None)
        elif fetch_missing:
            token_data = _stored_token_values()
        else:
            token_data = None
        if fetch_missing and payload.url:
            auth = ArticleAuth.from_url(payload.url, token_data)
        else:
            auth = ArticleAuth.from_mapping(token_data) if token_data else None
        result = await asyncio.to_thread(
            collect_article_metrics,
            url=payload.url,
            html=payload.html,
            appmsgext=payload.appmsgext,
            comments=payload.comments,
            auth=auth,
            context_hint=(
                _stored_article_context(payload.url) if payload.url else None
            ),
            fetch_missing=fetch_missing,
            include_comments=payload.include_comments,
            comment_limit=payload.comment_limit,
            max_comment_pages=payload.max_comment_pages,
        )
    except WeChatMetricsAuthError as exc:
        raise HTTPException(status_code=401, detail=_redact_error_detail(exc)) from exc
    except WeChatMetricsUpstreamError as exc:
        raise HTTPException(status_code=502, detail=_redact_error_detail(exc)) from exc
    except (WeChatArticleUrlError, WeChatMetricsError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=_redact_error_detail(exc)) from exc
    return result.as_dict()
