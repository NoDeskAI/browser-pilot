from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import shlex
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

import yaml

from app.config import (
    CONTAINER_PREFIX,
    NETWORK_EGRESS_CLASH_IMAGE,
    NETWORK_EGRESS_CLASH_PROXY_PORT,
    NETWORK_EGRESS_CONFIG_DIR,
    NETWORK_EGRESS_DOCKER_NETWORK,
    NETWORK_EGRESS_OPENVPN_IMAGE,
    NETWORK_EGRESS_OPENVPN_PROXY_PORT,
    PROJECT_ROOT,
)
from app.db import get_pool
from app.runtime_control import run_runtime_command as _run

VALID_EGRESS_TYPES = {"direct", "clash", "openvpn"}
MANAGED_EGRESS_TYPES = {"clash", "openvpn"}
CONFIG_URL_SCHEMES = ("http", "https")
MAX_CONFIG_URL_BYTES = 1024 * 1024
FETCH_TIMEOUT_SECONDS = 8


def _format_bytes(size: int) -> str:
    return f"{size} bytes"


class EgressError(RuntimeError):
    pass


class UnsupportedEgressError(EgressError):
    pass


@dataclass
class EffectiveEgress:
    id: str | None
    name: str
    type: str
    status: str
    proxy_url: str
    health_error: str = ""


@dataclass
class SessionEgressGateway:
    enabled: bool = False
    type: str = "direct"
    container_name: str = ""
    browser_proxy: str = ""
    full_tunnel: bool = False
    warnings: list[str] = field(default_factory=list)


def _config_root() -> Path:
    path = Path(NETWORK_EGRESS_CONFIG_DIR)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def _egress_dir(tenant_id: str, egress_id: str) -> Path:
    safe_tenant = "".join(ch for ch in tenant_id if ch.isalnum() or ch in ("-", "_"))[:80]
    return _config_root() / safe_tenant / egress_id


def egress_container_name(egress_id: str) -> str:
    return f"{CONTAINER_PREFIX}-egress-{egress_id[:12]}"


def egress_network_alias(egress_id: str) -> str:
    return f"{CONTAINER_PREFIX}-egress-{egress_id[:12]}"


def session_egress_gateway_name(session_id: str) -> str:
    return f"{CONTAINER_PREFIX}-egress-session-{session_id[:12]}"


def _session_egress_dir(session_id: str) -> Path:
    safe_session = "".join(ch for ch in session_id if ch.isalnum() or ch in ("-", "_"))[:80]
    return _config_root() / "sessions" / safe_session


def managed_proxy_url(egress_id: str, egress_type: str) -> str:
    port = NETWORK_EGRESS_CLASH_PROXY_PORT if egress_type == "clash" else NETWORK_EGRESS_OPENVPN_PROXY_PORT
    return f"http://{egress_network_alias(egress_id)}:{port}"


def public_egress_summary(row: Any | None) -> dict:
    if not row:
        return {
            "id": None,
            "name": "Direct",
            "type": "direct",
            "status": "healthy",
            "proxyUrl": "",
            "healthError": "",
            "lastCheckedAt": "",
        }
    def _get(key: str):
        try:
            return row[key]
        except (KeyError, IndexError, TypeError):
            return None

    created_at = _get("created_at")
    updated_at = _get("updated_at")
    return {
        "id": row["id"],
        "name": row["name"],
        "type": row["type"],
        "status": row["status"] or "unchecked",
        "proxyUrl": row["proxy_url"] or "",
        "healthError": row["health_error"] or "",
        "lastCheckedAt": row["last_checked_at"].isoformat() if row["last_checked_at"] else "",
        "createdAt": created_at.isoformat() if created_at else "",
        "updatedAt": updated_at.isoformat() if updated_at else "",
    }


async def ensure_docker_network() -> None:
    network = shlex.quote(NETWORK_EGRESS_DOCKER_NETWORK)
    _, _, rc = await _run(f"docker network inspect {network}", timeout=10)
    if rc == 0:
        return
    stdout, stderr, rc = await _run(f"docker network create {network}", timeout=20)
    if rc != 0:
        raise EgressError(f"docker network create failed: {(stderr or stdout)[:300]}")


async def update_egress_status(
    egress_id: str,
    status: str,
    health_error: str = "",
    *,
    checked: bool = True,
) -> None:
    pool = get_pool()
    if checked:
        await pool.execute(
            """
            UPDATE network_egress_profiles
            SET status = $1, health_error = $2, last_checked_at = NOW(), updated_at = NOW()
            WHERE id = $3
            """,
            status,
            health_error[:500],
            egress_id,
        )
    else:
        await pool.execute(
            """
            UPDATE network_egress_profiles
            SET status = $1, health_error = $2, updated_at = NOW()
            WHERE id = $3
            """,
            status,
            health_error[:500],
            egress_id,
        )


def _read_config_from_url_sync(config_url: str) -> str:
    value = config_url.strip()
    parsed = urlparse(value)
    if not parsed.scheme or parsed.scheme.lower() not in CONFIG_URL_SCHEMES:
        raise EgressError("Config URL must start with http:// or https://")

    req = urllib.request.Request(
        value,
        headers={"User-Agent": "BrowserPilot Network Egress"},
    )

    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", 200)
            if status < 200 or status >= 300:
                raise EgressError(f"Config URL returned HTTP {status}")
            data = response.read(MAX_CONFIG_URL_BYTES + 1)
    except (TimeoutError, urllib.error.URLError, OSError) as exc:
        raise EgressError(f"Failed to fetch config from URL: {exc}") from exc

    if len(data) > MAX_CONFIG_URL_BYTES:
        raise EgressError(f"Config URL exceeds size limit: {_format_bytes(MAX_CONFIG_URL_BYTES)}")

    text = data.decode("utf-8", errors="replace")
    if not text.strip():
        raise EgressError("Config URL returned empty content")
    return text


async def resolve_config_text(config_text: str | None, config_url: str | None) -> str:
    if config_text and config_text.strip():
        return config_text
    if config_url and config_url.strip():
        return await asyncio.to_thread(_read_config_from_url_sync, config_url)
    raise EgressError("Config content is required")


async def fetch_egress_for_tenant(tenant_id: str, egress_id: str | None) -> Any | None:
    if not egress_id:
        return None
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM network_egress_profiles WHERE id = $1 AND tenant_id = $2",
        egress_id,
        tenant_id,
    )
    if not row:
        raise EgressError("Network egress profile not found")
    return row


def effective_proxy_from_row(row: Any | None, fallback_proxy_url: str = "") -> EffectiveEgress:
    if row:
        if row["status"] == "disabled":
            raise EgressError("Network egress profile is disabled")
        egress_type = row["type"]
        if egress_type not in MANAGED_EGRESS_TYPES:
            raise EgressError("Unsupported egress type. Use a Clash or OpenVPN network egress profile.")
        proxy_url = row["proxy_url"] or ""
        proxy_url = managed_proxy_url(row["id"], egress_type)
        return EffectiveEgress(
            id=row["id"],
            name=row["name"],
            type=egress_type,
            status=row["status"] or "unchecked",
            proxy_url=proxy_url,
            health_error=row["health_error"] or "",
        )
    if str(fallback_proxy_url or "").strip():
        raise EgressError("Manual HTTP/SOCKS proxy is no longer supported. Use a Clash or OpenVPN network egress profile.")
    return EffectiveEgress(id=None, name="Direct", type="direct", status="healthy", proxy_url="")


async def resolve_egress(
    tenant_id: str,
    egress_id: str | None,
    fallback_proxy_url: str = "",
    *,
    ensure: bool = False,
) -> EffectiveEgress:
    row = await fetch_egress_for_tenant(tenant_id, egress_id)
    effective = effective_proxy_from_row(row, fallback_proxy_url)
    if ensure and row and row["type"] in MANAGED_EGRESS_TYPES:
        await ensure_managed_egress(row)
    return effective


async def _write_clash_config(tenant_id: str, egress_id: str, config_text: str) -> str:
    if not config_text.strip():
        raise EgressError("Clash config is required")
    root = _egress_dir(tenant_id, egress_id)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "config.yaml"
    path.write_text(config_text, encoding="utf-8")
    return str(path)


async def _write_openvpn_config(
    tenant_id: str,
    egress_id: str,
    config_text: str,
    username: str = "",
    password: str = "",
) -> str:
    if not config_text.strip():
        raise EgressError("OpenVPN config is required")
    root = _egress_dir(tenant_id, egress_id)
    root.mkdir(parents=True, exist_ok=True)
    (root / "client.ovpn").write_text(config_text, encoding="utf-8")
    if username or password:
        (root / "auth.txt").write_text(f"{username}\n{password}\n", encoding="utf-8")
    return str(root)


async def write_config_ref(
    tenant_id: str,
    egress_id: str,
    egress_type: str,
    config_text: str,
    username: str = "",
    password: str = "",
) -> str:
    if egress_type == "clash":
        return await _write_clash_config(tenant_id, egress_id, config_text)
    if egress_type == "openvpn":
        return await _write_openvpn_config(tenant_id, egress_id, config_text, username, password)
    return ""


async def _inspect_container_running(name: str) -> bool:
    stdout, _, rc = await _run(
        f"docker inspect --format '{{{{.State.Running}}}}' {shlex.quote(name)}",
        timeout=10,
    )
    return rc == 0 and stdout.strip().lower() == "true"


async def _remove_egress_container(egress_id: str) -> None:
    await _run(f"docker rm -f {shlex.quote(egress_container_name(egress_id))}", timeout=20)


async def remove_session_egress_gateway(session_id: str) -> None:
    await _run(f"docker rm -f {shlex.quote(session_egress_gateway_name(session_id))}", timeout=20)


async def stop_session_egress_gateway(session_id: str) -> None:
    name = session_egress_gateway_name(session_id)
    if await _inspect_container_running(name):
        await _run(f"docker stop {shlex.quote(name)}", timeout=20)


async def start_session_egress_gateway(session_id: str) -> None:
    name = session_egress_gateway_name(session_id)
    stdout, _, rc = await _run(
        f"docker inspect --format '{{{{.State.Status}}}}' {shlex.quote(name)}",
        timeout=10,
    )
    if rc != 0:
        return
    if stdout.strip().lower() == "running":
        return
    _, stderr, rc = await _run(f"docker start {shlex.quote(name)}", timeout=30)
    if rc != 0:
        raise EgressError(f"failed to start session egress gateway: {stderr[:300]}")


async def _wait_session_gateway_ready(
    name: str,
    egress_type: str,
    *,
    timeout: float = 20.0,
) -> tuple[bool, str]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    last_error = ""
    quoted_name = shlex.quote(name)

    while loop.time() < deadline:
        if not await _inspect_container_running(name):
            last_error = "egress gateway container is not running"
        elif egress_type == "openvpn":
            stdout, stderr, rc = await _run(
                f"docker exec {quoted_name} sh -c 'ip route | grep -E \"dev tun[0-9]+\" >/dev/null'",
                timeout=5,
            )
            if rc == 0:
                return True, ""
            last_error = stderr or stdout or "openvpn tunnel route is not ready"
        else:
            return True, ""
        await asyncio.sleep(0.5)

    return False, last_error or "egress gateway did not become ready"


async def _ensure_openvpn_image() -> None:
    image = NETWORK_EGRESS_OPENVPN_IMAGE
    _, _, rc = await _run(f"docker image inspect {shlex.quote(image)}", timeout=10)
    if rc == 0:
        return
    if image != "browser-pilot-openvpn-egress:latest":
        raise EgressError(f"OpenVPN image not found: {image}")
    build_dir = PROJECT_ROOT / "services" / "network-egress-openvpn"
    stdout, stderr, rc = await _run(
        f"docker build -t {shlex.quote(image)} {shlex.quote(str(build_dir))}",
        timeout=300,
    )
    if rc != 0:
        raise EgressError(f"OpenVPN egress image build failed: {(stderr or stdout)[:500]}")


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EgressError(f"Clash config is not readable: {exc}") from exc
    data = yaml.safe_load(raw) or {}
    if not isinstance(data, dict):
        raise EgressError("Clash config must be a YAML mapping")
    return data


def _write_clash_global_runtime_config(session_id: str, config_ref: str) -> Path:
    source = Path(config_ref)
    if not source.is_file():
        raise EgressError("Clash config is missing")

    data = _load_yaml_mapping(source)
    data["mode"] = "global"
    data["mixed-port"] = NETWORK_EGRESS_CLASH_PROXY_PORT
    data["allow-lan"] = True
    data.setdefault("bind-address", "*")
    data["external-controller"] = "127.0.0.1:9090"

    tun = data.get("tun") if isinstance(data.get("tun"), dict) else {}
    tun.update(
        {
            "enable": True,
            "stack": tun.get("stack") or "system",
            "auto-route": True,
            "auto-detect-interface": True,
            "strict-route": True,
        }
    )
    data["tun"] = tun

    dns = data.get("dns") if isinstance(data.get("dns"), dict) else {}
    dns.setdefault("enable", True)
    dns.setdefault("listen", "127.0.0.1:1053")
    dns.setdefault("enhanced-mode", "fake-ip")
    data["dns"] = dns

    runtime_dir = _session_egress_dir(session_id) / "clash"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    runtime_path = runtime_dir / "config.yaml"
    runtime_path.write_text(
        yaml.safe_dump(data, allow_unicode=False, sort_keys=False),
        encoding="utf-8",
    )
    return runtime_path


def _clash_runtime_is_global(config_path: Path) -> bool:
    try:
        data = _load_yaml_mapping(config_path)
    except EgressError:
        return False
    return str(data.get("mode") or "").strip().lower() == "global"


async def prepare_session_egress_gateway(
    row: Any | None,
    *,
    session_id: str,
    selenium_port: int,
    vnc_port: int,
) -> SessionEgressGateway:
    """Create a session-scoped egress namespace for managed full-tunnel exits.

    The browser container joins this container's network namespace. Host port
    publishing must therefore live on the gateway, not on the browser container.
    """
    if not row or row["type"] not in MANAGED_EGRESS_TYPES:
        return SessionEgressGateway()
    if row["status"] == "disabled":
        raise EgressError("Network egress profile is disabled")

    egress_type = row["type"]
    await ensure_docker_network()
    await remove_session_egress_gateway(session_id)

    name = session_egress_gateway_name(session_id)
    network = shlex.quote(NETWORK_EGRESS_DOCKER_NETWORK)
    session_label = f"{CONTAINER_PREFIX}.session_id={session_id}"
    gateway_label = f"{CONTAINER_PREFIX}.egress_gateway=true"
    egress_label = f"{CONTAINER_PREFIX}.egress_id={row['id']}"
    publish_args = f"-p {selenium_port}:4444 -p {vnc_port}:7900"
    common = (
        f"docker run -d --name {shlex.quote(name)} "
        f"--label {shlex.quote(session_label)} "
        f"--label {shlex.quote(gateway_label)} "
        f"--label {shlex.quote(egress_label)} "
        f"--network {network} --add-host host.docker.internal:host-gateway "
        f"{publish_args} "
    )
    warnings: list[str] = []

    if egress_type == "clash":
        config_ref = str(row["config_ref"] or "")
        if not config_ref:
            raise EgressError("Clash config is missing")
        runtime_config = _write_clash_global_runtime_config(session_id, config_ref)
        if not _clash_runtime_is_global(runtime_config):
            warnings.append("clash_global_mode_failed")
        cmd = (
            f"{common}"
            f"--cap-add=NET_ADMIN --device /dev/net/tun "
            f"-v {shlex.quote(str(runtime_config.parent))}:/root/.config/mihomo:ro "
            f"{shlex.quote(NETWORK_EGRESS_CLASH_IMAGE)} -d /root/.config/mihomo"
        )
        browser_proxy = f"http://127.0.0.1:{NETWORK_EGRESS_CLASH_PROXY_PORT}"
    else:
        config_ref = str(row["config_ref"] or "")
        if not config_ref:
            raise EgressError("OpenVPN config is missing")
        await _ensure_openvpn_image()
        cmd = (
            f"{common}"
            f"--cap-add=NET_ADMIN --device /dev/net/tun "
            f"-v {shlex.quote(config_ref)}:/config:ro "
            f"{shlex.quote(NETWORK_EGRESS_OPENVPN_IMAGE)}"
        )
        browser_proxy = f"http://127.0.0.1:{NETWORK_EGRESS_OPENVPN_PROXY_PORT}"

    stdout, stderr, rc = await _run(cmd, timeout=60)
    if rc != 0:
        message = (stderr or stdout)[:500]
        status = "unsupported" if egress_type == "openvpn" and ("/dev/net/tun" in message or "operation not permitted" in message.lower()) else "unhealthy"
        await update_egress_status(row["id"], status, message)
        if status == "unsupported":
            raise UnsupportedEgressError(message)
        raise EgressError(message)

    ready, ready_error = await _wait_session_gateway_ready(name, egress_type)
    if not ready:
        prefix = "openvpn_full_tunnel_failed" if egress_type == "openvpn" else "egress_gateway_unhealthy"
        message = f"{prefix}: {ready_error}"[:500]
        await update_egress_status(row["id"], "unhealthy", message)
        await remove_session_egress_gateway(session_id)
        raise EgressError(message)

    await update_egress_status(row["id"], "healthy", "")
    return SessionEgressGateway(
        enabled=True,
        type=egress_type,
        container_name=name,
        browser_proxy=browser_proxy,
        full_tunnel=True,
        warnings=warnings,
    )


async def ensure_managed_egress(row: Any) -> None:
    egress_id = row["id"]
    egress_type = row["type"]
    if egress_type not in MANAGED_EGRESS_TYPES:
        return
    if row["status"] == "disabled":
        raise EgressError("Network egress profile is disabled")

    await ensure_docker_network()
    name = egress_container_name(egress_id)
    if await _inspect_container_running(name):
        return

    await _remove_egress_container(egress_id)
    alias = egress_network_alias(egress_id)
    network = shlex.quote(NETWORK_EGRESS_DOCKER_NETWORK)
    label = f"{CONTAINER_PREFIX}.egress_id={egress_id}"

    if egress_type == "clash":
        config_ref = str(row["config_ref"] or "")
        if not config_ref:
            await update_egress_status(egress_id, "unhealthy", "Clash config is missing")
            raise EgressError("Clash config is missing")
        runtime_config = _write_clash_global_runtime_config(f"egress-{egress_id}", config_ref)
        cmd = (
            f"docker run -d --name {shlex.quote(name)} "
            f"--label {shlex.quote(label)} "
            f"--cap-add=NET_ADMIN --device /dev/net/tun "
            f"--network {network} --network-alias {shlex.quote(alias)} "
            f"-v {shlex.quote(str(runtime_config.parent))}:/root/.config/mihomo:ro "
            f"{shlex.quote(NETWORK_EGRESS_CLASH_IMAGE)} -d /root/.config/mihomo"
        )
    else:
        config_ref = str(row["config_ref"] or "")
        if not config_ref:
            await update_egress_status(egress_id, "unhealthy", "OpenVPN config is missing")
            raise EgressError("OpenVPN config is missing")
        await _ensure_openvpn_image()
        cmd = (
            f"docker run -d --name {shlex.quote(name)} "
            f"--label {shlex.quote(label)} "
            f"--cap-add=NET_ADMIN --device /dev/net/tun "
            f"--network {network} --network-alias {shlex.quote(alias)} "
            f"-v {shlex.quote(config_ref)}:/config:ro "
            f"{shlex.quote(NETWORK_EGRESS_OPENVPN_IMAGE)}"
        )

    stdout, stderr, rc = await _run(cmd, timeout=60)
    if rc != 0:
        message = (stderr or stdout)[:500]
        status = "unsupported" if egress_type == "openvpn" and ("/dev/net/tun" in message or "operation not permitted" in message.lower()) else "unhealthy"
        await update_egress_status(egress_id, status, message)
        if status == "unsupported":
            raise UnsupportedEgressError(message)
        raise EgressError(message)
    await update_egress_status(egress_id, "healthy", "")


async def remove_managed_egress(egress_id: str) -> None:
    await _remove_egress_container(egress_id)


async def check_egress(row: Any) -> dict:
    egress_type = row["type"]
    egress_id = row["id"]
    if row["status"] == "disabled":
        return {"status": "disabled", "healthError": row["health_error"] or ""}
    if egress_type == "direct":
        status, error = "healthy", ""
    elif egress_type in MANAGED_EGRESS_TYPES:
        try:
            await ensure_managed_egress(row)
            running = await _inspect_container_running(egress_container_name(egress_id))
            status, error = ("healthy", "") if running else ("unhealthy", "Container is not running")
        except UnsupportedEgressError as exc:
            status, error = "unsupported", str(exc)
        except Exception as exc:
            status, error = "unhealthy", str(exc)
    else:
        status, error = "unhealthy", f"Unknown egress type: {egress_type}"
    await update_egress_status(egress_id, status, error)
    return {
        "status": status,
        "healthError": error,
        "lastCheckedAt": datetime.now(timezone.utc).isoformat(),
    }
