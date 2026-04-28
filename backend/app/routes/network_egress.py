from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.dependencies import CurrentUser, get_current_user, require_role
from app.db import get_pool
from app.network_egress import (
    EgressError,
    VALID_EGRESS_TYPES,
    check_egress,
    fetch_egress_for_tenant,
    managed_proxy_url,
    public_egress_summary,
    remove_managed_egress,
    resolve_config_text,
    validate_proxy_url,
    write_config_ref,
)

router = APIRouter()


class EgressCreateBody(BaseModel):
    name: str
    type: str
    proxyUrl: str = ""
    configText: str = ""
    configUrl: str = ""
    username: str = ""
    password: str = ""
    disabled: bool = False


class EgressUpdateBody(BaseModel):
    name: str | None = None
    proxyUrl: str | None = None
    configText: str | None = None
    configUrl: str | None = None
    username: str = ""
    password: str = ""
    disabled: bool | None = None


def _clean_name(name: str) -> str:
    value = name.strip()
    if not value:
        raise HTTPException(422, "Name is required")
    return value[:120]


def _status_for(disabled: bool) -> str:
    return "disabled" if disabled else "unchecked"


def _response(row) -> dict:
    data = public_egress_summary(row)
    if data["type"] in ("clash", "openvpn") and data["id"]:
        data["proxyUrl"] = managed_proxy_url(data["id"], data["type"])
    data["managed"] = data["type"] in ("clash", "openvpn")
    return data


@router.get("/api/network-egress")
async def list_network_egress(user: CurrentUser = Depends(get_current_user)):
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT id, tenant_id, name, type, status, proxy_url, config_ref, health_error,
               last_checked_at, created_at, updated_at
        FROM network_egress_profiles
        WHERE tenant_id = $1
        ORDER BY created_at DESC
        """,
        user.tenant_id,
    )
    direct = {
        **public_egress_summary(None),
        "managed": False,
    }
    return {"profiles": [direct, *[_response(r) for r in rows]]}


@router.post("/api/network-egress")
async def create_network_egress(
    body: EgressCreateBody,
    user: CurrentUser = Depends(require_role(["superadmin", "admin"])),
):
    egress_type = body.type.strip()
    if egress_type not in VALID_EGRESS_TYPES or egress_type == "direct":
        raise HTTPException(422, "Unsupported egress type")

    proxy_url = ""
    config_ref = ""
    egress_id = str(uuid.uuid4())
    try:
        if egress_type == "external_proxy":
            proxy_url = validate_proxy_url(body.proxyUrl)
            if not proxy_url:
                raise HTTPException(422, "Proxy URL is required")
        elif egress_type in ("clash", "openvpn"):
            config_text = await resolve_config_text(body.configText, body.configUrl)
            config_ref = await write_config_ref(
                user.tenant_id, egress_id, egress_type, config_text, body.username, body.password
            )
            proxy_url = managed_proxy_url(egress_id, egress_type)
    except EgressError as exc:
        raise HTTPException(422, str(exc)) from exc

    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO network_egress_profiles
            (id, tenant_id, name, type, status, proxy_url, config_ref)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING *
        """,
        egress_id,
        user.tenant_id,
        _clean_name(body.name),
        egress_type,
        _status_for(body.disabled),
        proxy_url,
        config_ref,
    )
    return {"profile": _response(row)}


@router.patch("/api/network-egress/{egress_id}")
async def update_network_egress(
    egress_id: str,
    body: EgressUpdateBody,
    user: CurrentUser = Depends(require_role(["superadmin", "admin"])),
):
    row = await fetch_egress_for_tenant(user.tenant_id, egress_id)
    egress_type = row["type"]
    name = _clean_name(body.name) if body.name is not None else row["name"]
    proxy_url = row["proxy_url"] or ""
    config_ref = row["config_ref"] or ""
    status = row["status"] or "unchecked"

    if body.proxyUrl is not None:
        if egress_type != "external_proxy":
            raise HTTPException(422, "Only external proxy profiles accept proxyUrl")
        try:
            proxy_url = validate_proxy_url(body.proxyUrl)
            if not proxy_url:
                raise HTTPException(422, "Proxy URL is required")
        except EgressError as exc:
            raise HTTPException(422, str(exc)) from exc
        status = "unchecked"

    if egress_type in ("clash", "openvpn") and (body.configText is not None or body.configUrl is not None):
        if body.configText is None and body.configUrl is None:
            raise HTTPException(422, "Config content is required")
        try:
            config_text = await resolve_config_text(body.configText, body.configUrl)
            config_ref = await write_config_ref(user.tenant_id, egress_id, egress_type, config_text, body.username, body.password)
        except EgressError as exc:
            raise HTTPException(422, str(exc)) from exc
        status = "unchecked"
    elif body.configText is not None or body.configUrl is not None:
        if egress_type != "external_proxy":
            raise HTTPException(422, "This egress type does not accept config content")

    if body.disabled is not None:
        status = "disabled" if body.disabled else "unchecked"

    if egress_type in ("clash", "openvpn") and (body.configText is not None or body.disabled is True):
        await remove_managed_egress(egress_id)

    pool = get_pool()
    updated = await pool.fetchrow(
        """
        UPDATE network_egress_profiles
        SET name = $1, proxy_url = $2, config_ref = $3, status = $4,
            health_error = CASE WHEN $4 = 'disabled' THEN health_error ELSE '' END,
            updated_at = NOW()
        WHERE id = $5 AND tenant_id = $6
        RETURNING *
        """,
        name,
        proxy_url,
        config_ref,
        status,
        egress_id,
        user.tenant_id,
    )
    return {"profile": _response(updated)}


@router.delete("/api/network-egress/{egress_id}")
async def delete_network_egress(
    egress_id: str,
    user: CurrentUser = Depends(require_role(["superadmin", "admin"])),
):
    row = await fetch_egress_for_tenant(user.tenant_id, egress_id)
    pool = get_pool()
    count = await pool.fetchval(
        "SELECT COUNT(*) FROM sessions WHERE network_egress_id = $1",
        egress_id,
    )
    if count:
        raise HTTPException(409, "Network egress is still used by sessions")
    if row["type"] in ("clash", "openvpn"):
        await remove_managed_egress(egress_id)
    await pool.execute(
        "DELETE FROM network_egress_profiles WHERE id = $1 AND tenant_id = $2",
        egress_id,
        user.tenant_id,
    )
    return {"ok": True}


@router.post("/api/network-egress/{egress_id}/check")
async def check_network_egress(
    egress_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    row = await fetch_egress_for_tenant(user.tenant_id, egress_id)
    try:
        result = await check_egress(row)
    except EgressError as exc:
        result = {"status": "unhealthy", "healthError": str(exc)}
    return {"ok": True, **result}
