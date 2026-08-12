from __future__ import annotations

import base64
import importlib
import shlex
from typing import Any, Protocol

from app import container as docker_runtime
from app.config import BROWSER_RUNTIME_PROVIDER, EDITION

BROWSER_RUNTIME_STANDARD = docker_runtime.BROWSER_RUNTIME_STANDARD
BROWSER_RUNTIME_CLOAK = docker_runtime.BROWSER_RUNTIME_CLOAK
BROWSER_RUNTIME_LITE = "browser_lite"


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


async def _session_uses_browser_lite(session_id: str) -> bool:
    from app.browser_lite import session_is_browser_lite
    try:
        return await session_is_browser_lite(session_id)
    except RuntimeError as exc:
        if "Database pool not initialized" in str(exc):
            return False
        raise


async def _browser_lite_command(
    session_id: str,
    action: str,
    *,
    timeout: float = 45,
    **payload: Any,
) -> Any:
    from app.browser_lite import session_command

    return await session_command(session_id, action, payload=payload or None, timeout=timeout)


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
    if await _session_uses_browser_lite(session_id):
        raise RuntimeProviderError("Browser Lite does not expose a remote shell")
    return await get_runtime_provider().exec_in_container(session_id, cmd, timeout=timeout)


def _remote_shell_command(script: str) -> str:
    return f"sh -lc {shlex.quote(script)}"


async def paste_remote_clipboard(session_id: str, text: str) -> None:
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    script = (
        'export DISPLAY="${DISPLAY:-:99.0}"'
        " && tmp=$(mktemp)"
        ' && trap \'rm -f "$tmp"\' EXIT'
        f" && printf %s {shlex.quote(encoded)} | base64 -d > \"$tmp\""
        ' && (nohup xclip -selection clipboard < "$tmp" >/tmp/browser-pilot-clipboard-xclip.log 2>&1 &)'
        " && sleep 0.2"
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
    if await _session_uses_browser_lite(session_id):
        return
    await get_runtime_provider().sync_fingerprint_profile_to_container(
        session_id,
        fingerprint_profile,
        restart_agent=restart_agent,
    )


async def get_container_status(session_id: str) -> str:
    if await _session_uses_browser_lite(session_id):
        try:
            result = await _browser_lite_command(session_id, "status", timeout=3)
            return str((result or {}).get("status") or "not_found")
        except Exception:
            return "not_found"
    return await get_runtime_provider().get_container_status(session_id)


async def get_all_container_statuses() -> dict[str, str]:
    statuses = await get_runtime_provider().get_all_container_statuses()
    try:
        import asyncio

        from app.browser_lite import node_online
        from app.db import get_pool

        rows = await get_pool().fetch(
            "SELECT id, browser_lite_node_id FROM sessions WHERE COALESCE(browser_runtime, 'standard_chrome') = 'browser_lite'"
        )
        async def browser_lite_status(row) -> tuple[str, str]:
            session_id = row["id"]
            if not await node_online(row["browser_lite_node_id"]):
                return session_id[:12], "not_found"
            try:
                result = await _browser_lite_command(session_id, "status", timeout=3)
                return session_id[:12], str((result or {}).get("status") or "not_found")
            except Exception:
                return session_id[:12], "not_found"

        for session_id, status in await asyncio.gather(*(browser_lite_status(row) for row in rows)):
            statuses[session_id] = status
    except Exception:
        pass
    return statuses


async def ensure_container_running(session_id: str) -> dict[str, int]:
    if await _session_uses_browser_lite(session_id):
        result = await _browser_lite_command(session_id, "ensure")
        runtime = (result or {}).get("runtime") or {}
        return {"selenium_port": int(runtime.get("port") or 0), "vnc_port": 0}
    return await get_runtime_provider().ensure_container_running(session_id)


async def stop_container(session_id: str) -> None:
    if await _session_uses_browser_lite(session_id):
        await _browser_lite_command(session_id, "stop")
        return
    await get_runtime_provider().stop_container(session_id)


async def pause_container(session_id: str) -> None:
    if await _session_uses_browser_lite(session_id):
        await _browser_lite_command(session_id, "pause")
        return
    await get_runtime_provider().pause_container(session_id)


async def remove_container(session_id: str, *, keep_volume: bool = False) -> None:
    if await _session_uses_browser_lite(session_id):
        await _browser_lite_command(session_id, "stop" if keep_volume else "remove")
        return
    await get_runtime_provider().remove_container(session_id, keep_volume=keep_volume)


async def recreate_container(session_id: str, *args: Any, **kwargs: Any) -> None:
    if await _session_uses_browser_lite(session_id):
        await _browser_lite_command(session_id, "stop")
        options = {
            key: kwargs[key]
            for key in ("width", "height")
            if kwargs.get(key) is not None
        }
        await _browser_lite_command(session_id, "ensure", options=options)
        return
    await get_runtime_provider().recreate_container(session_id, *args, **kwargs)


async def resolve_selenium_base_url(session_id: str) -> str:
    if await _session_uses_browser_lite(session_id):
        return f"browser_lite://{session_id}"
    return await get_runtime_provider().resolve_selenium_base_url(session_id)


async def resolve_vnc_websocket_url(session_id: str) -> str:
    if await _session_uses_browser_lite(session_id):
        raise RuntimeProviderError("Browser Lite remote viewer is not enabled; open the app on the Mac node")
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
