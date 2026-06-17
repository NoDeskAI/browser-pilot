from __future__ import annotations

import base64
import importlib
import shlex
from typing import Any, Protocol

from app import container as docker_runtime
from app.config import BROWSER_RUNTIME_PROVIDER, EDITION

BROWSER_RUNTIME_STANDARD = docker_runtime.BROWSER_RUNTIME_STANDARD
BROWSER_RUNTIME_CLOAK = docker_runtime.BROWSER_RUNTIME_CLOAK


class RuntimeProviderError(RuntimeError):
    pass


class RuntimeProvider(Protocol):
    name: str

    def container_name(self, session_id: str) -> str: ...

    def session_vnc_password(self, session_id: str) -> str: ...

    async def ensure_localhost_bridge_for_url(self, session_id: str, url: str) -> dict[str, Any] | None: ...

    async def exec_in_container(self, session_id: str, cmd: str, timeout: float = 10) -> str: ...

    async def sync_fingerprint_profile_to_container(
        self,
        session_id: str,
        fingerprint_profile: dict,
        *,
        restart_agent: bool = True,
    ) -> None: ...

    async def get_container_status(self, session_id: str) -> str: ...

    async def get_all_container_statuses(self) -> dict[str, str]: ...

    async def ensure_container_running(self, session_id: str) -> dict[str, int]: ...

    async def stop_container(self, session_id: str) -> None: ...

    async def pause_container(self, session_id: str) -> None: ...

    async def remove_container(self, session_id: str, *, keep_volume: bool = False) -> None: ...

    async def recreate_container(self, session_id: str, *args: Any, **kwargs: Any) -> None: ...

    async def resolve_selenium_base_url(self, session_id: str) -> str: ...

    async def resolve_vnc_websocket_url(self, session_id: str) -> str: ...

    async def resolve_network_via_browser(
        self,
        runtime_ports: dict[str, int],
        *,
        session_id: str | None = None,
        mode: str = "fast",
    ) -> dict[str, Any]: ...


class DockerRuntimeProvider:
    name = "docker"

    def container_name(self, session_id: str) -> str:
        return docker_runtime.container_name(session_id)

    def session_vnc_password(self, session_id: str) -> str:
        return docker_runtime.session_vnc_password(session_id)

    async def ensure_localhost_bridge_for_url(self, session_id: str, url: str) -> dict[str, Any] | None:
        return await docker_runtime.ensure_localhost_bridge_for_url(session_id, url)

    async def exec_in_container(self, session_id: str, cmd: str, timeout: float = 10) -> str:
        return await docker_runtime.exec_in_container(session_id, cmd, timeout=timeout)

    async def sync_fingerprint_profile_to_container(
        self,
        session_id: str,
        fingerprint_profile: dict,
        *,
        restart_agent: bool = True,
    ) -> None:
        await docker_runtime.sync_fingerprint_profile_to_container(
            session_id,
            fingerprint_profile,
            restart_agent=restart_agent,
        )

    async def get_container_status(self, session_id: str) -> str:
        return await docker_runtime.get_container_status(session_id)

    async def get_all_container_statuses(self) -> dict[str, str]:
        return await docker_runtime.get_all_container_statuses()

    async def ensure_container_running(self, session_id: str) -> dict[str, int]:
        return await docker_runtime.ensure_container_running(session_id)

    async def stop_container(self, session_id: str) -> None:
        await docker_runtime.stop_container(session_id)

    async def pause_container(self, session_id: str) -> None:
        await docker_runtime.pause_container(session_id)

    async def remove_container(self, session_id: str, *, keep_volume: bool = False) -> None:
        await docker_runtime.remove_container(session_id, keep_volume=keep_volume)

    async def recreate_container(self, session_id: str, *args: Any, **kwargs: Any) -> None:
        await docker_runtime.recreate_container(session_id, *args, **kwargs)

    async def resolve_selenium_base_url(self, session_id: str) -> str:
        return await docker_runtime.resolve_selenium_base_url(session_id)

    async def resolve_vnc_websocket_url(self, session_id: str) -> str:
        return await docker_runtime.resolve_vnc_websocket_url(session_id)

    async def resolve_network_via_browser(
        self,
        runtime_ports: dict[str, int],
        *,
        session_id: str | None = None,
        mode: str = "fast",
    ) -> dict[str, Any]:
        return await docker_runtime.resolve_network_via_browser(
            runtime_ports,
            session_id=session_id,
            mode=mode,
        )


_provider: RuntimeProvider | None = None


def _load_ee_provider(provider_name: str) -> RuntimeProvider:
    if EDITION != "ee":
        raise RuntimeProviderError(f"BROWSER_RUNTIME_PROVIDER={provider_name} requires EDITION=ee")
    try:
        module = importlib.import_module("ee.backend.runtime")
    except ModuleNotFoundError as exc:
        missing_module = exc.name or ""
        if missing_module in {"ee", "ee.backend", "ee.backend.runtime"}:
            raise RuntimeProviderError(f"BROWSER_RUNTIME_PROVIDER={provider_name} is not available in this build") from exc
        raise
    create_provider = getattr(module, "create_provider", None)
    if not callable(create_provider):
        raise RuntimeProviderError(f"EE runtime provider factory is not available for {provider_name}")
    try:
        return create_provider(provider_name)
    except ModuleNotFoundError as exc:
        missing_module = exc.name or ""
        if missing_module.startswith("ee.backend.runtime."):
            raise RuntimeProviderError(f"BROWSER_RUNTIME_PROVIDER={provider_name} is not available in this build") from exc
        raise


def get_runtime_provider() -> RuntimeProvider:
    global _provider
    if _provider is not None:
        return _provider

    provider_name = BROWSER_RUNTIME_PROVIDER or "docker"
    if provider_name == "docker":
        _provider = DockerRuntimeProvider()
        return _provider
    _provider = _load_ee_provider(provider_name)
    return _provider


def validate_runtime_provider_config() -> None:
    get_runtime_provider()


def container_name(session_id: str) -> str:
    return get_runtime_provider().container_name(session_id)


def session_vnc_password(session_id: str) -> str:
    return get_runtime_provider().session_vnc_password(session_id)


async def ensure_localhost_bridge_for_url(session_id: str, url: str) -> dict[str, Any] | None:
    return await get_runtime_provider().ensure_localhost_bridge_for_url(session_id, url)


async def exec_in_container(session_id: str, cmd: str, timeout: float = 10) -> str:
    return await get_runtime_provider().exec_in_container(session_id, cmd, timeout=timeout)


def _remote_shell_command(script: str) -> str:
    return f"sh -lc {shlex.quote(script)}"


async def paste_remote_clipboard(session_id: str, text: str) -> None:
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    script = (
        'export DISPLAY="${DISPLAY:-:99.0}"'
        f" && printf %s {shlex.quote(encoded)} | base64 -d | xclip -selection clipboard"
        " && xdotool key --clearmodifiers ctrl+v"
    )
    await exec_in_container(session_id, _remote_shell_command(script), timeout=10)


async def get_remote_clipboard(session_id: str) -> str:
    script = 'export DISPLAY="${DISPLAY:-:99.0}" && xclip -selection clipboard -o 2>/dev/null || true'
    return await exec_in_container(session_id, _remote_shell_command(script), timeout=10)


async def sync_fingerprint_profile_to_container(
    session_id: str,
    fingerprint_profile: dict,
    *,
    restart_agent: bool = True,
) -> None:
    await get_runtime_provider().sync_fingerprint_profile_to_container(
        session_id,
        fingerprint_profile,
        restart_agent=restart_agent,
    )


async def get_container_status(session_id: str) -> str:
    return await get_runtime_provider().get_container_status(session_id)


async def get_all_container_statuses() -> dict[str, str]:
    return await get_runtime_provider().get_all_container_statuses()


async def ensure_container_running(session_id: str) -> dict[str, int]:
    return await get_runtime_provider().ensure_container_running(session_id)


async def stop_container(session_id: str) -> None:
    await get_runtime_provider().stop_container(session_id)


async def pause_container(session_id: str) -> None:
    await get_runtime_provider().pause_container(session_id)


async def remove_container(session_id: str, *, keep_volume: bool = False) -> None:
    await get_runtime_provider().remove_container(session_id, keep_volume=keep_volume)


async def recreate_container(session_id: str, *args: Any, **kwargs: Any) -> None:
    await get_runtime_provider().recreate_container(session_id, *args, **kwargs)


async def resolve_selenium_base_url(session_id: str) -> str:
    return await get_runtime_provider().resolve_selenium_base_url(session_id)


async def resolve_vnc_websocket_url(session_id: str) -> str:
    return await get_runtime_provider().resolve_vnc_websocket_url(session_id)


async def resolve_network_via_browser(
    runtime_ports: dict[str, int],
    *,
    session_id: str | None = None,
    mode: str = "fast",
) -> dict[str, Any]:
    return await get_runtime_provider().resolve_network_via_browser(
        runtime_ports,
        session_id=session_id,
        mode=mode,
    )
