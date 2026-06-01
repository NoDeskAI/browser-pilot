from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from app.auth.dependencies import CurrentUser, get_current_user, require_role
from app.auth.jwt import create_access_token
from app.auth.password import hash_password, verify_password
from app.config import REMEMBER_ME_DAYS
from app.db import get_pool
from app.platform_control import ensure_tenant_platform_defaults

logger = logging.getLogger("auth.routes")
router = APIRouter(prefix="/api/auth", tags=["auth"])

REMEMBER_COOKIE_NAME = "bp_remember_token"
REMEMBER_COOKIE_PATH = "/api/auth"


# --------------- Request models ---------------

class LoginBody(BaseModel):
    email: str
    password: str
    rememberMe: bool = False


class SetupBody(BaseModel):
    tenantName: str
    email: str
    password: str
    name: str


class CreateTokenBody(BaseModel):
    name: str
    sessionId: str | None = None


def _hash_remember_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def _set_remember_cookie(response: Response, raw_token: str, expires_at: datetime) -> None:
    now = datetime.now(timezone.utc)
    max_age = max(0, int((expires_at - now).total_seconds()))
    response.set_cookie(
        REMEMBER_COOKIE_NAME,
        raw_token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        path=REMEMBER_COOKIE_PATH,
    )


def _clear_remember_cookie(response: Response) -> None:
    response.delete_cookie(
        REMEMBER_COOKIE_NAME,
        path=REMEMBER_COOKIE_PATH,
        samesite="lax",
    )


async def _create_remember_token(
    response: Response,
    user_id: str,
    tenant_id: str,
    expires_at: datetime | None = None,
) -> None:
    raw_token = secrets.token_urlsafe(48)
    token_hash = _hash_remember_token(raw_token)
    token_id = str(uuid.uuid4())
    expires = expires_at or (datetime.now(timezone.utc) + timedelta(days=REMEMBER_ME_DAYS))

    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO remember_tokens (id, user_id, tenant_id, token_hash, expires_at)
        VALUES ($1, $2, $3, $4, $5)
        """,
        token_id,
        user_id,
        tenant_id,
        token_hash,
        expires,
    )
    _set_remember_cookie(response, raw_token, expires)


def _user_payload(row, email: str | None = None) -> dict:
    return {
        "id": row["id"],
        "email": email if email is not None else row["email"],
        "name": row["name"],
        "role": row["role"],
        "tenantId": row["tenant_id"],
    }


# --------------- Login ---------------

@router.post("/login")
async def login(body: LoginBody, response: Response):
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT id, tenant_id, password_hash, name, role, is_active FROM users WHERE email = $1",
        body.email,
    )
    if not row or not row["password_hash"]:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not row["is_active"]:
        raise HTTPException(status_code=401, detail="Account disabled")
    if not verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token(row["id"], row["tenant_id"], row["role"])
    if body.rememberMe:
        await _create_remember_token(response, row["id"], row["tenant_id"])
    return {
        "access_token": token,
        "user": _user_payload(row, body.email),
    }


@router.post("/refresh")
async def refresh(request: Request, response: Response):
    raw_token = request.cookies.get(REMEMBER_COOKIE_NAME)
    if not raw_token:
        raise HTTPException(status_code=401, detail="Missing remember token")

    token_hash = _hash_remember_token(raw_token)
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT rt.id AS remember_token_id, rt.expires_at,
               u.id, u.tenant_id, u.email, u.name, u.role
        FROM remember_tokens rt JOIN users u ON rt.user_id = u.id
        WHERE rt.token_hash = $1
          AND rt.revoked_at IS NULL
          AND rt.expires_at > NOW()
          AND u.is_active = TRUE
        """,
        token_hash,
    )
    if not row:
        _clear_remember_cookie(response)
        raise HTTPException(status_code=401, detail="Invalid or expired remember token")

    await pool.execute(
        "UPDATE remember_tokens SET revoked_at = NOW(), last_used_at = NOW() WHERE id = $1",
        row["remember_token_id"],
    )
    await _create_remember_token(response, row["id"], row["tenant_id"], row["expires_at"])
    token = create_access_token(row["id"], row["tenant_id"], row["role"])
    return {
        "access_token": token,
        "user": _user_payload(row),
    }


@router.post("/logout")
async def logout(request: Request, response: Response):
    raw_token = request.cookies.get(REMEMBER_COOKIE_NAME)
    if raw_token:
        pool = get_pool()
        await pool.execute(
            """
            UPDATE remember_tokens
            SET revoked_at = COALESCE(revoked_at, NOW()), last_used_at = NOW()
            WHERE token_hash = $1
            """,
            _hash_remember_token(raw_token),
        )
    _clear_remember_cookie(response)
    return {"ok": True}


# --------------- Me ---------------

@router.get("/me")
async def me(user: CurrentUser = Depends(get_current_user)):
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "tenantId": user.tenant_id,
        "createdAt": user.created_at,
    }


# --------------- Setup (first-run wizard) ---------------

@router.post("/setup")
async def setup(body: SetupBody):
    pool = get_pool()
    count = await pool.fetchval("SELECT COUNT(*) FROM users")
    if count > 0:
        raise HTTPException(status_code=403, detail="Setup already completed")

    tenant_id = str(uuid.uuid4())
    slug = body.tenantName.strip().lower().replace(" ", "-")[:64] or "default"
    await pool.execute(
        "INSERT INTO tenants (id, name, slug) VALUES ($1, $2, $3)",
        tenant_id, body.tenantName.strip(), slug,
    )

    user_id = str(uuid.uuid4())
    pw_hash = hash_password(body.password)
    await pool.execute(
        """INSERT INTO users (id, tenant_id, email, password_hash, name, role)
           VALUES ($1, $2, $3, $4, $5, 'superadmin')""",
        user_id, tenant_id, body.email.strip(), pw_hash, body.name.strip(),
    )

    # Backfill existing sessions that have no tenant/user
    await pool.execute(
        "UPDATE sessions SET tenant_id = $1, user_id = $2 WHERE tenant_id IS NULL",
        tenant_id, user_id,
    )
    await ensure_tenant_platform_defaults(
        pool,
        tenant_id,
        reason="tenant_auth_setup",
    )

    token = create_access_token(user_id, tenant_id, "superadmin")
    logger.info("Setup completed: tenant=%s user=%s", tenant_id, user_id)
    return {
        "access_token": token,
        "user": {
            "id": user_id,
            "email": body.email.strip(),
            "name": body.name.strip(),
            "role": "superadmin",
            "tenantId": tenant_id,
        },
    }


# --------------- API Tokens ---------------

@router.post("/tokens")
async def create_api_token(
    body: CreateTokenBody,
    user: CurrentUser = Depends(get_current_user),
):
    pool = get_pool()

    session_id = body.sessionId
    if session_id:
        row = await pool.fetchrow(
            "SELECT tenant_id FROM sessions WHERE id = $1", session_id,
        )
        if not row or (row["tenant_id"] and row["tenant_id"] != user.tenant_id):
            raise HTTPException(status_code=404, detail="Session not found")

    raw_token = f"bp_{secrets.token_urlsafe(32)}"
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    token_id = str(uuid.uuid4())
    await pool.execute(
        """INSERT INTO api_tokens (id, user_id, tenant_id, name, token_hash, session_id)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        token_id, user.id, user.tenant_id, body.name.strip(), token_hash, session_id,
    )
    return {"id": token_id, "name": body.name.strip(), "token": raw_token, "sessionId": session_id}


@router.get("/tokens")
async def list_api_tokens(user: CurrentUser = Depends(get_current_user)):
    pool = get_pool()
    rows = await pool.fetch(
        """SELECT t.id, t.name, t.created_at, t.last_used_at, t.session_id,
                  s.name AS session_name
           FROM api_tokens t LEFT JOIN sessions s ON t.session_id = s.id
           WHERE t.user_id = $1 ORDER BY t.created_at DESC""",
        user.id,
    )
    return {
        "tokens": [
            {
                "id": r["id"],
                "name": r["name"],
                "createdAt": r["created_at"].isoformat(),
                "lastUsedAt": r["last_used_at"].isoformat() if r["last_used_at"] else None,
                "sessionId": r["session_id"],
                "sessionName": r["session_name"],
            }
            for r in rows
        ]
    }


@router.delete("/tokens/{token_id}")
async def revoke_api_token(
    token_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    pool = get_pool()
    result = await pool.execute(
        "DELETE FROM api_tokens WHERE id = $1 AND user_id = $2",
        token_id, user.id,
    )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Token not found")
    return {"ok": True}
