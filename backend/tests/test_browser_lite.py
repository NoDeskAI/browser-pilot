from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse
from pathlib import Path

import pytest
from starlette.requests import Request

from app import browser_lite, runtime_provider
from app.tools.browser import session as browser_session


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _ConnectionContext:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args):
        return False


class AuthFlowPool:
    def __init__(self):
        self.executed = []
        self.exchange_row = {
            "id": "bla-test",
            "tenant_id": "tenant-1",
            "created_by": "user-1",
            "email": "user@example.com",
            "name": "User",
            "tenant_name": "Acme",
        }

    def acquire(self):
        return _ConnectionContext(self)

    def transaction(self):
        return _Transaction()

    async def execute(self, *args):
        self.executed.append(args)

    async def fetchrow(self, *_args):
        return self.exchange_row


def test_pairing_code_is_ten_digits():
    assert browser_lite.PairingBody(pairingCode="0123456789").pairingCode == "0123456789"
    with pytest.raises(ValueError):
        browser_lite.PairingBody(pairingCode="123456")
    with pytest.raises(ValueError):
        browser_lite.PairingBody(pairingCode="012345678a")


def test_pairing_rate_limit_is_scoped_by_client(monkeypatch):
    browser_lite._pairing_attempts.clear()
    monkeypatch.setattr(browser_lite, "PAIRING_RATE_LIMIT_ATTEMPTS", 2)
    request = Request({"type": "http", "client": ("192.0.2.10", 1234), "headers": []})
    browser_lite._check_pairing_rate_limit(request)
    browser_lite._check_pairing_rate_limit(request)
    with pytest.raises(browser_lite.HTTPException) as exc:
        browser_lite._check_pairing_rate_limit(request)
    assert exc.value.status_code == 429


def test_browser_lite_sso_uses_pkce_and_one_time_code(monkeypatch):
    verifier = "v" * 64
    state = "s" * 32
    request = Request({"type": "http", "client": ("192.0.2.10", 1234), "headers": []})
    auth_request = asyncio.run(browser_lite.create_auth_request(
        browser_lite.BrowserLiteAuthRequestBody(
            codeChallenge=browser_lite._pkce_challenge(verifier),
            state=state,
            displayName="Office Mac",
            platform="darwin",
            architecture="arm64",
            appVersion="0.5.17",
            chromiumVersion="140",
            capabilities=["webdriver", "task_spaces"],
        ),
        request,
    ))
    request_token = parse_qs(urlparse(auth_request["authorizePath"]).query)["request"][0]
    request_claims = browser_lite._decode_auth_token(request_token, "browser_lite_auth_request")
    assert request_claims["code_challenge"] == browser_lite._pkce_challenge(verifier)
    assert request_claims["node"]["displayName"] == "Office Mac"

    pool = AuthFlowPool()
    monkeypatch.setattr(browser_lite, "get_pool", lambda: pool)
    user = browser_lite.CurrentUser(
        id="user-1", tenant_id="tenant-1", email="user@example.com", name="User",
        role="admin", created_at="2026-08-31T00:00:00+00:00",
    )
    authorization = asyncio.run(browser_lite.authorize_auth_request(
        browser_lite.BrowserLiteAuthorizeBody(requestToken=request_token),
        user,
    ))
    callback = urlparse(authorization["deepLink"])
    callback_query = parse_qs(callback.query)
    assert callback.scheme == "browserlite"
    assert callback.netloc == "auth"
    assert callback.path == "/callback"
    assert callback_query["state"] == [state]

    authorization_code = callback_query["code"][0]
    code_claims = browser_lite._decode_auth_token(authorization_code, "browser_lite_authorization_code")
    pool.exchange_row["id"] = code_claims["jti"]
    exchanged = asyncio.run(browser_lite.exchange_auth_code(
        browser_lite.BrowserLiteTokenBody(
            authorizationCode=authorization_code,
            codeVerifier=verifier,
        )
    ))
    assert exchanged["nodeId"].startswith("bln_")
    assert exchanged["token"]
    assert exchanged["displayName"] == "Office Mac"
    assert exchanged["account"] == {
        "email": "user@example.com", "name": "User", "tenantName": "Acme",
    }
    assert any("SET used_at = NOW()" in call[0] for call in pool.executed)


def test_browser_lite_sso_rejects_wrong_pkce_before_database(monkeypatch):
    authorization_code = browser_lite._encode_auth_token(
        "browser_lite_authorization_code",
        {
            "jti": "bla-test",
            "code_challenge": browser_lite._pkce_challenge("v" * 64),
            "node": {"displayName": "Office Mac"},
        },
        60,
    )
    monkeypatch.setattr(browser_lite, "get_pool", lambda: (_ for _ in ()).throw(AssertionError("DB must not be read")))
    with pytest.raises(browser_lite.HTTPException) as exc:
        asyncio.run(browser_lite.exchange_auth_code(
            browser_lite.BrowserLiteTokenBody(
                authorizationCode=authorization_code,
                codeVerifier="x" * 64,
            )
        ))
    assert exc.value.status_code == 401
    assert exc.value.detail == "Browser Lite PKCE verification failed"


def test_wd_fetch_routes_browser_lite_without_http_client(monkeypatch):
    captured = {}

    async def fake_request(session_id, path, method="GET", body=None, *, timeout=30):
        captured.update(
            session_id=session_id,
            path=path,
            method=method,
            body=body,
            timeout=timeout,
        )
        return {"status": 200, "headers": {}, "data": {"value": {"ok": True}}}

    monkeypatch.setattr(browser_lite, "webdriver_request", fake_request)
    result = asyncio.run(
        browser_session.wd_fetch(
            "/session/wd/url",
            "POST",
            {"url": "https://example.com"},
            timeout=7,
            base_url="browser_lite://session-1",
        )
    )

    assert result == {"ok": True}
    assert captured == {
        "session_id": "session-1",
        "path": "/session/wd/url",
        "method": "POST",
        "body": {"url": "https://example.com"},
        "timeout": 7,
    }


def test_browser_lite_runtime_does_not_expose_remote_shell(monkeypatch):
    async def is_lite(_session_id):
        return True

    monkeypatch.setattr(runtime_provider, "_session_uses_browser_lite", is_lite)
    with pytest.raises(runtime_provider.RuntimeProviderError, match="does not expose a remote shell"):
        asyncio.run(runtime_provider.exec_in_container("session-1", "id"))


def test_browser_lite_recreate_forwards_only_window_size(monkeypatch):
    captured = []

    async def is_lite(_session_id):
        return True

    async def command(session_id, action, *, timeout=45, **payload):
        captured.append((session_id, action, timeout, payload))

    monkeypatch.setattr(runtime_provider, "_session_uses_browser_lite", is_lite)
    monkeypatch.setattr(runtime_provider, "_browser_lite_command", command)
    asyncio.run(
        runtime_provider.recreate_container(
            "session-1",
            width=1440,
            height=900,
            proxy="http://must-not-reach-node",
            fingerprint_profile={"must": "not-reach-node"},
        )
    )

    assert captured == [
        ("session-1", "stop", 45, {}),
        ("session-1", "ensure", 45, {"options": {"width": 1440, "height": 900}}),
    ]


def test_browser_lite_session_creation_uses_remote_webdriver(monkeypatch):
    calls = []

    async def fake_wd_fetch(path, method="GET", body=None, timeout=30.0, *, base_url=""):
        calls.append((path, method, body, timeout, base_url))
        if path == "/status":
            return {"nodes": []}
        return {
            "sessionId": "browser-lite-session-1",
            "capabilities": {"browserName": "chrome"},
        }

    monkeypatch.setattr(browser_session, "wd_fetch", fake_wd_fetch)
    state = browser_session.BrowserSession(selenium_base="browser_lite://session-1")
    session_id = asyncio.run(browser_session._ensure_session_impl(state))

    assert session_id == "browser-lite-session-1"
    assert calls[0][0:2] == ("/status", "GET")
    assert calls[1][0:2] == ("/session", "POST")
    assert calls[1][4] == "browser_lite://session-1"


def test_webdriver_request_rejects_node_non_json(monkeypatch):
    async def fake_command(*_args, **_kwargs):
        return {"status": 502, "headers": {"content-type": "text/html"}, "body": "bad gateway"}

    monkeypatch.setattr(browser_lite, "session_command", fake_command)
    with pytest.raises(RuntimeError, match="non-JSON"):
        asyncio.run(browser_lite.webdriver_request("session-1", "/status"))


def test_cli_exposes_browser_lite_node_selection_and_rejects_egress_mix():
    template = (Path(__file__).parents[1] / "app" / "cli_template.sh").read_text()
    assert "browser-lite nodes" in template
    assert "--browser-lite-node <id>" in template
    assert "standard_chrome|cloak_chromium|browser_lite" in template
    assert "Browser Lite uses the Mac node network; --network-egress is not supported." in template


def test_node_agent_reconnect_refreshes_task_space_capability():
    source = (Path(__file__).parents[2] / "services" / "browser-lite-app" / "src" / "node-agent.mjs").read_text()
    assert '"task_spaces"' in source
    assert 'type: "hello"' in source
    assert "capabilities: NODE_CAPABILITIES" in source


def test_task_space_command_uses_allowlisted_node_protocol(monkeypatch):
    captured = {}

    async def command(session_id, action, *, payload=None, timeout=45):
        captured.update(session_id=session_id, action=action, payload=payload, timeout=timeout)
        return {"taskSpaces": []}

    monkeypatch.setattr(browser_lite, "session_command", command)
    result = asyncio.run(browser_lite.task_space_command("session-1", "listTaskSpaces", timeout=7))
    assert result == {"taskSpaces": []}
    assert captured == {
        "session_id": "session-1",
        "action": "task_space",
        "payload": {"method": "listTaskSpaces", "args": []},
        "timeout": 7,
    }
    with pytest.raises(ValueError, match="Unknown Browser Lite task-space method"):
        asyncio.run(browser_lite.task_space_command("session-1", "evaluateArbitraryCode"))


def test_task_space_api_preserves_user_control_hard_stop(monkeypatch):
    async def node(_session_id):
        return {"tenant_id": "tenant-1"}

    async def command(*_args, **_kwargs):
        raise browser_lite.BrowserLiteNodeError(
            "The task is under user control.", error_code="EGO_TASK_SPACE_USER_IN_CONTROL"
        )

    monkeypatch.setattr(browser_lite, "session_node", node)
    monkeypatch.setattr(browser_lite, "task_space_command", command)
    user = type("User", (), {"tenant_id": "tenant-1"})()
    with pytest.raises(browser_lite.HTTPException) as exc:
        asyncio.run(browser_lite.task_space_api(
            browser_lite.TaskSpaceBody(sessionId="session-1", method="snapshot"), user
        ))
    assert exc.value.status_code == 409
    assert exc.value.detail["errorCode"] == "EGO_TASK_SPACE_USER_IN_CONTROL"
