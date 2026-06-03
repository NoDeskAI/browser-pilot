import asyncio
from types import SimpleNamespace

from app import edition


def test_edition_hooks_are_noop_when_not_ee(monkeypatch):
    def fail_load():
        raise AssertionError("EE hooks should not be loaded")

    monkeypatch.setattr(edition, "EDITION", "ce")
    monkeypatch.setattr(edition, "_load_ee_hooks", fail_load)

    async def run_hooks():
        await edition.before_session_create(object(), object())
        await edition.after_session_created(object(), "session-1", object())
        await edition.after_tenant_setup(tenant_id="tenant-1", user_id="user-1")
        await edition.before_session_runtime_start(object(), "session-1", action="test.start")
        await edition.after_session_runtime_started(object(), "session-1", action="test.start")
        await edition.after_session_runtime_start_failed(object(), "session-1", action="test.start", error=RuntimeError("x"))
        await edition.after_session_runtime_stopped(object(), "session-1", action="test.stop")
        await edition.before_viewer_ticket_issue(object(), "session-1", mode="control")
        await edition.after_viewer_stream(
            session_id="session-1",
            ticket=object(),
            outcome="succeeded",
            duration_ms=10,
            bytes_from_viewer=1,
            bytes_to_viewer=2,
            audit_event_id=None,
            error=None,
        )
        await edition.before_file_store(
            file_id="file-1",
            session_id="session-1",
            tenant_id="tenant-1",
            user_id="user-1",
            source="screenshot",
            size_bytes=3,
        )
        await edition.after_file_stored({"id": "file-1"})
        await edition.after_file_store_failed(
            file_id="file-1",
            session_id="session-1",
            tenant_id="tenant-1",
            user_id="user-1",
            source="screenshot",
            size_bytes=3,
            error=RuntimeError("x"),
        )
        await edition.after_file_deleted(file={"id": "file-1"}, object_deleted=True, record_deleted=True)

    asyncio.run(run_hooks())


def test_edition_hooks_dispatch_to_ee_hooks(monkeypatch):
    calls = []

    class FakeHooks:
        def features(self):
            calls.append(("features", (), {}))
            return {"extension": True}

        def before_session_create(self, *args, **kwargs):
            calls.append(("before_session_create", args, kwargs))

        async def after_session_created(self, *args, **kwargs):
            calls.append(("after_session_created", args, kwargs))

        async def assert_tenant_runtime_allowed(self, *args, **kwargs):
            calls.append(("assert_tenant_runtime_allowed", args, kwargs))

        async def after_tenant_setup(self, *args, **kwargs):
            calls.append(("after_tenant_setup", args, kwargs))

        async def before_session_runtime_start(self, *args, **kwargs):
            calls.append(("before_session_runtime_start", args, kwargs))

        def after_session_runtime_started(self, *args, **kwargs):
            calls.append(("after_session_runtime_started", args, kwargs))

        async def after_session_runtime_start_failed(self, *args, **kwargs):
            calls.append(("after_session_runtime_start_failed", args, kwargs))

        async def after_session_runtime_stopped(self, *args, **kwargs):
            calls.append(("after_session_runtime_stopped", args, kwargs))

        async def before_viewer_ticket_issue(self, *args, **kwargs):
            calls.append(("before_viewer_ticket_issue", args, kwargs))

        async def after_viewer_stream(self, *args, **kwargs):
            calls.append(("after_viewer_stream", args, kwargs))

        async def before_file_store(self, *args, **kwargs):
            calls.append(("before_file_store", args, kwargs))

        def after_file_stored(self, *args, **kwargs):
            calls.append(("after_file_stored", args, kwargs))

        async def after_file_store_failed(self, *args, **kwargs):
            calls.append(("after_file_store_failed", args, kwargs))

        async def after_file_deleted(self, *args, **kwargs):
            calls.append(("after_file_deleted", args, kwargs))

    user = SimpleNamespace(id="user-1", tenant_id="tenant-1")
    body = SimpleNamespace(name="Session")
    ticket = SimpleNamespace(id="ticket-1", user_id="user-1", mode="control")
    error = RuntimeError("start failed")

    monkeypatch.setattr(edition, "EDITION", "ee")
    monkeypatch.setattr(edition, "_load_ee_hooks", lambda: FakeHooks())

    async def run_hooks():
        assert edition.ee_features() == {"extension": True}
        await edition.assert_tenant_runtime_allowed("tenant-1", exclude_session_id="session-1")
        await edition.before_session_create(user, body)
        await edition.after_session_created(user, "session-1", body)
        await edition.after_tenant_setup(tenant_id="tenant-1", user_id="user-1")
        await edition.before_session_runtime_start(user, "session-1", action="session.container.start")
        await edition.after_session_runtime_started(user, "session-1", action="session.container.start")
        await edition.after_session_runtime_start_failed(user, "session-1", action="session.container.start", error=error)
        await edition.after_session_runtime_stopped(user, "session-1", action="session.container.stop")
        await edition.before_viewer_ticket_issue(user, "session-1", mode="control")
        await edition.after_viewer_stream(
            session_id="session-1",
            ticket=ticket,
            outcome="succeeded",
            duration_ms=1234,
            bytes_from_viewer=100,
            bytes_to_viewer=200,
            audit_event_id="audit-1",
            error=None,
        )
        await edition.before_file_store(
            file_id="file-1",
            session_id="session-1",
            tenant_id="tenant-1",
            user_id="user-1",
            source="user_upload",
            size_bytes=4096,
        )
        await edition.after_file_stored({"id": "file-1", "size": 4096})
        await edition.after_file_store_failed(
            file_id="file-1",
            session_id="session-1",
            tenant_id="tenant-1",
            user_id="user-1",
            source="user_upload",
            size_bytes=4096,
            error=RuntimeError("store failed"),
        )
        await edition.after_file_deleted(file={"id": "file-1"}, object_deleted=True, record_deleted=True)

    asyncio.run(run_hooks())

    call_names = [name for name, _args, _kwargs in calls]
    assert call_names == [
        "features",
        "assert_tenant_runtime_allowed",
        "before_session_create",
        "after_session_created",
        "after_tenant_setup",
        "before_session_runtime_start",
        "after_session_runtime_started",
        "after_session_runtime_start_failed",
        "after_session_runtime_stopped",
        "before_viewer_ticket_issue",
        "after_viewer_stream",
        "before_file_store",
        "after_file_stored",
        "after_file_store_failed",
        "after_file_deleted",
    ]
    setup_call = calls[4]
    assert setup_call[1] == ()
    assert setup_call[2] == {"tenant_id": "tenant-1", "user_id": "user-1"}
    runtime_call = calls[5]
    assert runtime_call[1] == (user, "session-1")
    assert runtime_call[2] == {"action": "session.container.start"}
    viewer_ticket_call = calls[9]
    assert viewer_ticket_call[1] == (user, "session-1")
    assert viewer_ticket_call[2] == {"mode": "control"}
    viewer_call = calls[10]
    assert viewer_call[1] == ()
    assert viewer_call[2] == {
        "session_id": "session-1",
        "ticket": ticket,
        "outcome": "succeeded",
        "duration_ms": 1234,
        "bytes_from_viewer": 100,
        "bytes_to_viewer": 200,
        "audit_event_id": "audit-1",
        "error": None,
    }
    file_store_call = calls[11]
    assert file_store_call[2] == {
        "file_id": "file-1",
        "session_id": "session-1",
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "source": "user_upload",
        "size_bytes": 4096,
    }
