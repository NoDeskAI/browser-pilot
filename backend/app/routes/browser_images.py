from __future__ import annotations

import asyncio
import logging
import platform
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.dependencies import CurrentUser, get_current_user, require_role
from app.config import PROJECT_ROOT
from app.db import get_pool

logger = logging.getLogger("routes.browser_images")
router = APIRouter()

_ARCH = platform.machine()
_IS_ARM = _ARCH in ("aarch64", "arm64")

_BUILD_DIR = str(PROJECT_ROOT / "services" / "selenium-chrome")


def _base_image_for(ver: str) -> str:
    if _IS_ARM:
        return f"seleniarm/standalone-chromium:{ver}"
    return f"selenium/standalone-chrome:{ver}"


def _image_tag_for(major: int) -> str:
    return f"browser-pilot-selenium:chrome-{major}"


async def _check_tag_exists(repo: str, tag: str) -> bool:
    url = f"https://hub.docker.com/v2/repositories/{repo}/tags/{tag}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            return resp.status_code == 200
    except Exception:
        return False


async def _run(cmd: str, timeout: float = 600) -> tuple[str, str, int]:
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


async def _do_build(image_id: str, base_image: str, image_tag: str, major: int) -> None:
    pool = get_pool()
    await pool.execute(
        "UPDATE browser_images SET status = 'building' WHERE id = $1", image_id
    )

    cmd = f"docker build --build-arg BASE_IMAGE={base_image} -t {image_tag} {_BUILD_DIR}"
    logger.info("Building image: %s", cmd)
    stdout, stderr, rc = await _run(cmd, timeout=900)

    if rc != 0:
        log_text = (stderr or stdout)[:4000]
        await pool.execute(
            "UPDATE browser_images SET status = 'failed', build_log = $1 WHERE id = $2",
            log_text, image_id,
        )
        logger.error("Build failed for %s: %s", image_tag, log_text[:200])
        return

    ver_cmd = f"docker run --rm {image_tag} chromium --version 2>/dev/null || docker run --rm {image_tag} google-chrome --version 2>/dev/null"
    ver_out, _, ver_rc = await _run(ver_cmd, timeout=30)
    chrome_version = ""
    if ver_rc == 0 and ver_out:
        import re
        m = re.search(r"(\d+\.\d+\.\d+\.\d+)", ver_out)
        if m:
            chrome_version = m.group(1)

    await pool.execute(
        "UPDATE browser_images SET status = 'ready', chrome_version = $1, build_log = $2 WHERE id = $3",
        chrome_version or f"{major}.0.0.0",
        f"Build OK. Detected version: {chrome_version or 'unknown'}",
        image_id,
    )
    logger.info("Image built: %s -> Chrome %s", image_tag, chrome_version)


class BuildBody(BaseModel):
    chromeVersion: str


@router.post("/api/browser-images/build")
async def build_image(
    body: BuildBody,
    user: CurrentUser = Depends(require_role(["superadmin", "admin"])),
):
    raw = body.chromeVersion.strip()
    major = int(raw.split(".")[0])
    ver_tag = f"{major}.0" if "." not in raw or raw == str(major) else raw

    base_image = _base_image_for(ver_tag)
    repo = base_image.split(":")[0]
    tag = base_image.split(":")[1]

    exists = await _check_tag_exists(repo, tag)
    if not exists:
        raise HTTPException(
            422,
            f"Docker Hub tag '{base_image}' not found. "
            f"{'ARM64 (seleniarm) images max out at 124.0.' if _IS_ARM else 'Check available versions on Docker Hub.'}",
        )

    pool = get_pool()
    image_id = str(uuid.uuid4())
    image_tag = _image_tag_for(major)

    await pool.execute(
        "INSERT INTO browser_images (id, tenant_id, chrome_major, chrome_version, base_image, image_tag, status) "
        "VALUES ($1, $2, $3, $4, $5, $6, 'pending')",
        image_id, user.tenant_id, major, "", base_image, image_tag,
    )

    asyncio.create_task(_do_build(image_id, base_image, image_tag, major))

    return {"id": image_id, "status": "building", "chromeMajor": major, "imageTag": image_tag}


@router.get("/api/browser-images")
async def list_images(user: CurrentUser = Depends(get_current_user)):
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT id, chrome_major, chrome_version, base_image, image_tag, status, build_log, created_at "
        "FROM browser_images WHERE tenant_id = $1 ORDER BY chrome_major DESC",
        user.tenant_id,
    )
    return {
        "images": [
            {
                "id": r["id"],
                "chromeMajor": r["chrome_major"],
                "chromeVersion": r["chrome_version"],
                "baseImage": r["base_image"],
                "imageTag": r["image_tag"],
                "status": r["status"],
                "buildLog": r["build_log"],
                "createdAt": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]
    }


@router.delete("/api/browser-images/{image_id}")
async def delete_image(
    image_id: str,
    user: CurrentUser = Depends(require_role(["superadmin", "admin"])),
):
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT image_tag FROM browser_images WHERE id = $1 AND tenant_id = $2",
        image_id, user.tenant_id,
    )
    if not row:
        raise HTTPException(404, "Image not found")

    await pool.execute("DELETE FROM browser_images WHERE id = $1", image_id)

    try:
        await _run(f"docker rmi {row['image_tag']}", timeout=30)
    except Exception:
        pass

    return {"ok": True}


@router.get("/api/browser-images/default")
async def get_default_image(user: CurrentUser = Depends(get_current_user)):
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT id, chrome_major, chrome_version, image_tag FROM browser_images "
        "WHERE tenant_id = $1 AND status = 'ready' ORDER BY chrome_major DESC LIMIT 1",
        user.tenant_id,
    )
    if not row:
        return {"default": None}
    return {
        "default": {
            "id": row["id"],
            "chromeMajor": row["chrome_major"],
            "chromeVersion": row["chrome_version"],
            "imageTag": row["image_tag"],
        }
    }
