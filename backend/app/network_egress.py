from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import shlex
import socket
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

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

VALID_EGRESS_TYPES = {"direct", "external_proxy", "clash", "openvpn"}
MANAGED_EGRESS_TYPES = {"clash", "openvpn"}
VALID_PROXY_SCHEMES = ("http://", "https://", "socks4://", "socks5://")
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


async def _run(cmd: str, timeout: float = 60) -> tuple[str, str, int]:
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return "", f"timeout after {timeout}s", -1
    return (
        stdout_b.decode("utf-8", errors="replace").strip(),
        stderr_b.decode("utf-8", errors="replace").strip(),
        proc.returncode or 0,
    )


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


def validate_proxy_url(proxy_url: str) -> str:
    value = proxy_url.strip()
    if value and not value.startswith(VALID_PROXY_SCHEMES):
        raise EgressError("Proxy URL must start with http://, https://, socks4://, or socks5://")
    return value


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
        proxy_url = row["proxy_url"] or ""
        if egress_type in MANAGED_EGRESS_TYPES:
            proxy_url = managed_proxy_url(row["id"], egress_type)
        return EffectiveEgress(
            id=row["id"],
            name=row["name"],
            type=egress_type,
            status=row["status"] or "unchecked",
            proxy_url=proxy_url,
            health_error=row["health_error"] or "",
        )
    proxy_url = validate_proxy_url(fallback_proxy_url or "")
    if proxy_url:
        return EffectiveEgress(
            id=None,
            name="Manual proxy",
            type="external_proxy",
            status="unchecked",
            proxy_url=proxy_url,
        )
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
        cmd = (
            f"docker run -d --name {shlex.quote(name)} "
            f"--label {shlex.quote(label)} "
            f"--network {network} --network-alias {shlex.quote(alias)} "
            f"-v {shlex.quote(config_ref)}:/root/.config/mihomo/config.yaml:ro "
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


async def _tcp_connect_check(proxy_url: str) -> tuple[str, str]:
    parsed = urlparse(proxy_url)
    host = parsed.hostname
    port = parsed.port
    if not host or not port:
        return "unhealthy", "Proxy URL must include host and port"

    def _connect() -> None:
        with socket.create_connection((host, port), timeout=3):
            return

    try:
        await asyncio.to_thread(_connect)
    except Exception as exc:
        return "unhealthy", str(exc)
    return "healthy", ""


async def check_egress(row: Any) -> dict:
    egress_type = row["type"]
    egress_id = row["id"]
    if row["status"] == "disabled":
        return {"status": "disabled", "healthError": row["health_error"] or ""}
    if egress_type == "direct":
        status, error = "healthy", ""
    elif egress_type == "external_proxy":
        status, error = await _tcp_connect_check(row["proxy_url"] or "")
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
