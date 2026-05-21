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
PROVIDER = "browser-pilot"
DEVICE_PROFILE = "browser"
COMPLIANCE_LEVEL = "level1_device_governance"
CONCURRENCY_MODEL = "exclusive"
SUPPORTED_LEASE_MODES = ["session_bound", "task_bound"]
UNSUPPORTED_PROFILES = ["control_transfer"]
SYSTEM_LEASE_EXPIRER = "system:lease_expirer"

LEVEL1_POLICY = {
    "leaseRequired": True,
    "exclusiveLease": True,
    "ownerlessActiveLeaseAllowed": False,
    "controlTransfer": "unsupported",
    "interventionRequest": "unsupported_level2",
}

LEVEL1_CAPABILITIES = [
    "visibility.read",
    "lease.acquire",
    "lease.renew",
    "lease.release",
    "lease.reclaim",
    "audit.read",
    "browser.navigate",
    "browser.observe",
    "browser.click",
    "browser.type",
    "browser.files",
]


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
    api_token_id = getattr(user, "api_token_id", None)
    if api_token_id:
        return f"token:{api_token_id}"
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


def _lease_with_audit(row: Any | None, audit_event_id: str | None) -> dict[str, Any]:
    lease = _lease_to_dict(row) or {}
    if audit_event_id:
        lease["audit_event_id"] = audit_event_id
    return lease


def _audit_to_dict(row: Any) -> dict[str, Any]:
    outcome = _row_value(row, "outcome")
    created_at = _iso(_row_value(row, "created_at"))
    details = _row_value(row, "details") or {}
    evidence_refs = _row_value(row, "evidence_refs") or []
    side_effect_level = _row_value(row, "side_effect_level")
    evidence_status = details.get("evidenceStatus") or _evidence_status(side_effect_level, evidence_refs)
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
        "executionStatus": outcome,
        "side_effect_level": side_effect_level,
        "sideEffectLevel": side_effect_level,
        "sideEffectStatus": _side_effect_status(outcome, side_effect_level),
        "failureCategory": _row_value(row, "error") if outcome in {"failed", "rejected"} else None,
        "auditStatus": "recorded",
        "evidenceStatus": evidence_status,
        "stateChanged": _state_changed(outcome, side_effect_level),
        "audit_boundary": _row_value(row, "audit_boundary"),
        "summary": _row_value(row, "summary"),
        "evidence_refs": evidence_refs,
        "evidenceRefs": evidence_refs,
        "details": details,
        "metadata": details,
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


def _current_page_evidence_ref(session_id: str) -> dict[str, Any]:
    return {
        "type": "browser_session",
        "ref": f"browser_session:{session_id}:current_page",
        "session_id": session_id,
        "surface": "current_page",
    }


def _extract_page_snapshot(response: dict[str, Any]) -> dict[str, Any]:
    page = response.get("currentPage")
    if isinstance(page, dict):
        return {
            "url": page.get("url") or "",
            "title": page.get("title") or "",
        }
    if response.get("url") is not None or response.get("title") is not None:
        return {
            "url": response.get("url") or "",
            "title": response.get("title") or "",
        }
    return {}


def _ensure_action_evidence(
    session_id: str,
    *,
    side_effect_level: str,
    status: str,
    evidence_refs: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    refs = list(evidence_refs or [])
    if side_effect_level == "external" and status == "succeeded" and not refs:
        refs.append(_current_page_evidence_ref(session_id))
    return refs


def _evidence_status(side_effect_level: str | None, evidence_refs: list[dict[str, Any]] | None) -> str:
    if evidence_refs:
        return "captured"
    if side_effect_level == "external":
        return "not_captured"
    return "not_required"


def _side_effect_status(status: str, side_effect_level: str | None) -> str:
    if side_effect_level in {None, "none"}:
        return "not_applicable"
    if status == "succeeded":
        return "applied"
    if status == "rejected":
        return "not_applied"
    return "unknown"


def _state_changed(status: str, side_effect_level: str | None) -> bool:
    return status == "succeeded" and side_effect_level not in {None, "none"}


def _details_with_evidence_semantics(
    ctx: AgentDeviceActionContext,
    response: dict[str, Any],
    details: dict[str, Any] | None,
    *,
    evidence_status: str,
) -> dict[str, Any]:
    merged = dict(details or {})
    page = _extract_page_snapshot(response)
    if page:
        merged.setdefault("currentPage", page)
        if page.get("url"):
            merged.setdefault("url", page["url"])
        if page.get("title"):
            merged.setdefault("title", page["title"])
    if ctx.side_effect_level == "external":
        merged.setdefault("actionParameters", dict(details or {}))
    merged["evidenceStatus"] = evidence_status
    return merged


class AgentDeviceLeaseError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 409,
        reason: str = "lease_conflict",
        lease: dict[str, Any] | None = None,
        next_step: str = "acquire_lease",
        audit_event_id: str | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.reason = reason
        self.lease = lease
        self.next_step = next_step
        self.audit_event_id = audit_event_id


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
    expired_rows = await conn.fetch(
        """
        SELECT *
        FROM agent_device_leases
        WHERE device_instance_id = $1
          AND status = 'active'
          AND expires_at IS NOT NULL
          AND expires_at <= NOW()
        """,
        device_id,
    )
    if not expired_rows:
        return
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
    for lease in expired_rows:
        await _insert_audit(
            conn,
            actor=SYSTEM_LEASE_EXPIRER,
            actor_owner_user_id=None,
            tenant_id=_row_value(lease, "tenant_id"),
            device_instance_id=device_id,
            lease_id=_row_value(lease, "id"),
            task_id=_row_value(lease, "task_id"),
            session_id=_row_value(lease, "session_id") or device_id,
            action="lease_expired",
            outcome="succeeded",
            side_effect_level="internal",
            summary="Device lease automatically expired",
            details={"invalidatedReason": "expires_at_elapsed"},
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
            audit_id = await _insert_audit(
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
            return _lease_with_audit(lease, audit_id)


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
        raise AgentDeviceLeaseError(
            "lease_mode must be session_bound or task_bound",
            status_code=422,
            reason="invalid_lease_mode",
            next_step="fix_request",
        )
    if lease_mode == "task_bound" and not task_id:
        raise AgentDeviceLeaseError(
            "task_bound leases require task_id",
            status_code=422,
            reason="task_id_required",
            next_step="fix_request",
        )

    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await _session_for_user(conn, device_id, user, lock=True)
            await _expire_active_leases(conn, device_id)
            active = await _active_lease(conn, device_id)
            if active:
                audit_id = await _insert_audit(
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
                    audit_event_id=audit_id,
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
            audit_id = await _insert_audit(
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
            return _lease_with_audit(lease, audit_id)


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
                audit_id = await _insert_audit(
                    conn,
                    actor=_operator_subject(user),
                    actor_owner_user_id=user.id,
                    tenant_id=_row_value(row, "tenant_id") or user.tenant_id,
                    device_instance_id=device_id,
                    lease_id=lease_id,
                    task_id=_row_value(lease, "task_id"),
                    session_id=device_id,
                    action="renew_lease",
                    outcome="rejected",
                    side_effect_level="none",
                    summary="Lease is not active",
                    details={"requestedLeaseId": lease_id},
                    error="lease_invalid",
                )
                raise AgentDeviceLeaseError(
                    "Lease is not active",
                    reason="lease_invalid",
                    next_step="acquire_lease",
                    lease=_lease_to_dict(lease),
                    audit_event_id=audit_id,
                )
            if not _lease_matches_actor(lease, user) and not _can_manage(row, user):
                audit_id = await _insert_audit(
                    conn,
                    actor=_operator_subject(user),
                    actor_owner_user_id=user.id,
                    tenant_id=_row_value(row, "tenant_id") or user.tenant_id,
                    device_instance_id=device_id,
                    lease_id=lease_id,
                    task_id=_row_value(lease, "task_id"),
                    session_id=device_id,
                    action="renew_lease",
                    outcome="rejected",
                    side_effect_level="none",
                    summary="Only the current operator can renew this lease",
                    details={"requestedLeaseId": lease_id, "currentOperator": _row_value(lease, "current_operator")},
                    error="operator_mismatch",
                )
                raise AgentDeviceLeaseError(
                    "Only the current operator can renew this lease",
                    reason="operator_mismatch",
                    next_step="reclaim_or_wait",
                    lease=_lease_to_dict(lease),
                    audit_event_id=audit_id,
                )
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
            audit_id = await _insert_audit(
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
            return _lease_with_audit(updated, audit_id)


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
                audit_id = await _insert_audit(
                    conn,
                    actor=_operator_subject(user),
                    actor_owner_user_id=user.id,
                    tenant_id=_row_value(row, "tenant_id") or user.tenant_id,
                    device_instance_id=device_id,
                    lease_id=lease_id,
                    task_id=_row_value(lease, "task_id"),
                    session_id=device_id,
                    action="release_device",
                    outcome="rejected",
                    side_effect_level="none",
                    summary="Lease is not active",
                    details={"requestedLeaseId": lease_id},
                    error="lease_invalid",
                )
                raise AgentDeviceLeaseError(
                    "Lease is not active",
                    reason="lease_invalid",
                    next_step="acquire_lease",
                    lease=_lease_to_dict(lease),
                    audit_event_id=audit_id,
                )
            if not _lease_matches_actor(lease, user) and not _can_manage(row, user):
                audit_id = await _insert_audit(
                    conn,
                    actor=_operator_subject(user),
                    actor_owner_user_id=user.id,
                    tenant_id=_row_value(row, "tenant_id") or user.tenant_id,
                    device_instance_id=device_id,
                    lease_id=lease_id,
                    task_id=_row_value(lease, "task_id"),
                    session_id=device_id,
                    action="release_device",
                    outcome="rejected",
                    side_effect_level="none",
                    summary="Only the current operator can release this lease",
                    details={"requestedLeaseId": lease_id, "currentOperator": _row_value(lease, "current_operator")},
                    error="operator_mismatch",
                )
                raise AgentDeviceLeaseError(
                    "Only the current operator can release this lease",
                    reason="operator_mismatch",
                    next_step="reclaim_or_wait",
                    lease=_lease_to_dict(lease),
                    audit_event_id=audit_id,
                )
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
            audit_id = await _insert_audit(
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
            return _lease_with_audit(released, audit_id)


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
        raise AgentDeviceLeaseError(
            "lease_mode must be session_bound or task_bound",
            status_code=422,
            reason="invalid_lease_mode",
            next_step="fix_request",
        )
    if lease_mode == "task_bound" and not task_id:
        raise AgentDeviceLeaseError(
            "task_bound leases require task_id",
            status_code=422,
            reason="task_id_required",
            next_step="fix_request",
        )
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await _session_for_user(conn, device_id, user, lock=True)
            if not _can_manage(row, user):
                audit_id = await _insert_audit(
                    conn,
                    actor=_operator_subject(user),
                    actor_owner_user_id=user.id,
                    tenant_id=_row_value(row, "tenant_id") or user.tenant_id,
                    device_instance_id=device_id,
                    lease_id=None,
                    task_id=task_id,
                    session_id=device_id,
                    action="force_reclaim",
                    outcome="rejected",
                    side_effect_level="none",
                    summary="Only the owner or an administrator can reclaim this device",
                    error="insufficient_permissions",
                )
                raise AgentDeviceLeaseError(
                    "Only the owner or an administrator can reclaim this device",
                    status_code=403,
                    reason="insufficient_permissions",
                    next_step="ask_admin",
                    audit_event_id=audit_id,
                )
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
            audit_id = await _insert_audit(
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
            return {"lease": _lease_with_audit(lease, audit_id), "reclaimedLease": _lease_to_dict(reclaimed)}


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
            device_instance_id=session_id,
            actor=_operator_subject(user),
            lease=exc.lease,
            action=action,
            status="rejected",
            side_effect_level="none",
            retry_safety="safe_after_new_lease",
            audit_event_id=audit_id,
            next_step=exc.next_step,
            failure_category=exc.reason,
            state_changed=False,
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
    failure_category: str | None = None,
) -> dict[str, Any]:
    outcome = "succeeded" if status == "succeeded" else "failed"
    normalized_evidence_refs = _ensure_action_evidence(
        ctx.session_id,
        side_effect_level=ctx.side_effect_level,
        status=status,
        evidence_refs=evidence_refs,
    )
    evidence_status = _evidence_status(ctx.side_effect_level, normalized_evidence_refs)
    normalized_details = _details_with_evidence_semantics(
        ctx,
        response,
        details,
        evidence_status=evidence_status,
    )
    audit_id = await record_action_event(
        session_id=ctx.session_id,
        actor=ctx.actor,
        actor_owner_user_id=ctx.actor_owner_user_id,
        lease=ctx.lease,
        action=ctx.action,
        outcome=outcome,
        side_effect_level=ctx.side_effect_level,
        summary=summary or ctx.action,
        evidence_refs=normalized_evidence_refs,
        details=normalized_details,
        error=None if status == "succeeded" else str(response.get("error") or ""),
    )
    return _agent_device_response(
        response,
        device_instance_id=ctx.session_id,
        actor=ctx.actor,
        lease=ctx.lease,
        action=ctx.action,
        status=status,
        side_effect_level=ctx.side_effect_level,
        retry_safety=retry_safety,
        audit_event_id=audit_id,
        next_step=next_step,
        evidence_refs=normalized_evidence_refs,
        evidence_status=evidence_status,
        failure_category=failure_category,
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
        failure_category="runtime_error",
    )


def _agent_device_response(
    response: dict[str, Any],
    *,
    device_instance_id: str | None = None,
    actor: str,
    lease: dict[str, Any] | None,
    action: str,
    status: str,
    side_effect_level: str,
    retry_safety: str,
    audit_event_id: str | None,
    next_step: str,
    evidence_refs: list[dict[str, Any]] | None = None,
    evidence_status: str | None = None,
    failure_category: str | None = None,
    state_changed: bool | None = None,
) -> dict[str, Any]:
    payload = dict(response)
    resolved_device_id = device_instance_id or (lease or {}).get("device_instance_id")
    resolved_evidence_status = evidence_status or _evidence_status(side_effect_level, evidence_refs)
    payload["agentDevice"] = {
        "deviceInstanceId": resolved_device_id,
        "leaseId": (lease or {}).get("lease_id"),
        "operator": actor,
        "currentOperator": (lease or {}).get("current_operator"),
        "action": action,
        "status": status,
        "executionStatus": status,
        "sideEffectLevel": side_effect_level,
        "sideEffectStatus": _side_effect_status(status, side_effect_level),
        "failureCategory": failure_category,
        "retrySafety": retry_safety,
        "auditEventId": audit_event_id,
        "auditStatus": "recorded" if audit_event_id else "not_recorded",
        "evidenceStatus": resolved_evidence_status,
        "evidenceRefs": evidence_refs or [],
        "stateChanged": _state_changed(status, side_effect_level) if state_changed is None else state_changed,
        "nextStep": next_step,
    }
    return payload


def control_action_response(
    response: dict[str, Any],
    *,
    device_id: str,
    user: CurrentUser,
    lease: dict[str, Any] | None,
    action: str,
    status: str,
    audit_event_id: str | None = None,
    next_step: str = "continue",
    retry_safety: str = "safe",
    failure_category: str | None = None,
    state_changed: bool | None = None,
) -> dict[str, Any]:
    return _agent_device_response(
        response,
        device_instance_id=device_id,
        actor=_operator_subject(user),
        lease=lease,
        action=action,
        status=status,
        side_effect_level="internal" if status == "succeeded" else "none",
        retry_safety=retry_safety,
        audit_event_id=audit_event_id,
        next_step=next_step,
        evidence_status="not_required",
        failure_category=failure_category,
        state_changed=status == "succeeded" if state_changed is None else state_changed,
    )


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


async def record_control_rejection(
    device_id: str,
    user: CurrentUser,
    *,
    action: str,
    summary: str,
    reason: str,
    lease: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> str | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        try:
            row = await _session_for_user(conn, device_id, user)
        except HTTPException:
            return None
        return await _insert_audit(
            conn,
            actor=_operator_subject(user),
            actor_owner_user_id=user.id,
            tenant_id=_row_value(row, "tenant_id") or user.tenant_id,
            device_instance_id=device_id,
            lease_id=(lease or {}).get("lease_id"),
            task_id=(lease or {}).get("task_id"),
            session_id=device_id,
            action=action,
            outcome="rejected",
            side_effect_level="none",
            summary=summary,
            details=details,
            error=reason,
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
            s.created_at AS session_created_at,
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
            a.side_effect_level AS last_side_effect_level,
            a.summary AS last_summary,
            a.evidence_refs AS last_evidence_refs,
            a.details AS last_details,
            a.error AS last_error,
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
    browser_pilot_state = "leased" if _row_value(row, "lease_id") else "idle"
    state = "ERROR" if str(container_status).lower() == "dead" else ("OCCUPIED" if _row_value(row, "lease_id") else "IDLE")
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
        last_details = _row_value(row, "last_details") or {}
        last_evidence_refs = _row_value(row, "last_evidence_refs") or []
        last_side_effect_level = _row_value(row, "last_side_effect_level")
        last_action_summary = {
            "action": _row_value(row, "last_action"),
            "status": _row_value(row, "last_outcome"),
            "executionStatus": _row_value(row, "last_outcome"),
            "sideEffectLevel": last_side_effect_level,
            "sideEffectStatus": _side_effect_status(_row_value(row, "last_outcome"), last_side_effect_level),
            "failureCategory": _row_value(row, "last_error") if _row_value(row, "last_outcome") in {"failed", "rejected"} else None,
            "auditEventId": _row_value(row, "last_audit_id"),
            "auditStatus": "recorded",
            "evidenceStatus": last_details.get("evidenceStatus") or _evidence_status(last_side_effect_level, last_evidence_refs),
            "summary": _row_value(row, "last_summary"),
            "occurredAt": _iso(_row_value(row, "last_audit_at")),
        }
    tenant_id = _row_value(row, "tenant_id")
    return {
        "device_instance_id": _row_value(row, "id"),
        "device_type": DEVICE_TYPE,
        "provider": PROVIDER,
        "device_profile": DEVICE_PROFILE,
        "display_name": _row_value(row, "name"),
        "session_name": _row_value(row, "name"),
        "owner_user_id": _row_value(row, "user_id"),
        "state": state,
        "browser_pilot_state": browser_pilot_state,
        "lease_id": _row_value(row, "lease_id"),
        "lease_mode": _row_value(row, "lease_mode"),
        "current_operator": _row_value(row, "current_operator"),
        "operator_owner_user_id": _row_value(row, "operator_owner_user_id"),
        "task_id": _row_value(row, "task_id"),
        "session_id": _row_value(row, "id"),
        "context_id": f"tenant:{tenant_id}" if tenant_id else "tenant:unassigned",
        "compliance_level": COMPLIANCE_LEVEL,
        "concurrency_model": CONCURRENCY_MODEL,
        "supported_lease_modes": SUPPORTED_LEASE_MODES,
        "unsupported_profiles": UNSUPPORTED_PROFILES,
        "policy": LEVEL1_POLICY,
        "admitted_by": "browser-pilot:agent-device-governance",
        "admitted_at": _iso(_row_value(row, "session_created_at") or _row_value(row, "session_updated_at")),
        "capabilities": LEVEL1_CAPABILITIES,
        "pause_capability": "soft_pause",
        "needs_intervention": False,
        "observable_surface_ref": None,
        "observable_surface_status": "not_required_level1",
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
