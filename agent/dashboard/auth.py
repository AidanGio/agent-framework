"""Authentication seam for the dashboard.

The template ships with a **no-auth dev mode**: every request is treated as a
single configured local user, so the dashboard runs out of the box with no
identity provider. This is the one place you replace to wire in real auth.

To add real auth (OAuth/OIDC/your own session cookie):

1. Implement your login flow (e.g. mount `/dashboard/api/auth/login` +
   `/auth/callback` routes that set a signed, httpOnly session cookie).
2. Rewrite `get_current_user` to validate that cookie/credential on each
   request and return the resolved :class:`SessionUser` (or raise 401).
3. Everything downstream — `/me`, `/profile`, the admin gate — keeps working
   unchanged, because it only depends on `get_current_user`.

The dev user is controlled by env vars:

- ``DASHBOARD_DEV_USER_ID``    (default ``"local"``)
- ``DASHBOARD_DEV_USER_NAME``  (default ``"Local User"``)
- ``DASHBOARD_DEV_USER_EMAIL`` (default ``"local@example.com"``)
"""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import Depends, HTTPException
from pydantic import BaseModel

from .admin import _admin_emails, is_admin


class SessionUser(BaseModel):
    """The authenticated user, as exposed to the frontend via ``/me``."""

    id: str
    name: str
    email: str | None = None
    avatar_url: str | None = None
    is_admin: bool = False


def _dev_user() -> SessionUser:
    user_id = os.environ.get("DASHBOARD_DEV_USER_ID", "local")
    name = os.environ.get("DASHBOARD_DEV_USER_NAME", "Local User")
    email = os.environ.get("DASHBOARD_DEV_USER_EMAIL", "local@example.com")
    # In dev the local user is an admin unless CONFIGURED_ADMINS is set and
    # excludes them — so the admin panel is reachable out of the box.
    admin = is_admin(email) or not _admin_emails()
    return SessionUser(id=user_id, name=name, email=email, is_admin=admin)


async def get_current_user() -> SessionUser:
    """Resolve the current user.

    No-auth dev mode: always returns the configured local user. Replace this
    with real session validation to secure the dashboard (see module docstring).
    """
    return _dev_user()


async def require_admin(user: Annotated[SessionUser, Depends(get_current_user)]) -> SessionUser:
    """Dependency that 403s unless the current user is an admin."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin access required")
    return user
