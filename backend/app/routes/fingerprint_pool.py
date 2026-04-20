from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.dependencies import CurrentUser, get_current_user, require_role
from app.db import get_pool
from app.fingerprint import _ensure_pool_seeded, clear_seeded_cache

router = APIRouter()

_VALID_GROUPS = {"platform", "gpu", "hardware", "screen"}


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class PoolEntryBody(BaseModel):
    groupName: str
    label: str
    data: dict
    tags: list[str] = []
    enabled: bool = True


class PoolEntryUpdateBody(BaseModel):
    label: str | None = None
    data: dict | None = None
    tags: list[str] | None = None
    enabled: bool | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/api/fingerprint-pool")
async def list_pool(user: CurrentUser = Depends(get_current_user)):
    """Return all pool entries for the current tenant, grouped by group_name."""
    await _ensure_pool_seeded(user.tenant_id)
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT id, group_name, label, data, tags, enabled, sort_order, created_at "
        "FROM fingerprint_pool WHERE tenant_id = $1 ORDER BY sort_order, created_at",
        user.tenant_id,
    )
    grouped: dict[str, list] = {g: [] for g in ("platform", "gpu", "hardware", "screen")}
    for r in rows:
        entry = {
            "id": r["id"],
            "groupName": r["group_name"],
            "label": r["label"],
            "data": r["data"],
            "tags": r["tags"] or [],
            "enabled": r["enabled"],
            "sortOrder": r["sort_order"],
        }
        grouped.setdefault(r["group_name"], []).append(entry)
    return {"pool": grouped}


@router.post("/api/fingerprint-pool")
async def create_pool_entry(
    body: PoolEntryBody,
    user: CurrentUser = Depends(require_role(["superadmin", "admin"])),
):
    if body.groupName not in _VALID_GROUPS:
        raise HTTPException(400, f"Invalid group: {body.groupName}")
    pool = get_pool()
    entry_id = str(uuid.uuid4())
    try:
        await pool.execute(
            "INSERT INTO fingerprint_pool (id, tenant_id, group_name, label, data, tags, enabled) "
            "VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)",
            entry_id, user.tenant_id, body.groupName, body.label, body.data, body.tags, body.enabled,
        )
    except Exception as exc:
        if "unique" in str(exc).lower():
            raise HTTPException(409, f"Entry '{body.label}' already exists in group '{body.groupName}'")
        raise
    return {"id": entry_id, "ok": True}


@router.put("/api/fingerprint-pool/{entry_id}")
async def update_pool_entry(
    entry_id: str,
    body: PoolEntryUpdateBody,
    user: CurrentUser = Depends(require_role(["superadmin", "admin"])),
):
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT tenant_id FROM fingerprint_pool WHERE id = $1", entry_id,
    )
    if not row or row["tenant_id"] != user.tenant_id:
        raise HTTPException(404, "Entry not found")

    updates: list[str] = []
    params: list = []
    idx = 1

    if body.label is not None:
        updates.append(f"label = ${idx}")
        params.append(body.label)
        idx += 1
    if body.data is not None:
        updates.append(f"data = ${idx}::jsonb")
        params.append(body.data)
        idx += 1
    if body.tags is not None:
        updates.append(f"tags = ${idx}")
        params.append(body.tags)
        idx += 1
    if body.enabled is not None:
        updates.append(f"enabled = ${idx}")
        params.append(body.enabled)
        idx += 1

    if not updates:
        return {"ok": True}

    params.append(entry_id)
    sql = f"UPDATE fingerprint_pool SET {', '.join(updates)} WHERE id = ${idx}"
    try:
        await pool.execute(sql, *params)
    except Exception as exc:
        if "unique" in str(exc).lower():
            raise HTTPException(409, "Label conflict")
        raise
    return {"ok": True}


@router.delete("/api/fingerprint-pool/{entry_id}")
async def delete_pool_entry(
    entry_id: str,
    user: CurrentUser = Depends(require_role(["superadmin", "admin"])),
):
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT tenant_id FROM fingerprint_pool WHERE id = $1", entry_id,
    )
    if not row or row["tenant_id"] != user.tenant_id:
        raise HTTPException(404, "Entry not found")
    await pool.execute("DELETE FROM fingerprint_pool WHERE id = $1", entry_id)
    return {"ok": True}


@router.post("/api/fingerprint-pool/reset")
async def reset_pool(
    user: CurrentUser = Depends(require_role(["superadmin", "admin"])),
):
    """Delete all tenant pool entries and re-seed from defaults."""
    pool = get_pool()
    await pool.execute(
        "DELETE FROM fingerprint_pool WHERE tenant_id = $1", user.tenant_id,
    )
    clear_seeded_cache(user.tenant_id)
    await _ensure_pool_seeded(user.tenant_id)
    return {"ok": True}
