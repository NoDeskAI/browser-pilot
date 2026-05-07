import asyncio
import json

from app import container


def test_localhost_bridge_port_only_matches_explicit_local_targets():
    assert container._localhost_bridge_port("http://localhost:18081/app") == 18081
    assert container._localhost_bridge_port("ws://127.0.0.1:5173/socket") == 5173
    assert container._localhost_bridge_port("https://localhost/path") == 443
    assert container._localhost_bridge_port("https://example.com") is None
    assert container._localhost_bridge_port("chrome://history") is None


def test_ensure_localhost_bridge_starts_container_side_bridge(monkeypatch):
    commands = []

    async def fake_run(cmd, timeout=30):
        commands.append(cmd)
        return json.dumps({"status": "started", "port": 18081, "pid": 123}), "", 0

    monkeypatch.setattr(container, "_run", fake_run)

    result = asyncio.run(
        container.ensure_localhost_bridge_for_url(
            "5a946e93-69fa-4fbf-99d6-c9a526d19810",
            "http://localhost:18081/",
        )
    )

    assert result == {
        "enabled": True,
        "port": 18081,
        "target": "host.docker.internal:18081",
        "status": "started",
    }
    assert "/opt/bin/localhost-bridge.py" in commands[0]
    assert "--listen-port 18081" in commands[0]
    assert "--target-host host.docker.internal" in commands[0]


def test_ensure_localhost_bridge_ignores_public_urls(monkeypatch):
    async def fake_run(_cmd, timeout=30):
        raise AssertionError("public navigation must not start localhost bridge")

    monkeypatch.setattr(container, "_run", fake_run)

    result = asyncio.run(
        container.ensure_localhost_bridge_for_url(
            "5a946e93-69fa-4fbf-99d6-c9a526d19810",
            "https://www.todetect.cn/",
        )
    )

    assert result is None
