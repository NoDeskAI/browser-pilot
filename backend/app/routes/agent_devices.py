from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import agent_devices
from app.auth.dependencies import CurrentUser, get_session_aware_user

router = APIRouter()


class LeaseBody(BaseModel):
    lease_mode: str = Field(default="session_bound", alias="leaseMode")
    task_id: str | None = Field(default=None, alias="taskId")
    ttl_seconds: int | None = Field(
        default=None,
        alias="ttlSeconds",
        ge=1,
        le=agent_devices.MAX_LEASE_TTL_SECONDS,
    )
    expires_at: datetime | None = Field(default=None, alias="expiresAt")

    model_config = {"populate_by_name": True}


def _lease_audit_id(lease: dict | None) -> str | None:
    if not lease:
        return None
    value = lease.get("audit_event_id")
    return str(value) if value else None


async def _lease_error_response(
    *,
    device_id: str,
    user: CurrentUser,
    action: str,
    exc: agent_devices.AgentDeviceLeaseError,
) -> JSONResponse:
    audit_event_id = exc.audit_event_id
    if not audit_event_id:
        audit_event_id = await agent_devices.record_control_rejection(
            device_id,
            user,
            action=action,
            summary=exc.message,
            reason=exc.reason,
            lease=exc.lease,
            details={"nextStep": exc.next_step},
        )
    payload = agent_devices.control_action_response(
        {"ok": False, "error": exc.message},
        device_id=device_id,
        user=user,
        lease=exc.lease,
        action=action,
        status="rejected",
        audit_event_id=audit_event_id,
        next_step=exc.next_step,
        retry_safety="safe_after_new_lease",
        failure_category=exc.reason,
        state_changed=False,
    )
    return JSONResponse(status_code=exc.status_code, content=payload)


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
        return await _lease_error_response(device_id=device_id, user=user, action="reserve_device", exc=exc)
    return agent_devices.control_action_response(
        {"ok": True, "lease": lease},
        device_id=device_id,
        user=user,
        lease=lease,
        action="reserve_device",
        status="succeeded",
        audit_event_id=_lease_audit_id(lease),
        next_step="continue",
        state_changed=True,
    )


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
        return await _lease_error_response(device_id=device_id, user=user, action="renew_lease", exc=exc)
    return agent_devices.control_action_response(
        {"ok": True, "lease": lease},
        device_id=device_id,
        user=user,
        lease=lease,
        action="renew_lease",
        status="succeeded",
        audit_event_id=_lease_audit_id(lease),
        next_step="continue",
        state_changed=True,
    )


@router.post("/api/agent-devices/{device_id}/leases/{lease_id}/release")
async def release_agent_device_lease(
    device_id: str,
    lease_id: str,
    user: CurrentUser = Depends(get_session_aware_user),
):
    try:
        lease = await agent_devices.release_lease(device_id, lease_id, user)
    except agent_devices.AgentDeviceLeaseError as exc:
        return await _lease_error_response(device_id=device_id, user=user, action="release_device", exc=exc)
    return agent_devices.control_action_response(
        {"ok": True, "lease": lease},
        device_id=device_id,
        user=user,
        lease=lease,
        action="release_device",
        status="succeeded",
        audit_event_id=_lease_audit_id(lease),
        next_step="continue",
        state_changed=True,
    )


@router.post("/api/agent-devices/{device_id}/reclaim")
async def reclaim_agent_device(
    device_id: str,
    body: LeaseBody | None = None,
    user: CurrentUser = Depends(get_session_aware_user),
):
    body = body or LeaseBody()
    try:
        result = await agent_devices.reclaim_device(
            device_id,
            user,
            lease_mode=body.lease_mode,
            task_id=body.task_id,
            ttl_seconds=body.ttl_seconds,
            expires_at=body.expires_at,
        )
    except agent_devices.AgentDeviceLeaseError as exc:
        return await _lease_error_response(device_id=device_id, user=user, action="force_reclaim", exc=exc)
    return agent_devices.control_action_response(
        {"ok": True, **result},
        device_id=device_id,
        user=user,
        lease=result.get("lease"),
        action="force_reclaim",
        status="succeeded",
        audit_event_id=_lease_audit_id(result.get("lease")),
        next_step="continue",
        state_changed=True,
    )


@router.get("/api/agent-devices/{device_id}/audit")
async def list_agent_device_audit_for_device(
    device_id: str,
    limit: int = Query(100, ge=1, le=500),
    user: CurrentUser = Depends(get_session_aware_user),
):
    return {"events": await agent_devices.list_audit_events(user, device_id=device_id, limit=limit)}
