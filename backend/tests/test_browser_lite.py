from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from starlette.requests import Request

from app import browser_lite, runtime_provider
from app.tools.browser import session as browser_session


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
