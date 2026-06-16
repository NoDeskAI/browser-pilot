import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app import container
from app.tools.browser import session as browser_session


@pytest.fixture(autouse=True)
def clear_webdriver_client():
    browser_session._client = None
    yield
    client = browser_session._client
    browser_session._client = None
    if client and not client.is_closed:
        asyncio.run(client.aclose())


def test_webdriver_client_ignores_proxy_environment(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")

    client = browser_session._get_client()

    assert getattr(client, "_trust_env") is False


def test_wd_fetch_reports_non_json_response_with_http_context():
    async def run():
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(
                502,
                headers={"content-type": "text/html"},
                text="",
            )
        )
        browser_session._client = httpx.AsyncClient(transport=transport, trust_env=False)

        with pytest.raises(RuntimeError) as exc:
            await browser_session.wd_fetch(
                "/session/wd-1/window/handles",
                base_url="http://selenium.local",
            )

        message = str(exc.value)
        assert "WebDriver returned non-JSON response for /session/wd-1/window/handles" in message
        assert "HTTP 502" in message
        assert "content-type=text/html" in message
        assert "body=<empty>" in message

    asyncio.run(run())


def test_webdriver_session_creation_reports_non_json_response():
    async def run():
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/status":
                return httpx.Response(200, json={"value": {"nodes": []}})
            if request.url.path == "/session":
                return httpx.Response(
                    200,
                    headers={"content-type": "text/html"},
                    text="<html>proxy error</html>",
                )
            raise AssertionError(f"unexpected request: {request.url}")

        browser_session._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            trust_env=False,
        )
        state = browser_session.BrowserSession(selenium_base="http://selenium.local")

        with pytest.raises(RuntimeError) as exc:
            await browser_session._ensure_session_impl(state)

        message = str(exc.value)
        assert "WebDriver returned non-JSON response for /session" in message
        assert "HTTP 200" in message
        assert "body=<html>proxy error</html>" in message

    asyncio.run(run())


def test_grid_ready_probe_ignores_proxy_environment(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, _url):
            return _JsonResponse({"value": {"ready": True}})

    class _JsonResponse:
        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

    monkeypatch.setattr(container.httpx, "AsyncClient", FakeClient)

    asyncio.run(container._wait_grid_ready(4444))

    assert captured["trust_env"] is False


def test_quick_observe_falls_back_to_current_page_when_dom_collection_fails(monkeypatch):
    calls = []

    async def fake_wd_fetch(url_path, method="GET", body=None, timeout=30.0, *, base_url=""):
        calls.append((url_path, method, timeout, base_url))
        if url_path.endswith("/execute/sync"):
            raise RuntimeError("WebDriver Error: Page.evaluate: Target crashed ")
        if url_path.endswith("/url"):
            return "https://www.xiaohongshu.com/explore"
        if url_path.endswith("/title"):
            return "小红书"
        raise AssertionError(f"unexpected WebDriver call: {url_path}")

    monkeypatch.setattr(browser_session, "wd_fetch", fake_wd_fetch)

    result = asyncio.run(browser_session.quick_observe("wd-1", base_url="http://selenium.local"))

    assert result == {
        "url": "https://www.xiaohongshu.com/explore",
        "title": "小红书",
        "elementCount": 0,
        "observeFailed": True,
    }
    assert calls[0][0] == "/session/wd-1/execute/sync"
    assert calls[1][0] == "/session/wd-1/url"
    assert calls[2][0] == "/session/wd-1/title"


def test_browser_session_operation_retries_transient_webdriver_error(monkeypatch):
    calls = []
    resets = []

    class FakeBrowserSession:
        async def __aenter__(self):
            return "wd-1", "http://selenium.local"

        async def __aexit__(self, *_exc):
            return None

    def fake_browser_session(_session_id):
        return FakeBrowserSession()

    async def fake_reset(session_id):
        resets.append(session_id)

    async def operation(sid, base):
        calls.append((sid, base))
        if len(calls) == 1:
            raise RuntimeError("WebDriver request failed: java.lang.InterruptedException")
        return {"ok": True}

    monkeypatch.setattr(browser_session, "browser_session", fake_browser_session)
    monkeypatch.setattr(browser_session, "reset_browser_session", fake_reset)

    result = asyncio.run(
        browser_session.run_browser_session_operation(
            "session-1",
            operation,
            operation_name="browser.current",
        )
    )

    assert result == {"ok": True}
    assert calls == [
        ("wd-1", "http://selenium.local"),
        ("wd-1", "http://selenium.local"),
    ]
    assert resets == ["session-1"]


def test_browser_session_operation_recreates_runtime_after_repeated_transient_error(monkeypatch):
    calls = []
    resets = []
    removes = []
    ensures = []
    lifecycle_actions = []

    class FakeBrowserSession:
        async def __aenter__(self):
            return "wd-1", "http://selenium.local"

        async def __aexit__(self, *_exc):
            return None

    def fake_browser_session(_session_id):
        return FakeBrowserSession()

    async def fake_reset(session_id):
        resets.append(session_id)

    async def fake_session_runtime_actor(session_id):
        return SimpleNamespace(id="user-1", tenant_id="tenant-1")

    async def fake_assert_tenant_runtime_allowed(tenant_id, *, exclude_session_id=None):
        lifecycle_actions.append(("assert", tenant_id, exclude_session_id))

    async def fake_before(_actor, session_id, *, action):
        lifecycle_actions.append(("before", session_id, action))

    async def fake_after_started(_actor, session_id, *, action):
        lifecycle_actions.append(("started", session_id, action))

    async def fake_after_failed(_actor, session_id, *, action, error):
        lifecycle_actions.append(("failed", session_id, action, str(error)))

    async def fake_remove_container(session_id, *, keep_volume=False):
        removes.append((session_id, keep_volume))

    async def fake_ensure_container_running(session_id):
        ensures.append(session_id)
        return {}

    async def operation(sid, base):
        calls.append((sid, base))
        if len(calls) <= 2:
            raise RuntimeError("WebDriver request failed: java.lang.InterruptedException")
        return {"ok": True}

    monkeypatch.setattr(browser_session, "browser_session", fake_browser_session)
    monkeypatch.setattr(browser_session, "reset_browser_session", fake_reset)
    monkeypatch.setattr(browser_session, "_session_runtime_actor", fake_session_runtime_actor)
    monkeypatch.setattr(browser_session, "assert_tenant_runtime_allowed", fake_assert_tenant_runtime_allowed)
    monkeypatch.setattr(browser_session, "before_session_runtime_start", fake_before)
    monkeypatch.setattr(browser_session, "after_session_runtime_started", fake_after_started)
    monkeypatch.setattr(browser_session, "after_session_runtime_start_failed", fake_after_failed)
    monkeypatch.setattr(browser_session, "remove_container", fake_remove_container)
    monkeypatch.setattr(browser_session, "ensure_container_running", fake_ensure_container_running)

    result = asyncio.run(
        browser_session.run_browser_session_operation(
            "session-1",
            operation,
            operation_name="browser.current",
            recreate_runtime_on_repeated_transient=True,
        )
    )

    assert result == {"ok": True}
    assert calls == [
        ("wd-1", "http://selenium.local"),
        ("wd-1", "http://selenium.local"),
        ("wd-1", "http://selenium.local"),
    ]
    assert resets == ["session-1", "session-1"]
    assert removes == [("session-1", True)]
    assert ensures == ["session-1"]
    assert lifecycle_actions == [
        ("assert", "tenant-1", "session-1"),
        ("before", "session-1", "browser.current.runtime_recreate"),
        ("started", "session-1", "browser.current.runtime_recreate"),
    ]


def test_browser_session_operation_reports_empty_exception_message(monkeypatch):
    class EmptyError(Exception):
        def __str__(self):
            return ""

    class FakeBrowserSession:
        async def __aenter__(self):
            return "wd-1", "http://selenium.local"

        async def __aexit__(self, *_exc):
            return None

    def fake_browser_session(_session_id):
        return FakeBrowserSession()

    async def operation(_sid, _base):
        raise EmptyError()

    monkeypatch.setattr(browser_session, "browser_session", fake_browser_session)

    with pytest.raises(RuntimeError) as exc:
        asyncio.run(
            browser_session.run_browser_session_operation(
                "session-1",
                operation,
                operation_name="browser.current",
                attempts=1,
            )
        )

    assert str(exc.value) == "browser.current failed with EmptyError"
