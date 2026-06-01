from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

import asyncpg
from fastapi import HTTPException

from app.config import (
    EE_SAAS_MODE,
    SAAS_DEFAULT_ACTIVE_SESSION_LIMIT,
    SAAS_DEFAULT_MAX_SESSION_SECONDS,
)
from app.db import get_pool

DEFAULT_PLAN_ID = "plan_default"
DEFAULT_PLAN_CODE = "default"
DEFAULT_RUNTIME_POOL_ID = "runtime_pool_default"
TENANT_STATUS_ACTIVE = "active"
TENANT_STATUS_SUSPENDED = "suspended"
TENANT_STATUS_DELETED = "deleted"
ACTIVE_RUNTIME_PHASES = ("provisioning", "starting", "ready")
DEFAULT_RUNTIME_IMAGE_POLICY = {
    "source": "approved_runtime_images",
    "tenantCustomImages": False,
}
RESOURCE_QUOTA_PER_ACTIVE_SESSION = {
    "pods": 2,
    "requests.cpu_m": 600,
    "requests.memory_mi": 2304,
    "requests.ephemeral_storage_mi": 4096,
    "limits.cpu_m": 2200,
    "limits.memory_mi": 4352,
    "limits.ephemeral_storage_mi": 8192,
}


@dataclass(frozen=True)
class RuntimeQuota:
    active_session_limit: int
    runtime_class_limits: dict[str, Any]
    max_session_seconds: int


@dataclass(frozen=True)
class RuntimePlacementReservation:
    id: str
    session_id: str
    tenant_id: str
    runtime_namespace: str
    runtime_class: str
    runtime_provider: str


@dataclass(frozen=True)
class RuntimePlacementReconcileTarget:
    id: str
    session_id: str
    tenant_id: str
    runtime_provider: str
    runtime_namespace: str
    runtime_pod_name: str | None
    runtime_service_name: str | None
    runtime_node_name: str | None
    runtime_class: str
    runtime_phase: str
    node_pool: str | None
    egress_gateway_pod_name: str | None
    network_policy_name: str | None
    secret_name: str | None
    config_map_name: str | None
    image_ref: str | None
    image_digest: str | None
    last_reconciled_at: Any
    last_error: str | None


@dataclass(frozen=True)
class RuntimePoolCapacityReservation:
    id: str
    session_id: str
    tenant_id: str
    runtime_pool_id: str
    runtime_class: str


@dataclass(frozen=True)
class ApprovedRuntimeImage:
    runtime_class: str
    image_ref: str
    image_digest: str
    chrome_version: str | None


def runtime_namespace_for_tenant(tenant_id: str) -> str:
    # Keep this aligned with migration 0020, which uses PostgreSQL's built-in md5().
    digest = hashlib.md5(tenant_id.encode("utf-8")).hexdigest()[:16]
    return f"bp-tenant-{digest}"


def _row_get(row, key: str, default=None):
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def runtime_resource_quota_for_limit(
    active_session_limit: int,
    runtime_class_limits: dict[str, Any] | None = None,
) -> dict[str, str]:
    active_sessions = max(0, int(active_session_limit))

    def scaled(key: str, suffix: str = "") -> str:
        value = RESOURCE_QUOTA_PER_ACTIVE_SESSION[key] * active_sessions
        return "0" if value == 0 else f"{value}{suffix}"

    quota = {
        "pods": scaled("pods"),
        "requests.cpu": scaled("requests.cpu_m", "m"),
        "requests.memory": scaled("requests.memory_mi", "Mi"),
        "requests.ephemeral-storage": scaled("requests.ephemeral_storage_mi", "Mi"),
        "limits.cpu": scaled("limits.cpu_m", "m"),
        "limits.memory": scaled("limits.memory_mi", "Mi"),
        "limits.ephemeral-storage": scaled("limits.ephemeral_storage_mi", "Mi"),
    }

    overrides = (runtime_class_limits or {}).get("resourceQuota")
    if isinstance(overrides, dict):
        quota.update({str(key): str(value) for key, value in overrides.items()})
    return quota


async def build_runtime_deploy_values(pool) -> dict[str, Any]:
    image_rows = await pool.fetch(
        """
        SELECT runtime_class, image_ref, image_digest, chrome_version
        FROM approved_runtime_images
        WHERE approval_status = 'approved'
          AND scan_status = 'passed'
        ORDER BY runtime_class, approved_at DESC NULLS LAST, updated_at DESC, created_at DESC
        """
    )
    tenant_rows = await pool.fetch(
        """
        SELECT tps.tenant_id, tps.status, tps.runtime_namespace,
               trq.active_session_limit, trq.runtime_class_limits, trq.max_session_seconds
        FROM tenant_platform_settings tps
        JOIN tenant_runtime_quotas trq ON trq.tenant_id = tps.tenant_id
        WHERE tps.status IN ('active', 'suspended')
        ORDER BY tps.runtime_namespace
        """
    )
    return {
        "runtime": {
            "approvedImages": [
                {
                    "runtimeClass": row["runtime_class"],
                    "imageRef": row["image_ref"],
                    "imageDigest": row["image_digest"],
                    **({"chromeVersion": row["chrome_version"]} if row["chrome_version"] else {}),
                }
                for row in image_rows
            ],
            "tenants": [
                {
                    "id": row["tenant_id"],
                    "namespace": row["runtime_namespace"],
                    "status": row["status"],
                    "activeSessionLimit": int(row["active_session_limit"]),
                    "maxSessionSeconds": int(row["max_session_seconds"]),
                    "resourceQuota": runtime_resource_quota_for_limit(
                        int(row["active_session_limit"]),
                        dict(row["runtime_class_limits"] or {}),
                    ),
                }
                for row in tenant_rows
            ],
        }
    }


async def resolve_approved_runtime_image(pool, runtime_class: str) -> ApprovedRuntimeImage:
    row = await pool.fetchrow(
        """
        SELECT runtime_class, image_ref, image_digest, chrome_version
        FROM approved_runtime_images
        WHERE runtime_class = $1
          AND approval_status = 'approved'
          AND scan_status = 'passed'
        ORDER BY approved_at DESC NULLS LAST, updated_at DESC, created_at DESC
        LIMIT 1
        """,
        runtime_class,
    )
    if not row:
        raise HTTPException(status_code=409, detail="approved_runtime_image_missing")
    return ApprovedRuntimeImage(
        runtime_class=row["runtime_class"],
        image_ref=row["image_ref"],
        image_digest=row["image_digest"],
        chrome_version=row["chrome_version"],
    )


async def ensure_default_plan(pool) -> None:
    await pool.execute(
        """
        INSERT INTO plans (
            id, code, name, default_active_session_limit,
            default_runtime_class_limits, default_max_session_seconds
        )
        VALUES ($1, $2, 'Default', $3, '{}'::jsonb, $4)
        ON CONFLICT (code) DO NOTHING
        """,
        DEFAULT_PLAN_ID,
        DEFAULT_PLAN_CODE,
        SAAS_DEFAULT_ACTIVE_SESSION_LIMIT,
        SAAS_DEFAULT_MAX_SESSION_SECONDS,
    )


async def ensure_default_runtime_pool(pool) -> None:
    await pool.execute(
        """
        INSERT INTO runtime_pools (
            id, name, runtime_classes, active_session_capacity
        )
        VALUES (
            $1,
            'Default runtime worker pool',
            '["standard_chrome", "cloak_chromium"]'::jsonb,
            100
        )
        ON CONFLICT (id) DO NOTHING
        """,
        DEFAULT_RUNTIME_POOL_ID,
    )


async def record_platform_audit(
    pool,
    *,
    action: str,
    target_type: str,
    target_id: str | None = None,
    tenant_id: str | None = None,
    actor_platform_user_id: str | None = None,
    actor_role: str | None = None,
    request_id: str | None = None,
    outcome: str = "success",
    reason: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    error: str | None = None,
) -> str:
    event_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO platform_audit_events (
            id, actor_platform_user_id, actor_role, action, target_type,
            target_id, tenant_id, request_id, outcome, reason, before, after, error
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12::jsonb, $13)
        """,
        event_id,
        actor_platform_user_id,
        actor_role,
        action,
        target_type,
        target_id,
        tenant_id,
        request_id,
        outcome,
        reason,
        before,
        after,
        error,
    )
    return event_id


async def ensure_tenant_platform_defaults(
    pool,
    tenant_id: str,
    *,
    actor_platform_user_id: str | None = None,
    active_session_limit: int | None = None,
    max_session_seconds: int | None = None,
    plan_id: str | None = DEFAULT_PLAN_ID,
    reason: str = "tenant_created",
) -> None:
    await ensure_default_plan(pool)
    namespace = runtime_namespace_for_tenant(tenant_id)
    limit = SAAS_DEFAULT_ACTIVE_SESSION_LIMIT if active_session_limit is None else active_session_limit
    max_seconds = SAAS_DEFAULT_MAX_SESSION_SECONDS if max_session_seconds is None else max_session_seconds

    await pool.execute(
        """
        INSERT INTO tenant_platform_settings (
            tenant_id, status, runtime_namespace, runtime_image_policy, created_by_platform_user_id
        )
        VALUES ($1, 'active', $2, $3::jsonb, $4)
        ON CONFLICT (tenant_id) DO NOTHING
        """,
        tenant_id,
        namespace,
        DEFAULT_RUNTIME_IMAGE_POLICY,
        actor_platform_user_id,
    )
    await pool.execute(
        """
        INSERT INTO tenant_entitlements (
            tenant_id, plan_id, active_session_limit_override,
            max_session_seconds_override, updated_by, update_reason
        )
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (tenant_id) DO NOTHING
        """,
        tenant_id,
        plan_id,
        active_session_limit,
        max_session_seconds,
        actor_platform_user_id,
        reason,
    )
    await pool.execute(
        """
        INSERT INTO tenant_runtime_quotas (
            tenant_id, active_session_limit, runtime_class_limits,
            max_session_seconds, updated_by, update_reason
        )
        VALUES ($1, $2, '{}'::jsonb, $3, $4, $5)
        ON CONFLICT (tenant_id) DO NOTHING
        """,
        tenant_id,
        limit,
        max_seconds,
        actor_platform_user_id,
        reason,
    )


async def get_tenant_runtime_quota(pool, tenant_id: str) -> RuntimeQuota | None:
    row = await pool.fetchrow(
        """
        SELECT active_session_limit, runtime_class_limits, max_session_seconds
        FROM tenant_runtime_quotas
        WHERE tenant_id = $1
        """,
        tenant_id,
    )
    if not row:
        return None
    return RuntimeQuota(
        active_session_limit=int(row["active_session_limit"]),
        runtime_class_limits=dict(row["runtime_class_limits"] or {}),
        max_session_seconds=int(row["max_session_seconds"]),
    )


async def count_active_runtime_usage(
    pool,
    tenant_id: str,
    *,
    exclude_session_id: str | None = None,
) -> int:
    try:
        return int(
            await pool.fetchval(
                """
                SELECT COUNT(*)
                FROM session_runtime_placements
                WHERE tenant_id = $1
                  AND ended_at IS NULL
                  AND runtime_phase = ANY($2::text[])
                  AND ($3::text IS NULL OR session_id <> $3)
                """,
                tenant_id,
                list(ACTIVE_RUNTIME_PHASES),
                exclude_session_id,
            )
            or 0
        )
    except asyncpg.UndefinedTableError:
        return int(
            await pool.fetchval(
                """
                SELECT COUNT(*)
                FROM sessions
                WHERE tenant_id = $1
                  AND ($2::text IS NULL OR id <> $2)
                """,
                tenant_id,
                exclude_session_id,
            )
            or 0
        )


async def reserve_runtime_pool_capacity(
    conn,
    *,
    session_id: str,
    tenant_id: str,
    runtime_class: str,
    runtime_pool_id: str | None = None,
) -> RuntimePoolCapacityReservation:
    pool_id = runtime_pool_id or DEFAULT_RUNTIME_POOL_ID
    await ensure_default_runtime_pool(conn)

    existing = await conn.fetchrow(
        """
        SELECT id, session_id, tenant_id, runtime_pool_id, runtime_class
        FROM runtime_capacity_reservations
        WHERE session_id = $1
          AND released_at IS NULL
        """,
        session_id,
    )
    if existing:
        return RuntimePoolCapacityReservation(
            id=existing["id"],
            session_id=existing["session_id"],
            tenant_id=existing["tenant_id"],
            runtime_pool_id=existing["runtime_pool_id"],
            runtime_class=existing["runtime_class"],
        )

    pool = await conn.fetchrow(
        """
        SELECT id, active_session_capacity, is_enabled, is_draining, runtime_classes
        FROM runtime_pools
        WHERE id = $1
        FOR UPDATE
        """,
        pool_id,
    )
    if not pool or not pool["is_enabled"]:
        raise HTTPException(status_code=429, detail="runtime_pool_unavailable")
    if pool["is_draining"]:
        raise HTTPException(status_code=429, detail="runtime_pool_draining")

    runtime_classes = list(pool["runtime_classes"] or [])
    if runtime_classes and runtime_class not in runtime_classes:
        raise HTTPException(status_code=409, detail="runtime_class_not_allowed_for_pool")

    active_reservations = int(
        await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM runtime_capacity_reservations
            WHERE runtime_pool_id = $1
              AND released_at IS NULL
              AND reserved_phase = 'reserved'
            """,
            pool_id,
        )
        or 0
    )
    if active_reservations >= int(pool["active_session_capacity"]):
        raise HTTPException(status_code=429, detail="runtime_pool_capacity_exhausted")

    reservation_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO runtime_capacity_reservations (
            id, session_id, tenant_id, runtime_pool_id, runtime_class
        )
        VALUES ($1, $2, $3, $4, $5)
        """,
        reservation_id,
        session_id,
        tenant_id,
        pool_id,
        runtime_class,
    )
    return RuntimePoolCapacityReservation(
        id=reservation_id,
        session_id=session_id,
        tenant_id=tenant_id,
        runtime_pool_id=pool_id,
        runtime_class=runtime_class,
    )


async def release_runtime_pool_capacity(conn, *, session_id: str, reason: str) -> None:
    await conn.execute(
        """
        UPDATE runtime_capacity_reservations
        SET reserved_phase = 'released',
            released_at = COALESCE(released_at, NOW()),
            release_reason = COALESCE(release_reason, $2),
            updated_at = NOW()
        WHERE session_id = $1
          AND released_at IS NULL
        """,
        session_id,
        reason,
    )


async def release_runtime_pool_capacity_for_placement(conn, *, placement_id: str, reason: str) -> None:
    await conn.execute(
        """
        UPDATE runtime_capacity_reservations
        SET reserved_phase = 'released',
            released_at = COALESCE(released_at, NOW()),
            release_reason = COALESCE(release_reason, $2),
            updated_at = NOW()
        WHERE session_id = (
            SELECT session_id
            FROM session_runtime_placements
            WHERE id = $1
        )
          AND released_at IS NULL
        """,
        placement_id,
        reason,
    )


async def assert_tenant_runtime_allowed(
    tenant_id: str,
    *,
    exclude_session_id: str | None = None,
) -> None:
    if not EE_SAAS_MODE:
        return

    pool = get_pool()
    settings = await pool.fetchrow(
        "SELECT status FROM tenant_platform_settings WHERE tenant_id = $1",
        tenant_id,
    )
    if not settings:
        raise HTTPException(status_code=403, detail="tenant_platform_settings_missing")
    status = str(settings["status"])
    if status == TENANT_STATUS_SUSPENDED:
        raise HTTPException(status_code=403, detail="tenant_suspended")
    if status == TENANT_STATUS_DELETED:
        raise HTTPException(status_code=403, detail="tenant_deleted")
    if status != TENANT_STATUS_ACTIVE:
        raise HTTPException(status_code=403, detail="tenant_not_active")

    quota = await get_tenant_runtime_quota(pool, tenant_id)
    if not quota:
        raise HTTPException(status_code=403, detail="tenant_runtime_quota_missing")
    active_count = await count_active_runtime_usage(
        pool,
        tenant_id,
        exclude_session_id=exclude_session_id,
    )
    if active_count >= quota.active_session_limit:
        raise HTTPException(status_code=429, detail="tenant_runtime_quota_exceeded")


async def reserve_runtime_placement(
    conn,
    *,
    session_id: str,
    tenant_id: str,
    runtime_class: str,
    runtime_provider: str = "kubernetes",
    runtime_namespace: str | None = None,
    placement_profile: dict[str, Any] | None = None,
    node_pool: str | None = None,
    node_selector: dict[str, Any] | None = None,
    tolerations: list[dict[str, Any]] | None = None,
    image_ref: str | None = None,
    image_digest: str | None = None,
    requested_cpu: str | None = None,
    requested_memory: str | None = None,
    requested_ephemeral_storage: str | None = None,
) -> RuntimePlacementReservation:
    settings = await conn.fetchrow(
        """
        SELECT status, runtime_namespace
        FROM tenant_platform_settings
        WHERE tenant_id = $1
        """,
        tenant_id,
    )
    if not settings:
        raise HTTPException(status_code=403, detail="tenant_platform_settings_missing")
    status = str(settings["status"])
    if status == TENANT_STATUS_SUSPENDED:
        raise HTTPException(status_code=403, detail="tenant_suspended")
    if status == TENANT_STATUS_DELETED:
        raise HTTPException(status_code=403, detail="tenant_deleted")
    if status != TENANT_STATUS_ACTIVE:
        raise HTTPException(status_code=403, detail="tenant_not_active")

    quota = await conn.fetchrow(
        """
        SELECT active_session_limit, runtime_class_limits, max_session_seconds
        FROM tenant_runtime_quotas
        WHERE tenant_id = $1
        FOR UPDATE
        """,
        tenant_id,
    )
    if not quota:
        raise HTTPException(status_code=403, detail="tenant_runtime_quota_missing")

    active_count = int(
        await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM session_runtime_placements
            WHERE tenant_id = $1
              AND ended_at IS NULL
              AND runtime_phase = ANY($2::text[])
              AND session_id <> $3
            """,
            tenant_id,
            list(ACTIVE_RUNTIME_PHASES),
            session_id,
        )
        or 0
    )
    if active_count >= int(quota["active_session_limit"]):
        raise HTTPException(status_code=429, detail="tenant_runtime_quota_exceeded")

    existing = await conn.fetchrow(
        """
        SELECT id, runtime_provider, runtime_namespace, runtime_class
        FROM session_runtime_placements
        WHERE session_id = $1 AND ended_at IS NULL
        """,
        session_id,
    )
    if existing:
        return RuntimePlacementReservation(
            id=existing["id"],
            session_id=session_id,
            tenant_id=tenant_id,
            runtime_namespace=existing["runtime_namespace"],
            runtime_class=existing["runtime_class"],
            runtime_provider=existing["runtime_provider"],
        )

    pool_id = node_pool or DEFAULT_RUNTIME_POOL_ID
    await reserve_runtime_pool_capacity(
        conn,
        session_id=session_id,
        tenant_id=tenant_id,
        runtime_class=runtime_class,
        runtime_pool_id=pool_id,
    )

    namespace = runtime_namespace or settings["runtime_namespace"] or runtime_namespace_for_tenant(tenant_id)
    placement_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO session_runtime_placements (
            id, session_id, tenant_id, runtime_provider, runtime_namespace,
            runtime_class, placement_profile, node_pool, node_selector,
            tolerations, runtime_phase, image_ref, image_digest,
            requested_cpu, requested_memory, requested_ephemeral_storage
        )
        VALUES (
            $1, $2, $3, $4, $5,
            $6, $7::jsonb, $8, $9::jsonb,
            $10::jsonb, 'provisioning', $11, $12,
            $13, $14, $15
        )
        """,
        placement_id,
        session_id,
        tenant_id,
        runtime_provider,
        namespace,
        runtime_class,
        placement_profile or {},
        pool_id,
        node_selector or {},
        tolerations or [],
        image_ref,
        image_digest,
        requested_cpu,
        requested_memory,
        requested_ephemeral_storage,
    )
    return RuntimePlacementReservation(
        id=placement_id,
        session_id=session_id,
        tenant_id=tenant_id,
        runtime_namespace=namespace,
        runtime_class=runtime_class,
        runtime_provider=runtime_provider,
    )


async def update_runtime_placement_resources(
    conn,
    *,
    placement_id: str,
    runtime_phase: str,
    runtime_pod_name: str | None = None,
    runtime_service_name: str | None = None,
    runtime_node_name: str | None = None,
    egress_gateway_pod_name: str | None = None,
    network_policy_name: str | None = None,
    secret_name: str | None = None,
    config_map_name: str | None = None,
) -> None:
    await conn.execute(
        """
        UPDATE session_runtime_placements
        SET runtime_phase = $2,
            runtime_pod_name = COALESCE($3, runtime_pod_name),
            runtime_service_name = COALESCE($4, runtime_service_name),
            runtime_node_name = COALESCE($5, runtime_node_name),
            egress_gateway_pod_name = COALESCE($6, egress_gateway_pod_name),
            network_policy_name = COALESCE($7, network_policy_name),
            secret_name = COALESCE($8, secret_name),
            config_map_name = COALESCE($9, config_map_name),
            updated_at = NOW()
        WHERE id = $1
        """,
        placement_id,
        runtime_phase,
        runtime_pod_name,
        runtime_service_name,
        runtime_node_name,
        egress_gateway_pod_name,
        network_policy_name,
        secret_name,
        config_map_name,
    )


async def mark_runtime_placement_ready(conn, *, placement_id: str, runtime_node_name: str | None = None) -> None:
    await conn.execute(
        """
        UPDATE session_runtime_placements
        SET runtime_phase = 'ready',
            runtime_node_name = COALESCE($2, runtime_node_name),
            ready_at = COALESCE(ready_at, NOW()),
            updated_at = NOW(),
            last_error = NULL
        WHERE id = $1
        """,
        placement_id,
        runtime_node_name,
    )


async def mark_runtime_placement_failed(
    conn,
    *,
    placement_id: str,
    failure_reason: str,
    failure_message: str | None = None,
    end_placement: bool = False,
) -> None:
    await conn.execute(
        """
        UPDATE session_runtime_placements
        SET runtime_phase = 'failed',
            failure_reason = $2,
            failure_message = $3,
            last_error = $3,
            ended_at = CASE WHEN $4 THEN COALESCE(ended_at, NOW()) ELSE ended_at END,
            updated_at = NOW()
        WHERE id = $1
        """,
        placement_id,
        failure_reason,
        failure_message,
        end_placement,
    )
    if end_placement:
        await release_runtime_pool_capacity_for_placement(
            conn,
            placement_id=placement_id,
            reason=f"placement_failed:{failure_reason}",
        )


async def mark_runtime_placement_reclaiming(conn, *, session_id: str) -> None:
    await conn.execute(
        """
        UPDATE session_runtime_placements
        SET runtime_phase = 'reclaiming',
            updated_at = NOW()
        WHERE session_id = $1
          AND ended_at IS NULL
        """,
        session_id,
    )


async def mark_runtime_placement_ended(conn, *, session_id: str) -> None:
    await conn.execute(
        """
        UPDATE session_runtime_placements
        SET runtime_phase = 'stopping',
            ended_at = COALESCE(ended_at, NOW()),
            updated_at = NOW()
        WHERE session_id = $1
          AND ended_at IS NULL
        """,
        session_id,
    )
    await release_runtime_pool_capacity(conn, session_id=session_id, reason="runtime_placement_ended")


async def mark_runtime_placement_revoke_failed(conn, *, session_id: str, error: str) -> None:
    await conn.execute(
        """
        UPDATE session_runtime_placements
        SET last_error = $2,
            failure_reason = COALESCE(failure_reason, 'runtime_revoke_failed'),
            failure_message = $2,
            updated_at = NOW()
        WHERE session_id = $1
          AND ended_at IS NULL
        """,
        session_id,
        error,
    )


async def list_runtime_placements_for_reconciliation(
    conn,
    *,
    runtime_provider: str = "kubernetes",
    limit: int = 100,
    include_failed: bool = True,
) -> list[RuntimePlacementReconcileTarget]:
    phases = ["provisioning", "starting", "ready", "stopping", "reclaiming"]
    if include_failed:
        phases.append("failed")
    safe_limit = max(1, min(500, int(limit or 100)))
    rows = await conn.fetch(
        """
        SELECT id, session_id, tenant_id, runtime_provider, runtime_namespace,
               runtime_pod_name, runtime_service_name, runtime_node_name,
               runtime_class, runtime_phase, node_pool, egress_gateway_pod_name,
               network_policy_name, secret_name, config_map_name,
               image_ref, image_digest, last_reconciled_at, last_error
        FROM session_runtime_placements
        WHERE runtime_provider = $1
          AND ended_at IS NULL
          AND runtime_phase = ANY($2::text[])
        ORDER BY last_reconciled_at ASC NULLS FIRST, updated_at ASC
        LIMIT $3
        """,
        runtime_provider,
        phases,
        safe_limit,
    )
    return [
        RuntimePlacementReconcileTarget(
            id=row["id"],
            session_id=row["session_id"],
            tenant_id=row["tenant_id"],
            runtime_provider=row["runtime_provider"],
            runtime_namespace=row["runtime_namespace"],
            runtime_pod_name=_row_get(row, "runtime_pod_name"),
            runtime_service_name=_row_get(row, "runtime_service_name"),
            runtime_node_name=_row_get(row, "runtime_node_name"),
            runtime_class=row["runtime_class"],
            runtime_phase=row["runtime_phase"],
            node_pool=_row_get(row, "node_pool"),
            egress_gateway_pod_name=_row_get(row, "egress_gateway_pod_name"),
            network_policy_name=_row_get(row, "network_policy_name"),
            secret_name=_row_get(row, "secret_name"),
            config_map_name=_row_get(row, "config_map_name"),
            image_ref=_row_get(row, "image_ref"),
            image_digest=_row_get(row, "image_digest"),
            last_reconciled_at=_row_get(row, "last_reconciled_at"),
            last_error=_row_get(row, "last_error"),
        )
        for row in rows
    ]


async def mark_runtime_placement_reconciled(
    conn,
    *,
    placement_id: str,
    runtime_phase: str | None = None,
    runtime_node_name: str | None = None,
    heartbeat: bool = False,
    clear_error: bool = True,
) -> None:
    await conn.execute(
        """
        UPDATE session_runtime_placements
        SET runtime_phase = COALESCE($2, runtime_phase),
            runtime_node_name = COALESCE($3, runtime_node_name),
            last_reconciled_at = NOW(),
            last_heartbeat_at = CASE WHEN $4 THEN NOW() ELSE last_heartbeat_at END,
            last_error = CASE WHEN $5 THEN NULL ELSE last_error END,
            updated_at = NOW()
        WHERE id = $1
          AND ended_at IS NULL
        """,
        placement_id,
        runtime_phase,
        runtime_node_name,
        heartbeat,
        clear_error,
    )


async def record_runtime_placement_reconcile_error(conn, *, placement_id: str, error: str) -> None:
    await conn.execute(
        """
        UPDATE session_runtime_placements
        SET last_reconciled_at = NOW(),
            last_error = $2,
            updated_at = NOW()
        WHERE id = $1
          AND ended_at IS NULL
        """,
        placement_id,
        error,
    )


async def active_runtime_session_ids(conn, tenant_id: str) -> list[str]:
    try:
        rows = await conn.fetch(
            """
            SELECT DISTINCT session_id
            FROM session_runtime_placements
            WHERE tenant_id = $1
              AND ended_at IS NULL
              AND runtime_phase <> 'failed'
            ORDER BY session_id
            """,
            tenant_id,
        )
    except asyncpg.UndefinedTableError:
        rows = []
    if rows:
        return [row["session_id"] for row in rows]

    fallback = await conn.fetch(
        """
        SELECT id AS session_id
        FROM sessions
        WHERE tenant_id = $1
        ORDER BY id
        """,
        tenant_id,
    )
    return [row["session_id"] for row in fallback]


async def update_tenant_runtime_quota(
    pool,
    *,
    tenant_id: str,
    active_session_limit: int,
    max_session_seconds: int,
    runtime_class_limits: dict[str, Any] | None,
    actor_platform_user_id: str,
    actor_role: str,
    reason: str,
) -> RuntimeQuota:
    before_row = await pool.fetchrow(
        """
        SELECT active_session_limit, runtime_class_limits, max_session_seconds
        FROM tenant_runtime_quotas
        WHERE tenant_id = $1
        """,
        tenant_id,
    )
    if not before_row:
        raise HTTPException(status_code=404, detail="Tenant quota not found")

    before = {
        "activeSessionLimit": before_row["active_session_limit"],
        "runtimeClassLimits": before_row["runtime_class_limits"] or {},
        "maxSessionSeconds": before_row["max_session_seconds"],
    }
    await pool.execute(
        """
        UPDATE tenant_runtime_quotas
        SET active_session_limit = $2,
            runtime_class_limits = $3::jsonb,
            max_session_seconds = $4,
            updated_by = $5,
            update_reason = $6,
            updated_at = NOW()
        WHERE tenant_id = $1
        """,
        tenant_id,
        active_session_limit,
        runtime_class_limits or {},
        max_session_seconds,
        actor_platform_user_id,
        reason,
    )
    after = {
        "activeSessionLimit": active_session_limit,
        "runtimeClassLimits": runtime_class_limits or {},
        "maxSessionSeconds": max_session_seconds,
    }
    await record_platform_audit(
        pool,
        action="tenant.quota.update",
        target_type="tenant_runtime_quota",
        target_id=tenant_id,
        tenant_id=tenant_id,
        actor_platform_user_id=actor_platform_user_id,
        actor_role=actor_role,
        reason=reason,
        before=before,
        after=after,
    )
    return RuntimeQuota(
        active_session_limit=active_session_limit,
        runtime_class_limits=runtime_class_limits or {},
        max_session_seconds=max_session_seconds,
    )
