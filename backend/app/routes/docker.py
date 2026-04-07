from __future__ import annotations

import asyncio
import base64
import logging
import re

from fastapi import APIRouter
from pydantic import BaseModel

from app.container import container_name

logger = logging.getLogger("docker")
router = APIRouter()


async def _exec_in_container(cname: str, bash_cmd: str, timeout: float = 15) -> tuple[str, str, int]:
    cmd = f"docker exec {cname} bash -c '{bash_cmd}'"
    proc = await asyncio.create_subprocess_shell(
        cmd,
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


DISPLAY = ":99.0"
WM_CLASS = "chromium"


class NavigateRequest(BaseModel):
    sessionId: str
    url: str


@router.post("/api/docker/navigate")
async def docker_navigate(body: NavigateRequest):
    cname = container_name(body.sessionId)
    logger.info("navigate [%s] -> %s", cname, body.url)

    safe_url = body.url.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")

    bash_cmd = " && ".join([
        f"export DISPLAY={DISPLAY}",
        f'WID=$(xdotool search --name " - " --class {WM_CLASS} 2>/dev/null | head -1)',
        f"[ -z \"$WID\" ] && WID=$(xdotool search --class {WM_CLASS} 2>/dev/null | tail -1)",
        'if [ -z "$WID" ]; then echo "NO_WINDOW"; exit 1; fi',
        'echo "FOUND_WID=$WID"',
        "xdotool windowactivate --sync $WID",
        "xdotool windowfocus --sync $WID",
        "sleep 0.3",
        "xdotool key ctrl+l",
        "sleep 0.3",
        f'xdotool type --clearmodifiers --delay 12 "{safe_url}"',
        "xdotool key Return",
    ])

    stdout, stderr, rc = await _exec_in_container(cname, bash_cmd, timeout=15)

    if rc != 0:
        if "NO_WINDOW" in stdout or "NO_WINDOW" in stderr:
            return {"ok": False, "error": "未找到浏览器窗口，请确认容器内浏览器已启动"}
        return {"ok": False, "error": (stderr or stdout)[:300]}

    return {"ok": True, "url": body.url}


class ClipboardRequest(BaseModel):
    sessionId: str
    action: str
    text: str | None = None


@router.post("/api/docker/clipboard")
async def docker_clipboard(body: ClipboardRequest):
    cname = container_name(body.sessionId)

    if body.action == "paste":
        if not body.text:
            return {"ok": False, "error": "缺少 text 参数"}
        logger.info("clipboard [%s]: paste (%d chars)", cname, len(body.text))
        b64 = base64.b64encode(body.text.encode("utf-8")).decode("ascii")
        bash_cmd = " && ".join([
            f"export DISPLAY={DISPLAY}",
            f'echo "{b64}" | base64 -d | xclip -selection clipboard',
            "xdotool key --clearmodifiers ctrl+v",
        ])
        stdout, stderr, rc = await _exec_in_container(cname, bash_cmd, timeout=10)
        if rc != 0:
            return {"ok": False, "error": (stderr or stdout)[:300] or "clipboard paste failed"}
        return {"ok": True}

    logger.info("clipboard [%s]: get", cname)
    bash_cmd = f"export DISPLAY={DISPLAY} && xclip -selection clipboard -o 2>/dev/null || true"
    stdout, stderr, rc = await _exec_in_container(cname, bash_cmd, timeout=10)
    if rc != 0:
        return {"ok": False, "error": (stderr or stdout)[:300] or "clipboard get failed"}
    return {"ok": True, "text": stdout}


class BrowserLangRequest(BaseModel):
    sessionId: str
    lang: str | None = None


@router.post("/api/docker/browser-lang")
async def docker_browser_lang(body: BrowserLangRequest):
    cname = container_name(body.sessionId)
    safe_lang = re.sub(r"[^a-zA-Z0-9_-]", "", body.lang or "zh-CN")
    logger.info("browser-lang [%s]: switching to %s", cname, safe_lang)
    bash_cmd = f'echo "{safe_lang}" > /tmp/browser-lang && pkill -x chromium || true'
    stdout, stderr, rc = await _exec_in_container(cname, bash_cmd, timeout=10)
    if rc != 0 and "timeout" in stderr:
        return {"ok": False, "error": "command timed out"}
    return {"ok": True, "lang": safe_lang}
