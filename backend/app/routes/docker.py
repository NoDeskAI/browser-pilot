from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import PROJECT_ROOT
from app.tools.docker import SERVICE_MAP, _docker_compose, _get_statuses, _images_exist

logger = logging.getLogger("routes.docker")
router = APIRouter()

XDOTOOL_TARGETS: dict[str, dict[str, str]] = {
    "selenium": {"service": "selenium", "display": ":99.0", "wmClass": "chromium"},
}


async def _exec_in_container(service: str, bash_cmd: str, timeout: float = 15) -> tuple[str, str, int]:
    cmd = f"docker compose exec -T {service} bash -c '{bash_cmd}'"
    proc = await asyncio.create_subprocess_shell(
        cmd,
        cwd=str(PROJECT_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return "", "timeout", -1
    return (
        stdout_bytes.decode("utf-8", errors="replace"),
        stderr_bytes.decode("utf-8", errors="replace"),
        proc.returncode or 0,
    )


# -----------------------------------------------------------------------
# GET /api/docker/status
# -----------------------------------------------------------------------

@router.get("/api/docker/status")
async def docker_status():
    statuses = await _get_statuses()
    return {"statuses": statuses}


# -----------------------------------------------------------------------
# POST /api/docker/start-all  |  stop-all
# -----------------------------------------------------------------------

@router.post("/api/docker/start-all")
async def docker_start_all():
    logger.info("start-all: building and starting all services")
    t0 = time.monotonic()
    try:
        stdout, stderr = await _docker_compose("up -d --build", timeout=900)
        elapsed = time.monotonic() - t0
        logger.info("start-all: done (%.1fs)", elapsed)
        return {"ok": True, "output": stdout + stderr}
    except Exception as exc:
        elapsed = time.monotonic() - t0
        logger.error("start-all: failed after %.1fs — %s", elapsed, exc)
        return {"ok": False, "error": str(exc)[:500]}


@router.post("/api/docker/stop-all")
async def docker_stop_all():
    logger.info("stop-all: stopping all services")
    t0 = time.monotonic()
    try:
        stdout, stderr = await _docker_compose("stop", timeout=60)
        elapsed = time.monotonic() - t0
        logger.info("stop-all: done (%.1fs)", elapsed)
        return {"ok": True, "output": stdout + stderr}
    except Exception as exc:
        elapsed = time.monotonic() - t0
        logger.error("stop-all: failed after %.1fs — %s", elapsed, exc)
        return {"ok": False, "error": str(exc)[:500]}


# -----------------------------------------------------------------------
# POST /api/docker/start/{solution_id}  |  stop/{solution_id}
# -----------------------------------------------------------------------

@router.post("/api/docker/start/{solution_id}")
async def docker_start(solution_id: str):
    services = SERVICE_MAP.get(solution_id)
    if not services:
        return {"error": f"Unknown solution: {solution_id}"}
    logger.info("start [%s]: %s", solution_id, ", ".join(services))
    t0 = time.monotonic()
    try:
        has_images = await _images_exist(services)
        timeout = 60 if has_images else 600
        stdout, stderr = await _docker_compose(f"up -d {' '.join(services)}", timeout=timeout)
        elapsed = time.monotonic() - t0
        logger.info("start [%s]: done (%.1fs, images_cached=%s)", solution_id, elapsed, has_images)
        return {"ok": True, "output": stdout + stderr}
    except Exception as exc:
        elapsed = time.monotonic() - t0
        logger.error("start [%s]: failed after %.1fs — %s", solution_id, elapsed, exc)
        return {"ok": False, "error": str(exc)[:500]}


@router.post("/api/docker/stop/{solution_id}")
async def docker_stop(solution_id: str):
    services = SERVICE_MAP.get(solution_id)
    if not services:
        return {"error": f"Unknown solution: {solution_id}"}
    logger.info("stop [%s]: %s", solution_id, ", ".join(services))
    t0 = time.monotonic()
    try:
        stdout, stderr = await _docker_compose(f"stop {' '.join(services)}", timeout=60)
        elapsed = time.monotonic() - t0
        logger.info("stop [%s]: done (%.1fs)", solution_id, elapsed)
        return {"ok": True, "output": stdout + stderr}
    except Exception as exc:
        elapsed = time.monotonic() - t0
        logger.error("stop [%s]: failed after %.1fs — %s", solution_id, elapsed, exc)
        return {"ok": False, "error": str(exc)[:500]}


# -----------------------------------------------------------------------
# POST /api/docker/navigate
# -----------------------------------------------------------------------

class NavigateRequest(BaseModel):
    solutionId: str
    url: str


@router.post("/api/docker/navigate")
async def docker_navigate(body: NavigateRequest):
    target = XDOTOOL_TARGETS.get(body.solutionId)
    if not target:
        return {"ok": False, "error": f"未知方案: {body.solutionId}"}

    logger.info("navigate [%s] -> %s", body.solutionId, body.url)

    safe_url = body.url.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
    bash_cmd = " && ".join([
        f"export DISPLAY={target['display']}",
        f"WID=$(xdotool search --name \" - \" --class {target['wmClass']} 2>/dev/null | head -1)",
        f"[ -z \"$WID\" ] && WID=$(xdotool search --class {target['wmClass']} 2>/dev/null | tail -1)",
        'if [ -z "$WID" ]; then echo "NO_WINDOW"; exit 1; fi',
        "xdotool windowactivate $WID",
        "sleep 0.2",
        "xdotool key ctrl+l",
        "sleep 0.3",
        f'xdotool type --clearmodifiers --delay 12 "{safe_url}"',
        "xdotool key Return",
    ])

    stdout, stderr, rc = await _exec_in_container(target["service"], bash_cmd, timeout=15)
    if rc != 0:
        if "NO_WINDOW" in stdout or "NO_WINDOW" in stderr:
            logger.warning("navigate [%s]: no browser window found", body.solutionId)
            return {"ok": False, "error": "未找到浏览器窗口，请确认容器内浏览器已启动"}
        logger.error("navigate [%s]: xdotool failed (rc=%d)", body.solutionId, rc)
        return {"ok": False, "error": (stderr or stdout)[:300]}

    logger.info("navigate [%s]: done", body.solutionId)
    return {"ok": True, "url": body.url}


# -----------------------------------------------------------------------
# POST /api/docker/clipboard
# -----------------------------------------------------------------------

class ClipboardRequest(BaseModel):
    solutionId: str
    action: str
    text: str | None = None


@router.post("/api/docker/clipboard")
async def docker_clipboard(body: ClipboardRequest):
    target = XDOTOOL_TARGETS.get(body.solutionId)
    if not target:
        return {"ok": False, "error": f"未知方案: {body.solutionId}"}

    if body.action == "paste":
        if not body.text:
            return {"ok": False, "error": "缺少 text 参数"}
        logger.info("clipboard [%s]: paste (%d chars)", body.solutionId, len(body.text))
        b64 = base64.b64encode(body.text.encode("utf-8")).decode("ascii")
        bash_cmd = " && ".join([
            f"export DISPLAY={target['display']}",
            f'echo "{b64}" | base64 -d | xclip -selection clipboard',
            "xdotool key --clearmodifiers ctrl+v",
        ])
        stdout, stderr, rc = await _exec_in_container(target["service"], bash_cmd, timeout=10)
        if rc != 0:
            logger.error("clipboard [%s]: paste failed (rc=%d)", body.solutionId, rc)
            return {"ok": False, "error": (stderr or stdout)[:300] or "clipboard paste failed"}
        return {"ok": True}

    logger.info("clipboard [%s]: get", body.solutionId)
    bash_cmd = f"export DISPLAY={target['display']} && xclip -selection clipboard -o 2>/dev/null || true"
    stdout, stderr, rc = await _exec_in_container(target["service"], bash_cmd, timeout=10)
    if rc != 0:
        logger.error("clipboard [%s]: get failed (rc=%d)", body.solutionId, rc)
        return {"ok": False, "error": (stderr or stdout)[:300] or "clipboard get failed"}
    return {"ok": True, "text": stdout}


# -----------------------------------------------------------------------
# POST /api/docker/browser-lang
# -----------------------------------------------------------------------

class BrowserLangRequest(BaseModel):
    solutionId: str
    lang: str | None = None


@router.post("/api/docker/browser-lang")
async def docker_browser_lang(body: BrowserLangRequest):
    target = XDOTOOL_TARGETS.get(body.solutionId)
    if not target:
        return {"ok": False, "error": f"未知方案: {body.solutionId}"}

    safe_lang = re.sub(r"[^a-zA-Z0-9_-]", "", body.lang or "zh-CN")
    logger.info("browser-lang [%s]: switching to %s", body.solutionId, safe_lang)
    bash_cmd = f'echo "{safe_lang}" > /tmp/browser-lang && pkill -x chromium || true'
    stdout, stderr, rc = await _exec_in_container(target["service"], bash_cmd, timeout=10)
    if rc != 0 and "timeout" in stderr:
        logger.error("browser-lang [%s]: timed out", body.solutionId)
        return {"ok": False, "error": "command timed out"}
    logger.info("browser-lang [%s]: done", body.solutionId)
    return {"ok": True, "lang": safe_lang}
