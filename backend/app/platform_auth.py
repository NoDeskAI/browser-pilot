from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import jwt
from fastapi import Depends, HTTPException, Request

from app.auth.jwt import decode_access_token
from app.db import get_pool


@dataclass
class CurrentPlatformUser:
    id: str
    email: str
    name: str
    role: str


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


async def get_current_platform_user(request: Request) -> CurrentPlatformUser:
    raw = _extract_token(request)
    if not raw:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_access_token(raw)
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    if payload.get("kind") != "platform":
        raise HTTPException(status_code=401, detail="Malformed platform token")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Malformed platform token")

    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT id, email, name, role
        FROM platform_users
        WHERE id = $1 AND is_active = TRUE
        """,
        user_id,
    )
    if not row:
        raise HTTPException(status_code=401, detail="Platform user not found or disabled")
    return CurrentPlatformUser(
        id=row["id"],
        email=row["email"],
        name=row["name"],
        role=row["role"],
    )


def require_platform_role(allowed: Sequence[str]):
    async def _check(
        user: CurrentPlatformUser = Depends(get_current_platform_user),
    ) -> CurrentPlatformUser:
        if user.role not in allowed:
            raise HTTPException(status_code=403, detail="Insufficient platform permissions")
        return user

    return _check
