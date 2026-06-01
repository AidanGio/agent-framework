"""FastAPI router for the dashboard backend.

Identity is resolved by the pluggable seam in :mod:`agent.dashboard.auth`.
The template ships in no-auth dev mode (single local user); swap
``get_current_user`` there to add real auth without touching these routes.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response

from .auth import SessionUser, get_current_user, require_admin
from .options import SUPPORTED_MODELS
from .profiles import ProfileUpdate, get_profile, list_profiles, upsert_profile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard/api", tags=["dashboard"])

CurrentUser = Annotated[SessionUser, Depends(get_current_user)]
AdminUser = Annotated[SessionUser, Depends(require_admin)]


@router.get("/me")
async def me(user: CurrentUser) -> SessionUser:
    return user


@router.post("/auth/logout", status_code=204)
async def logout() -> Response:
    # No-op in dev mode (there is no server session to clear). When you add
    # real auth, expire the session cookie here.
    return Response(status_code=204)


@router.get("/options")
async def options() -> dict:
    return {"models": SUPPORTED_MODELS}


@router.get("/profile")
async def get_my_profile(user: CurrentUser) -> dict:
    return await get_profile(user.id) or {}


@router.put("/profile")
async def save_my_profile(update: ProfileUpdate, user: CurrentUser) -> dict:
    try:
        update.validate_pairing()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return await upsert_profile(user.id, user.email or "", update)


@router.get("/admin/profiles")
async def admin_list_profiles(_: AdminUser) -> list[dict]:
    return await list_profiles()


@router.put("/admin/profiles/{user_id}")
async def admin_save_profile(user_id: str, update: ProfileUpdate, _: AdminUser) -> dict:
    try:
        update.validate_pairing()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    existing = await get_profile(user_id) or {}
    return await upsert_profile(user_id, existing.get("email", ""), update)
