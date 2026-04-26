from __future__ import annotations

import asyncio
import logging
import platform
import re
import time
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

_version_cache: dict = {"versions": [], "fetched_at": 0.0}
_CACHE_TTL = 21600  # 6 hours


async def _fetch_available_versions() -> list[dict]:
    repo = "selenium/standalone-chrome"
    base_url = f"https://hub.docker.com/v2/repositories/{repo}/tags"
    majors: set[int] = set()

    async with httpx.AsyncClient(timeout=15) as client:
        resps = await asyncio.gather(
            *[client.get(base_url, params={"page": p, "page_size": 100}) for p in range(1, 6)],
            return_exceptions=True,
        )
        for resp in resps:
            if isinstance(resp, Exception) or resp.status_code != 200:
                continue
            for tag in resp.json().get("results", []):
                m = re.match(r"^(\d+)\.\d+", tag["name"])
                if m:
                    majors.add(int(m.group(1)))

    return [
        {"tag": f"{v}.0", "major": v}
        for v in sorted(majors, reverse=True)
        if v >= 80
    ]


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


async def _do_build(
    image_id: str,
    base_image: str,
    image_tag: str,
    major: int,
    chrome_version: str = "",
    docker_platform: str = "",
) -> None:
    pool = get_pool()
    await pool.execute(
        "UPDATE browser_images SET status = 'building' WHERE id = $1", image_id
    )

    platform_arg = f"--platform={docker_platform} " if docker_platform else ""
    pull_cmd = f"docker pull {platform_arg}{base_image}"
    logger.info("Pulling base image: %s", pull_cmd)
    _, pull_err, pull_rc = await _run(pull_cmd, timeout=120)
    if pull_rc != 0:
        logger.warning("Pull failed (rc=%d), proceeding with local cache: %s", pull_rc, pull_err[:200])

    cmd = f"docker build {platform_arg}--build-arg BASE_IMAGE={base_image}"
    if chrome_version:
        cmd += f" --build-arg CHROME_VERSION={chrome_version}"
    cmd += f" -t {image_tag} {_BUILD_DIR}"
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

    ver_cmd = f"docker run --rm {platform_arg}{image_tag} chromium --version 2>/dev/null || docker run --rm {platform_arg}{image_tag} google-chrome --version 2>/dev/null"
    ver_out, _, ver_rc = await _run(ver_cmd, timeout=30)
    chrome_version = ""
    if ver_rc == 0 and ver_out:
        m = re.search(r"(\d+\.\d+\.\d+\.\d+)", ver_out)
        if m:
            chrome_version = m.group(1)

    actual_major = int(chrome_version.split(".")[0]) if chrome_version else 0
    if actual_major and actual_major != major:
        await pool.execute(
            "UPDATE browser_images SET status = 'failed', build_log = $1 WHERE id = $2",
            f"Version mismatch: requested Chrome {major} but got {chrome_version}. "
            f"This platform does not have Chrome {major} available.",
            image_id,
        )
        logger.error("Version mismatch for %s: requested %d, got %s", image_tag, major, chrome_version)
        try:
            await _run(f"docker rmi {image_tag}", timeout=30)
        except Exception:
            pass
        return

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

    chrome_version_arg = ""
    docker_platform = ""
    exists = await _check_tag_exists(repo, tag)
    if not exists:
        if _IS_ARM:
            amd64_base = f"selenium/standalone-chrome:{ver_tag}"
            amd64_exists = await _check_tag_exists("selenium/standalone-chrome", ver_tag)
            if not amd64_exists:
                raise HTTPException(
                    422,
                    f"Chrome {major} is not available. Docker Hub tag '{amd64_base}' was not found.",
                )
            base_image = amd64_base
            docker_platform = "linux/amd64"
            logger.info(
                "ARM64 tag %s not found, using amd64 emulation: base=%s platform=%s",
                ver_tag,
                base_image,
                docker_platform,
            )
        else:
            raise HTTPException(422, f"Docker Hub tag '{base_image}' not found. Check available versions on Docker Hub.")

    pool = get_pool()
    image_tag = _image_tag_for(major)

    existing = await pool.fetchrow(
        "SELECT id, status FROM browser_images WHERE tenant_id = $1 AND image_tag = $2",
        user.tenant_id, image_tag,
    )
    if existing:
        if existing["status"] in ("building", "pending"):
            raise HTTPException(409, "This version is already being built.")
        if existing["status"] == "ready":
            raise HTTPException(409, "This version is already built.")
        image_id = existing["id"]
        await pool.execute(
            "UPDATE browser_images SET status = 'pending', build_log = '', base_image = $1, chrome_version = '' WHERE id = $2",
            base_image, image_id,
        )
    else:
        image_id = str(uuid.uuid4())
        await pool.execute(
            "INSERT INTO browser_images (id, tenant_id, chrome_major, chrome_version, base_image, image_tag, status) "
            "VALUES ($1, $2, $3, $4, $5, $6, 'pending')",
            image_id, user.tenant_id, major, "", base_image, image_tag,
        )

    asyncio.create_task(
        _do_build(
            image_id,
            base_image,
            image_tag,
            major,
            chrome_version=chrome_version_arg,
            docker_platform=docker_platform,
        )
    )

    return {"id": image_id, "status": "building", "chromeMajor": major, "imageTag": image_tag}


@router.get("/api/browser-images")
async def list_images(user: CurrentUser = Depends(get_current_user)):
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT bi.id, bi.chrome_major, bi.chrome_version, bi.base_image, bi.image_tag, "
        "       bi.status, bi.build_log, bi.created_at, "
        "       COALESCE(sc.cnt, 0) as session_count "
        "FROM browser_images bi "
        "LEFT JOIN ( "
        "    SELECT tenant_id, chrome_version, COUNT(*) as cnt "
        "    FROM sessions "
        "    WHERE chrome_version IS NOT NULL AND chrome_version != '' "
        "    GROUP BY tenant_id, chrome_version "
        ") sc ON bi.tenant_id = sc.tenant_id AND bi.chrome_version = sc.chrome_version "
        "WHERE bi.tenant_id = $1 "
        "ORDER BY bi.chrome_major DESC",
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
                "sessionCount": int(r["session_count"]),
            }
            for r in rows
        ]
    }


@router.get("/api/browser-images/available-versions")
async def available_versions(
    refresh: bool = False,
    user: CurrentUser = Depends(get_current_user),
):
    now = time.time()
    if not refresh and _version_cache["versions"] and now - _version_cache["fetched_at"] < _CACHE_TTL:
        return {"versions": _version_cache["versions"], "isArm": _IS_ARM}
    try:
        versions = await _fetch_available_versions()
    except Exception:
        logger.warning("Failed to fetch available versions from Docker Hub")
        if _version_cache["versions"]:
            return {"versions": _version_cache["versions"], "isArm": _IS_ARM}
        raise HTTPException(502, "Failed to fetch available versions from Docker Hub")
    _version_cache["versions"] = versions
    _version_cache["fetched_at"] = now
    return {"versions": versions, "isArm": _IS_ARM}


@router.delete("/api/browser-images/{image_id}")
async def delete_image(
    image_id: str,
    user: CurrentUser = Depends(require_role(["superadmin", "admin"])),
):
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT image_tag, chrome_version FROM browser_images WHERE id = $1 AND tenant_id = $2",
        image_id, user.tenant_id,
    )
    if not row:
        raise HTTPException(404, "Image not found")

    if row["chrome_version"]:
        cnt = await pool.fetchval(
            "SELECT COUNT(*) FROM sessions WHERE tenant_id = $1 AND chrome_version = $2",
            user.tenant_id, row["chrome_version"],
        )
        if cnt > 0:
            raise HTTPException(409, f"Image is in use by {cnt} session(s). Delete those sessions first.")

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
