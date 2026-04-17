from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.dependencies import CurrentUser, require_role
from app.auth.password import hash_password
from app.db import get_pool

logger = logging.getLogger("routes.users")
router = APIRouter(prefix="/api/users", tags=["users"])


class InviteUserBody(BaseModel):
    email: str
    name: str
    role: str = "member"
    password: str


class UpdateUserBody(BaseModel):
    name: str | None = None
    role: str | None = None
    is_active: bool | None = None
    password: str | None = None


@router.get("")
async def list_users(user: CurrentUser = Depends(require_role(["superadmin", "admin"]))):
    pool = get_pool()
    rows = await pool.fetch(
        """SELECT id, email, name, role, is_active, created_at
           FROM users WHERE tenant_id = $1 ORDER BY created_at""",
        user.tenant_id,
    )
    return {
        "users": [
            {
                "id": r["id"],
                "email": r["email"],
                "name": r["name"],
                "role": r["role"],
                "isActive": r["is_active"],
                "createdAt": r["created_at"].isoformat(),
            }
            for r in rows
        ]
    }


@router.post("")
async def create_user(body: InviteUserBody, user: CurrentUser = Depends(require_role(["superadmin", "admin"]))):
    if body.role not in ("member", "admin"):
        raise HTTPException(status_code=400, detail="Invalid role")
    if user.role == "admin" and body.role == "admin":
        raise HTTPException(status_code=403, detail="Only superadmin can create admin users")

    pool = get_pool()
    exists = await pool.fetchval(
        "SELECT 1 FROM users WHERE tenant_id = $1 AND email = $2",
        user.tenant_id, body.email.strip(),
    )
    if exists:
        raise HTTPException(status_code=409, detail="Email already exists in this tenant")

    user_id = str(uuid.uuid4())
    pw_hash = hash_password(body.password)
    await pool.execute(
        """INSERT INTO users (id, tenant_id, email, password_hash, name, role)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        user_id, user.tenant_id, body.email.strip(), pw_hash, body.name.strip(), body.role,
    )
    logger.info("User created: %s (%s) by %s", user_id, body.email, user.id)
    return {"id": user_id, "email": body.email.strip(), "name": body.name.strip(), "role": body.role}


@router.patch("/{user_id}")
async def update_user(user_id: str, body: UpdateUserBody, user: CurrentUser = Depends(require_role(["superadmin", "admin"]))):
    pool = get_pool()
    target = await pool.fetchrow(
        "SELECT id, role, tenant_id FROM users WHERE id = $1",
        user_id,
    )
    if not target or target["tenant_id"] != user.tenant_id:
        raise HTTPException(status_code=404, detail="User not found")
    if target["role"] == "superadmin" and user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Cannot modify superadmin")

    updates = []
    params = []
    idx = 1
    if body.name is not None:
        updates.append(f"name = ${idx}")
        params.append(body.name.strip())
        idx += 1
    if body.role is not None:
        if body.role not in ("member", "admin"):
            raise HTTPException(status_code=400, detail="Invalid role")
        if target["role"] == "superadmin":
            raise HTTPException(status_code=400, detail="Cannot change superadmin role")
        updates.append(f"role = ${idx}")
        params.append(body.role)
        idx += 1
    if body.is_active is not None:
        if target["role"] == "superadmin":
            raise HTTPException(status_code=400, detail="Cannot disable superadmin")
        updates.append(f"is_active = ${idx}")
        params.append(body.is_active)
        idx += 1
    if body.password is not None:
        if len(body.password) < 6:
            raise HTTPException(status_code=400, detail="New password must be at least 6 characters")
        updates.append(f"password_hash = ${idx}")
        params.append(hash_password(body.password))
        idx += 1

    if not updates:
        return {"ok": True}

    updates.append(f"updated_at = NOW()")
    params.append(user_id)
    sql = f"UPDATE users SET {', '.join(updates)} WHERE id = ${idx}"
    await pool.execute(sql, *params)
    return {"ok": True}


@router.delete("/{user_id}")
async def delete_user(user_id: str, user: CurrentUser = Depends(require_role(["superadmin"]))):
    pool = get_pool()
    target = await pool.fetchrow(
        "SELECT id, role, tenant_id FROM users WHERE id = $1",
        user_id,
    )
    if not target or target["tenant_id"] != user.tenant_id:
        raise HTTPException(status_code=404, detail="User not found")
    if target["role"] == "superadmin":
        raise HTTPException(status_code=400, detail="Cannot delete superadmin")
    if target["id"] == user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    await pool.execute("DELETE FROM api_tokens WHERE user_id = $1", user_id)
    await pool.execute("DELETE FROM users WHERE id = $1", user_id)
    logger.info("User deleted: %s by %s", user_id, user.id)
    return {"ok": True}
