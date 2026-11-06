"""Compatibility aggregator for official-account API routes.

The token/export and metrics implementations live in separate modules so they
can evolve independently. Applications may include this router once, or
include the two child routers explicitly.
"""

from __future__ import annotations

from fastapi import APIRouter

from .mp_metrics_router import router as metrics_router
from .mp_tools_router import router as tools_router


router = APIRouter()
router.include_router(tools_router)
router.include_router(metrics_router)

__all__ = ["router"]
