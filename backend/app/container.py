from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

logger = logging.getLogger("container")

CONTAINER_PREFIX = "ndb-"
IMAGE_NAME = "no-window-browser-selenium"
SHM_SIZE = "2g"

CONTAINER_ENV = {
    "SE_VNC_NO_PASSWORD": "1",
    "SE_SCREEN_WIDTH": "1280",
    "SE_SCREEN_HEIGHT": "800",
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


async def create_container(session_id: str) -> str:
    name = container_name(session_id)
    vol_name = f"{name}-data"
    env_args = " ".join(f"-e {k}={v}" for k, v in CONTAINER_ENV.items())
    cmd = (
        f"docker run -d --name {name} "
        f"--label ndb.session_id={session_id} "
        f"-p 0:4444 -p 0:7900 "
        f"--shm-size={SHM_SIZE} "
        f"-v {vol_name}:/home/seluser/chrome-data "
        f"{env_args} "
        f"{IMAGE_NAME}"
    )
    logger.info("Creating container: %s", name)
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


async def remove_container(session_id: str) -> None:
    name = container_name(session_id)
    vol_name = f"{name}-data"
    logger.info("Removing container: %s", name)
    _, stderr, rc = await _run(f"docker rm -f {name}", timeout=15)
    if rc != 0:
        logger.warning("docker rm -f %s failed (rc=%d): %s — ignoring", name, rc, stderr[:200])
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
    """Batch query all ndb- containers. Returns {container_name: status}."""
    stdout, _, rc = await _run(
        'docker ps -a --filter "name=ndb-" --format "{{.Names}}\\t{{.State}}"',
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
    url = f"http://localhost:{selenium_port}/status"
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


async def ensure_container_running(session_id: str) -> dict[str, int]:
    status = await get_container_status(session_id)

    if status == "not_found":
        await create_container(session_id)
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
