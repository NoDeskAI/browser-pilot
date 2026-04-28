import asyncio

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
