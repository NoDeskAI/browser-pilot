import asyncio
import yaml

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


def test_clash_runtime_config_forces_global_without_mutating_original(tmp_path, monkeypatch):
    source = tmp_path / "config.yaml"
    source.write_text(
        """
mode: rule
mixed-port: 7899
proxies:
  - name: proxy-a
    type: socks5
    server: 127.0.0.1
    port: 1080
proxy-groups:
  - name: GLOBAL
    type: select
    proxies: [proxy-a]
rules:
  - MATCH,proxy-a
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(network_egress, "_config_root", lambda: tmp_path / "runtime")

    runtime = network_egress._write_clash_global_runtime_config("session-1", str(source))

    original = yaml.safe_load(source.read_text(encoding="utf-8"))
    generated = yaml.safe_load(runtime.read_text(encoding="utf-8"))
    assert original["mode"] == "rule"
    assert original["mixed-port"] == 7899
    assert generated["mode"] == "global"
    assert generated["mixed-port"] == network_egress.NETWORK_EGRESS_CLASH_PROXY_PORT
    assert generated["tun"]["enable"] is True
    assert generated["tun"]["auto-route"] is True
    assert generated["dns"]["enable"] is True
    assert generated["proxies"][0]["name"] == "proxy-a"


def test_prepare_session_clash_gateway_uses_runtime_namespace_and_global_config(tmp_path, monkeypatch):
    source = tmp_path / "config.yaml"
    source.write_text("mode: rule\nproxies: []\n", encoding="utf-8")
    calls = []

    async def fake_run(cmd, timeout=60):
        calls.append(cmd)
        return "container-id", "", 0

    async def noop_update(*_args, **_kwargs):
        return None

    monkeypatch.setattr(network_egress, "_config_root", lambda: tmp_path / "runtime")
    monkeypatch.setattr(network_egress, "ensure_docker_network", lambda: asyncio.sleep(0))
    monkeypatch.setattr(network_egress, "update_egress_status", noop_update)
    monkeypatch.setattr(network_egress, "_run", fake_run)

    row = Row({
        "id": "egress-123456",
        "name": "Clash",
        "type": "clash",
        "status": "unchecked",
        "config_ref": str(source),
    })
    gateway = asyncio.run(
        network_egress.prepare_session_egress_gateway(
            row,
            session_id="session-1",
            selenium_port=55100,
            vnc_port=55101,
        )
    )

    docker_run = next(cmd for cmd in calls if "docker run -d" in cmd)
    assert gateway.enabled is True
    assert gateway.full_tunnel is True
    assert gateway.browser_proxy == f"http://127.0.0.1:{network_egress.NETWORK_EGRESS_CLASH_PROXY_PORT}"
    assert gateway.warnings == []
    assert "--cap-add=NET_ADMIN --device /dev/net/tun" in docker_run
    assert "-p 55100:4444 -p 55101:7900" in docker_run
    assert "bp-egress-session-session-1" in docker_run
    runtime = tmp_path / "runtime" / "sessions" / "session-1" / "clash" / "config.yaml"
    assert yaml.safe_load(runtime.read_text(encoding="utf-8"))["mode"] == "global"


def test_prepare_session_clash_gateway_reports_global_mode_warning(tmp_path, monkeypatch):
    source = tmp_path / "config.yaml"
    source.write_text("mode: rule\nproxies: []\n", encoding="utf-8")

    async def fake_run(*_args, **_kwargs):
        return "container-id", "", 0

    async def noop_update(*_args, **_kwargs):
        return None

    monkeypatch.setattr(network_egress, "_config_root", lambda: tmp_path / "runtime")
    monkeypatch.setattr(network_egress, "ensure_docker_network", lambda: asyncio.sleep(0))
    monkeypatch.setattr(network_egress, "update_egress_status", noop_update)
    monkeypatch.setattr(network_egress, "_run", fake_run)
    monkeypatch.setattr(network_egress, "_clash_runtime_is_global", lambda _path: False)

    row = Row({
        "id": "egress-123456",
        "name": "Clash",
        "type": "clash",
        "status": "unchecked",
        "config_ref": str(source),
    })
    gateway = asyncio.run(
        network_egress.prepare_session_egress_gateway(
            row,
            session_id="session-1",
            selenium_port=55100,
            vnc_port=55101,
        )
    )

    assert gateway.warnings == ["clash_global_mode_failed"]
