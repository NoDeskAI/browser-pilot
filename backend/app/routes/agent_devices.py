from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app import agent_devices
from app.auth.dependencies import CurrentUser, get_session_aware_user

router = APIRouter()


class LeaseBody(BaseModel):
    lease_mode: str = Field(default="session_bound", alias="leaseMode")
    task_id: str | None = Field(default=None, alias="taskId")
    ttl_seconds: int | None = Field(default=None, alias="ttlSeconds")
    expires_at: datetime | None = Field(default=None, alias="expiresAt")

    model_config = {"populate_by_name": True}


@router.get("/api/agent-devices")
async def list_agent_devices(user: CurrentUser = Depends(get_session_aware_user)):
    return {"devices": await agent_devices.list_device_visibility(user)}


@router.get("/api/agent-devices/audit")
async def list_agent_device_audit(
    device: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    user: CurrentUser = Depends(get_session_aware_user),
):
    return {"events": await agent_devices.list_audit_events(user, device_id=device, limit=limit)}


@router.get("/api/agent-devices/{device_id}")
async def get_agent_device(device_id: str, user: CurrentUser = Depends(get_session_aware_user)):
    return {"device": await agent_devices.get_device_visibility(device_id, user)}


@router.post("/api/agent-devices/{device_id}/leases")
async def acquire_agent_device_lease(
    device_id: str,
    body: LeaseBody | None = None,
    user: CurrentUser = Depends(get_session_aware_user),
):
    body = body or LeaseBody()
    try:
        lease = await agent_devices.acquire_lease(
            device_id,
            user,
            lease_mode=body.lease_mode,
            task_id=body.task_id,
            ttl_seconds=body.ttl_seconds,
            expires_at=body.expires_at,
        )
    except agent_devices.AgentDeviceLeaseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"ok": True, "lease": lease}


@router.patch("/api/agent-devices/{device_id}/leases/{lease_id}")
async def renew_agent_device_lease(
    device_id: str,
    lease_id: str,
    body: LeaseBody | None = None,
    user: CurrentUser = Depends(get_session_aware_user),
):
    body = body or LeaseBody()
    try:
        lease = await agent_devices.renew_lease(
            device_id,
            lease_id,
            user,
            ttl_seconds=body.ttl_seconds,
            expires_at=body.expires_at,
        )
    except agent_devices.AgentDeviceLeaseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"ok": True, "lease": lease}


@router.post("/api/agent-devices/{device_id}/leases/{lease_id}/release")
async def release_agent_device_lease(
    device_id: str,
    lease_id: str,
    user: CurrentUser = Depends(get_session_aware_user),
):
    try:
        lease = await agent_devices.release_lease(device_id, lease_id, user)
    except agent_devices.AgentDeviceLeaseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"ok": True, "lease": lease}


@router.post("/api/agent-devices/{device_id}/reclaim")
async def reclaim_agent_device(
    device_id: str,
    body: LeaseBody | None = None,
    user: CurrentUser = Depends(get_session_aware_user),
):
    body = body or LeaseBody()
    result = await agent_devices.reclaim_device(
        device_id,
        user,
        lease_mode=body.lease_mode,
        task_id=body.task_id,
        ttl_seconds=body.ttl_seconds,
        expires_at=body.expires_at,
    )
    return {"ok": True, **result}


@router.get("/api/agent-devices/{device_id}/audit")
async def list_agent_device_audit_for_device(
    device_id: str,
    limit: int = Query(100, ge=1, le=500),
    user: CurrentUser = Depends(get_session_aware_user),
):
    return {"events": await agent_devices.list_audit_events(user, device_id=device_id, limit=limit)}
