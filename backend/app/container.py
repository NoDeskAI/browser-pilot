from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

import httpx

from app.config import DOCKER_HOST_ADDR, CONTAINER_PREFIX as _PREFIX, SELENIUM_IMAGE_NAME
from app.db import get_pool
from app.device_presets import DEVICE_PRESETS, DEFAULT_PRESET, get_preset
from app.fingerprint import generate_profile

logger = logging.getLogger("container")

CONTAINER_PREFIX = f"{_PREFIX}-"
IMAGE_NAME = SELENIUM_IMAGE_NAME
SHM_SIZE = "2g"

_STATIC_ENV = {
    "SE_VNC_NO_PASSWORD": "1",
    "SE_SCREEN_DEPTH": "24",
    "SE_NODE_SESSION_TIMEOUT": "86400",
    "SE_NODE_OVERRIDE_MAX_SESSIONS": "true",
    "SE_NODE_MAX_SESSIONS": "1",
}

GRID_READY_TIMEOUT = 15
GRID_POLL_INTERVAL = 0.5


def container_name(session_id: str) -> str:
    return f"{CONTAINER_PREFIX}{session_id[:12]}"


async def _run(cmd: str, timeout: float = 30) -> tuple[str, str, int]:
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


async def exec_in_container(session_id: str, cmd: str, timeout: float = 10) -> str:
    name = container_name(session_id)
    stdout, stderr, rc = await _run(f"docker exec {name} {cmd}", timeout=timeout)
    if rc != 0:
        raise RuntimeError(f"docker exec failed (rc={rc}): {stderr[:300]}")
    return stdout


async def create_container(
    session_id: str,
    width: int = 1920,
    height: int = 1080,
    user_agent: str | None = None,
    proxy: str | None = None,
    fingerprint_profile: dict | None = None,
    browser_lang: str = "zh-CN",
    image_name: str | None = None,
) -> str:
    name = container_name(session_id)
    vol_name = f"{name}-data"
    env = {
        **_STATIC_ENV,
        "SE_SCREEN_WIDTH": str(width),
        "SE_SCREEN_HEIGHT": str(height),
        "BROWSER_LANG": browser_lang,
    }
    if user_agent:
        env["BROWSER_UA"] = user_agent
    if proxy:
        env["BROWSER_PROXY"] = proxy
    if fingerprint_profile is not None:
        env["FINGERPRINT_PROFILE"] = base64.b64encode(
            json.dumps(fingerprint_profile, separators=(",", ":")).encode()
        ).decode()
    env_args = " ".join(f"-e {k}='{v}'" for k, v in env.items())
    if not image_name:
        raise RuntimeError(
            "No browser image available. Build one in Settings > Browser Images."
        )
    cmd = (
        f"docker run -d --name {name} "
        f"--label {_PREFIX}.session_id={session_id} "
        f"-p 0:4444 -p 0:7900 "
        f"--shm-size={SHM_SIZE} "
        f"-v {vol_name}:/home/seluser/chrome-data "
        f"{env_args} "
        f"{image_name}"
    )
    logger.info("Creating container: %s (%dx%d)", name, width, height)
    stdout, stderr, rc = await _run(cmd, timeout=30)
    if rc != 0:
        raise RuntimeError(f"docker run failed (rc={rc}): {stderr[:300]}")
    logger.info("Container created: %s -> %s", name, stdout[:12])
    return stdout


async def start_container(session_id: str) -> None:
    name = container_name(session_id)
    logger.info("Starting container: %s", name)
    _, stderr, rc = await _run(f"docker start {name}", timeout=15)
    if rc != 0:
        raise RuntimeError(f"docker start failed: {stderr[:300]}")


async def stop_container(session_id: str) -> None:
    name = container_name(session_id)
    logger.info("Stopping container: %s", name)
    _, stderr, rc = await _run(f"docker stop {name}", timeout=30)
    if rc != 0:
        raise RuntimeError(f"docker stop failed: {stderr[:300]}")


async def pause_container(session_id: str) -> None:
    name = container_name(session_id)
    logger.info("Pausing container: %s", name)
    _, stderr, rc = await _run(f"docker pause {name}", timeout=10)
    if rc != 0:
        raise RuntimeError(f"docker pause failed: {stderr[:300]}")


async def unpause_container(session_id: str) -> None:
    name = container_name(session_id)
    logger.info("Unpausing container: %s", name)
    _, stderr, rc = await _run(f"docker unpause {name}", timeout=10)
    if rc != 0:
        raise RuntimeError(f"docker unpause failed: {stderr[:300]}")


async def remove_container(session_id: str, *, keep_volume: bool = False) -> None:
    name = container_name(session_id)
    vol_name = f"{name}-data"
    logger.info("Removing container: %s (keep_volume=%s)", name, keep_volume)
    _, stderr, rc = await _run(f"docker rm -f {name}", timeout=15)
    if rc != 0:
        logger.warning("docker rm -f %s failed (rc=%d): %s — ignoring", name, rc, stderr[:200])
    if not keep_volume:
        await _run(f"docker volume rm -f {vol_name}", timeout=10)


async def get_container_ports(session_id: str) -> dict[str, int]:
    name = container_name(session_id)
    stdout, stderr, rc = await _run(f"docker port {name}", timeout=5)
    if rc != 0:
        raise RuntimeError(f"docker port failed: {stderr[:300]}")
    ports: dict[str, int] = {}
    for line in stdout.splitlines():
        if not line.strip():
            continue
        # Format: "4444/tcp -> 0.0.0.0:49152"  or  "7900/tcp -> 0.0.0.0:49153"
        left, _, right = line.partition("->")
        container_port = left.strip().split("/")[0]
        host_port = right.strip().rsplit(":", 1)[-1]
        if container_port == "4444":
            ports["selenium_port"] = int(host_port)
        elif container_port == "7900":
            ports["vnc_port"] = int(host_port)
    if "selenium_port" not in ports or "vnc_port" not in ports:
        raise RuntimeError(f"Could not parse ports from: {stdout}")
    return ports


async def get_container_status(session_id: str) -> str:
    name = container_name(session_id)
    stdout, _, rc = await _run(
        f"docker inspect --format '{{{{.State.Status}}}}' {name}", timeout=5
    )
    if rc != 0:
        return "not_found"
    return stdout.strip("'\" \n")


async def start_cdp_logger(session_id: str) -> None:
    name = container_name(session_id)
    await _run(f"docker exec {name} supervisorctl start cdp-logger", timeout=5)


async def stop_cdp_logger(session_id: str) -> None:
    name = container_name(session_id)
    await _run(f"docker exec {name} supervisorctl stop cdp-logger", timeout=5)


async def get_all_container_statuses() -> dict[str, str]:
    """Batch query all bp- containers. Returns {container_name: status}."""
    stdout, _, rc = await _run(
        f'docker ps -a --filter "name={CONTAINER_PREFIX}" --format '
        '"{{.Names}}\\t{{.State}}"',
        timeout=10,
    )
    if rc != 0:
        return {}
    result: dict[str, str] = {}
    for line in stdout.splitlines():
        if "\t" not in line:
            continue
        cname, state = line.split("\t", 1)
        prefix = cname.removeprefix(CONTAINER_PREFIX)
        result[prefix] = state.strip().lower()
    return result


async def _wait_grid_ready(selenium_port: int) -> None:
    url = f"http://{DOCKER_HOST_ADDR}:{selenium_port}/status"
    elapsed = 0.0
    async with httpx.AsyncClient(timeout=3) as client:
        while elapsed < GRID_READY_TIMEOUT:
            try:
                resp = await client.get(url)
                data = resp.json()
                if data.get("value", {}).get("ready"):
                    logger.info("Grid ready on port %d (%.1fs)", selenium_port, elapsed)
                    return
            except Exception:
                pass
            await asyncio.sleep(GRID_POLL_INTERVAL)
            elapsed += GRID_POLL_INTERVAL
    logger.warning("Grid readiness timeout on port %d after %.1fs", selenium_port, elapsed)


async def _session_container_params(session_id: str) -> tuple[int, int, str | None, str | None, dict | None, str, str | None]:
    """Read device_preset + proxy_url + fingerprint_profile + browser_lang + chrome_version from DB and resolve to container params."""
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT device_preset, proxy_url, fingerprint_profile, browser_lang, tenant_id, chrome_version FROM sessions WHERE id = $1",
        session_id,
    )
    if not row:
        preset_data = get_preset(DEFAULT_PRESET)
        return preset_data["width"], preset_data["height"], None, None, None, "zh-CN", None
    preset_data = get_preset(row["device_preset"] or DEFAULT_PRESET)
    proxy = row["proxy_url"] or None
    browser_lang = row["browser_lang"] or "zh-CN"

    fp_profile = row["fingerprint_profile"]
    if fp_profile is None and row["tenant_id"]:
        fp_profile = await generate_profile(row["tenant_id"], browser_lang=browser_lang)
        await pool.execute(
            "UPDATE sessions SET fingerprint_profile = $1::jsonb WHERE id = $2",
            fp_profile, session_id,
        )
        logger.info("Lazy-generated fingerprint profile for session %s", session_id)

    chrome_version = row.get("chrome_version")
    tenant_id = row["tenant_id"]
    image_name: str | None = None
    if chrome_version and tenant_id:
        img_row = await pool.fetchrow(
            "SELECT image_tag FROM browser_images WHERE tenant_id = $1 AND chrome_version = $2 AND status = 'ready' LIMIT 1",
            tenant_id, chrome_version,
        )
        if img_row:
            image_name = img_row["image_tag"]

    if not image_name and tenant_id:
        img_row = await pool.fetchrow(
            "SELECT image_tag FROM browser_images WHERE tenant_id = $1 AND status = 'ready' ORDER BY chrome_major DESC LIMIT 1",
            row["tenant_id"],
        )
        if img_row:
            image_name = img_row["image_tag"]

    return preset_data["width"], preset_data["height"], preset_data.get("user_agent"), proxy, fp_profile, browser_lang, image_name


async def ensure_container_running(session_id: str) -> dict[str, int]:
    status = await get_container_status(session_id)

    if status == "not_found":
        width, height, ua, proxy, fp_profile, lang, img = await _session_container_params(session_id)
        await create_container(session_id, width=width, height=height, user_agent=ua, proxy=proxy, fingerprint_profile=fp_profile, browser_lang=lang, image_name=img)
        ports = await get_container_ports(session_id)
        await _wait_grid_ready(ports["selenium_port"])
        return ports

    if status == "paused":
        await unpause_container(session_id)
        return await get_container_ports(session_id)

    if status != "running":
        await start_container(session_id)
        ports = await get_container_ports(session_id)
        await _wait_grid_ready(ports["selenium_port"])
        return ports

    return await get_container_ports(session_id)


async def recreate_container(
    session_id: str,
    width: int,
    height: int,
    user_agent: str | None = None,
    proxy: str | None = None,
    fingerprint_profile: dict | None = None,
    browser_lang: str = "zh-CN",
    image_name: str | None = None,
) -> dict[str, int]:
    """Stop, remove (keep volume), create with new params, wait for grid."""
    try:
        await stop_container(session_id)
    except Exception:
        pass
    await remove_container(session_id, keep_volume=True)
    await create_container(session_id, width=width, height=height, user_agent=user_agent, proxy=proxy, fingerprint_profile=fingerprint_profile, browser_lang=browser_lang, image_name=image_name)
    ports = await get_container_ports(session_id)
    await _wait_grid_ready(ports["selenium_port"])
    return ports
