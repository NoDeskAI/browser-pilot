import asyncio

import pytest

from app import network_egress


class Row(dict):
    pass


def test_effective_direct_egress_has_no_proxy():
    effective = network_egress.effective_proxy_from_row(None, "")

    assert effective.type == "direct"
    assert effective.proxy_url == ""
    assert effective.status == "healthy"


def test_effective_manual_proxy_keeps_url_without_lookup():
    effective = network_egress.effective_proxy_from_row(None, "socks5://proxy.internal:1080")

    assert effective.type == "external_proxy"
    assert effective.proxy_url == "socks5://proxy.internal:1080"


def test_managed_egress_uses_stable_network_alias():
    row = Row({
        "id": "1234567890abcdef",
        "name": "Office Clash",
        "type": "clash",
        "status": "unchecked",
        "proxy_url": "",
        "health_error": "",
    })

    effective = network_egress.effective_proxy_from_row(row, "")

    assert effective.proxy_url == "http://bp-egress-1234567890ab:7890"
    assert network_egress.egress_container_name(row["id"]) == "bp-egress-1234567890ab"


def test_external_proxy_health_check_only_opens_proxy_socket(monkeypatch):
    calls = []

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_create_connection(addr, timeout):
        calls.append((addr, timeout))
        return FakeSocket()

    monkeypatch.setattr(network_egress.socket, "create_connection", fake_create_connection)

    status, error = asyncio.run(
        network_egress._tcp_connect_check("http://proxy.internal:8080")
    )

    assert status == "healthy"
    assert error == ""
    assert calls == [(("proxy.internal", 8080), 3)]


class _FakeResponse:
    def __init__(self, data: bytes, status: int = 200):
        self._data = data
        self.status = status

    def read(self, _limit: int | None = None) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_resolve_config_prefers_inline_text(monkeypatch):
    def fake_urlopen(*_args, **_kwargs):
        assert False, "should not fetch URL when configText is provided"

    monkeypatch.setattr(network_egress.urllib.request, "urlopen", fake_urlopen)
    result = asyncio.run(network_egress.resolve_config_text("inline-config", "https://example.com/conf"))

    assert result == "inline-config"


def test_resolve_config_reads_from_url(monkeypatch):
    def fake_urlopen(req, timeout=8):
        assert str(req.full_url) == "https://example.com/conf"
        return _FakeResponse(b"url-config", status=200)

    monkeypatch.setattr(network_egress.urllib.request, "urlopen", fake_urlopen)
    result = asyncio.run(network_egress.resolve_config_text(None, "https://example.com/conf"))

    assert result == "url-config"


def test_resolve_config_url_invalid_scheme():
    with pytest.raises(network_egress.EgressError) as exc:
        asyncio.run(network_egress.resolve_config_text(None, "ftp://example.com/conf"))

    assert "http:// or https://" in str(exc.value)


def test_resolve_config_url_empty_response(monkeypatch):
    def fake_urlopen(req, timeout=8):
        return _FakeResponse(b"", status=200)

    monkeypatch.setattr(network_egress.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(network_egress.EgressError) as exc:
        asyncio.run(network_egress.resolve_config_text(None, "https://example.com/empty"))

    assert "empty content" in str(exc.value)


def test_resolve_config_url_oversize_response(monkeypatch):
    data = b"x" * (network_egress.MAX_CONFIG_URL_BYTES + 1)

    def fake_urlopen(req, timeout=8):
        return _FakeResponse(data, status=200)

    monkeypatch.setattr(network_egress.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(network_egress.EgressError) as exc:
        asyncio.run(network_egress.resolve_config_text(None, "https://example.com/oversize"))

    assert "size limit" in str(exc.value)
