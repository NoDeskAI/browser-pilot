from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Sequence

import jwt
from fastapi import Depends, HTTPException, Request

from app.auth.jwt import decode_access_token
from app.db import get_pool

logger = logging.getLogger("auth")


@dataclass
class CurrentUser:
    id: str
    tenant_id: str
    email: str
    name: str
    role: str  # superadmin | admin | member
    created_at: str


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


async def _resolve_api_token(raw: str) -> CurrentUser | None:
    """Resolve a bp_... API token to its owner."""
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT t.user_id, t.tenant_id, u.email, u.name, u.role, u.created_at
        FROM api_tokens t JOIN users u ON t.user_id = u.id
        WHERE t.token_hash = $1 AND u.is_active = TRUE
        """,
        token_hash,
    )
    if not row:
        return None
    await pool.execute(
        "UPDATE api_tokens SET last_used_at = NOW() WHERE token_hash = $1",
        token_hash,
    )
    return CurrentUser(
        id=row["user_id"],
        tenant_id=row["tenant_id"],
        email=row["email"],
        name=row["name"],
        role=row["role"],
        created_at=row["created_at"].isoformat(),
    )


async def get_current_user(request: Request) -> CurrentUser:
    """FastAPI dependency: require a valid JWT or API token."""
    raw = _extract_token(request)
    if not raw:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # API token (bp_ prefix)
    if raw.startswith("bp_"):
        user = await _resolve_api_token(raw)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API token")
        return user

    # JWT
    try:
        payload = decode_access_token(raw)
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user_id = payload.get("sub")
    tenant_id = payload.get("tid")
    if not user_id or not tenant_id:
        raise HTTPException(status_code=401, detail="Malformed token")

    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT email, name, role, created_at FROM users WHERE id = $1 AND tenant_id = $2 AND is_active = TRUE",
        user_id, tenant_id,
    )
    if not row:
        raise HTTPException(status_code=401, detail="User not found or disabled")

    return CurrentUser(
        id=user_id,
        tenant_id=tenant_id,
        email=row["email"],
        name=row["name"],
        role=row["role"],
        created_at=row["created_at"].isoformat(),
    )


async def get_optional_user(request: Request) -> CurrentUser | None:
    """Same as get_current_user but returns None instead of raising 401."""
    raw = _extract_token(request)
    if not raw:
        return None
    try:
        return await get_current_user(request)
    except HTTPException:
        return None


def require_role(allowed: Sequence[str]):
    """Return a dependency that checks the user has one of the allowed roles."""
    async def _check(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in allowed:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return _check
