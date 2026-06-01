import asyncio
import base64
import inspect
import io
import time
from datetime import datetime, timedelta, timezone

from app.auth.dependencies import CurrentUser
from fastapi import UploadFile
import pytest

from app import file_service
from app import file_capture
from app.file_urls import sign_file_download
from app.main import app
from app.routes import browser, files, sessions


def _user(session_scope=None):
    return CurrentUser(
        id="user-1",
        tenant_id="tenant-1",
        email="user@example.com",
        name="User",
        role="admin",
        created_at="2026-05-12T00:00:00Z",
        session_scope=session_scope,
    )


class FakeBrowserSession:
    async def __aenter__(self):
        return "wd-session-1", "http://selenium"

    async def __aexit__(self, *_exc):
        return None


@pytest.fixture(autouse=True)
def stub_agent_device_governance(monkeypatch):
    class FakeActionContext:
        session_id = "session-1"
        action = "test"

    async def fake_begin_compatible_action(*_args, **kwargs):
        ctx = FakeActionContext()
        ctx.action = kwargs.get("action", "test")
        return ctx, None

    async def fake_complete_compatible_action(_ctx, response, **_kwargs):
        return response

    async def fake_fail_compatible_action(_ctx, error, **_kwargs):
        return {"ok": False, "error": error}

    async def fake_record_runtime_action(*_args, **_kwargs):
        return "audit-1"

    monkeypatch.setattr(browser.agent_devices, "begin_compatible_action", fake_begin_compatible_action)
    monkeypatch.setattr(browser.agent_devices, "complete_compatible_action", fake_complete_compatible_action)
    monkeypatch.setattr(browser.agent_devices, "fail_compatible_action", fake_fail_compatible_action)
    monkeypatch.setattr(browser.agent_devices, "record_runtime_action", fake_record_runtime_action)


def test_screenshot_stores_file_and_keeps_base64_compat(monkeypatch):
    raw = b"png-bytes"
    captured = {}

    async def fake_verify(*_args, **_kwargs):
        return None

    async def fake_wd_fetch(*_args, **_kwargs):
        return base64.b64encode(raw).decode()

    async def fake_save_bytes(**kwargs):
        captured.update(kwargs)
        return {"id": "file-1", "url": "http://localhost:8000/api/files/file-1.png"}

    monkeypatch.setattr(browser, "verify_session_access", fake_verify)
    monkeypatch.setattr(browser, "browser_session", lambda _session_id: FakeBrowserSession())
    monkeypatch.setattr(browser, "wd_fetch", fake_wd_fetch)
    monkeypatch.setattr(file_service, "save_bytes", fake_save_bytes)

    result = asyncio.run(browser.api_screenshot("session-1", includeBase64=True, user=_user()))

    assert result["ok"] is True
    assert result["screenshot"] == base64.b64encode(raw).decode()
    assert result["file"]["id"] == "file-1"
    assert captured["session_id"] == "session-1"
    assert captured["source"] == "screenshot"
    assert captured["data"] == raw
    assert captured["content_type"] == "image/png"


def test_screenshot_default_returns_file_only(monkeypatch):
    raw = b"png-bytes"

    async def fake_verify(*_args, **_kwargs):
        return None

    async def fake_wd_fetch(*_args, **_kwargs):
        return base64.b64encode(raw).decode()

    async def fake_save_bytes(**_kwargs):
        return {"id": "file-1", "url": "http://localhost:8000/api/files/file-1.png?expires=1&signature=sig"}

    monkeypatch.setattr(browser, "verify_session_access", fake_verify)
    monkeypatch.setattr(browser, "browser_session", lambda _session_id: FakeBrowserSession())
    monkeypatch.setattr(browser, "wd_fetch", fake_wd_fetch)
    monkeypatch.setattr(file_service, "save_bytes", fake_save_bytes)

    result = asyncio.run(browser.api_screenshot("session-1", user=_user()))

    assert result["ok"] is True
    assert result["screenshot"] is None
    assert result["file"]["id"] == "file-1"


def test_screenshot_include_base64_false_returns_file_only(monkeypatch):
    raw = b"png-bytes"

    async def fake_verify(*_args, **_kwargs):
        return None

    async def fake_wd_fetch(*_args, **_kwargs):
        return base64.b64encode(raw).decode()

    async def fake_save_bytes(**_kwargs):
        return {"id": "file-1", "url": "http://localhost:8000/api/files/file-1.png"}

    monkeypatch.setattr(browser, "verify_session_access", fake_verify)
    monkeypatch.setattr(browser, "browser_session", lambda _session_id: FakeBrowserSession())
    monkeypatch.setattr(browser, "wd_fetch", fake_wd_fetch)
    monkeypatch.setattr(file_service, "save_bytes", fake_save_bytes)

    result = asyncio.run(browser.api_screenshot("session-1", includeBase64=False, user=_user()))

    assert result == {
        "ok": True,
        "file": {"id": "file-1", "url": "http://localhost:8000/api/files/file-1.png"},
        "screenshot": None,
    }


def test_screenshot_openapi_does_not_expose_store_parameter():
    names = {
        parameter["name"]
        for parameter in app.openapi()["paths"]["/api/browser/screenshot"]["get"]["parameters"]
    }
    assert "store" not in names
    assert "store" not in inspect.signature(browser.api_screenshot).parameters


def test_files_route_serves_session_file(monkeypatch):
    async def fake_get_file_payload(file_id, user):
        assert file_id == "file-1"
        assert user.session_scope == "session-1"
        return b"data", "text/plain"

    monkeypatch.setattr(file_service, "get_file_payload", fake_get_file_payload)

    response = asyncio.run(files.serve_file("file-1", "txt", user=_user(session_scope="session-1")))

    assert response.body == b"data"
    assert response.media_type == "text/plain"


def test_files_route_serves_signed_file_without_auth(monkeypatch):
    expires = int(time.time()) + 900
    signature = sign_file_download("file-1", "txt", expires)

    async def fake_get_signed_file_payload(file_id):
        assert file_id == "file-1"
        return b"data", "text/plain"

    monkeypatch.setattr(file_service, "get_signed_file_payload", fake_get_signed_file_payload)

    response = asyncio.run(
        files.serve_file("file-1", "txt", expires=expires, signature=signature, user=None)
    )

    assert response.body == b"data"
    assert response.media_type == "text/plain"


def test_files_route_rejects_invalid_signed_file_url(monkeypatch):
    expires = int(time.time()) + 900

    with pytest.raises(files.HTTPException) as exc:
        asyncio.run(
            files.serve_file("file-1", "txt", expires=expires, signature="bad", user=None)
        )

    assert exc.value.status_code == 403


def test_session_files_route_verifies_access_and_lists_files(monkeypatch):
    calls = {}

    async def fake_verify(session_id, user):
        calls["verify"] = (session_id, user.id)

    async def fake_list(session_id):
        calls["list"] = session_id
        return [{"id": "file-1"}]

    monkeypatch.setattr(sessions, "verify_session_access", fake_verify)
    monkeypatch.setattr(file_service, "list_session_files", fake_list)

    result = asyncio.run(sessions.list_session_files_route("session-1", user=_user(session_scope="session-1")))

    assert result == {"files": [{"id": "file-1"}]}
    assert calls == {"verify": ("session-1", "user-1"), "list": "session-1"}


def test_session_files_route_is_not_lease_gated(monkeypatch):
    async def fake_verify(_session_id, _user):
        return None

    async def fail_begin_action(*_args, **_kwargs):
        raise AssertionError("listing session files must not require an active lease")

    async def fake_list(_session_id):
        return [{"id": "file-1"}]

    monkeypatch.setattr(sessions, "verify_session_access", fake_verify)
    monkeypatch.setattr(sessions.agent_devices, "begin_compatible_action", fail_begin_action)
    monkeypatch.setattr(file_service, "list_session_files", fake_list)

    result = asyncio.run(sessions.list_session_files_route("session-1", user=_user()))

    assert result == {"files": [{"id": "file-1"}]}


def test_active_lease_payload_identifies_api_token_operator():
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=60)
    payload = sessions._active_lease_payload_from_row({
        "lease_id": "lease-1",
        "lease_mode": "session_bound",
        "lease_task_id": None,
        "current_operator": "token:token-1",
        "operator_owner_user_id": "user-1",
        "lease_token_name": "QA Agent",
        "lease_owner_name": "User One",
        "lease_owner_email": "user@example.com",
        "lease_expires_at": expires_at,
        "lease_updated_at": None,
    })

    assert payload == {
        "id": "lease-1",
        "leaseId": "lease-1",
        "leaseMode": "session_bound",
        "taskId": None,
        "currentOperator": "token:token-1",
        "operatorOwnerUserId": "user-1",
        "operatorType": "api_token",
        "operatorName": "QA Agent",
        "expiresAt": expires_at.isoformat(),
        "updatedAt": None,
    }


def test_upload_session_file_verifies_access_and_saves_user_upload(monkeypatch):
    captured = {}

    async def fake_verify(session_id, user):
        captured["verify"] = (session_id, user.id, user.session_scope)

    async def fake_save_file(**kwargs):
        captured["save"] = kwargs
        captured["bytes"] = kwargs["path"].read_bytes()
        return {
            "id": "file-1",
            "name": kwargs["filename"],
            "status": "completed",
            "source": "user_upload",
            "url": "http://localhost:8000/api/files/file-1.txt",
        }

    monkeypatch.setattr(files, "verify_session_access", fake_verify)
    monkeypatch.setattr(file_service, "save_file", fake_save_file)

    upload = UploadFile(filename="ignored.txt", file=io.BytesIO(b"uploaded"))
    result = asyncio.run(
        files.upload_session_file(
            "session-1",
            file=upload,
            originalName="report.txt",
            user=_user(session_scope="session-1"),
        )
    )

    assert result["ok"] is True
    assert result["file"]["source"] == "user_upload"
    assert captured["verify"] == ("session-1", "user-1", "session-1")
    assert captured["save"]["session_id"] == "session-1"
    assert captured["save"]["source"] == "user_upload"
    assert captured["save"]["filename"] == "report.txt"
    assert captured["save"].get("source_id") is None
    assert captured["save"].get("source_path") is None
    assert captured["bytes"] == b"uploaded"


def test_session_file_management_routes_verify_access(monkeypatch):
    captured = {}

    async def fake_verify(session_id, user):
        captured.setdefault("verify", []).append((session_id, user.id))

    async def fake_get(session_id, file_id):
        captured["get"] = (session_id, file_id)
        return {"id": file_id, "name": "a.txt"}

    async def fake_rename(session_id, file_id, name):
        captured["rename"] = (session_id, file_id, name)
        return {"id": file_id, "name": name}

    async def fake_delete(session_id, file_id):
        captured["delete"] = (session_id, file_id)
        return {"ok": True, "objectDeleted": True, "recordDeleted": True, "warning": None}

    monkeypatch.setattr(files, "verify_session_access", fake_verify)
    monkeypatch.setattr(file_service, "get_session_file", fake_get)
    monkeypatch.setattr(file_service, "rename_session_file", fake_rename)
    monkeypatch.setattr(file_service, "delete_session_file", fake_delete)

    user = _user(session_scope="session-1")
    get_result = asyncio.run(files.get_session_file_route("session-1", "file-1", user=user))
    rename_result = asyncio.run(
        files.rename_session_file_route(
            "session-1",
            "file-1",
            files.RenameSessionFileBody(name="renamed.txt"),
            user=user,
        )
    )
    delete_result = asyncio.run(files.delete_session_file_route("session-1", "file-1", user=user))

    assert get_result == {"file": {"id": "file-1", "name": "a.txt"}}
    assert rename_result == {"ok": True, "file": {"id": "file-1", "name": "renamed.txt"}}
    assert delete_result == {"ok": True, "objectDeleted": True, "recordDeleted": True, "warning": None}
    assert captured["verify"] == [
        ("session-1", "user-1"),
        ("session-1", "user-1"),
        ("session-1", "user-1"),
    ]
    assert captured["get"] == ("session-1", "file-1")
    assert captured["rename"] == ("session-1", "file-1", "renamed.txt")
    assert captured["delete"] == ("session-1", "file-1")


def test_global_file_routes_use_user_level_file_service(monkeypatch):
    captured = {}

    async def fake_list(user):
        captured["list"] = (user.id, user.session_scope)
        return [{"id": "file-1"}]

    async def fake_rename(file_id, user, name):
        captured["rename"] = (file_id, user.id, name)
        return {"id": file_id, "name": name}

    async def fake_delete(file_id, user):
        captured["delete"] = (file_id, user.id)
        return {"ok": True, "objectDeleted": True, "recordDeleted": True, "warning": None}

    monkeypatch.setattr(file_service, "list_global_files", fake_list)
    monkeypatch.setattr(file_service, "rename_global_file", fake_rename)
    monkeypatch.setattr(file_service, "delete_global_file", fake_delete)

    user = _user()
    list_result = asyncio.run(files.list_files_route(user=user))
    rename_result = asyncio.run(
        files.rename_file_route("file-1", files.RenameSessionFileBody(name="renamed.txt"), user=user)
    )
    delete_result = asyncio.run(files.delete_file_route("file-1", user=user))

    assert list_result == {"files": [{"id": "file-1"}]}
    assert rename_result == {"ok": True, "file": {"id": "file-1", "name": "renamed.txt"}}
    assert delete_result == {"ok": True, "objectDeleted": True, "recordDeleted": True, "warning": None}
    assert captured == {
        "list": ("user-1", None),
        "rename": ("file-1", "user-1", "renamed.txt"),
        "delete": ("file-1", "user-1"),
    }


def test_delete_session_passes_file_selection_and_hard_deletes(monkeypatch):
    calls = []

    class FakePool:
        async def fetchrow(self, query, *args):
            if "FROM sessions WHERE id = $1" in query:
                return {"tenant_id": "tenant-1", "user_id": "user-1"}
            return None

        async def execute(self, query, *args):
            calls.append(("execute", query.strip(), args))
            return "DELETE 1"

    async def fake_handle(session_id, user, *, file_delete_mode, delete_file_ids):
        calls.append(("files", session_id, user.id, file_delete_mode, delete_file_ids))
        return {"mode": file_delete_mode, "deletedFileIds": delete_file_ids}

    async def fake_stop(session_id):
        calls.append(("stop_watcher", session_id))

    async def fake_remove(session_id):
        calls.append(("remove_container", session_id))

    async def fail_begin_action(*_args, **_kwargs):
        raise AssertionError("deleting a session must not require an active device lease")

    monkeypatch.setattr(sessions, "get_pool", lambda: FakePool())
    monkeypatch.setattr(sessions.agent_devices, "begin_compatible_action", fail_begin_action)
    monkeypatch.setattr(file_service, "handle_session_delete_files", fake_handle)
    monkeypatch.setattr(sessions, "stop_download_watcher", fake_stop)
    monkeypatch.setattr(sessions, "remove_container", fake_remove)

    result = asyncio.run(
        sessions.delete_session(
            "session-1",
            body=sessions.DeleteSessionBody(fileDeleteMode="selected", deleteFileIds=["file-1"]),
            user=_user(),
        )
    )

    assert result == {"ok": True, "files": {"mode": "selected", "deletedFileIds": ["file-1"]}}
    assert calls[0] == ("files", "session-1", "user-1", "selected", ["file-1"])
    assert calls[1:] == [
        ("stop_watcher", "session-1"),
        ("remove_container", "session-1"),
        ("execute", "DELETE FROM sessions WHERE id = $1", ("session-1",)),
    ]


def test_session_file_upload_rejects_cross_session_token(monkeypatch):
    async def fake_verify(_session_id, _user):
        raise files.HTTPException(403, "Token not authorized for this session")

    monkeypatch.setattr(files, "verify_session_access", fake_verify)

    upload = UploadFile(filename="report.txt", file=io.BytesIO(b"uploaded"))
    with pytest.raises(files.HTTPException) as exc:
        asyncio.run(
            files.upload_session_file(
                "session-1",
                file=upload,
                user=_user(session_scope="session-2"),
            )
        )

    assert exc.value.status_code == 403


def test_ingest_route_verifies_runtime_token_and_saves_file(monkeypatch):
    captured = {}

    async def fake_verify(session_id, raw_token):
        captured["verify"] = (session_id, raw_token)
        return {"tenant_id": "tenant-1"}

    async def fake_save_file(**kwargs):
        captured["save"] = kwargs
        return {"id": "file-1", "url": "http://localhost:8000/api/files/file-1.txt"}

    async def fake_heartbeat(session_id, status="running", error=None):
        captured["heartbeat"] = (session_id, status, error)

    monkeypatch.setattr(file_capture, "verify_file_capture_token", fake_verify)
    monkeypatch.setattr(file_capture, "heartbeat_file_capture", fake_heartbeat)
    monkeypatch.setattr(file_service, "save_file", fake_save_file)

    upload = UploadFile(filename="ignored.txt", file=io.BytesIO(b"downloaded"))
    result = asyncio.run(
        files.ingest_session_file(
            "session-1",
            file=upload,
            source="browser_download",
            sourceId="guid-1",
            originalName="report.txt",
            contentType="text/plain",
            sizeBytes=len(b"downloaded"),
            sourcePath="/home/seluser/Downloads/report.txt",
            sourceMtime=123.0,
            sha256="b7a8a844a613be796bc1892dc480f9d92c50d32a5713a87758e5c5addc4ec814",
            authorization="Bearer bpr_token",
        )
    )

    assert result == {"ok": True, "file": {"id": "file-1", "url": "http://localhost:8000/api/files/file-1.txt"}}
    assert captured["verify"] == ("session-1", "bpr_token")
    assert captured["save"]["session_id"] == "session-1"
    assert captured["save"]["source"] == "browser_download"
    assert captured["save"]["filename"] == "report.txt"
    assert captured["save"]["content_type"] == "text/plain"
    assert captured["save"]["source_id"] == "guid-1"
    assert captured["save"]["source_path"] == "/home/seluser/Downloads/report.txt"
    assert captured["save"]["source_mtime"] == 123.0
    assert captured["save"]["sha256"] == "b7a8a844a613be796bc1892dc480f9d92c50d32a5713a87758e5c5addc4ec814"
    assert captured["heartbeat"] == ("session-1", "running", None)


def test_heartbeat_route_accepts_active_download_snapshot(monkeypatch):
    captured = {}

    async def fake_verify(session_id, raw_token):
        captured["verify"] = (session_id, raw_token)
        return {"tenant_id": "tenant-1"}

    async def fake_heartbeat(session_id, status="running", error=None, downloads=None):
        captured["heartbeat"] = (session_id, status, error, downloads)

    monkeypatch.setattr(file_capture, "verify_file_capture_token", fake_verify)
    monkeypatch.setattr(file_capture, "heartbeat_file_capture", fake_heartbeat)

    result = asyncio.run(
        files.heartbeat_file_capture_route(
            "session-1",
            body=files.FileCaptureHeartbeat(
                status="running",
                downloads=[{"id": "guid-1", "name": "report.pdf"}],
            ),
            authorization="Bearer bpr_token",
        )
    )

    assert result == {"ok": True}
    assert captured["verify"] == ("session-1", "bpr_token")
    assert captured["heartbeat"] == ("session-1", "running", "", [{"id": "guid-1", "name": "report.pdf"}])


def test_ingest_route_rejects_bad_runtime_token(monkeypatch):
    captured = {}

    async def fake_verify(_session_id, _raw_token):
        raise file_capture.HTTPException(401, "Invalid runtime token")

    async def fake_record_runtime_action(session_id, **kwargs):
        captured["audit"] = (session_id, kwargs)
        return "audit-1"

    monkeypatch.setattr(file_capture, "verify_file_capture_token", fake_verify)
    monkeypatch.setattr(files.agent_devices, "record_runtime_action", fake_record_runtime_action)

    upload = UploadFile(filename="report.txt", file=io.BytesIO(b"downloaded"))
    with pytest.raises(file_capture.HTTPException) as exc:
        asyncio.run(
            files.ingest_session_file(
                "session-1",
                file=upload,
                authorization="Bearer wrong",
            )
        )

    assert exc.value.status_code == 401
    assert captured["audit"][0] == "session-1"
    assert captured["audit"][1]["action"] == "session.files.ingest"
    assert captured["audit"][1]["outcome"] == "rejected"
    assert captured["audit"][1]["error"] == "invalid_runtime_token"


def test_heartbeat_route_rejects_bad_runtime_token_and_audits(monkeypatch):
    captured = {}

    async def fake_verify(_session_id, _raw_token):
        raise file_capture.HTTPException(401, "Invalid runtime token")

    async def fake_record_runtime_action(session_id, **kwargs):
        captured["audit"] = (session_id, kwargs)
        return "audit-1"

    monkeypatch.setattr(file_capture, "verify_file_capture_token", fake_verify)
    monkeypatch.setattr(files.agent_devices, "record_runtime_action", fake_record_runtime_action)

    with pytest.raises(file_capture.HTTPException) as exc:
        asyncio.run(
            files.heartbeat_file_capture_route(
                "session-1",
                body=files.FileCaptureHeartbeat(status="running"),
                authorization="Bearer wrong",
            )
        )

    assert exc.value.status_code == 401
    assert captured["audit"][0] == "session-1"
    assert captured["audit"][1]["action"] == "session.files.heartbeat"
    assert captured["audit"][1]["outcome"] == "rejected"
    assert captured["audit"][1]["error"] == "invalid_runtime_token"
