from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth.jwt import create_platform_access_token
from app.auth.password import hash_password, verify_password
from app.config import SAAS_DEFAULT_ACTIVE_SESSION_LIMIT, SAAS_DEFAULT_MAX_SESSION_SECONDS
from app.db import get_pool
from app.download_watcher import stop_download_watcher
from app.file_store import get_store
from app.platform_auth import CurrentPlatformUser, get_current_platform_user, require_platform_role
from app.platform_control import (
    DEFAULT_PLAN_ID,
    DEFAULT_RUNTIME_POOL_ID,
    active_runtime_session_ids,
    build_runtime_deploy_values,
    ensure_default_plan,
    ensure_default_runtime_pool,
    ensure_tenant_platform_defaults,
    mark_runtime_placement_ended,
    mark_runtime_placement_reclaiming,
    mark_runtime_placement_revoke_failed,
    record_platform_audit,
    runtime_namespace_for_tenant,
    update_tenant_runtime_quota,
)
from app.runtime_provider import remove_container

router = APIRouter(prefix="/api/platform", tags=["platform"])

DEFAULT_TENANT_DELETE_RETENTION_DAYS = 30


class PlatformSetupBody(BaseModel):
    email: str
    password: str
    name: str


class PlatformLoginBody(BaseModel):
    email: str
    password: str


class InitialTenantAdminBody(BaseModel):
    email: str
    password: str
    name: str


class CreateTenantBody(BaseModel):
    name: str
    slug: str | None = None
    activeSessionLimit: int | None = Field(default=None, ge=0)
    maxSessionSeconds: int | None = Field(default=None, gt=0)
    planId: str | None = DEFAULT_PLAN_ID
    initialAdmin: InitialTenantAdminBody | None = None
    reason: str = "tenant_created"


class TenantQuotaBody(BaseModel):
    activeSessionLimit: int = Field(ge=0)
    maxSessionSeconds: int = Field(gt=0)
    runtimeClassLimits: dict[str, Any] | None = None
    reason: str = "quota_update"


class PlanBody(BaseModel):
    code: str
    name: str
    defaultActiveSessionLimit: int = Field(ge=0)
    defaultRuntimeClassLimits: dict[str, Any] | None = None
    defaultMaxSessionSeconds: int = Field(gt=0)
    isActive: bool = True
    reason: str = "plan_create"


class PlanUpdateBody(BaseModel):
    name: str | None = None
    defaultActiveSessionLimit: int | None = Field(default=None, ge=0)
    defaultRuntimeClassLimits: dict[str, Any] | None = None
    defaultMaxSessionSeconds: int | None = Field(default=None, gt=0)
    isActive: bool | None = None
    reason: str = "plan_update"


class TenantEntitlementBody(BaseModel):
    planId: str | None = DEFAULT_PLAN_ID
    activeSessionLimitOverride: int | None = Field(default=None, ge=0)
    runtimeClassLimitsOverride: dict[str, Any] | None = None
    maxSessionSecondsOverride: int | None = Field(default=None, gt=0)
    contractRef: str | None = None
    trialEndsAt: datetime | None = None
    effectiveUntil: datetime | None = None
    reason: str = "entitlement_update"


class TenantLifecycleBody(BaseModel):
    reason: str = ""


class TenantDeleteBody(BaseModel):
    reason: str = "tenant_delete"
    retentionDays: int = Field(default=DEFAULT_TENANT_DELETE_RETENTION_DAYS, ge=0, le=3650)


class TenantPurgeRequestBody(BaseModel):
    reason: str = "tenant_purge_request"


class TenantPurgeBody(BaseModel):
    reason: str = "tenant_purge"


class TenantRuntimeRevokeBody(BaseModel):
    reason: str = "tenant_runtime_revoke"


class RuntimeImageBody(BaseModel):
    runtimeClass: str
    imageRef: str
    imageDigest: str
    chromeVersion: str | None = None
    buildId: str | None = None
    scanStatus: Literal["pending", "passed", "failed"] = "pending"
    approvalStatus: Literal["pending", "approved", "rejected", "revoked"] = "pending"
    reason: str = "runtime_image_create"


class RuntimeImageUpdateBody(BaseModel):
    scanStatus: Literal["pending", "passed", "failed"] | None = None
    approvalStatus: Literal["pending", "approved", "rejected", "revoked"] | None = None
    reason: str = "runtime_image_update"


class RuntimePoolBody(BaseModel):
    id: str | None = None
    name: str
    runtimeClasses: list[str] = Field(default_factory=lambda: ["standard_chrome", "cloak_chromium"])
    activeSessionCapacity: int = Field(ge=0)
    isEnabled: bool = True
    isDraining: bool = False
    reason: str = "runtime_pool_create"


class RuntimePoolUpdateBody(BaseModel):
    name: str | None = None
    runtimeClasses: list[str] | None = None
    activeSessionCapacity: int | None = Field(default=None, ge=0)
    isEnabled: bool | None = None
    isDraining: bool | None = None
    reason: str = "runtime_pool_update"


class RuntimeNodeBody(BaseModel):
    runtimePoolId: str = DEFAULT_RUNTIME_POOL_ID
    providerNodeName: str
    status: Literal["active", "draining", "disabled"] = "active"
    labels: dict[str, Any] | None = None
    capacity: dict[str, Any] | None = None
    allocatable: dict[str, Any] | None = None
    reason: str = "runtime_node_register"


class RuntimeNodeUpdateBody(BaseModel):
    status: Literal["active", "draining", "disabled"] | None = None
    labels: dict[str, Any] | None = None
    capacity: dict[str, Any] | None = None
    allocatable: dict[str, Any] | None = None
    reason: str = "runtime_node_update"


class PlatformAuditEventBody(BaseModel):
    action: str
    targetType: str = "deployment"
    targetId: str | None = None
    tenantId: str | None = None
    requestId: str | None = None
    outcome: Literal["success", "failure"] = "success"
    reason: str | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    error: str | None = None


def _slug_for(name: str, explicit: str | None = None) -> str:
    raw = (explicit or name).strip().lower()
    slug = re.sub(r"[^a-z0-9-]+", "-", raw).strip("-")[:64]
    return slug or "tenant"


def _platform_user_payload(row, *, token: str | None = None) -> dict[str, Any]:
    payload = {
        "id": row["id"],
        "email": row["email"],
        "name": row["name"],
        "role": row["role"],
    }
    if token:
        payload["access_token"] = token
    return payload


def _tenant_payload(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "slug": row["slug"],
        "createdAt": row["created_at"].isoformat() if row["created_at"] else None,
        "status": row["status"],
        "runtimeNamespace": row["runtime_namespace"],
        "runtimeImagePolicy": row["runtime_image_policy"] or {},
        "activeSessionLimit": row["active_session_limit"],
        "runtimeClassLimits": row["runtime_class_limits"] or {},
        "maxSessionSeconds": row["max_session_seconds"],
        "planId": row["plan_id"],
        "planCode": row["plan_code"],
        "contractRef": row["contract_ref"],
        "trialEndsAt": row["trial_ends_at"].isoformat() if row["trial_ends_at"] else None,
        "sessionCount": int(row["session_count"] or 0),
        "suspendedAt": row["suspended_at"].isoformat() if row["suspended_at"] else None,
        "suspendReason": row["suspend_reason"],
        "deletedAt": row["deleted_at"].isoformat() if row["deleted_at"] else None,
        "deleteReason": row["delete_reason"],
        "retentionUntil": row["retention_until"].isoformat() if row["retention_until"] else None,
        "purgeRequestedAt": row["purge_requested_at"].isoformat() if row["purge_requested_at"] else None,
        "purgeRequestReason": row["purge_request_reason"],
    }


def _plan_payload(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "code": row["code"],
        "name": row["name"],
        "defaultActiveSessionLimit": row["default_active_session_limit"],
        "defaultRuntimeClassLimits": row["default_runtime_class_limits"] or {},
        "defaultMaxSessionSeconds": row["default_max_session_seconds"],
        "isActive": row["is_active"],
        "createdAt": row["created_at"].isoformat() if row["created_at"] else None,
        "updatedAt": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


def _runtime_pool_payload(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "runtimeClasses": row["runtime_classes"] or [],
        "activeSessionCapacity": row["active_session_capacity"],
        "activeReservationCount": int(row["active_reservation_count"] or 0),
        "isEnabled": row["is_enabled"],
        "isDraining": row["is_draining"],
        "drainReason": row["drain_reason"],
        "drainedBy": row["drained_by"],
        "drainedAt": row["drained_at"].isoformat() if row["drained_at"] else None,
        "createdAt": row["created_at"].isoformat() if row["created_at"] else None,
        "updatedAt": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


def _runtime_node_payload(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "runtimePoolId": row["runtime_pool_id"],
        "providerNodeName": row["provider_node_name"],
        "status": row["status"],
        "labels": row["labels"] or {},
        "capacity": row["capacity"] or {},
        "allocatable": row["allocatable"] or {},
        "drainReason": row["drain_reason"],
        "drainedBy": row["drained_by"],
        "drainedAt": row["drained_at"].isoformat() if row["drained_at"] else None,
        "disabledReason": row["disabled_reason"],
        "disabledBy": row["disabled_by"],
        "disabledAt": row["disabled_at"].isoformat() if row["disabled_at"] else None,
        "lastSeenAt": row["last_seen_at"].isoformat() if row["last_seen_at"] else None,
        "createdAt": row["created_at"].isoformat() if row["created_at"] else None,
        "updatedAt": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


def _validate_sha256_digest(image_digest: str) -> None:
    if not re.fullmatch(r"sha256:[a-fA-F0-9]{64}", image_digest.strip()):
        raise HTTPException(status_code=422, detail="Runtime image digest must be a pinned sha256 digest")


def _validate_runtime_image_approval(scan_status: str, approval_status: str) -> None:
    if approval_status == "approved" and scan_status != "passed":
        raise HTTPException(status_code=422, detail="Approved runtime image must have passed scan status")


def _as_utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _affected_count(result: str) -> int:
    try:
        return int(result.rsplit(" ", 1)[1])
    except (IndexError, ValueError):
        return 0


async def _tenant_row(pool, tenant_id: str):
    return await pool.fetchrow(
        """
        SELECT t.id, t.name, t.slug, t.created_at,
               COALESCE(tps.status, 'active') AS status,
               tps.runtime_namespace, tps.runtime_image_policy,
               tps.suspended_at, tps.suspend_reason,
               tps.deleted_at, tps.delete_reason,
               tps.retention_until, tps.purge_requested_at, tps.purge_request_reason,
               trq.active_session_limit, trq.runtime_class_limits, trq.max_session_seconds,
               te.plan_id, te.contract_ref, te.trial_ends_at,
               p.code AS plan_code,
               (SELECT COUNT(*) FROM sessions s WHERE s.tenant_id = t.id) AS session_count
        FROM tenants t
        LEFT JOIN tenant_platform_settings tps ON tps.tenant_id = t.id
        LEFT JOIN tenant_runtime_quotas trq ON trq.tenant_id = t.id
        LEFT JOIN tenant_entitlements te ON te.tenant_id = t.id
        LEFT JOIN plans p ON p.id = te.plan_id
        WHERE t.id = $1
        """,
        tenant_id,
    )


def _entitlement_payload(row) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "planId": row["plan_id"],
        "activeSessionLimitOverride": row["active_session_limit_override"],
        "runtimeClassLimitsOverride": row["runtime_class_limits_override"] or {},
        "maxSessionSecondsOverride": row["max_session_seconds_override"],
        "contractRef": row["contract_ref"],
        "trialEndsAt": row["trial_ends_at"].isoformat() if row["trial_ends_at"] else None,
        "effectiveUntil": row["effective_until"].isoformat() if row["effective_until"] else None,
    }


async def _revoke_tenant_runtime(tenant_id: str) -> dict[str, Any]:
    pool = get_pool()
    session_ids = await active_runtime_session_ids(pool, tenant_id)
    failures: list[dict[str, str]] = []
    for session_id in session_ids:
        try:
            await mark_runtime_placement_reclaiming(pool, session_id=session_id)
            await stop_download_watcher(session_id)
            await remove_container(session_id)
            await mark_runtime_placement_ended(pool, session_id=session_id)
        except Exception as exc:
            error = str(exc)
            await mark_runtime_placement_revoke_failed(pool, session_id=session_id, error=error)
            failures.append({"sessionId": session_id, "error": error})
    return {
        "affectedSessionCount": len(session_ids),
        "failedResources": failures,
    }


async def _active_runtime_placement_count(conn, tenant_id: str) -> int:
    try:
        value = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM session_runtime_placements
            WHERE tenant_id = $1
              AND ended_at IS NULL
            """,
            tenant_id,
        )
    except asyncpg.UndefinedTableError:
        return 0
    return int(value or 0)


async def _tenant_file_rows(conn, tenant_id: str):
    return await conn.fetch(
        """
        SELECT id, object_key
        FROM session_files
        WHERE tenant_id = $1
           OR session_id IN (SELECT id FROM sessions WHERE tenant_id = $1)
           OR archived_session_id IN (SELECT id FROM sessions WHERE tenant_id = $1)
        ORDER BY created_at
        """,
        tenant_id,
    )


async def _delete_tenant_file_objects(conn, tenant_id: str) -> dict[str, Any]:
    rows = await _tenant_file_rows(conn, tenant_id)
    store = await get_store()
    failed: list[dict[str, str]] = []
    for row in rows:
        try:
            await store.delete_by_key(row["object_key"])
        except Exception as exc:
            failed.append({"fileId": row["id"], "error": str(exc)})
    return {
        "fileObjectCount": len(rows),
        "failedFileObjects": failed,
    }


async def _delete_tenant_db_rows(conn, tenant_id: str) -> dict[str, int]:
    delete_statements = [
        (
            "sessionFiles",
            """
            DELETE FROM session_files
            WHERE tenant_id = $1
               OR session_id IN (SELECT id FROM sessions WHERE tenant_id = $1)
               OR archived_session_id IN (SELECT id FROM sessions WHERE tenant_id = $1)
            """,
        ),
        (
            "sessionViewerTickets",
            """
            DELETE FROM session_viewer_tickets
            WHERE tenant_id = $1
               OR session_id IN (SELECT id FROM sessions WHERE tenant_id = $1)
            """,
        ),
        (
            "agentDeviceAuditEvents",
            """
            DELETE FROM agent_device_audit_events
            WHERE tenant_id = $1
               OR session_id IN (SELECT id FROM sessions WHERE tenant_id = $1)
            """,
        ),
        (
            "agentDeviceLeases",
            """
            DELETE FROM agent_device_leases
            WHERE tenant_id = $1
               OR session_id IN (SELECT id FROM sessions WHERE tenant_id = $1)
               OR device_instance_id IN (SELECT id FROM sessions WHERE tenant_id = $1)
            """,
        ),
        (
            "runtimeCapacityReservations",
            """
            DELETE FROM runtime_capacity_reservations
            WHERE tenant_id = $1
               OR session_id IN (SELECT id FROM sessions WHERE tenant_id = $1)
            """,
        ),
        (
            "sessionRuntimePlacements",
            """
            DELETE FROM session_runtime_placements
            WHERE tenant_id = $1
               OR session_id IN (SELECT id FROM sessions WHERE tenant_id = $1)
            """,
        ),
        (
            "sessionRuntimeTokens",
            """
            DELETE FROM session_runtime_tokens
            WHERE tenant_id = $1
               OR session_id IN (SELECT id FROM sessions WHERE tenant_id = $1)
            """,
        ),
        (
            "sessionRuntimeStatus",
            """
            DELETE FROM session_runtime_status
            WHERE session_id IN (SELECT id FROM sessions WHERE tenant_id = $1)
            """,
        ),
        ("sessions", "DELETE FROM sessions WHERE tenant_id = $1"),
        ("browserImages", "DELETE FROM browser_images WHERE tenant_id = $1"),
        ("networkEgressProfiles", "DELETE FROM network_egress_profiles WHERE tenant_id = $1"),
        ("fingerprintPool", "DELETE FROM fingerprint_pool WHERE tenant_id = $1"),
        ("rememberTokens", "DELETE FROM remember_tokens WHERE tenant_id = $1"),
        ("apiTokens", "DELETE FROM api_tokens WHERE tenant_id = $1"),
        ("users", "DELETE FROM users WHERE tenant_id = $1"),
        ("tenantRuntimeQuotas", "DELETE FROM tenant_runtime_quotas WHERE tenant_id = $1"),
        ("tenantEntitlements", "DELETE FROM tenant_entitlements WHERE tenant_id = $1"),
        ("tenantPlatformSettings", "DELETE FROM tenant_platform_settings WHERE tenant_id = $1"),
        ("tenants", "DELETE FROM tenants WHERE id = $1"),
    ]
    counts: dict[str, int] = {}
    for key, statement in delete_statements:
        counts[key] = _affected_count(await conn.execute(statement, tenant_id))
    return counts


async def _entitlement_row(pool, tenant_id: str):
    return await pool.fetchrow(
        """
        SELECT plan_id, active_session_limit_override, runtime_class_limits_override,
               max_session_seconds_override, contract_ref, trial_ends_at, effective_until
        FROM tenant_entitlements
        WHERE tenant_id = $1
        """,
        tenant_id,
    )


@router.post("/setup")
async def setup_platform_admin(body: PlatformSetupBody):
    pool = get_pool()
    count = await pool.fetchval("SELECT COUNT(*) FROM platform_users")
    if count > 0:
        raise HTTPException(status_code=403, detail="Platform setup already completed")

    user_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO platform_users (id, email, password_hash, name, role)
        VALUES ($1, $2, $3, $4, 'platform_admin')
        """,
        user_id,
        body.email.strip(),
        hash_password(body.password),
        body.name.strip(),
    )
    await record_platform_audit(
        pool,
        action="platform_user.create",
        target_type="platform_user",
        target_id=user_id,
        actor_platform_user_id=user_id,
        actor_role="platform_admin",
        reason="platform_setup",
        after={"email": body.email.strip(), "role": "platform_admin"},
    )
    token = create_platform_access_token(user_id, "platform_admin")
    return {
        "access_token": token,
        "user": {
            "id": user_id,
            "email": body.email.strip(),
            "name": body.name.strip(),
            "role": "platform_admin",
        },
    }


@router.post("/login")
async def login_platform(body: PlatformLoginBody):
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT id, email, password_hash, name, role, is_active
        FROM platform_users
        WHERE email = $1
        """,
        body.email.strip(),
    )
    if not row or not row["password_hash"]:
        raise HTTPException(status_code=401, detail="Invalid platform credentials")
    if not row["is_active"]:
        raise HTTPException(status_code=401, detail="Platform account disabled")
    if not verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid platform credentials")

    await pool.execute("UPDATE platform_users SET last_login_at = NOW() WHERE id = $1", row["id"])
    token = create_platform_access_token(row["id"], row["role"])
    return {"access_token": token, "user": _platform_user_payload(row)}


@router.get("/me")
async def platform_me(user: CurrentPlatformUser = Depends(get_current_platform_user)):
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
    }


@router.get("/plans")
async def list_plans(
    _user: CurrentPlatformUser = Depends(require_platform_role(["platform_admin", "platform_operator"])),
):
    pool = get_pool()
    await ensure_default_plan(pool)
    rows = await pool.fetch("SELECT * FROM plans ORDER BY created_at DESC")
    return {"plans": [_plan_payload(row) for row in rows]}


@router.post("/plans")
async def create_plan(
    body: PlanBody,
    user: CurrentPlatformUser = Depends(require_platform_role(["platform_admin"])),
):
    pool = get_pool()
    plan_id = str(uuid.uuid4())
    try:
        await pool.execute(
            """
            INSERT INTO plans (
                id, code, name, default_active_session_limit,
                default_runtime_class_limits, default_max_session_seconds, is_active
            )
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
            """,
            plan_id,
            body.code.strip(),
            body.name.strip(),
            body.defaultActiveSessionLimit,
            body.defaultRuntimeClassLimits or {},
            body.defaultMaxSessionSeconds,
            body.isActive,
        )
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(status_code=409, detail="Plan code already exists") from exc
    await record_platform_audit(
        pool,
        action="plan.create",
        target_type="plan",
        target_id=plan_id,
        actor_platform_user_id=user.id,
        actor_role=user.role,
        reason=body.reason,
        after=body.model_dump(),
    )
    row = await pool.fetchrow("SELECT * FROM plans WHERE id = $1", plan_id)
    return {"plan": _plan_payload(row)}


@router.patch("/plans/{plan_id}")
async def update_plan(
    plan_id: str,
    body: PlanUpdateBody,
    user: CurrentPlatformUser = Depends(require_platform_role(["platform_admin"])),
):
    pool = get_pool()
    before = await pool.fetchrow("SELECT * FROM plans WHERE id = $1", plan_id)
    if not before:
        raise HTTPException(status_code=404, detail="Plan not found")
    await pool.execute(
        """
        UPDATE plans
        SET name = COALESCE($2, name),
            default_active_session_limit = COALESCE($3, default_active_session_limit),
            default_runtime_class_limits = COALESCE($4::jsonb, default_runtime_class_limits),
            default_max_session_seconds = COALESCE($5, default_max_session_seconds),
            is_active = COALESCE($6, is_active),
            updated_at = NOW()
        WHERE id = $1
        """,
        plan_id,
        body.name.strip() if body.name is not None else None,
        body.defaultActiveSessionLimit,
        body.defaultRuntimeClassLimits,
        body.defaultMaxSessionSeconds,
        body.isActive,
    )
    after = await pool.fetchrow("SELECT * FROM plans WHERE id = $1", plan_id)
    await record_platform_audit(
        pool,
        action="plan.update",
        target_type="plan",
        target_id=plan_id,
        actor_platform_user_id=user.id,
        actor_role=user.role,
        reason=body.reason,
        before=_plan_payload(before),
        after=_plan_payload(after),
    )
    return {"plan": _plan_payload(after)}


@router.get("/tenants")
async def list_tenants(
    _user: CurrentPlatformUser = Depends(require_platform_role(["platform_admin", "platform_operator"])),
):
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT t.id, t.name, t.slug, t.created_at,
               COALESCE(tps.status, 'active') AS status,
               tps.runtime_namespace, tps.runtime_image_policy,
               tps.suspended_at, tps.suspend_reason,
               tps.deleted_at, tps.delete_reason,
               tps.retention_until, tps.purge_requested_at, tps.purge_request_reason,
               trq.active_session_limit, trq.runtime_class_limits, trq.max_session_seconds,
               te.plan_id, te.contract_ref, te.trial_ends_at,
               p.code AS plan_code,
               (SELECT COUNT(*) FROM sessions s WHERE s.tenant_id = t.id) AS session_count
        FROM tenants t
        LEFT JOIN tenant_platform_settings tps ON tps.tenant_id = t.id
        LEFT JOIN tenant_runtime_quotas trq ON trq.tenant_id = t.id
        LEFT JOIN tenant_entitlements te ON te.tenant_id = t.id
        LEFT JOIN plans p ON p.id = te.plan_id
        ORDER BY t.created_at DESC
        """
    )
    return {"tenants": [_tenant_payload(row) for row in rows]}


@router.post("/tenants")
async def create_tenant(
    body: CreateTenantBody,
    user: CurrentPlatformUser = Depends(require_platform_role(["platform_admin"])),
):
    pool = get_pool()
    tenant_id = str(uuid.uuid4())
    slug = _slug_for(body.name, body.slug)
    admin_user_id: str | None = None

    async with pool.acquire() as conn:
        async with conn.transaction():
            await ensure_default_plan(conn)
            try:
                await conn.execute(
                    "INSERT INTO tenants (id, name, slug) VALUES ($1, $2, $3)",
                    tenant_id,
                    body.name.strip(),
                    slug,
                )
            except asyncpg.UniqueViolationError as exc:
                raise HTTPException(status_code=409, detail="Tenant slug already exists") from exc

            if body.initialAdmin:
                admin_user_id = str(uuid.uuid4())
                await conn.execute(
                    """
                    INSERT INTO users (id, tenant_id, email, password_hash, name, role)
                    VALUES ($1, $2, $3, $4, $5, 'superadmin')
                    """,
                    admin_user_id,
                    tenant_id,
                    body.initialAdmin.email.strip(),
                    hash_password(body.initialAdmin.password),
                    body.initialAdmin.name.strip(),
                )

            await ensure_tenant_platform_defaults(
                conn,
                tenant_id,
                actor_platform_user_id=user.id,
                active_session_limit=body.activeSessionLimit,
                max_session_seconds=body.maxSessionSeconds,
                plan_id=body.planId,
                reason=body.reason,
            )
            await record_platform_audit(
                conn,
                action="tenant.create",
                target_type="tenant",
                target_id=tenant_id,
                tenant_id=tenant_id,
                actor_platform_user_id=user.id,
                actor_role=user.role,
                reason=body.reason,
                after={
                    "name": body.name.strip(),
                    "slug": slug,
                    "runtimeNamespace": runtime_namespace_for_tenant(tenant_id),
                    "activeSessionLimit": body.activeSessionLimit,
                    "maxSessionSeconds": body.maxSessionSeconds,
                    "initialAdminUserId": admin_user_id,
                },
            )

    row = await _tenant_row(pool, tenant_id)
    return {"tenant": _tenant_payload(row)}


@router.get("/tenants/{tenant_id}")
async def get_tenant(
    tenant_id: str,
    _user: CurrentPlatformUser = Depends(require_platform_role(["platform_admin", "platform_operator"])),
):
    pool = get_pool()
    row = await _tenant_row(pool, tenant_id)
    if not row:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return {"tenant": _tenant_payload(row)}


@router.put("/tenants/{tenant_id}/quota")
async def update_tenant_quota(
    tenant_id: str,
    body: TenantQuotaBody,
    user: CurrentPlatformUser = Depends(require_platform_role(["platform_admin"])),
):
    pool = get_pool()
    row = await _tenant_row(pool, tenant_id)
    if not row:
        raise HTTPException(status_code=404, detail="Tenant not found")
    await pool.execute(
        """
        UPDATE tenant_entitlements
        SET active_session_limit_override = $2,
            runtime_class_limits_override = $3::jsonb,
            max_session_seconds_override = $4,
            updated_by = $5,
            update_reason = $6,
            updated_at = NOW()
        WHERE tenant_id = $1
        """,
        tenant_id,
        body.activeSessionLimit,
        body.runtimeClassLimits or {},
        body.maxSessionSeconds,
        user.id,
        body.reason,
    )
    quota = await update_tenant_runtime_quota(
        pool,
        tenant_id=tenant_id,
        active_session_limit=body.activeSessionLimit,
        max_session_seconds=body.maxSessionSeconds,
        runtime_class_limits=body.runtimeClassLimits,
        actor_platform_user_id=user.id,
        actor_role=user.role,
        reason=body.reason,
    )
    return {
        "quota": {
            "activeSessionLimit": quota.active_session_limit,
            "runtimeClassLimits": quota.runtime_class_limits,
            "maxSessionSeconds": quota.max_session_seconds,
        }
    }


@router.put("/tenants/{tenant_id}/entitlement")
async def update_tenant_entitlement(
    tenant_id: str,
    body: TenantEntitlementBody,
    user: CurrentPlatformUser = Depends(require_platform_role(["platform_admin"])),
):
    pool = get_pool()
    row = await _tenant_row(pool, tenant_id)
    if not row:
        raise HTTPException(status_code=404, detail="Tenant not found")
    before = await _entitlement_row(pool, tenant_id)

    plan = None
    if body.planId:
        plan = await pool.fetchrow("SELECT * FROM plans WHERE id = $1 AND is_active = true", body.planId)
        if not plan:
            raise HTTPException(status_code=404, detail="Active plan not found")

    active_limit = (
        body.activeSessionLimitOverride
        if body.activeSessionLimitOverride is not None
        else int(plan["default_active_session_limit"] if plan else SAAS_DEFAULT_ACTIVE_SESSION_LIMIT)
    )
    max_seconds = (
        body.maxSessionSecondsOverride
        if body.maxSessionSecondsOverride is not None
        else int(plan["default_max_session_seconds"] if plan else SAAS_DEFAULT_MAX_SESSION_SECONDS)
    )
    runtime_class_limits = (
        body.runtimeClassLimitsOverride
        if body.runtimeClassLimitsOverride is not None
        else dict((plan["default_runtime_class_limits"] if plan else {}) or {})
    )

    await pool.execute(
        """
        INSERT INTO tenant_entitlements (
            tenant_id, plan_id, active_session_limit_override,
            runtime_class_limits_override, max_session_seconds_override,
            contract_ref, trial_ends_at, effective_until, updated_by, update_reason
        )
        VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8, $9, $10)
        ON CONFLICT (tenant_id) DO UPDATE SET
            plan_id = EXCLUDED.plan_id,
            active_session_limit_override = EXCLUDED.active_session_limit_override,
            runtime_class_limits_override = EXCLUDED.runtime_class_limits_override,
            max_session_seconds_override = EXCLUDED.max_session_seconds_override,
            contract_ref = EXCLUDED.contract_ref,
            trial_ends_at = EXCLUDED.trial_ends_at,
            effective_until = EXCLUDED.effective_until,
            updated_by = EXCLUDED.updated_by,
            update_reason = EXCLUDED.update_reason,
            updated_at = NOW()
        """,
        tenant_id,
        body.planId,
        body.activeSessionLimitOverride,
        body.runtimeClassLimitsOverride or {},
        body.maxSessionSecondsOverride,
        body.contractRef,
        body.trialEndsAt,
        body.effectiveUntil,
        user.id,
        body.reason,
    )
    quota = await update_tenant_runtime_quota(
        pool,
        tenant_id=tenant_id,
        active_session_limit=active_limit,
        max_session_seconds=max_seconds,
        runtime_class_limits=runtime_class_limits,
        actor_platform_user_id=user.id,
        actor_role=user.role,
        reason=body.reason,
    )
    after = await _entitlement_row(pool, tenant_id)
    await record_platform_audit(
        pool,
        action="tenant.entitlement.update",
        target_type="tenant_entitlement",
        target_id=tenant_id,
        tenant_id=tenant_id,
        actor_platform_user_id=user.id,
        actor_role=user.role,
        reason=body.reason,
        before=_entitlement_payload(before),
        after={
            **(_entitlement_payload(after) or {}),
            "effectiveRuntimeQuota": {
                "activeSessionLimit": quota.active_session_limit,
                "runtimeClassLimits": quota.runtime_class_limits,
                "maxSessionSeconds": quota.max_session_seconds,
            },
        },
    )
    return {
        "entitlement": _entitlement_payload(after),
        "quota": {
            "activeSessionLimit": quota.active_session_limit,
            "runtimeClassLimits": quota.runtime_class_limits,
            "maxSessionSeconds": quota.max_session_seconds,
        },
    }


@router.post("/tenants/{tenant_id}/suspend")
async def suspend_tenant(
    tenant_id: str,
    body: TenantLifecycleBody,
    user: CurrentPlatformUser = Depends(require_platform_role(["platform_admin", "platform_operator"])),
):
    pool = get_pool()
    row = await _tenant_row(pool, tenant_id)
    if not row:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if row["status"] == "deleted":
        raise HTTPException(status_code=409, detail="Tenant already deleted")

    await pool.execute(
        """
        UPDATE tenant_platform_settings
        SET status = 'suspended',
            suspended_at = NOW(),
            suspended_by = $2,
            suspend_reason = $3,
            updated_at = NOW()
        WHERE tenant_id = $1
        """,
        tenant_id,
        user.id,
        body.reason,
    )
    revoke_result = await _revoke_tenant_runtime(tenant_id)
    outcome = "failure" if revoke_result["failedResources"] else "success"
    await record_platform_audit(
        pool,
        action="tenant.suspend",
        target_type="tenant",
        target_id=tenant_id,
        tenant_id=tenant_id,
        actor_platform_user_id=user.id,
        actor_role=user.role,
        outcome=outcome,
        reason=body.reason,
        before={"status": row["status"]},
        after={"status": "suspended", **revoke_result},
        error="runtime_revoke_failed" if revoke_result["failedResources"] else None,
    )
    return {"ok": True, "revoke": revoke_result}


@router.post("/tenants/{tenant_id}/resume")
async def resume_tenant(
    tenant_id: str,
    body: TenantLifecycleBody,
    user: CurrentPlatformUser = Depends(require_platform_role(["platform_admin"])),
):
    pool = get_pool()
    row = await _tenant_row(pool, tenant_id)
    if not row:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if row["status"] == "deleted":
        raise HTTPException(status_code=409, detail="Deleted tenant cannot be resumed")

    await pool.execute(
        """
        UPDATE tenant_platform_settings
        SET status = 'active', updated_at = NOW()
        WHERE tenant_id = $1
        """,
        tenant_id,
    )
    await record_platform_audit(
        pool,
        action="tenant.resume",
        target_type="tenant",
        target_id=tenant_id,
        tenant_id=tenant_id,
        actor_platform_user_id=user.id,
        actor_role=user.role,
        reason=body.reason,
        before={"status": row["status"]},
        after={"status": "active"},
    )
    return {"ok": True}


@router.post("/tenants/{tenant_id}/runtime-revoke")
async def revoke_tenant_runtime(
    tenant_id: str,
    body: TenantRuntimeRevokeBody,
    user: CurrentPlatformUser = Depends(require_platform_role(["platform_admin", "platform_operator"])),
):
    pool = get_pool()
    row = await _tenant_row(pool, tenant_id)
    if not row:
        raise HTTPException(status_code=404, detail="Tenant not found")

    revoke_result = await _revoke_tenant_runtime(tenant_id)
    outcome = "failure" if revoke_result["failedResources"] else "success"
    await record_platform_audit(
        pool,
        action="tenant.runtime_revoke",
        target_type="tenant",
        target_id=tenant_id,
        tenant_id=tenant_id,
        actor_platform_user_id=user.id,
        actor_role=user.role,
        outcome=outcome,
        reason=body.reason,
        before={"status": row["status"]},
        after=revoke_result,
        error="runtime_revoke_failed" if revoke_result["failedResources"] else None,
    )
    if revoke_result["failedResources"]:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "runtime_revoke_failed",
                "revoke": revoke_result,
            },
        )
    return {"ok": True, "revoke": revoke_result}


@router.delete("/tenants/{tenant_id}")
async def delete_tenant(
    tenant_id: str,
    body: TenantDeleteBody | None = None,
    user: CurrentPlatformUser = Depends(require_platform_role(["platform_admin"])),
):
    delete_body = body or TenantDeleteBody()
    retention_days = getattr(delete_body, "retentionDays", DEFAULT_TENANT_DELETE_RETENTION_DAYS)
    retention_until = datetime.now(timezone.utc) + timedelta(days=retention_days)
    pool = get_pool()
    row = await _tenant_row(pool, tenant_id)
    if not row:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if row["status"] == "deleted":
        return {"ok": True, "revoke": {"affectedSessionCount": 0, "failedResources": []}}

    if row["status"] != "suspended":
        await pool.execute(
            """
            UPDATE tenant_platform_settings
            SET status = 'suspended',
                suspended_at = COALESCE(suspended_at, NOW()),
                suspended_by = COALESCE(suspended_by, $2),
                suspend_reason = COALESCE(suspend_reason, $3),
                updated_at = NOW()
            WHERE tenant_id = $1
            """,
            tenant_id,
            user.id,
            delete_body.reason,
        )
    revoke_result = await _revoke_tenant_runtime(tenant_id)
    if revoke_result["failedResources"]:
        await record_platform_audit(
            pool,
            action="tenant.delete",
            target_type="tenant",
            target_id=tenant_id,
            tenant_id=tenant_id,
            actor_platform_user_id=user.id,
            actor_role=user.role,
            outcome="failure",
            reason=delete_body.reason,
            before={"status": row["status"]},
            after={"status": "suspended", **revoke_result},
            error="runtime_revoke_failed",
        )
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "runtime_revoke_failed",
                "revoke": revoke_result,
            },
        )
    await pool.execute(
        """
        UPDATE tenant_platform_settings
        SET status = 'deleted',
            deleted_at = NOW(),
            deleted_by = $2,
            delete_reason = $3,
            retention_until = $4,
            purge_requested_at = NULL,
            purge_requested_by = NULL,
            purge_request_reason = NULL,
            updated_at = NOW()
        WHERE tenant_id = $1
        """,
        tenant_id,
        user.id,
        delete_body.reason,
        retention_until,
    )
    outcome = "failure" if revoke_result["failedResources"] else "success"
    await record_platform_audit(
        pool,
        action="tenant.delete",
        target_type="tenant",
        target_id=tenant_id,
        tenant_id=tenant_id,
        actor_platform_user_id=user.id,
        actor_role=user.role,
        outcome=outcome,
        reason=delete_body.reason,
        before={"status": row["status"]},
        after={
            "status": "deleted",
            "retentionDays": retention_days,
            "retentionUntil": retention_until.isoformat(),
            **revoke_result,
        },
        error="runtime_revoke_failed" if revoke_result["failedResources"] else None,
    )
    return {"ok": True, "revoke": revoke_result}


@router.post("/tenants/{tenant_id}/purge-request")
async def request_tenant_purge(
    tenant_id: str,
    body: TenantPurgeRequestBody,
    user: CurrentPlatformUser = Depends(require_platform_role(["platform_admin"])),
):
    pool = get_pool()
    row = await _tenant_row(pool, tenant_id)
    if not row:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if row["status"] != "deleted":
        await record_platform_audit(
            pool,
            action="tenant.purge_request",
            target_type="tenant",
            target_id=tenant_id,
            tenant_id=tenant_id,
            actor_platform_user_id=user.id,
            actor_role=user.role,
            outcome="failure",
            reason=body.reason,
            before={"status": row["status"]},
            error="tenant_not_deleted",
        )
        raise HTTPException(status_code=409, detail="tenant_not_deleted")

    retention_until = _as_utc_datetime(row["retention_until"])
    if retention_until is None or retention_until > datetime.now(timezone.utc):
        error = "tenant_retention_missing" if retention_until is None else "tenant_retention_not_elapsed"
        await record_platform_audit(
            pool,
            action="tenant.purge_request",
            target_type="tenant",
            target_id=tenant_id,
            tenant_id=tenant_id,
            actor_platform_user_id=user.id,
            actor_role=user.role,
            outcome="failure",
            reason=body.reason,
            before={
                "status": row["status"],
                "retentionUntil": retention_until.isoformat() if retention_until else None,
            },
            error=error,
        )
        raise HTTPException(
            status_code=409,
            detail={
                "reason": error,
                "retentionUntil": retention_until.isoformat() if retention_until else None,
            },
        )

    await pool.execute(
        """
        UPDATE tenant_platform_settings
        SET purge_requested_at = NOW(),
            purge_requested_by = $2,
            purge_request_reason = $3,
            updated_at = NOW()
        WHERE tenant_id = $1
        """,
        tenant_id,
        user.id,
        body.reason,
    )
    after = await _tenant_row(pool, tenant_id)
    if not after:
        raise HTTPException(status_code=404, detail="Tenant not found")
    await record_platform_audit(
        pool,
        action="tenant.purge_request",
        target_type="tenant",
        target_id=tenant_id,
        tenant_id=tenant_id,
        actor_platform_user_id=user.id,
        actor_role=user.role,
        reason=body.reason,
        before={
            "status": row["status"],
            "retentionUntil": retention_until.isoformat(),
        },
        after={
            "status": after["status"],
            "purgeRequestedAt": after["purge_requested_at"].isoformat() if after["purge_requested_at"] else None,
            "purgeRequestReason": after["purge_request_reason"],
        },
    )
    return {"ok": True, "tenant": _tenant_payload(after)}


@router.post("/tenants/{tenant_id}/purge")
async def purge_tenant(
    tenant_id: str,
    body: TenantPurgeBody,
    user: CurrentPlatformUser = Depends(require_platform_role(["platform_admin"])),
):
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await _tenant_row(conn, tenant_id)
        if not row:
            raise HTTPException(status_code=404, detail="Tenant not found")
        before = {
            "status": row["status"],
            "retentionUntil": row["retention_until"].isoformat() if row["retention_until"] else None,
            "purgeRequestedAt": row["purge_requested_at"].isoformat() if row["purge_requested_at"] else None,
        }
        if row["status"] != "deleted":
            await record_platform_audit(
                conn,
                action="tenant.purge",
                target_type="tenant",
                target_id=tenant_id,
                tenant_id=tenant_id,
                actor_platform_user_id=user.id,
                actor_role=user.role,
                outcome="failure",
                reason=body.reason,
                before=before,
                error="tenant_not_deleted",
            )
            raise HTTPException(status_code=409, detail="tenant_not_deleted")
        if row["purge_requested_at"] is None:
            await record_platform_audit(
                conn,
                action="tenant.purge",
                target_type="tenant",
                target_id=tenant_id,
                tenant_id=tenant_id,
                actor_platform_user_id=user.id,
                actor_role=user.role,
                outcome="failure",
                reason=body.reason,
                before=before,
                error="tenant_purge_request_missing",
            )
            raise HTTPException(status_code=409, detail="tenant_purge_request_missing")

        retention_until = _as_utc_datetime(row["retention_until"])
        if retention_until is None or retention_until > datetime.now(timezone.utc):
            error = "tenant_retention_missing" if retention_until is None else "tenant_retention_not_elapsed"
            await record_platform_audit(
                conn,
                action="tenant.purge",
                target_type="tenant",
                target_id=tenant_id,
                tenant_id=tenant_id,
                actor_platform_user_id=user.id,
                actor_role=user.role,
                outcome="failure",
                reason=body.reason,
                before=before,
                error=error,
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "reason": error,
                    "retentionUntil": retention_until.isoformat() if retention_until else None,
                },
            )

        active_placements = await _active_runtime_placement_count(conn, tenant_id)
        if active_placements > 0:
            await record_platform_audit(
                conn,
                action="tenant.purge",
                target_type="tenant",
                target_id=tenant_id,
                tenant_id=tenant_id,
                actor_platform_user_id=user.id,
                actor_role=user.role,
                outcome="failure",
                reason=body.reason,
                before=before,
                after={"activeRuntimePlacementCount": active_placements},
                error="tenant_runtime_placements_not_reclaimed",
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "reason": "tenant_runtime_placements_not_reclaimed",
                    "activeRuntimePlacementCount": active_placements,
                },
            )

        file_purge = await _delete_tenant_file_objects(conn, tenant_id)
        if file_purge["failedFileObjects"]:
            await record_platform_audit(
                conn,
                action="tenant.purge",
                target_type="tenant",
                target_id=tenant_id,
                tenant_id=tenant_id,
                actor_platform_user_id=user.id,
                actor_role=user.role,
                outcome="failure",
                reason=body.reason,
                before=before,
                after=file_purge,
                error="tenant_file_object_delete_failed",
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "reason": "tenant_file_object_delete_failed",
                    **file_purge,
                },
            )

        async with conn.transaction():
            deleted_rows = await _delete_tenant_db_rows(conn, tenant_id)
            await record_platform_audit(
                conn,
                action="tenant.purge",
                target_type="tenant",
                target_id=tenant_id,
                tenant_id=tenant_id,
                actor_platform_user_id=user.id,
                actor_role=user.role,
                reason=body.reason,
                before=before,
                after={
                    "fileObjectCount": file_purge["fileObjectCount"],
                    "deletedRows": deleted_rows,
                },
            )
    return {
        "ok": True,
        "fileObjectCount": file_purge["fileObjectCount"],
        "deletedRows": deleted_rows,
    }


@router.get("/runtime-images")
async def list_approved_runtime_images(
    runtimeClass: str | None = None,
    _user: CurrentPlatformUser = Depends(require_platform_role(["platform_admin", "platform_operator"])),
):
    pool = get_pool()
    if runtimeClass:
        rows = await pool.fetch(
            """
            SELECT *
            FROM approved_runtime_images
            WHERE runtime_class = $1
            ORDER BY created_at DESC
            """,
            runtimeClass,
        )
    else:
        rows = await pool.fetch("SELECT * FROM approved_runtime_images ORDER BY created_at DESC")
    return {
        "images": [
            {
                "id": row["id"],
                "runtimeClass": row["runtime_class"],
                "imageRef": row["image_ref"],
                "imageDigest": row["image_digest"],
                "chromeVersion": row["chrome_version"],
                "buildId": row["build_id"],
                "scanStatus": row["scan_status"],
                "approvalStatus": row["approval_status"],
                "approvedBy": row["approved_by"],
                "approvedAt": row["approved_at"].isoformat() if row["approved_at"] else None,
                "createdAt": row["created_at"].isoformat() if row["created_at"] else None,
            }
            for row in rows
        ]
    }


@router.post("/runtime-images")
async def create_approved_runtime_image(
    body: RuntimeImageBody,
    user: CurrentPlatformUser = Depends(require_platform_role(["platform_admin"])),
):
    _validate_sha256_digest(body.imageDigest)
    _validate_runtime_image_approval(body.scanStatus, body.approvalStatus)
    pool = get_pool()
    image_id = str(uuid.uuid4())
    approved_by = user.id if body.approvalStatus == "approved" else None
    await pool.execute(
        """
        INSERT INTO approved_runtime_images (
            id, runtime_class, image_ref, image_digest, chrome_version,
            build_id, scan_status, approval_status, approved_by, approved_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, CASE WHEN $8 = 'approved' THEN NOW() ELSE NULL END)
        """,
        image_id,
        body.runtimeClass,
        body.imageRef,
        body.imageDigest,
        body.chromeVersion,
        body.buildId,
        body.scanStatus,
        body.approvalStatus,
        approved_by,
    )
    await record_platform_audit(
        pool,
        action="runtime_image.create",
        target_type="approved_runtime_image",
        target_id=image_id,
        actor_platform_user_id=user.id,
        actor_role=user.role,
        reason=body.reason,
        after=body.model_dump(),
    )
    return {"id": image_id}


@router.patch("/runtime-images/{image_id}")
async def update_approved_runtime_image(
    image_id: str,
    body: RuntimeImageUpdateBody,
    user: CurrentPlatformUser = Depends(require_platform_role(["platform_admin"])),
):
    pool = get_pool()
    before = await pool.fetchrow("SELECT * FROM approved_runtime_images WHERE id = $1", image_id)
    if not before:
        raise HTTPException(status_code=404, detail="Runtime image not found")
    next_scan_status = body.scanStatus or before["scan_status"]
    next_approval_status = body.approvalStatus or before["approval_status"]
    _validate_runtime_image_approval(next_scan_status, next_approval_status)
    await pool.execute(
        """
        UPDATE approved_runtime_images
        SET scan_status = COALESCE($2, scan_status),
            approval_status = COALESCE($3, approval_status),
            approved_by = CASE WHEN $3 = 'approved' THEN $4 ELSE approved_by END,
            approved_at = CASE WHEN $3 = 'approved' THEN NOW() ELSE approved_at END,
            updated_at = NOW()
        WHERE id = $1
        """,
        image_id,
        body.scanStatus,
        body.approvalStatus,
        user.id,
    )
    after = await pool.fetchrow("SELECT * FROM approved_runtime_images WHERE id = $1", image_id)
    await record_platform_audit(
        pool,
        action="runtime_image.update",
        target_type="approved_runtime_image",
        target_id=image_id,
        actor_platform_user_id=user.id,
        actor_role=user.role,
        reason=body.reason,
        before={
            "scanStatus": before["scan_status"],
            "approvalStatus": before["approval_status"],
        },
        after={
            "scanStatus": after["scan_status"],
            "approvalStatus": after["approval_status"],
        },
    )
    return {"ok": True}


@router.get("/deploy/runtime-values")
async def get_runtime_deploy_values(
    _user: CurrentPlatformUser = Depends(require_platform_role(["platform_admin", "platform_operator"])),
):
    pool = get_pool()
    return await build_runtime_deploy_values(pool)


@router.get("/runtime-pools")
async def list_runtime_pools(
    _user: CurrentPlatformUser = Depends(require_platform_role(["platform_admin", "platform_operator"])),
):
    pool = get_pool()
    await ensure_default_runtime_pool(pool)
    rows = await pool.fetch(
        """
        SELECT rp.*,
               (
                   SELECT COUNT(*)
                   FROM runtime_capacity_reservations rcr
                   WHERE rcr.runtime_pool_id = rp.id
                     AND rcr.released_at IS NULL
                     AND rcr.reserved_phase = 'reserved'
               ) AS active_reservation_count
        FROM runtime_pools rp
        ORDER BY rp.id
        """
    )
    return {"runtimePools": [_runtime_pool_payload(row) for row in rows]}


@router.post("/runtime-pools")
async def create_runtime_pool(
    body: RuntimePoolBody,
    user: CurrentPlatformUser = Depends(require_platform_role(["platform_admin"])),
):
    pool = get_pool()
    pool_id = body.id.strip() if body.id else str(uuid.uuid4())
    try:
        await pool.execute(
            """
            INSERT INTO runtime_pools (
                id, name, runtime_classes, active_session_capacity,
                is_enabled, is_draining, drain_reason, drained_by, drained_at
            )
            VALUES (
                $1, $2, $3::jsonb, $4,
                $5, $6, CASE WHEN $6 THEN $7 ELSE NULL END,
                CASE WHEN $6 THEN $8 ELSE NULL END,
                CASE WHEN $6 THEN NOW() ELSE NULL END
            )
            """,
            pool_id,
            body.name.strip(),
            body.runtimeClasses,
            body.activeSessionCapacity,
            body.isEnabled,
            body.isDraining,
            body.reason,
            user.id,
        )
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(status_code=409, detail="Runtime pool already exists") from exc
    await record_platform_audit(
        pool,
        action="runtime_pool.create",
        target_type="runtime_pool",
        target_id=pool_id,
        actor_platform_user_id=user.id,
        actor_role=user.role,
        reason=body.reason,
        after=body.model_dump(),
    )
    row = await pool.fetchrow(
        """
        SELECT rp.*, 0 AS active_reservation_count
        FROM runtime_pools rp
        WHERE rp.id = $1
        """,
        pool_id,
    )
    return {"runtimePool": _runtime_pool_payload(row)}


@router.patch("/runtime-pools/{pool_id}")
async def update_runtime_pool(
    pool_id: str,
    body: RuntimePoolUpdateBody,
    user: CurrentPlatformUser = Depends(require_platform_role(["platform_admin"])),
):
    pool = get_pool()
    before = await pool.fetchrow("SELECT * FROM runtime_pools WHERE id = $1", pool_id)
    if not before:
        raise HTTPException(status_code=404, detail="Runtime pool not found")
    await pool.execute(
        """
        UPDATE runtime_pools
        SET name = COALESCE($2, name),
            runtime_classes = COALESCE($3::jsonb, runtime_classes),
            active_session_capacity = COALESCE($4, active_session_capacity),
            is_enabled = COALESCE($5, is_enabled),
            is_draining = COALESCE($6, is_draining),
            drain_reason = CASE
                WHEN $6 = true THEN $7
                WHEN $6 = false THEN NULL
                ELSE drain_reason
            END,
            drained_by = CASE
                WHEN $6 = true THEN $8
                WHEN $6 = false THEN NULL
                ELSE drained_by
            END,
            drained_at = CASE
                WHEN $6 = true THEN NOW()
                WHEN $6 = false THEN NULL
                ELSE drained_at
            END,
            updated_at = NOW()
        WHERE id = $1
        """,
        pool_id,
        body.name.strip() if body.name is not None else None,
        body.runtimeClasses,
        body.activeSessionCapacity,
        body.isEnabled,
        body.isDraining,
        body.reason,
        user.id,
    )
    after = await pool.fetchrow(
        """
        SELECT rp.*,
               (
                   SELECT COUNT(*)
                   FROM runtime_capacity_reservations rcr
                   WHERE rcr.runtime_pool_id = rp.id
                     AND rcr.released_at IS NULL
                     AND rcr.reserved_phase = 'reserved'
               ) AS active_reservation_count
        FROM runtime_pools rp
        WHERE rp.id = $1
        """,
        pool_id,
    )
    await record_platform_audit(
        pool,
        action="runtime_pool.update",
        target_type="runtime_pool",
        target_id=pool_id,
        actor_platform_user_id=user.id,
        actor_role=user.role,
        reason=body.reason,
        before={
            "name": before["name"],
            "runtimeClasses": before["runtime_classes"] or [],
            "activeSessionCapacity": before["active_session_capacity"],
            "isEnabled": before["is_enabled"],
            "isDraining": before["is_draining"],
        },
        after=_runtime_pool_payload(after),
    )
    return {"runtimePool": _runtime_pool_payload(after)}


@router.get("/runtime-nodes")
async def list_runtime_nodes(
    runtimePoolId: str | None = None,
    status: Literal["active", "draining", "disabled"] | None = None,
    _user: CurrentPlatformUser = Depends(require_platform_role(["platform_admin", "platform_operator"])),
):
    pool = get_pool()
    await ensure_default_runtime_pool(pool)
    rows = await pool.fetch(
        """
        SELECT *
        FROM runtime_nodes
        WHERE ($1::text IS NULL OR runtime_pool_id = $1)
          AND ($2::text IS NULL OR status = $2)
        ORDER BY runtime_pool_id, provider_node_name
        """,
        runtimePoolId,
        status,
    )
    return {"runtimeNodes": [_runtime_node_payload(row) for row in rows]}


@router.post("/runtime-nodes")
async def register_runtime_node(
    body: RuntimeNodeBody,
    user: CurrentPlatformUser = Depends(require_platform_role(["platform_admin", "platform_operator"])),
):
    pool = get_pool()
    await ensure_default_runtime_pool(pool)
    node_id = str(uuid.uuid4())
    try:
        await pool.execute(
            """
            INSERT INTO runtime_nodes (
                id, runtime_pool_id, provider_node_name, status,
                labels, capacity, allocatable,
                drain_reason, drained_by, drained_at,
                disabled_reason, disabled_by, disabled_at,
                last_seen_at
            )
            VALUES (
                $1, $2, $3, $4,
                $5::jsonb, $6::jsonb, $7::jsonb,
                CASE WHEN $4 = 'draining' THEN $8 ELSE NULL END,
                CASE WHEN $4 = 'draining' THEN $9 ELSE NULL END,
                CASE WHEN $4 = 'draining' THEN NOW() ELSE NULL END,
                CASE WHEN $4 = 'disabled' THEN $8 ELSE NULL END,
                CASE WHEN $4 = 'disabled' THEN $9 ELSE NULL END,
                CASE WHEN $4 = 'disabled' THEN NOW() ELSE NULL END,
                NOW()
            )
            """,
            node_id,
            body.runtimePoolId,
            body.providerNodeName.strip(),
            body.status,
            body.labels or {},
            body.capacity or {},
            body.allocatable or {},
            body.reason,
            user.id,
        )
    except asyncpg.ForeignKeyViolationError as exc:
        raise HTTPException(status_code=404, detail="Runtime pool not found") from exc
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(status_code=409, detail="Runtime node already exists") from exc
    await record_platform_audit(
        pool,
        action="runtime_node.register",
        target_type="runtime_node",
        target_id=node_id,
        actor_platform_user_id=user.id,
        actor_role=user.role,
        reason=body.reason,
        after=body.model_dump(),
    )
    row = await pool.fetchrow("SELECT * FROM runtime_nodes WHERE id = $1", node_id)
    return {"runtimeNode": _runtime_node_payload(row)}


@router.patch("/runtime-nodes/{node_id}")
async def update_runtime_node(
    node_id: str,
    body: RuntimeNodeUpdateBody,
    user: CurrentPlatformUser = Depends(require_platform_role(["platform_admin", "platform_operator"])),
):
    pool = get_pool()
    before = await pool.fetchrow("SELECT * FROM runtime_nodes WHERE id = $1", node_id)
    if not before:
        raise HTTPException(status_code=404, detail="Runtime node not found")
    await pool.execute(
        """
        UPDATE runtime_nodes
        SET status = COALESCE($2, status),
            labels = COALESCE($3::jsonb, labels),
            capacity = COALESCE($4::jsonb, capacity),
            allocatable = COALESCE($5::jsonb, allocatable),
            drain_reason = CASE
                WHEN $2 = 'draining' THEN $6
                WHEN $2 = 'active' THEN NULL
                ELSE drain_reason
            END,
            drained_by = CASE
                WHEN $2 = 'draining' THEN $7
                WHEN $2 = 'active' THEN NULL
                ELSE drained_by
            END,
            drained_at = CASE
                WHEN $2 = 'draining' THEN NOW()
                WHEN $2 = 'active' THEN NULL
                ELSE drained_at
            END,
            disabled_reason = CASE
                WHEN $2 = 'disabled' THEN $6
                WHEN $2 = 'active' THEN NULL
                ELSE disabled_reason
            END,
            disabled_by = CASE
                WHEN $2 = 'disabled' THEN $7
                WHEN $2 = 'active' THEN NULL
                ELSE disabled_by
            END,
            disabled_at = CASE
                WHEN $2 = 'disabled' THEN NOW()
                WHEN $2 = 'active' THEN NULL
                ELSE disabled_at
            END,
            last_seen_at = NOW(),
            updated_at = NOW()
        WHERE id = $1
        """,
        node_id,
        body.status,
        body.labels,
        body.capacity,
        body.allocatable,
        body.reason,
        user.id,
    )
    after = await pool.fetchrow("SELECT * FROM runtime_nodes WHERE id = $1", node_id)
    await record_platform_audit(
        pool,
        action="runtime_node.update",
        target_type="runtime_node",
        target_id=node_id,
        actor_platform_user_id=user.id,
        actor_role=user.role,
        reason=body.reason,
        before={
            "status": before["status"],
            "labels": before["labels"] or {},
            "capacity": before["capacity"] or {},
            "allocatable": before["allocatable"] or {},
        },
        after=_runtime_node_payload(after),
    )
    return {"runtimeNode": _runtime_node_payload(after)}


@router.get("/runtime-placements")
async def list_runtime_placements(
    tenantId: str | None = None,
    phase: str | None = None,
    limit: int = 100,
    _user: CurrentPlatformUser = Depends(require_platform_role(["platform_admin", "platform_operator"])),
):
    pool = get_pool()
    safe_limit = max(1, min(500, int(limit or 100)))
    rows = await pool.fetch(
        """
        SELECT *
        FROM session_runtime_placements
        WHERE ($1::text IS NULL OR tenant_id = $1)
          AND ($2::text IS NULL OR runtime_phase = $2)
        ORDER BY updated_at DESC
        LIMIT $3
        """,
        tenantId,
        phase,
        safe_limit,
    )
    return {
        "placements": [
            {
                "id": row["id"],
                "sessionId": row["session_id"],
                "tenantId": row["tenant_id"],
                "runtimeProvider": row["runtime_provider"],
                "runtimeNamespace": row["runtime_namespace"],
                "runtimePodName": row["runtime_pod_name"],
                "runtimeServiceName": row["runtime_service_name"],
                "runtimeNodeName": row["runtime_node_name"],
                "runtimeClass": row["runtime_class"],
                "placementProfile": row["placement_profile"] or {},
                "nodePool": row["node_pool"],
                "nodeSelector": row["node_selector"] or {},
                "tolerations": row["tolerations"] or [],
                "runtimePhase": row["runtime_phase"],
                "egressGatewayPodName": row["egress_gateway_pod_name"],
                "networkPolicyName": row["network_policy_name"],
                "secretName": row["secret_name"],
                "configMapName": row["config_map_name"],
                "imageRef": row["image_ref"],
                "imageDigest": row["image_digest"],
                "requestedCpu": row["requested_cpu"],
                "requestedMemory": row["requested_memory"],
                "requestedEphemeralStorage": row["requested_ephemeral_storage"],
                "failureReason": row["failure_reason"],
                "failureMessage": row["failure_message"],
                "createdAt": row["created_at"].isoformat() if row["created_at"] else None,
                "updatedAt": row["updated_at"].isoformat() if row["updated_at"] else None,
                "readyAt": row["ready_at"].isoformat() if row["ready_at"] else None,
                "endedAt": row["ended_at"].isoformat() if row["ended_at"] else None,
                "lastHeartbeatAt": row["last_heartbeat_at"].isoformat() if row["last_heartbeat_at"] else None,
                "lastReconciledAt": row["last_reconciled_at"].isoformat() if row["last_reconciled_at"] else None,
                "lastError": row["last_error"],
            }
            for row in rows
        ]
    }


@router.get("/audit-events")
async def list_platform_audit_events(
    tenantId: str | None = None,
    limit: int = 100,
    _user: CurrentPlatformUser = Depends(require_platform_role(["platform_admin", "platform_operator"])),
):
    pool = get_pool()
    safe_limit = max(1, min(500, int(limit or 100)))
    if tenantId:
        rows = await pool.fetch(
            """
            SELECT *
            FROM platform_audit_events
            WHERE tenant_id = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            tenantId,
            safe_limit,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT *
            FROM platform_audit_events
            ORDER BY created_at DESC
            LIMIT $1
            """,
            safe_limit,
        )
    return {
        "events": [
            {
                "id": row["id"],
                "actorPlatformUserId": row["actor_platform_user_id"],
                "actorRole": row["actor_role"],
                "action": row["action"],
                "targetType": row["target_type"],
                "targetId": row["target_id"],
                "tenantId": row["tenant_id"],
                "requestId": row["request_id"],
                "outcome": row["outcome"],
                "reason": row["reason"],
                "before": row["before"],
                "after": row["after"],
                "error": row["error"],
                "createdAt": row["created_at"].isoformat() if row["created_at"] else None,
            }
            for row in rows
        ]
    }


@router.post("/audit-events")
async def create_platform_audit_event(
    body: PlatformAuditEventBody,
    user: CurrentPlatformUser = Depends(require_platform_role(["platform_admin", "platform_operator"])),
):
    pool = get_pool()
    event_id = await record_platform_audit(
        pool,
        action=body.action,
        target_type=body.targetType,
        target_id=body.targetId,
        tenant_id=body.tenantId,
        actor_platform_user_id=user.id,
        actor_role=user.role,
        request_id=body.requestId,
        outcome=body.outcome,
        reason=body.reason,
        before=body.before,
        after=body.after,
        error=body.error,
    )
    return {"id": event_id}
