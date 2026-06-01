import asyncio
import hashlib

import pytest
from fastapi import HTTPException

from app import platform_control


class FakePool:
    def __init__(self, *, status="active", quota=3, active_count=0):
        self.status = status
        self.quota = quota
        self.active_count = active_count
        self.fetches = []

    async def fetchrow(self, query, *args):
        self.fetches.append((query, *args))
        if "tenant_platform_settings" in query:
            return {"status": self.status}
        if "tenant_runtime_quotas" in query:
            return {
                "active_session_limit": self.quota,
                "runtime_class_limits": {},
                "max_session_seconds": 3600,
            }
        return None

    async def fetchval(self, query, *args):
        self.fetches.append((query, *args))
        return self.active_count


class FakeReservationConn:
    def __init__(
        self,
        *,
        active_count=0,
        quota=2,
        existing=None,
        active_reservations=0,
        pool_capacity=10,
        pool_enabled=True,
        pool_draining=False,
    ):
        self.active_count = active_count
        self.quota = quota
        self.existing = existing
        self.active_reservations = active_reservations
        self.pool_capacity = pool_capacity
        self.pool_enabled = pool_enabled
        self.pool_draining = pool_draining
        self.executes = []

    async def fetchrow(self, query, *args):
        if "tenant_platform_settings" in query:
            return {"status": "active", "runtime_namespace": "bp-tenant-test"}
        if "tenant_runtime_quotas" in query:
            return {
                "active_session_limit": self.quota,
                "runtime_class_limits": {},
                "max_session_seconds": 3600,
            }
        if "session_runtime_placements" in query:
            return self.existing
        if "runtime_capacity_reservations" in query:
            return None
        if "runtime_pools" in query:
            return {
                "id": platform_control.DEFAULT_RUNTIME_POOL_ID,
                "active_session_capacity": self.pool_capacity,
                "is_enabled": self.pool_enabled,
                "is_draining": self.pool_draining,
                "runtime_classes": ["standard_chrome", "cloak_chromium"],
            }
        return None

    async def fetchval(self, query, *args):
        if "runtime_capacity_reservations" in query:
            return self.active_reservations
        return self.active_count

    async def execute(self, query, *args):
        self.executes.append((query, args))


class FakeDeployValuesPool:
    async def fetch(self, query, *args):
        if "FROM approved_runtime_images" in query:
            return [
                {
                    "runtime_class": "standard_chrome",
                    "image_ref": "registry/runtime",
                    "image_digest": "sha256:" + "a" * 64,
                    "chrome_version": "126",
                }
            ]
        if "FROM tenant_platform_settings" in query:
            return [
                {
                    "tenant_id": "tenant-1",
                    "status": "active",
                    "runtime_namespace": "bp-tenant-test",
                    "active_session_limit": 2,
                    "runtime_class_limits": {"resourceQuota": {"limits.cpu": "8"}},
                    "max_session_seconds": 3600,
                }
            ]
        return []


class FakeApprovedImagePool:
    def __init__(self, row):
        self.row = row
        self.queries = []

    async def fetchrow(self, query, *args):
        self.queries.append((query, args))
        return self.row


class FakeReconciliationConn:
    def __init__(self):
        self.fetch_args = None
        self.executes = []

    async def fetch(self, query, *args):
        self.fetch_args = (query, args)
        return [
            {
                "id": "placement-1",
                "session_id": "session-1",
                "tenant_id": "tenant-1",
                "runtime_provider": "kubernetes",
                "runtime_namespace": "bp-tenant-test",
                "runtime_pod_name": "bp-session-1",
                "runtime_service_name": "bp-session-1",
                "runtime_node_name": None,
                "runtime_class": "standard_chrome",
                "runtime_phase": "starting",
                "node_pool": platform_control.DEFAULT_RUNTIME_POOL_ID,
                "egress_gateway_pod_name": "bp-egress-session-1",
                "network_policy_name": "bp-session-1",
                "secret_name": "bp-session-1",
                "config_map_name": "bp-session-1",
                "image_ref": "registry/runtime",
                "image_digest": "sha256:" + "a" * 64,
                "last_reconciled_at": None,
                "last_error": "old error",
            }
        ]

    async def execute(self, query, *args):
        self.executes.append((query, args))


def test_runtime_namespace_for_tenant_is_deterministic_dns_label():
    namespace = platform_control.runtime_namespace_for_tenant("tenant-1")

    assert namespace == platform_control.runtime_namespace_for_tenant("tenant-1")
    assert namespace == f"bp-tenant-{hashlib.md5(b'tenant-1').hexdigest()[:16]}"
    assert namespace.startswith("bp-tenant-")
    assert namespace.islower()


def test_saas_runtime_guard_rejects_suspended_tenant(monkeypatch):
    pool = FakePool(status="suspended")
    monkeypatch.setattr(platform_control, "EE_SAAS_MODE", True)
    monkeypatch.setattr(platform_control, "get_pool", lambda: pool)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(platform_control.assert_tenant_runtime_allowed("tenant-1"))

    assert exc.value.status_code == 403
    assert exc.value.detail == "tenant_suspended"


def test_saas_runtime_guard_rejects_quota_exceeded(monkeypatch):
    pool = FakePool(status="active", quota=2, active_count=2)
    monkeypatch.setattr(platform_control, "EE_SAAS_MODE", True)
    monkeypatch.setattr(platform_control, "get_pool", lambda: pool)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(platform_control.assert_tenant_runtime_allowed("tenant-1"))

    assert exc.value.status_code == 429
    assert exc.value.detail == "tenant_runtime_quota_exceeded"


def test_saas_runtime_guard_allows_below_quota(monkeypatch):
    pool = FakePool(status="active", quota=2, active_count=1)
    monkeypatch.setattr(platform_control, "EE_SAAS_MODE", True)
    monkeypatch.setattr(platform_control, "get_pool", lambda: pool)

    asyncio.run(platform_control.assert_tenant_runtime_allowed("tenant-1"))


def test_reserve_runtime_placement_inserts_provisioning_row():
    conn = FakeReservationConn(active_count=1, quota=2)

    reservation = asyncio.run(
        platform_control.reserve_runtime_placement(
            conn,
            session_id="session-1",
            tenant_id="tenant-1",
            runtime_class="standard_chrome",
            image_ref="registry/browser",
            image_digest="sha256:abc",
        )
    )

    assert reservation.session_id == "session-1"
    assert reservation.runtime_namespace == "bp-tenant-test"
    assert reservation.runtime_provider == "kubernetes"
    queries = [query for query, _args in conn.executes]
    assert any("INSERT INTO runtime_capacity_reservations" in query for query in queries)
    placement_exec = next((query, args) for query, args in conn.executes if "INSERT INTO session_runtime_placements" in query)
    assert placement_exec[1][7] == platform_control.DEFAULT_RUNTIME_POOL_ID
    assert placement_exec[1][9] == []
    assert placement_exec[1][11] == "sha256:abc"


def test_reserve_runtime_placement_rejects_quota_before_insert():
    conn = FakeReservationConn(active_count=2, quota=2)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            platform_control.reserve_runtime_placement(
                conn,
                session_id="session-1",
                tenant_id="tenant-1",
                runtime_class="standard_chrome",
            )
        )

    assert exc.value.status_code == 429
    assert exc.value.detail == "tenant_runtime_quota_exceeded"
    assert not any("INSERT INTO runtime_capacity_reservations" in query for query, _args in conn.executes)
    assert not any("INSERT INTO session_runtime_placements" in query for query, _args in conn.executes)


def test_reserve_runtime_placement_rejects_pool_capacity_before_placement_insert():
    conn = FakeReservationConn(active_count=0, quota=2, active_reservations=1, pool_capacity=1)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            platform_control.reserve_runtime_placement(
                conn,
                session_id="session-1",
                tenant_id="tenant-1",
                runtime_class="standard_chrome",
            )
        )

    assert exc.value.status_code == 429
    assert exc.value.detail == "runtime_pool_capacity_exhausted"
    assert not any("INSERT INTO session_runtime_placements" in query for query, _args in conn.executes)


def test_runtime_resource_quota_is_derived_from_tenant_limit_with_platform_override():
    quota = platform_control.runtime_resource_quota_for_limit(
        2,
        {"resourceQuota": {"limits.cpu": "8"}},
    )

    assert quota["pods"] == "4"
    assert quota["requests.cpu"] == "1200m"
    assert quota["limits.memory"] == "8704Mi"
    assert quota["limits.cpu"] == "8"


def test_build_runtime_deploy_values_exports_approved_images_and_tenant_namespaces():
    values = asyncio.run(platform_control.build_runtime_deploy_values(FakeDeployValuesPool()))

    assert values["runtime"]["approvedImages"] == [
        {
            "runtimeClass": "standard_chrome",
            "imageRef": "registry/runtime",
            "imageDigest": "sha256:" + "a" * 64,
            "chromeVersion": "126",
        }
    ]
    assert values["runtime"]["tenants"][0]["id"] == "tenant-1"
    assert values["runtime"]["tenants"][0]["namespace"] == "bp-tenant-test"
    assert values["runtime"]["tenants"][0]["resourceQuota"]["pods"] == "4"


def test_resolve_approved_runtime_image_returns_latest_approved_passed_digest():
    pool = FakeApprovedImagePool(
        {
            "runtime_class": "standard_chrome",
            "image_ref": "registry/runtime",
            "image_digest": "sha256:" + "a" * 64,
            "chrome_version": "126",
        }
    )

    image = asyncio.run(platform_control.resolve_approved_runtime_image(pool, "standard_chrome"))

    assert image.runtime_class == "standard_chrome"
    assert image.image_digest == "sha256:" + "a" * 64
    assert "approval_status = 'approved'" in pool.queries[0][0]
    assert "scan_status = 'passed'" in pool.queries[0][0]


def test_resolve_approved_runtime_image_fails_closed_when_missing():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(platform_control.resolve_approved_runtime_image(FakeApprovedImagePool(None), "standard_chrome"))

    assert exc.value.status_code == 409
    assert exc.value.detail == "approved_runtime_image_missing"


def test_list_runtime_placements_for_reconciliation_returns_resource_ledger():
    conn = FakeReconciliationConn()

    targets = asyncio.run(platform_control.list_runtime_placements_for_reconciliation(conn, limit=20))

    assert len(targets) == 1
    assert targets[0].id == "placement-1"
    assert targets[0].runtime_namespace == "bp-tenant-test"
    assert targets[0].runtime_pod_name == "bp-session-1"
    assert targets[0].egress_gateway_pod_name == "bp-egress-session-1"
    assert targets[0].image_digest == "sha256:" + "a" * 64
    query, args = conn.fetch_args
    assert "last_reconciled_at ASC NULLS FIRST" in query
    assert args[0] == "kubernetes"
    assert "failed" in args[1]
    assert args[2] == 20


def test_mark_runtime_placement_reconciled_updates_heartbeat_and_clears_error():
    conn = FakeReconciliationConn()

    asyncio.run(
        platform_control.mark_runtime_placement_reconciled(
            conn,
            placement_id="placement-1",
            runtime_phase="ready",
            runtime_node_name="node-1",
            heartbeat=True,
        )
    )

    query, args = conn.executes[0]
    assert "last_reconciled_at = NOW()" in query
    assert "last_heartbeat_at = CASE WHEN $4 THEN NOW()" in query
    assert "last_error = CASE WHEN $5 THEN NULL ELSE last_error END" in query
    assert args == ("placement-1", "ready", "node-1", True, True)


def test_record_runtime_placement_reconcile_error_preserves_current_placement():
    conn = FakeReconciliationConn()

    asyncio.run(
        platform_control.record_runtime_placement_reconcile_error(
            conn,
            placement_id="placement-1",
            error="pod not found",
        )
    )

    query, args = conn.executes[0]
    assert "last_reconciled_at = NOW()" in query
    assert "last_error = $2" in query
    assert "ended_at IS NULL" in query
    assert args == ("placement-1", "pod not found")
