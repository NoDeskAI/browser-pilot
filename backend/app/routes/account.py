from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.dependencies import CurrentUser, get_current_user
from app.auth.password import hash_password, verify_password
from app.db import get_pool

logger = logging.getLogger("routes.account")
router = APIRouter(prefix="/api/account", tags=["account"])


class UpdateProfileBody(BaseModel):
    name: str


class ChangePasswordBody(BaseModel):
    currentPassword: str
    newPassword: str


@router.patch("/profile")
async def update_profile(
    body: UpdateProfileBody, user: CurrentUser = Depends(get_current_user)
):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")

    pool = get_pool()
    await pool.execute(
        "UPDATE users SET name = $1, updated_at = NOW() WHERE id = $2",
        name,
        user.id,
    )
    logger.info("User %s updated profile name", user.id)
    return {"ok": True}


@router.post("/change-password")
async def change_password(
    body: ChangePasswordBody, user: CurrentUser = Depends(get_current_user)
):
    if len(body.newPassword) < 6:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters")

    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT password_hash FROM users WHERE id = $1",
        user.id,
    )
    if not row or not row["password_hash"]:
        raise HTTPException(status_code=400, detail="User has no password set")

    if not verify_password(body.currentPassword, row["password_hash"]):
        raise HTTPException(status_code=403, detail="Incorrect current password")

    new_hash = hash_password(body.newPassword)
    await pool.execute(
        "UPDATE users SET password_hash = $1, updated_at = NOW() WHERE id = $2",
        new_hash,
        user.id,
    )
    logger.info("User %s changed password", user.id)
    return {"ok": True}
