from __future__ import annotations

import asyncio
import sys
import types

import pytest

import app.runtime_provider as runtime_provider


@pytest.fixture(autouse=True)
def reset_runtime_provider(monkeypatch):
    monkeypatch.setattr(runtime_provider, "_provider", None)
    yield
    monkeypatch.setattr(runtime_provider, "_provider", None)


def test_runtime_provider_defaults_to_docker(monkeypatch):
    monkeypatch.setattr(runtime_provider, "BROWSER_RUNTIME_PROVIDER", "docker")

    provider = runtime_provider.get_runtime_provider()

    assert isinstance(provider, runtime_provider.DockerRuntimeProvider)
    assert runtime_provider.get_runtime_provider() is provider


def test_runtime_provider_proxies_docker_runtime(monkeypatch):
    captured = {}

    async def fake_ensure_container_running(session_id: str):
        captured["session_id"] = session_id
        return {"vnc": 7900, "selenium": 4444}

    monkeypatch.setattr(runtime_provider, "BROWSER_RUNTIME_PROVIDER", "docker")
    monkeypatch.setattr(
        runtime_provider.docker_runtime,
        "ensure_container_running",
        fake_ensure_container_running,
    )

    result = asyncio.run(runtime_provider.ensure_container_running("session-1"))

    assert result == {"vnc": 7900, "selenium": 4444}
    assert captured == {"session_id": "session-1"}


def test_paste_remote_clipboard_uses_base64_encoded_text(monkeypatch):
    captured = {}

    async def fake_exec(session_id: str, cmd: str, timeout: float = 10):
        captured["session_id"] = session_id
        captured["cmd"] = cmd
        captured["timeout"] = timeout
        return ""

    monkeypatch.setattr(runtime_provider, "exec_in_container", fake_exec)

    asyncio.run(runtime_provider.paste_remote_clipboard("session-1", 'hello "$(rm -rf /)" 中文'))

    assert captured["session_id"] == "session-1"
    assert captured["timeout"] == 10
    assert "base64 -d > \"$tmp\"" in captured["cmd"]
    assert "nohup xclip -selection clipboard" in captured["cmd"]
    assert "xdotool key --clearmodifiers ctrl+v" in captured["cmd"]
    assert 'hello "$(rm -rf /)" 中文' not in captured["cmd"]


def test_get_remote_clipboard_reads_x11_clipboard(monkeypatch):
    captured = {}

    async def fake_exec(session_id: str, cmd: str, timeout: float = 10):
        captured["session_id"] = session_id
        captured["cmd"] = cmd
        captured["timeout"] = timeout
        return "remote text"

    monkeypatch.setattr(runtime_provider, "exec_in_container", fake_exec)

    result = asyncio.run(runtime_provider.get_remote_clipboard("session-1"))

    assert result == "remote text"
    assert captured["session_id"] == "session-1"
    assert "xclip -selection clipboard -o" in captured["cmd"]


def test_non_docker_provider_in_ce_fails_closed(monkeypatch):
    monkeypatch.setattr(runtime_provider, "BROWSER_RUNTIME_PROVIDER", "managed")
    monkeypatch.setattr(runtime_provider, "EDITION", "ce")

    with pytest.raises(runtime_provider.RuntimeProviderError, match="requires EDITION=ee"):
        runtime_provider.validate_runtime_provider_config()


def test_non_docker_provider_without_ee_runtime_fails_closed(monkeypatch):
    monkeypatch.setattr(runtime_provider, "BROWSER_RUNTIME_PROVIDER", "managed")
    monkeypatch.setattr(runtime_provider, "EDITION", "ee")

    with pytest.raises(runtime_provider.RuntimeProviderError, match="not available"):
        runtime_provider.validate_runtime_provider_config()


def test_non_docker_provider_loads_ee_runtime_factory(monkeypatch):
    module_name = "ee.backend.runtime"

    class FakeRuntimeProvider:
        name = "managed"

    fake_module = types.ModuleType(module_name)
    fake_module.create_provider = lambda provider_name: FakeRuntimeProvider()
    monkeypatch.setitem(sys.modules, "ee", types.ModuleType("ee"))
    monkeypatch.setitem(sys.modules, "ee.backend", types.ModuleType("ee.backend"))
    monkeypatch.setitem(sys.modules, module_name, fake_module)
    monkeypatch.setattr(runtime_provider, "BROWSER_RUNTIME_PROVIDER", "managed")
    monkeypatch.setattr(runtime_provider, "EDITION", "ee")

    provider = runtime_provider.get_runtime_provider()

    assert isinstance(provider, FakeRuntimeProvider)
    assert provider.name == "managed"


def test_kubernetes_provider_loads_from_real_ee_runtime(monkeypatch):
    monkeypatch.setattr(runtime_provider, "BROWSER_RUNTIME_PROVIDER", "kubernetes")
    monkeypatch.setattr(runtime_provider, "EDITION", "ee")

    provider = runtime_provider.get_runtime_provider()

    assert provider.name == "kubernetes"
    assert provider.__class__.__module__ == "ee.backend.runtime.kubernetes_provider"


def test_unknown_runtime_provider_fails_closed(monkeypatch):
    monkeypatch.setattr(runtime_provider, "BROWSER_RUNTIME_PROVIDER", "nomad")
    monkeypatch.setattr(runtime_provider, "EDITION", "ce")

    with pytest.raises(runtime_provider.RuntimeProviderError, match="requires EDITION=ee"):
        runtime_provider.validate_runtime_provider_config()
