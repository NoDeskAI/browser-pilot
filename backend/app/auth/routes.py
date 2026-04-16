from __future__ import annotations

import hashlib
import logging
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.dependencies import CurrentUser, get_current_user, require_role
from app.auth.jwt import create_access_token
from app.auth.password import hash_password, verify_password
from app.db import get_pool

logger = logging.getLogger("auth.routes")
router = APIRouter(prefix="/api/auth", tags=["auth"])


# --------------- Request models ---------------

class LoginBody(BaseModel):
    email: str
    password: str


class SetupBody(BaseModel):
    tenantName: str
    email: str
    password: str
    name: str


class CreateTokenBody(BaseModel):
    name: str


# --------------- Login ---------------

@router.post("/login")
async def login(body: LoginBody):
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
    return {
        "access_token": token,
        "user": {
            "id": row["id"],
            "email": body.email,
            "name": row["name"],
            "role": row["role"],
            "tenantId": row["tenant_id"],
        },
    }


# --------------- Me ---------------

@router.get("/me")
async def me(user: CurrentUser = Depends(get_current_user)):
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "tenantId": user.tenant_id,
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
    raw_token = f"bp_{secrets.token_urlsafe(32)}"
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    token_id = str(uuid.uuid4())
    pool = get_pool()
    await pool.execute(
        """INSERT INTO api_tokens (id, user_id, tenant_id, name, token_hash)
           VALUES ($1, $2, $3, $4, $5)""",
        token_id, user.id, user.tenant_id, body.name.strip(), token_hash,
    )
    return {"id": token_id, "name": body.name.strip(), "token": raw_token}


@router.get("/tokens")
async def list_api_tokens(user: CurrentUser = Depends(get_current_user)):
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT id, name, created_at, last_used_at FROM api_tokens WHERE user_id = $1 ORDER BY created_at DESC",
        user.id,
    )
    return {
        "tokens": [
            {
                "id": r["id"],
                "name": r["name"],
                "createdAt": r["created_at"].isoformat(),
                "lastUsedAt": r["last_used_at"].isoformat() if r["last_used_at"] else None,
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
