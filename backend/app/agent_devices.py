from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.auth.dependencies import CurrentUser
from app.container import get_all_container_statuses, get_container_status
from app.db import get_pool

DEVICE_TYPE = "browser_session"
AUDIT_BOUNDARY = "browser_pilot"


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _operator_subject(user: CurrentUser) -> str:
    if user.api_token_id:
        return f"token:{user.api_token_id}"
    return f"user:{user.id}"


def _runtime_operator(session_id: str) -> str:
    return f"runtime:file_capture:{session_id}"


def _actor_owner(user: CurrentUser | None) -> str | None:
    return user.id if user else None


def _lease_owner(row: Any) -> str | None:
    return _row_value(row, "operator_owner_user_id")


def _lease_matches_actor(lease: Any, user: CurrentUser) -> bool:
    current_operator = _row_value(lease, "current_operator")
    if current_operator == _operator_subject(user):
        return True
    if current_operator == f"user:{user.id}" and _lease_owner(lease) == user.id:
        return True
    return False


def _can_manage(row: Any, user: CurrentUser) -> bool:
    if user.role in ("superadmin", "admin"):
        return True
    return _row_value(row, "user_id") == user.id


def _normalize_expires(ttl_seconds: int | None, expires_at: datetime | None) -> datetime | None:
    if ttl_seconds is not None:
        from datetime import timedelta

        return datetime.now(timezone.utc) + timedelta(seconds=max(1, int(ttl_seconds)))
    return expires_at


def _lease_to_dict(row: Any | None) -> dict[str, Any] | None:
    if row is None:
        return None
    lease_id = _row_value(row, "id")
    return {
        "id": lease_id,
        "lease_id": lease_id,
        "device_instance_id": _row_value(row, "device_instance_id"),
        "device_type": _row_value(row, "device_type") or DEVICE_TYPE,
        "lease_mode": _row_value(row, "lease_mode"),
        "task_id": _row_value(row, "task_id"),
        "session_id": _row_value(row, "session_id"),
        "tenant_id": _row_value(row, "tenant_id"),
        "operator_subject": _row_value(row, "operator_subject"),
        "operator_owner_user_id": _row_value(row, "operator_owner_user_id"),
        "current_operator": _row_value(row, "current_operator"),
        "authorized_operators": _row_value(row, "authorized_operators") or [],
        "status": _row_value(row, "status"),
        "expires_at": _iso(_row_value(row, "expires_at")),
        "released_at": _iso(_row_value(row, "released_at")),
        "reclaimed_at": _iso(_row_value(row, "reclaimed_at")),
        "invalidated_reason": _row_value(row, "invalidated_reason"),
        "created_at": _iso(_row_value(row, "created_at")),
        "updated_at": _iso(_row_value(row, "updated_at")),
    }


def _audit_to_dict(row: Any) -> dict[str, Any]:
    outcome = _row_value(row, "outcome")
    created_at = _iso(_row_value(row, "created_at"))
    return {
        "id": _row_value(row, "id"),
        "tenant_id": _row_value(row, "tenant_id"),
        "actor": _row_value(row, "actor"),
        "operator": _row_value(row, "actor"),
        "actor_owner_user_id": _row_value(row, "actor_owner_user_id"),
        "operator_owner_user_id": _row_value(row, "actor_owner_user_id"),
        "device_instance_id": _row_value(row, "device_instance_id"),
        "device_type": DEVICE_TYPE,
        "lease_id": _row_value(row, "lease_id"),
        "task_id": _row_value(row, "task_id"),
        "session_id": _row_value(row, "session_id"),
        "action": _row_value(row, "action"),
        "outcome": outcome,
        "status": outcome,
        "side_effect_level": _row_value(row, "side_effect_level"),
        "audit_boundary": _row_value(row, "audit_boundary"),
        "summary": _row_value(row, "summary"),
        "evidence_refs": _row_value(row, "evidence_refs") or [],
        "details": _row_value(row, "details") or {},
        "metadata": _row_value(row, "details") or {},
        "error": _row_value(row, "error"),
        "created_at": created_at,
        "occurred_at": created_at,
    }


@dataclass
class AgentDeviceActionContext:
    session_id: str
    action: str
    actor: str
    actor_owner_user_id: str | None
    lease: dict[str, Any]
    side_effect_level: str = "external"


class AgentDeviceLeaseError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 409,
        reason: str = "lease_conflict",
        lease: dict[str, Any] | None = None,
        next_step: str = "acquire_lease",
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.reason = reason
        self.lease = lease
        self.next_step = next_step


async def _session_for_user(conn: Any, session_id: str, user: CurrentUser, *, lock: bool = False) -> Any:
    suffix = " FOR UPDATE" if lock else ""
    row = await conn.fetchrow(
        f"SELECT id, tenant_id, user_id, name FROM sessions WHERE id = $1{suffix}",
        session_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if user.session_scope and session_id != user.session_scope:
        raise HTTPException(status_code=403, detail="Token not authorized for this session")
    if _row_value(row, "tenant_id") and _row_value(row, "tenant_id") != user.tenant_id:
        raise HTTPException(status_code=404, detail="Session not found")
    if user.role == "member" and _row_value(row, "user_id") != user.id:
        raise HTTPException(status_code=404, detail="Session not found")
    return row


async def _expire_active_leases(conn: Any, device_id: str) -> None:
    await conn.execute(
        """
        UPDATE agent_device_leases
        SET status = 'expired',
            invalidated_reason = 'expires_at_elapsed',
            updated_at = NOW()
        WHERE device_instance_id = $1
          AND status = 'active'
          AND expires_at IS NOT NULL
          AND expires_at <= NOW()
        """,
        device_id,
    )


async def _active_lease(conn: Any, device_id: str) -> Any | None:
    return await conn.fetchrow(
        """
        SELECT *
        FROM agent_device_leases
        WHERE device_instance_id = $1
          AND status = 'active'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        device_id,
    )


async def create_initial_lease(session_id: str, user: CurrentUser) -> dict[str, Any]:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await _session_for_user(conn, session_id, user, lock=True)
            await _expire_active_leases(conn, session_id)
            active = await _active_lease(conn, session_id)
            if active:
                return _lease_to_dict(active) or {}
            lease = await conn.fetchrow(
                """
                INSERT INTO agent_device_leases (
                    id, device_instance_id, lease_mode, task_id, session_id, tenant_id,
                    operator_subject, operator_owner_user_id, current_operator, authorized_operators
                )
                VALUES ($1, $2, 'session_bound', NULL, $2, $3, $4, $5, $4, '[]'::jsonb)
                RETURNING *
                """,
                str(uuid.uuid4()),
                session_id,
                _row_value(row, "tenant_id") or user.tenant_id,
                _operator_subject(user),
                user.id,
            )
            await _insert_audit(
                conn,
                actor=_operator_subject(user),
                actor_owner_user_id=user.id,
                tenant_id=_row_value(row, "tenant_id") or user.tenant_id,
                device_instance_id=session_id,
                lease_id=_row_value(lease, "id"),
                task_id=None,
                session_id=session_id,
                action="reserve_device",
                outcome="succeeded",
                side_effect_level="internal",
                summary="Initial session-bound device lease created",
            )
            return _lease_to_dict(lease) or {}


async def acquire_lease(
    device_id: str,
    user: CurrentUser,
    *,
    lease_mode: str = "session_bound",
    task_id: str | None = None,
    ttl_seconds: int | None = None,
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    if lease_mode not in {"session_bound", "task_bound"}:
        raise HTTPException(status_code=422, detail="lease_mode must be session_bound or task_bound")
    if lease_mode == "task_bound" and not task_id:
        raise HTTPException(status_code=422, detail="task_bound leases require task_id")

    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await _session_for_user(conn, device_id, user, lock=True)
            await _expire_active_leases(conn, device_id)
            active = await _active_lease(conn, device_id)
            if active:
                await _insert_audit(
                    conn,
                    actor=_operator_subject(user),
                    actor_owner_user_id=user.id,
                    tenant_id=_row_value(row, "tenant_id") or user.tenant_id,
                    device_instance_id=device_id,
                    lease_id=_row_value(active, "id"),
                    task_id=_row_value(active, "task_id"),
                    session_id=device_id,
                    action="reserve_device",
                    outcome="rejected",
                    side_effect_level="none",
                    summary="Device already has an active exclusive lease",
                    error="device_occupied",
                )
                raise AgentDeviceLeaseError(
                    "Device already has an active exclusive lease",
                    lease=_lease_to_dict(active),
                    reason="device_occupied",
                    next_step="refresh_visibility",
                )

            lease = await conn.fetchrow(
                """
                INSERT INTO agent_device_leases (
                    id, device_instance_id, lease_mode, task_id, session_id, tenant_id,
                    operator_subject, operator_owner_user_id, current_operator,
                    authorized_operators, expires_at
                )
                VALUES ($1, $2, $3, $4, $2, $5, $6, $7, $6, '[]'::jsonb, $8)
                RETURNING *
                """,
                str(uuid.uuid4()),
                device_id,
                lease_mode,
                task_id,
                _row_value(row, "tenant_id") or user.tenant_id,
                _operator_subject(user),
                user.id,
                _normalize_expires(ttl_seconds, expires_at),
            )
            await _insert_audit(
                conn,
                actor=_operator_subject(user),
                actor_owner_user_id=user.id,
                tenant_id=_row_value(row, "tenant_id") or user.tenant_id,
                device_instance_id=device_id,
                lease_id=_row_value(lease, "id"),
                task_id=task_id,
                session_id=device_id,
                action="reserve_device",
                outcome="succeeded",
                side_effect_level="internal",
                summary="Exclusive device lease acquired",
            )
            return _lease_to_dict(lease) or {}


async def renew_lease(
    device_id: str,
    lease_id: str,
    user: CurrentUser,
    *,
    ttl_seconds: int | None = None,
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await _session_for_user(conn, device_id, user, lock=True)
            await _expire_active_leases(conn, device_id)
            lease = await conn.fetchrow(
                """
                SELECT *
                FROM agent_device_leases
                WHERE id = $1 AND device_instance_id = $2
                FOR UPDATE
                """,
                lease_id,
                device_id,
            )
            if not lease or _row_value(lease, "status") != "active":
                raise AgentDeviceLeaseError("Lease is not active", reason="lease_invalid")
            if not _lease_matches_actor(lease, user) and not _can_manage(row, user):
                raise AgentDeviceLeaseError("Only the current operator can renew this lease", reason="operator_mismatch")
            updated = await conn.fetchrow(
                """
                UPDATE agent_device_leases
                SET expires_at = $3,
                    updated_at = NOW()
                WHERE id = $1 AND device_instance_id = $2
                RETURNING *
                """,
                lease_id,
                device_id,
                _normalize_expires(ttl_seconds, expires_at),
            )
            await _insert_audit(
                conn,
                actor=_operator_subject(user),
                actor_owner_user_id=user.id,
                tenant_id=_row_value(row, "tenant_id") or user.tenant_id,
                device_instance_id=device_id,
                lease_id=lease_id,
                task_id=_row_value(updated, "task_id"),
                session_id=device_id,
                action="renew_lease",
                outcome="succeeded",
                side_effect_level="internal",
                summary="Device lease expiration updated",
            )
            return _lease_to_dict(updated) or {}


async def release_lease(device_id: str, lease_id: str, user: CurrentUser) -> dict[str, Any]:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await _session_for_user(conn, device_id, user, lock=True)
            await _expire_active_leases(conn, device_id)
            lease = await conn.fetchrow(
                "SELECT * FROM agent_device_leases WHERE id = $1 AND device_instance_id = $2 FOR UPDATE",
                lease_id,
                device_id,
            )
            if not lease or _row_value(lease, "status") != "active":
                raise AgentDeviceLeaseError("Lease is not active", reason="lease_invalid")
            if not _lease_matches_actor(lease, user) and not _can_manage(row, user):
                raise AgentDeviceLeaseError("Only the current operator can release this lease", reason="operator_mismatch")
            released = await conn.fetchrow(
                """
                UPDATE agent_device_leases
                SET status = 'released',
                    released_at = NOW(),
                    invalidated_reason = 'released_by_operator',
                    updated_at = NOW()
                WHERE id = $1 AND device_instance_id = $2
                RETURNING *
                """,
                lease_id,
                device_id,
            )
            await _insert_audit(
                conn,
                actor=_operator_subject(user),
                actor_owner_user_id=user.id,
                tenant_id=_row_value(row, "tenant_id") or user.tenant_id,
                device_instance_id=device_id,
                lease_id=lease_id,
                task_id=_row_value(released, "task_id"),
                session_id=device_id,
                action="release_device",
                outcome="succeeded",
                side_effect_level="internal",
                summary="Device lease released",
            )
            return _lease_to_dict(released) or {}


async def reclaim_device(
    device_id: str,
    user: CurrentUser,
    *,
    lease_mode: str = "session_bound",
    task_id: str | None = None,
    ttl_seconds: int | None = None,
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    if lease_mode not in {"session_bound", "task_bound"}:
        raise HTTPException(status_code=422, detail="lease_mode must be session_bound or task_bound")
    if lease_mode == "task_bound" and not task_id:
        raise HTTPException(status_code=422, detail="task_bound leases require task_id")
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await _session_for_user(conn, device_id, user, lock=True)
            if not _can_manage(row, user):
                raise HTTPException(status_code=403, detail="Insufficient permissions")
            await _expire_active_leases(conn, device_id)
            active = await _active_lease(conn, device_id)
            reclaimed = None
            if active:
                reclaimed = await conn.fetchrow(
                    """
                    UPDATE agent_device_leases
                    SET status = 'reclaimed',
                        reclaimed_at = NOW(),
                        invalidated_reason = 'force_reclaim',
                        updated_at = NOW()
                    WHERE id = $1
                    RETURNING *
                    """,
                    _row_value(active, "id"),
                )
            lease = await conn.fetchrow(
                """
                INSERT INTO agent_device_leases (
                    id, device_instance_id, lease_mode, task_id, session_id, tenant_id,
                    operator_subject, operator_owner_user_id, current_operator,
                    authorized_operators, expires_at
                )
                VALUES ($1, $2, $3, $4, $2, $5, $6, $7, $6, '[]'::jsonb, $8)
                RETURNING *
                """,
                str(uuid.uuid4()),
                device_id,
                lease_mode,
                task_id,
                _row_value(row, "tenant_id") or user.tenant_id,
                _operator_subject(user),
                user.id,
                _normalize_expires(ttl_seconds, expires_at),
            )
            await _insert_audit(
                conn,
                actor=_operator_subject(user),
                actor_owner_user_id=user.id,
                tenant_id=_row_value(row, "tenant_id") or user.tenant_id,
                device_instance_id=device_id,
                lease_id=_row_value(lease, "id"),
                task_id=task_id,
                session_id=device_id,
                action="force_reclaim",
                outcome="succeeded",
                side_effect_level="internal",
                summary="Device lease force reclaimed",
                details={"reclaimedLeaseId": _row_value(reclaimed, "id") if reclaimed else None},
            )
            return {"lease": _lease_to_dict(lease), "reclaimedLease": _lease_to_dict(reclaimed)}


async def require_active_lease(
    session_id: str,
    user: CurrentUser,
    *,
    action: str,
    side_effect_level: str = "external",
) -> AgentDeviceActionContext:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _session_for_user(conn, session_id, user, lock=True)
            await _expire_active_leases(conn, session_id)
            active = await _active_lease(conn, session_id)
            if not active:
                raise AgentDeviceLeaseError("Device has no active lease", reason="lease_required")
            if not _lease_matches_actor(active, user):
                raise AgentDeviceLeaseError(
                    "Device is occupied by another operator",
                    lease=_lease_to_dict(active),
                    reason="operator_mismatch",
                    next_step="reclaim_or_wait",
                )
            lease = _lease_to_dict(active) or {}
    return AgentDeviceActionContext(
        session_id=session_id,
        action=action,
        actor=_operator_subject(user),
        actor_owner_user_id=user.id,
        lease=lease,
        side_effect_level=side_effect_level,
    )


async def begin_compatible_action(
    session_id: str,
    user: CurrentUser,
    *,
    action: str,
    side_effect_level: str = "external",
) -> tuple[AgentDeviceActionContext | None, dict[str, Any] | None]:
    try:
        return await require_active_lease(
            session_id,
            user,
            action=action,
            side_effect_level=side_effect_level,
        ), None
    except AgentDeviceLeaseError as exc:
        audit_id = await record_action_event(
            session_id=session_id,
            actor=_operator_subject(user),
            actor_owner_user_id=user.id,
            lease=exc.lease,
            action=action,
            outcome="rejected",
            side_effect_level="none",
            summary=exc.message,
            error=exc.reason,
        )
        return None, _agent_device_response(
            {"ok": False, "error": exc.message},
            actor=_operator_subject(user),
            lease=exc.lease,
            action=action,
            status="rejected",
            side_effect_level="none",
            retry_safety="safe_after_new_lease",
            audit_event_id=audit_id,
            next_step=exc.next_step,
        )


async def complete_compatible_action(
    ctx: AgentDeviceActionContext,
    response: dict[str, Any],
    *,
    status: str = "succeeded",
    summary: str = "",
    evidence_refs: list[dict[str, Any]] | None = None,
    details: dict[str, Any] | None = None,
    retry_safety: str = "unknown",
    next_step: str = "continue",
) -> dict[str, Any]:
    outcome = "succeeded" if status == "succeeded" else "failed"
    audit_id = await record_action_event(
        session_id=ctx.session_id,
        actor=ctx.actor,
        actor_owner_user_id=ctx.actor_owner_user_id,
        lease=ctx.lease,
        action=ctx.action,
        outcome=outcome,
        side_effect_level=ctx.side_effect_level,
        summary=summary or ctx.action,
        evidence_refs=evidence_refs,
        details=details,
        error=None if status == "succeeded" else str(response.get("error") or ""),
    )
    return _agent_device_response(
        response,
        actor=ctx.actor,
        lease=ctx.lease,
        action=ctx.action,
        status=status,
        side_effect_level=ctx.side_effect_level,
        retry_safety=retry_safety,
        audit_event_id=audit_id,
        next_step=next_step,
    )


async def fail_compatible_action(
    ctx: AgentDeviceActionContext,
    error: str,
    *,
    retry_safety: str = "unknown",
    next_step: str = "refresh_visibility",
) -> dict[str, Any]:
    return await complete_compatible_action(
        ctx,
        {"ok": False, "error": error},
        status="failed",
        summary=f"{ctx.action} failed: {error}",
        retry_safety=retry_safety,
        next_step=next_step,
    )


def _agent_device_response(
    response: dict[str, Any],
    *,
    actor: str,
    lease: dict[str, Any] | None,
    action: str,
    status: str,
    side_effect_level: str,
    retry_safety: str,
    audit_event_id: str | None,
    next_step: str,
) -> dict[str, Any]:
    payload = dict(response)
    payload["agentDevice"] = {
        "deviceInstanceId": (lease or {}).get("device_instance_id"),
        "leaseId": (lease or {}).get("lease_id"),
        "operator": actor,
        "currentOperator": (lease or {}).get("current_operator"),
        "action": action,
        "status": status,
        "sideEffectLevel": side_effect_level,
        "retrySafety": retry_safety,
        "auditEventId": audit_event_id,
        "nextStep": next_step,
    }
    return payload


async def _insert_audit(
    conn: Any,
    *,
    actor: str,
    actor_owner_user_id: str | None,
    tenant_id: str | None,
    device_instance_id: str,
    lease_id: str | None,
    task_id: str | None,
    session_id: str,
    action: str,
    outcome: str,
    side_effect_level: str,
    summary: str,
    evidence_refs: list[dict[str, Any]] | None = None,
    details: dict[str, Any] | None = None,
    error: str | None = None,
) -> str:
    audit_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO agent_device_audit_events (
            id, tenant_id, actor, actor_owner_user_id, device_instance_id,
            lease_id, task_id, session_id, action, outcome, side_effect_level,
            audit_boundary, summary, evidence_refs, details, error
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14::jsonb, $15::jsonb, $16)
        """,
        audit_id,
        tenant_id,
        actor,
        actor_owner_user_id,
        device_instance_id,
        lease_id,
        task_id,
        session_id,
        action,
        outcome,
        side_effect_level,
        AUDIT_BOUNDARY,
        summary,
        evidence_refs or [],
        details or {},
        error,
    )
    return audit_id


async def record_action_event(
    *,
    session_id: str,
    actor: str,
    actor_owner_user_id: str | None,
    lease: dict[str, Any] | None,
    action: str,
    outcome: str,
    side_effect_level: str,
    summary: str,
    evidence_refs: list[dict[str, Any]] | None = None,
    details: dict[str, Any] | None = None,
    error: str | None = None,
) -> str | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT tenant_id FROM sessions WHERE id = $1", session_id)
        tenant_id = _row_value(row, "tenant_id") if row else (lease or {}).get("tenant_id")
        return await _insert_audit(
            conn,
            actor=actor,
            actor_owner_user_id=actor_owner_user_id,
            tenant_id=tenant_id,
            device_instance_id=session_id,
            lease_id=(lease or {}).get("lease_id"),
            task_id=(lease or {}).get("task_id"),
            session_id=session_id,
            action=action,
            outcome=outcome,
            side_effect_level=side_effect_level,
            summary=summary,
            evidence_refs=evidence_refs,
            details=details,
            error=error,
        )


async def record_runtime_action(
    session_id: str,
    *,
    action: str,
    outcome: str,
    summary: str,
    side_effect_level: str = "internal",
    evidence_refs: list[dict[str, Any]] | None = None,
    details: dict[str, Any] | None = None,
    error: str | None = None,
) -> str | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await _expire_active_leases(conn, session_id)
        lease = await _active_lease(conn, session_id)
        row = await conn.fetchrow("SELECT tenant_id, user_id FROM sessions WHERE id = $1", session_id)
        return await _insert_audit(
            conn,
            actor=_runtime_operator(session_id),
            actor_owner_user_id=_row_value(row, "user_id"),
            tenant_id=_row_value(row, "tenant_id"),
            device_instance_id=session_id,
            lease_id=_row_value(lease, "id"),
            task_id=_row_value(lease, "task_id"),
            session_id=session_id,
            action=action,
            outcome=outcome,
            side_effect_level=side_effect_level,
            summary=summary,
            evidence_refs=evidence_refs,
            details=details,
            error=error,
        )


async def list_device_visibility(user: CurrentUser) -> list[dict[str, Any]]:
    pool = get_pool()
    async with pool.acquire() as conn:
        if user.session_scope:
            rows = await conn.fetch(
                _visibility_sql("s.id = $1 AND (s.tenant_id = $2 OR s.tenant_id IS NULL)"),
                user.session_scope,
                user.tenant_id,
            )
        elif user.role in ("superadmin", "admin"):
            rows = await conn.fetch(_visibility_sql("s.tenant_id = $1"), user.tenant_id)
        else:
            rows = await conn.fetch(_visibility_sql("s.tenant_id = $1 AND s.user_id = $2"), user.tenant_id, user.id)
    statuses = await get_all_container_statuses()
    return [_visibility_from_row(row, statuses.get(str(row["id"])[:12], "not_found")) for row in rows]


async def get_device_visibility(device_id: str, user: CurrentUser) -> dict[str, Any]:
    pool = get_pool()
    async with pool.acquire() as conn:
        await _session_for_user(conn, device_id, user)
        row = await conn.fetchrow(_visibility_sql("s.id = $1"), device_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    return _visibility_from_row(row, await get_container_status(device_id))


def _visibility_sql(where_sql: str) -> str:
    return f"""
        SELECT
            s.id,
            s.name,
            s.tenant_id,
            s.user_id,
            s.updated_at AS session_updated_at,
            l.id AS lease_id,
            l.lease_mode,
            l.task_id,
            l.current_operator,
            l.operator_owner_user_id,
            l.status AS lease_status,
            l.expires_at,
            l.updated_at AS lease_updated_at,
            a.id AS last_audit_id,
            a.action AS last_action,
            a.outcome AS last_outcome,
            a.summary AS last_summary,
            a.created_at AS last_audit_at
        FROM sessions s
        LEFT JOIN agent_device_leases l
          ON l.device_instance_id = s.id
         AND l.status = 'active'
         AND (l.expires_at IS NULL OR l.expires_at > NOW())
        LEFT JOIN LATERAL (
            SELECT *
            FROM agent_device_audit_events ae
            WHERE ae.device_instance_id = s.id
            ORDER BY ae.created_at DESC
            LIMIT 1
        ) a ON TRUE
        WHERE {where_sql}
        ORDER BY s.updated_at DESC
    """


def _visibility_from_row(row: Any, container_status: str) -> dict[str, Any]:
    state = "leased" if _row_value(row, "lease_id") else "idle"
    lease = None
    if _row_value(row, "lease_id"):
        lease = {
            "id": _row_value(row, "lease_id"),
            "lease_id": _row_value(row, "lease_id"),
            "device_instance_id": _row_value(row, "id"),
            "device_type": DEVICE_TYPE,
            "lease_mode": _row_value(row, "lease_mode"),
            "task_id": _row_value(row, "task_id"),
            "session_id": _row_value(row, "id"),
            "tenant_id": _row_value(row, "tenant_id"),
            "operator_subject": _row_value(row, "current_operator"),
            "operator_owner_user_id": _row_value(row, "operator_owner_user_id"),
            "current_operator": _row_value(row, "current_operator"),
            "status": "active",
            "expires_at": _iso(_row_value(row, "expires_at")),
            "updated_at": _iso(_row_value(row, "lease_updated_at")),
        }
    last_action_summary = None
    if _row_value(row, "last_audit_id"):
        last_action_summary = {
            "action": _row_value(row, "last_action"),
            "status": _row_value(row, "last_outcome"),
            "auditEventId": _row_value(row, "last_audit_id"),
            "summary": _row_value(row, "last_summary"),
            "occurredAt": _iso(_row_value(row, "last_audit_at")),
        }
    return {
        "device_instance_id": _row_value(row, "id"),
        "device_type": DEVICE_TYPE,
        "display_name": _row_value(row, "name"),
        "session_name": _row_value(row, "name"),
        "owner_user_id": _row_value(row, "user_id"),
        "state": state,
        "lease_id": _row_value(row, "lease_id"),
        "lease_mode": _row_value(row, "lease_mode"),
        "current_operator": _row_value(row, "current_operator"),
        "operator_owner_user_id": _row_value(row, "operator_owner_user_id"),
        "task_id": _row_value(row, "task_id"),
        "session_id": _row_value(row, "id"),
        "pause_capability": "soft_pause",
        "needs_intervention": False,
        "observable_surface_ref": None,
        "last_action_summary": last_action_summary,
        "last_action": _row_value(row, "last_action"),
        "last_action_outcome": _row_value(row, "last_outcome"),
        "last_audit_event_id": _row_value(row, "last_audit_id"),
        "runtime_state": container_status,
        "containerStatus": container_status,
        "expires_at": _iso(_row_value(row, "expires_at")),
        "lease": lease,
        "updated_at": _iso(_row_value(row, "lease_updated_at") or _row_value(row, "session_updated_at")),
    }


async def list_audit_events(
    user: CurrentUser,
    *,
    device_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    pool = get_pool()
    limit = max(1, min(int(limit), 500))
    async with pool.acquire() as conn:
        if device_id:
            await _session_for_user(conn, device_id, user)
            rows = await conn.fetch(
                """
                SELECT *
                FROM agent_device_audit_events
                WHERE device_instance_id = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                device_id,
                limit,
            )
        elif user.session_scope:
            rows = await conn.fetch(
                """
                SELECT *
                FROM agent_device_audit_events
                WHERE session_id = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                user.session_scope,
                limit,
            )
        elif user.role in ("superadmin", "admin"):
            rows = await conn.fetch(
                """
                SELECT *
                FROM agent_device_audit_events
                WHERE tenant_id = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                user.tenant_id,
                limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT ae.*
                FROM agent_device_audit_events ae
                JOIN sessions s ON s.id = ae.session_id
                WHERE s.tenant_id = $1 AND s.user_id = $2
                ORDER BY ae.created_at DESC
                LIMIT $3
                """,
                user.tenant_id,
                user.id,
                limit,
            )
    return [_audit_to_dict(row) for row in rows]
