import asyncio
import base64

from app.auth.dependencies import CurrentUser
from app import file_service
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


def test_files_route_serves_session_file(monkeypatch):
    async def fake_get_file_payload(file_id, user):
        assert file_id == "file-1"
        assert user.session_scope == "session-1"
        return b"data", "text/plain"

    monkeypatch.setattr(file_service, "get_file_payload", fake_get_file_payload)

    response = asyncio.run(files.serve_file("file-1", "txt", _user(session_scope="session-1")))

    assert response.body == b"data"
    assert response.media_type == "text/plain"


def test_session_files_route_verifies_access_and_lists_files(monkeypatch):
    calls = {}

    async def fake_verify(session_id, user):
        calls["verify"] = (session_id, user.id)

    async def fake_list(session_id):
        calls["list"] = session_id
        return [{"id": "file-1"}]

    monkeypatch.setattr(sessions, "verify_session_access", fake_verify)
    monkeypatch.setattr(file_service, "list_session_files", fake_list)

    result = asyncio.run(sessions.list_session_files_route("session-1", _user(session_scope="session-1")))

    assert result == {"files": [{"id": "file-1"}]}
    assert calls == {"verify": ("session-1", "user-1"), "list": "session-1"}
