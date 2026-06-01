import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.auth.jwt import create_platform_access_token
from app import platform_auth
from app.routes import platform as platform_routes


class FakeRequest:
    def __init__(self, token: str | None):
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}


class FakePool:
    async def fetchrow(self, *_args):
        return {
            "id": "platform-user-1",
            "email": "ops@example.com",
            "name": "Ops",
            "role": "platform_admin",
        }


def test_platform_auth_resolves_only_platform_tokens(monkeypatch):
    monkeypatch.setattr(platform_auth, "get_pool", lambda: FakePool())
    token = create_platform_access_token("platform-user-1", "platform_admin")

    user = asyncio.run(platform_auth.get_current_platform_user(FakeRequest(token)))

    assert user.id == "platform-user-1"
    assert user.role == "platform_admin"


def test_platform_auth_rejects_tenant_token_shape():
    request = FakeRequest(None)
    request.headers = {"Authorization": "Bearer invalid-token"}

    with pytest.raises(HTTPException) as exc:
        asyncio.run(platform_auth.get_current_platform_user(request))

    assert exc.value.status_code == 401


def test_runtime_image_digest_must_be_pinned_sha256():
    platform_routes._validate_sha256_digest("sha256:" + "a" * 64)

    with pytest.raises(HTTPException) as exc:
        platform_routes._validate_sha256_digest("browser-pilot-runtime:latest")

    assert exc.value.status_code == 422


def test_approved_runtime_image_must_have_passed_scan():
    platform_routes._validate_runtime_image_approval("passed", "approved")
    platform_routes._validate_runtime_image_approval("pending", "pending")

    with pytest.raises(HTTPException) as exc:
        platform_routes._validate_runtime_image_approval("pending", "approved")

    assert exc.value.status_code == 422


class DeleteTenantPool:
    def __init__(self):
        self.executed = []

    async def execute(self, query, *args):
        self.executed.append((query, args))
        return "OK"


class FakeTenantPurgeConn:
    def __init__(
        self,
        *,
        row,
        active_placements=0,
        files=None,
        delete_counts=None,
    ):
        self.row = row
        self.active_placements = active_placements
        self.files = files or []
        self.delete_counts = delete_counts or {}
        self.executed = []
        self.in_transaction = False

    def acquire(self):
        return self

    def transaction(self):
        return self

    async def __aenter__(self):
        self.in_transaction = True
        return self

    async def __aexit__(self, *_args):
        self.in_transaction = False

    async def fetchval(self, query, *args):
        assert "session_runtime_placements" in query
        return self.active_placements

    async def fetch(self, query, *args):
        assert "session_files" in query
        return self.files

    async def execute(self, query, *args):
        self.executed.append((query, args))
        if query.strip().startswith("DELETE FROM"):
            table = query.strip().split()[2]
            return f"DELETE {self.delete_counts.get(table, 0)}"
        return "INSERT 0 1"


class FakeFileStore:
    def __init__(self, *, fail_keys=None):
        self.deleted_keys = []
        self.fail_keys = set(fail_keys or [])

    async def delete_by_key(self, key):
        if key in self.fail_keys:
            raise RuntimeError("object delete failed")
        self.deleted_keys.append(key)


def platform_tenant_row(**overrides):
    row = {
        "id": "tenant-1",
        "name": "Tenant 1",
        "slug": "tenant-1",
        "created_at": None,
        "status": "deleted",
        "runtime_namespace": "bp-tenant-test",
        "runtime_image_policy": {},
        "active_session_limit": 3,
        "runtime_class_limits": {},
        "max_session_seconds": 3600,
        "plan_id": "plan_default",
        "plan_code": "default",
        "contract_ref": None,
        "trial_ends_at": None,
        "session_count": 0,
        "suspended_at": None,
        "suspend_reason": None,
        "deleted_at": datetime.now(timezone.utc),
        "delete_reason": "contract ended",
        "retention_until": datetime.now(timezone.utc) - timedelta(days=1),
        "purge_requested_at": None,
        "purge_request_reason": None,
    }
    row.update(overrides)
    return row


def test_delete_tenant_does_not_mark_deleted_when_runtime_revoke_fails(monkeypatch):
    pool = DeleteTenantPool()
    audits = []

    async def fake_tenant_row(_pool, tenant_id):
        assert tenant_id == "tenant-1"
        return {"status": "active"}

    async def fake_revoke(_tenant_id):
        return {
            "affectedSessionCount": 1,
            "failedResources": [{"sessionId": "session-1", "error": "pod delete failed"}],
        }

    async def fake_audit(*_args, **kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(platform_routes, "get_pool", lambda: pool)
    monkeypatch.setattr(platform_routes, "_tenant_row", fake_tenant_row)
    monkeypatch.setattr(platform_routes, "_revoke_tenant_runtime", fake_revoke)
    monkeypatch.setattr(platform_routes, "record_platform_audit", fake_audit)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            platform_routes.delete_tenant(
                "tenant-1",
                platform_routes.TenantLifecycleBody(reason="contract ended"),
                SimpleNamespace(id="platform-user-1", role="platform_admin"),
            )
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["reason"] == "runtime_revoke_failed"
    assert any("SET status = 'suspended'" in query for query, _args in pool.executed)
    assert not any("SET status = 'deleted'" in query for query, _args in pool.executed)
    assert audits[0]["outcome"] == "failure"
    assert audits[0]["error"] == "runtime_revoke_failed"


def test_delete_tenant_records_retention_window(monkeypatch):
    pool = DeleteTenantPool()
    audits = []

    async def fake_tenant_row(_pool, tenant_id):
        assert tenant_id == "tenant-1"
        return {"status": "suspended"}

    async def fake_revoke(_tenant_id):
        return {"affectedSessionCount": 0, "failedResources": []}

    async def fake_audit(*_args, **kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(platform_routes, "get_pool", lambda: pool)
    monkeypatch.setattr(platform_routes, "_tenant_row", fake_tenant_row)
    monkeypatch.setattr(platform_routes, "_revoke_tenant_runtime", fake_revoke)
    monkeypatch.setattr(platform_routes, "record_platform_audit", fake_audit)

    result = asyncio.run(
        platform_routes.delete_tenant(
            "tenant-1",
            platform_routes.TenantDeleteBody(reason="contract ended", retentionDays=7),
            SimpleNamespace(id="platform-user-1", role="platform_admin"),
        )
    )

    assert result["ok"] is True
    delete_exec = next((item for item in pool.executed if "SET status = 'deleted'" in item[0]), None)
    assert delete_exec is not None
    assert delete_exec[1][0:3] == ("tenant-1", "platform-user-1", "contract ended")
    assert isinstance(delete_exec[1][3], datetime)
    assert audits[0]["action"] == "tenant.delete"
    assert audits[0]["after"]["retentionDays"] == 7
    assert audits[0]["after"]["retentionUntil"]


def test_purge_request_rejects_before_retention_window(monkeypatch):
    audits = []

    async def fake_tenant_row(_pool, _tenant_id):
        return platform_tenant_row(retention_until=datetime.now(timezone.utc) + timedelta(days=3))

    async def fake_audit(*_args, **kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(platform_routes, "get_pool", lambda: DeleteTenantPool())
    monkeypatch.setattr(platform_routes, "_tenant_row", fake_tenant_row)
    monkeypatch.setattr(platform_routes, "record_platform_audit", fake_audit)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            platform_routes.request_tenant_purge(
                "tenant-1",
                platform_routes.TenantPurgeRequestBody(reason="customer requested purge"),
                SimpleNamespace(id="platform-user-1", role="platform_admin"),
            )
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["reason"] == "tenant_retention_not_elapsed"
    assert audits[0]["action"] == "tenant.purge_request"
    assert audits[0]["outcome"] == "failure"
    assert audits[0]["error"] == "tenant_retention_not_elapsed"


def test_purge_request_records_admin_intent_after_retention_window(monkeypatch):
    pool = DeleteTenantPool()
    audits = []
    rows = [
        platform_tenant_row(),
        platform_tenant_row(
            purge_requested_at=datetime.now(timezone.utc),
            purge_request_reason="customer requested purge",
        ),
    ]

    async def fake_tenant_row(_pool, _tenant_id):
        return rows.pop(0)

    async def fake_audit(*_args, **kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(platform_routes, "get_pool", lambda: pool)
    monkeypatch.setattr(platform_routes, "_tenant_row", fake_tenant_row)
    monkeypatch.setattr(platform_routes, "record_platform_audit", fake_audit)

    result = asyncio.run(
        platform_routes.request_tenant_purge(
            "tenant-1",
            platform_routes.TenantPurgeRequestBody(reason="customer requested purge"),
            SimpleNamespace(id="platform-user-1", role="platform_admin"),
        )
    )

    assert result["ok"] is True
    assert result["tenant"]["purgeRequestReason"] == "customer requested purge"
    assert any("SET purge_requested_at = NOW()" in query for query, _args in pool.executed)
    assert audits[0]["action"] == "tenant.purge_request"
    assert audits[0]["after"]["purgeRequestReason"] == "customer requested purge"


def test_purge_tenant_requires_purge_request(monkeypatch):
    audits = []
    conn = FakeTenantPurgeConn(row=platform_tenant_row(purge_requested_at=None))

    async def fake_tenant_row(_pool, _tenant_id):
        return conn.row

    async def fake_audit(*_args, **kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(platform_routes, "get_pool", lambda: conn)
    monkeypatch.setattr(platform_routes, "_tenant_row", fake_tenant_row)
    monkeypatch.setattr(platform_routes, "record_platform_audit", fake_audit)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            platform_routes.purge_tenant(
                "tenant-1",
                platform_routes.TenantPurgeBody(reason="purge"),
                SimpleNamespace(id="platform-user-1", role="platform_admin"),
            )
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "tenant_purge_request_missing"
    assert audits[0]["action"] == "tenant.purge"
    assert audits[0]["error"] == "tenant_purge_request_missing"


def test_purge_tenant_rejects_active_runtime_placements(monkeypatch):
    audits = []
    conn = FakeTenantPurgeConn(
        row=platform_tenant_row(purge_requested_at=datetime.now(timezone.utc)),
        active_placements=1,
    )

    async def fake_tenant_row(_pool, _tenant_id):
        return conn.row

    async def fake_audit(*_args, **kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(platform_routes, "get_pool", lambda: conn)
    monkeypatch.setattr(platform_routes, "_tenant_row", fake_tenant_row)
    monkeypatch.setattr(platform_routes, "record_platform_audit", fake_audit)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            platform_routes.purge_tenant(
                "tenant-1",
                platform_routes.TenantPurgeBody(reason="purge"),
                SimpleNamespace(id="platform-user-1", role="platform_admin"),
            )
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["reason"] == "tenant_runtime_placements_not_reclaimed"
    assert audits[0]["error"] == "tenant_runtime_placements_not_reclaimed"
    assert audits[0]["after"]["activeRuntimePlacementCount"] == 1


def test_purge_tenant_deletes_file_objects_and_db_rows(monkeypatch):
    audits = []
    file_store = FakeFileStore()
    conn = FakeTenantPurgeConn(
        row=platform_tenant_row(purge_requested_at=datetime.now(timezone.utc)),
        files=[
            {"id": "file-1", "object_key": "files/session-1/file-1/a.txt"},
            {"id": "file-2", "object_key": "files/session-2/file-2/b.txt"},
        ],
        delete_counts={
            "session_files": 2,
            "sessions": 2,
            "tenant_platform_settings": 1,
            "tenants": 1,
        },
    )

    async def fake_tenant_row(_pool, _tenant_id):
        return conn.row

    async def fake_audit(*_args, **kwargs):
        audits.append(kwargs)

    async def fake_get_store():
        return file_store

    monkeypatch.setattr(platform_routes, "get_pool", lambda: conn)
    monkeypatch.setattr(platform_routes, "_tenant_row", fake_tenant_row)
    monkeypatch.setattr(platform_routes, "record_platform_audit", fake_audit)
    monkeypatch.setattr(platform_routes, "get_store", fake_get_store)

    result = asyncio.run(
        platform_routes.purge_tenant(
            "tenant-1",
            platform_routes.TenantPurgeBody(reason="purge"),
            SimpleNamespace(id="platform-user-1", role="platform_admin"),
        )
    )

    assert result["ok"] is True
    assert result["fileObjectCount"] == 2
    assert file_store.deleted_keys == [
        "files/session-1/file-1/a.txt",
        "files/session-2/file-2/b.txt",
    ]
    assert result["deletedRows"]["sessionFiles"] == 2
    assert result["deletedRows"]["sessions"] == 2
    assert result["deletedRows"]["tenants"] == 1
    assert audits[0]["action"] == "tenant.purge"
    assert audits[0]["after"]["deletedRows"]["tenantPlatformSettings"] == 1


def test_purge_tenant_fails_closed_when_file_object_delete_fails(monkeypatch):
    audits = []
    conn = FakeTenantPurgeConn(
        row=platform_tenant_row(purge_requested_at=datetime.now(timezone.utc)),
        files=[{"id": "file-1", "object_key": "files/session-1/file-1/a.txt"}],
    )

    async def fake_tenant_row(_pool, _tenant_id):
        return conn.row

    async def fake_audit(*_args, **kwargs):
        audits.append(kwargs)

    async def fake_get_store():
        return FakeFileStore(fail_keys={"files/session-1/file-1/a.txt"})

    monkeypatch.setattr(platform_routes, "get_pool", lambda: conn)
    monkeypatch.setattr(platform_routes, "_tenant_row", fake_tenant_row)
    monkeypatch.setattr(platform_routes, "record_platform_audit", fake_audit)
    monkeypatch.setattr(platform_routes, "get_store", fake_get_store)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            platform_routes.purge_tenant(
                "tenant-1",
                platform_routes.TenantPurgeBody(reason="purge"),
                SimpleNamespace(id="platform-user-1", role="platform_admin"),
            )
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["reason"] == "tenant_file_object_delete_failed"
    assert not any(query.strip().startswith("DELETE FROM") for query, _args in conn.executed)
    assert audits[0]["error"] == "tenant_file_object_delete_failed"


def test_platform_audit_event_endpoint_records_deploy_audit(monkeypatch):
    captured = {}

    async def fake_record_platform_audit(pool, **kwargs):
        captured["pool"] = pool
        captured["kwargs"] = kwargs
        return "audit-1"

    monkeypatch.setattr(platform_routes, "get_pool", lambda: "pool")
    monkeypatch.setattr(platform_routes, "record_platform_audit", fake_record_platform_audit)

    result = asyncio.run(
        platform_routes.create_platform_audit_event(
            platform_routes.PlatformAuditEventBody(
                action="deploy.apply",
                targetType="deployment",
                targetId="browser-pilot-ee-saas",
                outcome="failure",
                reason="helm apply failed",
                error="exit_code_1",
            ),
            SimpleNamespace(id="platform-user-1", role="platform_operator"),
        )
    )

    assert result == {"id": "audit-1"}
    assert captured["pool"] == "pool"
    assert captured["kwargs"]["action"] == "deploy.apply"
    assert captured["kwargs"]["actor_role"] == "platform_operator"
    assert captured["kwargs"]["outcome"] == "failure"
    assert captured["kwargs"]["error"] == "exit_code_1"


def test_platform_runtime_revoke_endpoint_records_failures(monkeypatch):
    audits = []

    async def fake_tenant_row(_pool, tenant_id):
        assert tenant_id == "tenant-1"
        return {"status": "active"}

    async def fake_revoke(_tenant_id):
        return {
            "affectedSessionCount": 1,
            "failedResources": [{"sessionId": "session-1", "error": "delete failed"}],
        }

    async def fake_audit(*_args, **kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(platform_routes, "get_pool", lambda: "pool")
    monkeypatch.setattr(platform_routes, "_tenant_row", fake_tenant_row)
    monkeypatch.setattr(platform_routes, "_revoke_tenant_runtime", fake_revoke)
    monkeypatch.setattr(platform_routes, "record_platform_audit", fake_audit)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            platform_routes.revoke_tenant_runtime(
                "tenant-1",
                platform_routes.TenantRuntimeRevokeBody(reason="quota downgrade"),
                SimpleNamespace(id="platform-user-1", role="platform_operator"),
            )
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["reason"] == "runtime_revoke_failed"
    assert audits[0]["action"] == "tenant.runtime_revoke"
    assert audits[0]["outcome"] == "failure"
    assert audits[0]["error"] == "runtime_revoke_failed"
