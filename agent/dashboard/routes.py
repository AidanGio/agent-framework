"""FastAPI router for the dashboard backend.

NOTE: Auth has been removed and needs to be rebuilt. For now
this router exposes only the public ``/options`` endpoint plus list_profiles,
which the admin UI can hit once auth is back.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from .options import SUPPORTED_MODELS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard/api", tags=["dashboard"])


@router.get("/options")
async def options() -> dict:
    return {"models": SUPPORTED_MODELS}
