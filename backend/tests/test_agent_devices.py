import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import agent_devices
from app.auth.dependencies import CurrentUser


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _user(user_id="user-1", role="member", *, session_scope=None, api_token_id=None):
    return CurrentUser(
        id=user_id,
        tenant_id="tenant-1",
        email=f"{user_id}@example.com",
        name=user_id,
        role=role,
        created_at="2026-05-20T00:00:00Z",
        session_scope=session_scope,
        api_token_id=api_token_id,
    )


def _lease(
    lease_id="lease-1",
    *,
    device_id="session-1",
    operator="user:user-1",
    owner="user-1",
    status="active",
    task_id=None,
    expires_at=None,
):
    now = datetime.now(timezone.utc)
    return {
        "id": lease_id,
        "device_instance_id": device_id,
        "device_type": agent_devices.DEVICE_TYPE,
        "lease_mode": "task_bound" if task_id else "session_bound",
        "task_id": task_id,
        "session_id": device_id,
        "tenant_id": "tenant-1",
        "operator_subject": operator,
        "operator_owner_user_id": owner,
        "current_operator": operator,
        "authorized_operators": [],
        "status": status,
        "expires_at": expires_at,
        "created_at": now,
        "updated_at": now,
        "released_at": None,
        "reclaimed_at": None,
        "invalidated_reason": None,
    }


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None


class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_exc):
        return None


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return FakeAcquire(self.conn)


class FakeConn:
    def __init__(self):
        self.sessions = {
            "session-1": {
                "id": "session-1",
                "tenant_id": "tenant-1",
                "user_id": "user-1",
                "name": "Session 1",
            }
        }
        self.leases = []
        self.audits = []
        self.next_lease_id = 1

    def transaction(self):
        return FakeTransaction()

    async def fetchrow(self, query, *args):
        sql = " ".join(query.lower().split())
        if "select id, tenant_id, user_id, name from sessions" in sql:
            return self.sessions.get(args[0])
        if "select tenant_id from sessions" in sql:
            row = self.sessions.get(args[0])
            return {"tenant_id": row["tenant_id"]} if row else None
        if "select tenant_id, user_id from sessions" in sql:
            row = self.sessions.get(args[0])
            return {"tenant_id": row["tenant_id"], "user_id": row["user_id"]} if row else None
        if "where id = $1 and device_instance_id = $2" in sql and "select *" in sql:
            lease_id, device_id = args
            return next((lease for lease in self.leases if lease["id"] == lease_id and lease["device_instance_id"] == device_id), None)
        if "where device_instance_id = $1" in sql and "status = 'active'" in sql and "select *" in sql:
            device_id = args[0]
            return next((lease for lease in reversed(self.leases) if lease["device_instance_id"] == device_id and lease["status"] == "active"), None)
        if "insert into agent_device_leases" in sql:
            if len(args) == 5:
                lease_id, device_id, tenant_id, operator, owner = args
                lease_mode = "session_bound"
                task_id = None
                expires_at = None
            else:
                lease_id, device_id, lease_mode, task_id, tenant_id, operator, owner, expires_at = args
            lease = _lease(
                lease_id=lease_id,
                device_id=device_id,
                operator=operator,
                owner=owner,
                task_id=task_id,
                expires_at=expires_at,
            )
            lease["lease_mode"] = lease_mode
            lease["tenant_id"] = tenant_id
            self.leases.append(lease)
            return lease
        if "update agent_device_leases set expires_at" in sql:
            lease_id, device_id, expires_at = args
            lease = next(lease for lease in self.leases if lease["id"] == lease_id and lease["device_instance_id"] == device_id)
            lease["expires_at"] = expires_at
            lease["updated_at"] = datetime.now(timezone.utc)
            return lease
        if "set status = 'released'" in sql:
            lease_id, device_id = args
            lease = next(lease for lease in self.leases if lease["id"] == lease_id and lease["device_instance_id"] == device_id)
            lease["status"] = "released"
            lease["released_at"] = datetime.now(timezone.utc)
            lease["invalidated_reason"] = "released_by_operator"
            return lease
        if "set status = 'reclaimed'" in sql:
            lease_id = args[0]
            lease = next(lease for lease in self.leases if lease["id"] == lease_id)
            lease["status"] = "reclaimed"
            lease["reclaimed_at"] = datetime.now(timezone.utc)
            lease["invalidated_reason"] = "force_reclaim"
            return lease
        return None

    async def fetch(self, *_args):
        return []

    async def execute(self, query, *args):
        sql = " ".join(query.lower().split())
        if "update agent_device_leases" in sql and "expires_at_elapsed" in sql:
            device_id = args[0]
            now = datetime.now(timezone.utc)
            for lease in self.leases:
                if lease["device_instance_id"] == device_id and lease["status"] == "active" and lease["expires_at"] and lease["expires_at"] <= now:
                    lease["status"] = "expired"
                    lease["invalidated_reason"] = "expires_at_elapsed"
            return "UPDATE"
        if "insert into agent_device_audit_events" in sql:
            self.audits.append(
                {
                    "id": args[0],
                    "actor": args[2],
                    "device_instance_id": args[4],
                    "lease_id": args[5],
                    "task_id": args[6],
                    "action": args[8],
                    "outcome": args[9],
                    "summary": args[12],
                    "error": args[15],
                }
            )
            return "INSERT"
        return "OK"


def test_acquire_rejects_active_conflict_and_audits(monkeypatch):
    conn = FakeConn()
    conn.leases.append(_lease(operator="user:user-2", owner="user-2"))
    monkeypatch.setattr(agent_devices, "get_pool", lambda: FakePool(conn))

    with pytest.raises(agent_devices.AgentDeviceLeaseError) as exc:
        asyncio.run(agent_devices.acquire_lease("session-1", _user(), lease_mode="session_bound"))

    assert exc.value.reason == "device_occupied"
    assert conn.audits[-1]["action"] == "reserve_device"
    assert conn.audits[-1]["outcome"] == "rejected"
    assert conn.audits[-1]["error"] == "device_occupied"


def test_acquire_renew_release_and_reclaim_flow(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(agent_devices, "get_pool", lambda: FakePool(conn))
    admin = _user(role="admin")

    acquired = asyncio.run(
        agent_devices.acquire_lease("session-1", admin, lease_mode="task_bound", task_id="task-1", ttl_seconds=60)
    )
    assert acquired["status"] == "active"
    assert acquired["task_id"] == "task-1"
    assert conn.audits[-1]["outcome"] == "succeeded"

    renewed = asyncio.run(agent_devices.renew_lease("session-1", acquired["lease_id"], admin, ttl_seconds=120))
    assert renewed["lease_id"] == acquired["lease_id"]
    assert renewed["expires_at"]

    released = asyncio.run(agent_devices.release_lease("session-1", acquired["lease_id"], admin))
    assert released["status"] == "released"

    conn.leases.append(_lease(lease_id="lease-other", operator="user:user-2", owner="user-2"))
    reclaimed = asyncio.run(agent_devices.reclaim_device("session-1", admin))
    assert reclaimed["reclaimedLease"]["status"] == "reclaimed"
    assert reclaimed["lease"]["status"] == "active"
    assert conn.audits[-1]["action"] == "force_reclaim"


def test_session_scoped_token_can_use_owner_session_lease(monkeypatch):
    conn = FakeConn()
    conn.leases.append(_lease(operator="user:user-1", owner="user-1"))
    monkeypatch.setattr(agent_devices, "get_pool", lambda: FakePool(conn))

    ctx = asyncio.run(
        agent_devices.require_active_lease(
            "session-1",
            _user(session_scope="session-1", api_token_id="token-1"),
            action="browser.navigate",
        )
    )

    assert ctx.actor == "token:token-1"
    assert ctx.lease["lease_id"] == "lease-1"


def test_reclaim_rejects_invalid_lease_mode(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(agent_devices, "get_pool", lambda: FakePool(conn))

    with pytest.raises(agent_devices.AgentDeviceLeaseError) as exc:
        asyncio.run(agent_devices.reclaim_device("session-1", _user(role="admin"), lease_mode="invalid"))

    assert exc.value.status_code == 422
    assert exc.value.reason == "invalid_lease_mode"


def test_begin_compatible_action_returns_legacy_safe_conflict(monkeypatch):
    lease = _lease()

    async def fake_require(*_args, **_kwargs):
        raise agent_devices.AgentDeviceLeaseError(
            "Device is occupied by another operator",
            lease=agent_devices._lease_to_dict(lease),
            reason="operator_mismatch",
            next_step="reclaim_or_wait",
        )

    async def fake_record_action_event(**_kwargs):
        return "audit-1"

    monkeypatch.setattr(agent_devices, "require_active_lease", fake_require)
    monkeypatch.setattr(agent_devices, "record_action_event", fake_record_action_event)

    ctx, rejected = asyncio.run(
        agent_devices.begin_compatible_action("session-1", _user(), action="browser.click")
    )

    assert ctx is None
    assert rejected["ok"] is False
    assert rejected["agentDevice"]["status"] == "rejected"
    assert rejected["agentDevice"]["executionStatus"] == "rejected"
    assert rejected["agentDevice"]["failureCategory"] == "operator_mismatch"
    assert rejected["agentDevice"]["auditStatus"] == "recorded"
    assert rejected["agentDevice"]["evidenceStatus"] == "not_required"
    assert rejected["agentDevice"]["stateChanged"] is False
    assert rejected["agentDevice"]["auditEventId"] == "audit-1"
    assert rejected["agentDevice"]["nextStep"] == "reclaim_or_wait"


def test_control_action_response_extends_legacy_session_agent_device_fields():
    payload = agent_devices.control_action_response(
        {"ok": True, "id": "session-1"},
        device_id="session-1",
        user=_user(),
        lease=agent_devices._lease_to_dict(_lease()) or {},
        action="reserve_device",
        status="succeeded",
        audit_event_id="audit-1",
        next_step="continue",
        state_changed=True,
    )

    agent_device = payload["agentDevice"]
    assert payload["id"] == "session-1"
    assert agent_device["deviceInstanceId"] == "session-1"
    assert agent_device["leaseId"] == "lease-1"
    assert agent_device["operator"] == "user:user-1"
    assert agent_device["status"] == "succeeded"
    assert agent_device["executionStatus"] == "succeeded"
    assert agent_device["sideEffectStatus"] == "applied"
    assert agent_device["auditStatus"] == "recorded"
    assert agent_device["evidenceStatus"] == "not_required"
    assert agent_device["stateChanged"] is True
    assert agent_device["auditEventId"] == "audit-1"


def test_visibility_and_audit_protocol_aliases():
    now = datetime.now(timezone.utc)
    visibility = agent_devices._visibility_from_row(
        {
            "id": "session-1",
            "name": "Session 1",
            "tenant_id": "tenant-1",
            "user_id": "user-1",
            "session_created_at": now,
            "session_updated_at": now,
            "lease_id": "lease-1",
            "lease_mode": "session_bound",
            "task_id": None,
            "current_operator": "user:user-1",
            "operator_owner_user_id": "user-1",
            "expires_at": now + timedelta(seconds=60),
            "lease_updated_at": now,
            "last_audit_id": "audit-1",
            "last_action": "browser.observe",
            "last_outcome": "succeeded",
            "last_side_effect_level": "none",
            "last_summary": "Observed page",
            "last_evidence_refs": [],
            "last_details": {"evidenceStatus": "not_required"},
            "last_error": None,
            "last_audit_at": now,
        },
        "running",
    )

    assert visibility["state"] == "OCCUPIED"
    assert visibility["browser_pilot_state"] == "leased"
    assert visibility["provider"] == "browser-pilot"
    assert visibility["device_profile"] == "browser"
    assert visibility["context_id"] == "tenant:tenant-1"
    assert visibility["compliance_level"] == "level1_device_governance"
    assert visibility["concurrency_model"] == "exclusive"
    assert visibility["supported_lease_modes"] == ["session_bound", "task_bound"]
    assert visibility["unsupported_profiles"] == ["control_transfer"]
    assert visibility["observable_surface_status"] == "not_required_level1"
    assert visibility["session_id"] == "session-1"
    assert visibility["lease"]["lease_id"] == "lease-1"
    assert visibility["last_action_summary"]["auditEventId"] == "audit-1"
    assert visibility["last_action_summary"]["auditStatus"] == "recorded"
    assert visibility["last_action_summary"]["evidenceStatus"] == "not_required"
    assert visibility["containerStatus"] == "running"

    audit = agent_devices._audit_to_dict(
        {
            "id": "audit-1",
            "tenant_id": "tenant-1",
            "actor": "user:user-1",
            "actor_owner_user_id": "user-1",
            "device_instance_id": "session-1",
            "lease_id": "lease-1",
            "task_id": None,
            "session_id": "session-1",
            "action": "browser.observe",
            "outcome": "succeeded",
            "side_effect_level": "none",
            "audit_boundary": "browser_pilot",
            "summary": "Observed page",
            "evidence_refs": [],
            "details": {"count": 1},
            "error": None,
            "created_at": now,
        }
    )

    assert audit["operator"] == "user:user-1"
    assert audit["status"] == "succeeded"
    assert audit["executionStatus"] == "succeeded"
    assert audit["auditStatus"] == "recorded"
    assert audit["evidenceStatus"] == "not_required"
    assert audit["occurred_at"] == now.isoformat()


def test_dead_container_maps_to_protocol_error_state():
    now = datetime.now(timezone.utc)
    visibility = agent_devices._visibility_from_row(
        {
            "id": "session-1",
            "name": "Session 1",
            "tenant_id": "tenant-1",
            "user_id": "user-1",
            "session_created_at": now,
            "session_updated_at": now,
            "lease_id": None,
            "lease_mode": None,
            "task_id": None,
            "current_operator": None,
            "operator_owner_user_id": None,
            "expires_at": None,
            "lease_updated_at": None,
            "last_audit_id": None,
            "last_action": None,
            "last_outcome": None,
            "last_side_effect_level": None,
            "last_summary": None,
            "last_evidence_refs": None,
            "last_details": None,
            "last_error": None,
            "last_audit_at": None,
        },
        "dead",
    )

    assert visibility["state"] == "ERROR"
    assert visibility["browser_pilot_state"] == "idle"


def test_complete_external_action_adds_governed_page_evidence(monkeypatch):
    captured = {}

    async def fake_record_action_event(**kwargs):
        captured.update(kwargs)
        return "audit-1"

    monkeypatch.setattr(agent_devices, "record_action_event", fake_record_action_event)

    ctx = agent_devices.AgentDeviceActionContext(
        session_id="session-1",
        action="browser.click",
        actor="user:user-1",
        actor_owner_user_id="user-1",
        lease=agent_devices._lease_to_dict(_lease()) or {},
        side_effect_level="external",
    )
    result = asyncio.run(
        agent_devices.complete_compatible_action(
            ctx,
            {"ok": True, "currentPage": {"url": "https://example.com", "title": "Example"}},
            summary="Clicked browser",
            details={"x": 10, "y": 20},
        )
    )

    assert captured["evidence_refs"] == [
        {
            "type": "browser_session",
            "ref": "browser_session:session-1:current_page",
            "session_id": "session-1",
            "surface": "current_page",
        }
    ]
    assert captured["details"]["currentPage"]["url"] == "https://example.com"
    assert captured["details"]["actionParameters"] == {"x": 10, "y": 20}
    assert captured["details"]["evidenceStatus"] == "captured"
    assert result["agentDevice"]["evidenceStatus"] == "captured"
    assert result["agentDevice"]["sideEffectStatus"] == "applied"
    assert result["agentDevice"]["stateChanged"] is True


def test_expire_active_leases_writes_system_audit():
    class ExpireConn(FakeConn):
        async def fetch(self, query, *args):
            sql = " ".join(query.lower().split())
            if "from agent_device_leases" in sql and "expires_at <= now()" in sql:
                return [
                    lease
                    for lease in self.leases
                    if lease["device_instance_id"] == args[0]
                    and lease["status"] == "active"
                    and lease["expires_at"] is not None
                    and lease["expires_at"] <= datetime.now(timezone.utc)
                ]
            return []

    conn = ExpireConn()
    conn.leases.append(_lease(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))

    asyncio.run(agent_devices._expire_active_leases(conn, "session-1"))

    assert conn.leases[0]["status"] == "expired"
    assert conn.audits[-1]["actor"] == "system:lease_expirer"
    assert conn.audits[-1]["action"] == "lease_expired"
    assert conn.audits[-1]["outcome"] == "succeeded"


def test_level1_contract_migration_revokes_ownerless_active_leases():
    migration = BACKEND_ROOT / "alembic" / "versions" / "0016_agent_device_level1_contract.py"
    text = migration.read_text()

    assert "operator_owner_user_id IS NULL" in text
    assert "status = 'revoked'" in text
    assert "ownerless_active_blocked" in text
    assert "revoke_ownerless_active_lease" in text


def test_initial_lease_cleanup_migration_revokes_only_implicit_initial_leases():
    migration = BACKEND_ROOT / "alembic" / "versions" / "0017_remove_implicit_initial_leases.py"
    text = migration.read_text()

    assert "implicit_initial_lease_removed" in text
    assert "revoke_implicit_initial_lease" in text
    assert "Initial session-bound device lease created" in text
    assert "l.id = 'lease_' || md5(l.device_instance_id || '-agent-device-initial')" in text
    assert "l.expires_at IS NULL" in text
