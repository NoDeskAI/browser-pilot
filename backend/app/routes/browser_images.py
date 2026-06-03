from __future__ import annotations

import asyncio
import logging
import platform
import re
import shlex
import time
import uuid
from datetime import datetime, timezone
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.dependencies import CurrentUser, get_current_user, require_role
from app.config import BROWSER_RUNTIME_COMMAND_MAX_TIMEOUT, CLOAK_BROWSER_IMAGE_NAME, PROJECT_ROOT
from app.db import get_pool
from app.edition import reject_browser_images_api
from app.runtime_control import run_runtime_command as _run

logger = logging.getLogger("routes.browser_images")
router = APIRouter()

_ARCH = platform.machine()
_IS_ARM = _ARCH in ("aarch64", "arm64")

_BUILD_DIR = str(PROJECT_ROOT / "services" / "selenium-chrome")
_CLOAK_BUILD_DIR = str(PROJECT_ROOT / "services" / "cloak-chromium-runtime")
_RUNTIME_STANDARD = "standard_chrome"
_RUNTIME_CLOAK = "cloak_chromium"
_CLOAK_LEGACY_IMAGE_ID = "cloak_chromium_legacy"

_version_cache: dict = {"versions": [], "fetched_at": 0.0}
_CACHE_TTL = 21600  # 6 hours
_CLOAK_BUILD_TIMEOUT = max(3600, BROWSER_RUNTIME_COMMAND_MAX_TIMEOUT)
_cloak_build_lock = asyncio.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _elapsed_seconds(started_at: float | int | None) -> int:
    if not started_at:
        return 0
    return max(0, int(time.time() - float(started_at)))


def _estimated_progress(elapsed_seconds: int, *, timeout: int) -> int:
    if elapsed_seconds <= 0:
        return 3
    return max(8, min(95, int(8 + (elapsed_seconds / max(1, timeout)) * 87)))


def _progress_payload(
    *,
    status: str,
    stage: str,
    progress: int,
    started_at: float | int | None = None,
    updated_at: str | None = None,
    log: str = "",
    manual_command: str = "",
) -> dict:
    elapsed = _elapsed_seconds(started_at)
    return {
        "stage": stage,
        "progress": max(0, min(100, int(progress or 0))),
        "elapsedSeconds": elapsed,
        "startedAt": datetime.fromtimestamp(float(started_at), timezone.utc).isoformat() if started_at else None,
        "updatedAt": updated_at,
        "log": log,
        "manualCommand": manual_command,
        "indeterminate": status in {"pending", "building"} and progress >= 95,
    }


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


def _is_full_chrome_version(ver: str) -> bool:
    return bool(re.fullmatch(r"\d+\.\d+\.\d+\.\d+", ver.strip()))


def _image_tag_for(ver_tag: str, image_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", ver_tag.strip()).strip(".-")
    slug = slug[:80] or "unknown"
    return f"browser-pilot-selenium:chrome-{slug}-{image_id[:8]}"


def _safe_image_name(raw: str, fallback: str) -> str:
    name = re.sub(r"\s+", " ", str(raw or "").strip())
    return name[:80] or fallback


def _slug_for(raw: str, fallback: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(raw or "").strip()).strip(".-").lower()
    return (slug[:72] or fallback).strip(".-") or fallback


def _cloak_image_repo() -> str:
    return CLOAK_BROWSER_IMAGE_NAME.split(":", 1)[0] or "browser-pilot-cloak"


def _cloak_image_tag_for(image_id: str, image_name: str) -> str:
    slug = _slug_for(image_name, "runtime")
    return f"{_cloak_image_repo()}:{slug}-{image_id[:8]}"


async def _docker_image_created_at(image_tag: str) -> str | None:
    out, _, rc = await _run(
        f"docker image inspect --format '{{{{.Created}}}}' {shlex.quote(image_tag)}",
        timeout=20,
    )
    if rc != 0:
        return None
    created_at = out.strip()
    return created_at or None


async def _legacy_cloak_runtime_image_payload(session_count: int = 0) -> dict | None:
    image_tag = CLOAK_BROWSER_IMAGE_NAME
    docker_created_at = await _docker_image_created_at(image_tag)
    if not docker_created_at and session_count <= 0:
        return None
    status = "ready" if docker_created_at else "missing"
    build_log = "Legacy Cloak Chromium image is available locally." if docker_created_at else ""
    stage = status
    progress = 100 if docker_created_at else 0
    return {
        "id": _CLOAK_LEGACY_IMAGE_ID,
        "runtime": _RUNTIME_CLOAK,
        "name": "Cloak Chromium Legacy",
        "chromeMajor": None,
        "chromeVersion": None,
        "imageTag": image_tag,
        "baseImage": "services/cloak-chromium-runtime",
        "status": status,
        "buildLog": build_log,
        "createdAt": docker_created_at,
        "sessionCount": int(session_count or 0),
        "buildProgress": _progress_payload(
            status=status,
            stage=stage,
            progress=progress,
            started_at=None,
            updated_at=None,
            log=build_log,
            manual_command=f"docker build -t {shlex.quote(image_tag)} {shlex.quote(_CLOAK_BUILD_DIR)}",
        ),
    }


def _row_value(row, key: str, default=None):
    try:
        return row[key]
    except Exception:
        return default


def _version_tuple(version: str) -> tuple[int, ...]:
    values: list[int] = []
    for part in re.findall(r"\d+", str(version or "")):
        try:
            values.append(int(part))
        except ValueError:
            values.append(0)
    return tuple(values or [0])


def _display_rank(row) -> tuple:
    status = _row_value(row, "status", "")
    status_rank = 3 if status in {"building", "pending"} else 2 if status == "ready" else 1
    image_tag = str(_row_value(row, "image_tag", "") or "")
    fpagent_rank = 0 if image_tag.endswith("-fpagent") else 1
    created_at = _row_value(row, "created_at")
    created_ts = created_at.timestamp() if hasattr(created_at, "timestamp") else 0
    return (
        status_rank,
        _version_tuple(str(_row_value(row, "chrome_version", "") or "")),
        fpagent_rank,
        created_ts,
    )


def _canonical_image_rows(rows) -> list:
    by_major: dict[int, object] = {}
    for row in rows:
        major = int(_row_value(row, "chrome_major", 0) or 0)
        current = by_major.get(major)
        if current is None or _display_rank(row) > _display_rank(current):
            by_major[major] = row
    return sorted(by_major.values(), key=lambda r: int(_row_value(r, "chrome_major", 0) or 0), reverse=True)


def _browser_image_payload(row) -> dict:
    major = _row_value(row, "chrome_major")
    runtime = _row_value(row, "runtime", _RUNTIME_STANDARD) or _RUNTIME_STANDARD
    name = _row_value(row, "name", "") or ""
    return {
        "id": _row_value(row, "id"),
        "runtime": runtime,
        "name": name or (f"Chrome {major}" if runtime == _RUNTIME_STANDARD else "Cloak Chromium"),
        "chromeMajor": major if runtime == _RUNTIME_STANDARD else None,
        "chromeVersion": _row_value(row, "chrome_version"),
        "baseImage": _row_value(row, "base_image"),
        "imageTag": _row_value(row, "image_tag"),
        "status": _row_value(row, "status"),
        "buildLog": _row_value(row, "build_log"),
        "createdAt": _row_value(row, "created_at").isoformat() if _row_value(row, "created_at") else None,
        "sessionCount": int(_row_value(row, "session_count", 0) or 0),
        "buildProgress": _image_build_progress(row),
    }


def _image_build_progress(row) -> dict:
    status = str(_row_value(row, "status", "") or "")
    runtime = _row_value(row, "runtime", _RUNTIME_STANDARD) or _RUNTIME_STANDARD
    created_at = _row_value(row, "created_at")
    started_at = created_at.timestamp() if hasattr(created_at, "timestamp") else None
    if status == "ready":
        progress = 100
        stage = "ready"
    elif status == "failed":
        progress = 100
        stage = "failed"
    elif status == "pending":
        progress = 3
        stage = "queued"
    elif status == "building":
        progress = _estimated_progress(_elapsed_seconds(started_at), timeout=BROWSER_RUNTIME_COMMAND_MAX_TIMEOUT)
        stage = "building"
    else:
        progress = 0
        stage = status or "unknown"
    build_dir = _CLOAK_BUILD_DIR if runtime == _RUNTIME_CLOAK else _BUILD_DIR
    return _progress_payload(
        status=status,
        stage=stage,
        progress=progress,
        started_at=started_at,
        updated_at=None,
        log=str(_row_value(row, "build_log", "") or ""),
        manual_command=f"docker build -t {_row_value(row, 'image_tag', '')} {build_dir}",
    )


async def _check_tag_exists(repo: str, tag: str) -> bool:
    url = f"https://hub.docker.com/v2/repositories/{repo}/tags/{tag}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            return resp.status_code == 200
    except Exception:
        return False


async def _do_build(
    image_id: str,
    tenant_id: str,
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

    ver_out, ver_err, ver_rc = await _run(f"docker run --rm {platform_arg}{image_tag} chromium --version", timeout=30)
    if ver_rc != 0:
        ver_out, ver_err, ver_rc = await _run(
            f"docker run --rm {platform_arg}{image_tag} google-chrome --version",
            timeout=30,
        )
    chrome_version = ""
    ver_text = ver_out or ver_err
    if ver_rc == 0 and ver_text:
        m = re.search(r"(\d+\.\d+\.\d+\.\d+)", ver_text)
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

    detected_version = chrome_version or f"{major}.0.0.0"
    duplicate_image_tag = ""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1), hashtext($2))",
                tenant_id,
                detected_version,
            )
            duplicate = await conn.fetchrow(
                "SELECT id, image_tag FROM browser_images "
                "WHERE tenant_id = $1 AND chrome_version = $2 AND status = 'ready' "
                "AND COALESCE(runtime, 'standard_chrome') = 'standard_chrome' AND id <> $3 "
                "ORDER BY created_at DESC LIMIT 1",
                tenant_id,
                detected_version,
                image_id,
            )
            if duplicate:
                await conn.execute(
                    "UPDATE browser_images SET status = 'failed', build_log = $1 WHERE id = $2",
                    f"Chrome {detected_version} is already built.",
                    image_id,
                )
                duplicate_image_tag = duplicate["image_tag"]
            else:
                await conn.execute(
                    "UPDATE browser_images SET status = 'ready', chrome_version = $1, build_log = $2 WHERE id = $3",
                    detected_version,
                    f"Build OK. Detected version: {detected_version}",
                    image_id,
                )
    if duplicate_image_tag:
        logger.info(
            "Duplicate Chrome version detected for %s: %s already provided by %s",
            image_tag,
            detected_version,
            duplicate_image_tag,
        )
        try:
            await _run(f"docker rmi {image_tag}", timeout=30)
        except Exception:
            pass
        return
    logger.info("Image built: %s -> Chrome %s", image_tag, detected_version)


async def _do_build_cloak_runtime(image_id: str, image_tag: str) -> None:
    pool = get_pool()
    await pool.execute(
        "UPDATE browser_images SET status = 'building', build_log = $1 WHERE id = $2",
        "Pulling base image and building Cloak Chromium runtime image. First build can take a while.",
        image_id,
    )
    cmd = f"docker build -t {shlex.quote(image_tag)} {shlex.quote(_CLOAK_BUILD_DIR)}"
    logger.info("Building Cloak runtime image: %s", cmd)
    stdout, stderr, rc = await _run(cmd, timeout=_CLOAK_BUILD_TIMEOUT)
    log_text = (stderr or stdout or "").strip()
    if rc != 0:
        await pool.execute(
            "UPDATE browser_images SET status = 'failed', build_log = $1 WHERE id = $2",
            log_text[:4000] or "Cloak runtime image build failed.",
            image_id,
        )
        logger.error("Cloak runtime image build failed: %s", log_text[:300])
        return
    await pool.execute(
        "UPDATE browser_images SET status = 'ready', build_log = $1 WHERE id = $2",
        "Build OK. Cloak Chromium runtime image is available locally.",
        image_id,
    )
    logger.info("Cloak runtime image built: %s", image_tag)


class BuildBody(BaseModel):
    chromeVersion: str = ""
    runtime: Literal["standard_chrome", "cloak_chromium"] = "standard_chrome"
    imageName: str = ""


@router.post("/api/browser-images/cloak/build")
async def build_cloak_runtime_image(
    user: CurrentUser = Depends(require_role(["superadmin", "admin"])),
):
    reject_browser_images_api()
    return await _start_cloak_runtime_build(user=user, image_name="")


async def _start_cloak_runtime_build(*, user: CurrentUser, image_name: str = ""):
    async with _cloak_build_lock:
        pool = get_pool()
        display_name = _safe_image_name(image_name, f"Cloak Chromium {_now_iso()[:19].replace('T', ' ')}")
        image_id = str(uuid.uuid4())
        image_tag = _cloak_image_tag_for(image_id, display_name)
        await pool.execute(
            "INSERT INTO browser_images "
            "(id, tenant_id, runtime, name, chrome_major, chrome_version, base_image, image_tag, status, build_log) "
            "VALUES ($1, $2, 'cloak_chromium', $3, 0, '', $4, $5, 'pending', $6)",
            image_id,
            user.tenant_id,
            display_name,
            "services/cloak-chromium-runtime",
            image_tag,
            "Cloak Chromium runtime image build queued.",
        )
        asyncio.create_task(_do_build_cloak_runtime(image_id, image_tag))
        return {
            "id": image_id,
            "runtime": _RUNTIME_CLOAK,
            "name": display_name,
            "status": "building",
            "imageTag": image_tag,
        }


@router.post("/api/browser-images/build")
async def build_image(
    body: BuildBody,
    user: CurrentUser = Depends(require_role(["superadmin", "admin"])),
):
    reject_browser_images_api()
    if body.runtime == "cloak_chromium":
        return await _start_cloak_runtime_build(user=user, image_name=body.imageName)

    raw = body.chromeVersion.strip()
    if not raw:
        raise HTTPException(422, "Chrome version is required.")
    try:
        major = int(raw.split(".")[0])
    except ValueError as exc:
        raise HTTPException(422, "Invalid Chrome version.") from exc
    ver_tag = f"{major}.0" if "." not in raw or raw == str(major) else raw

    base_image = _base_image_for(ver_tag)
    repo = base_image.split(":")[0]
    tag = base_image.split(":")[1]

    chrome_version_arg = raw if _is_full_chrome_version(raw) else ""
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
    if _is_full_chrome_version(raw):
        existing_version = await pool.fetchrow(
            "SELECT id, status FROM browser_images "
            "WHERE tenant_id = $1 AND chrome_version = $2 AND status = 'ready' "
            "AND COALESCE(runtime, 'standard_chrome') = 'standard_chrome' "
            "ORDER BY created_at DESC LIMIT 1",
            user.tenant_id,
            raw,
        )
        if existing_version:
            raise HTTPException(409, f"Chrome {raw} is already built.")
    existing_build = await pool.fetchrow(
        "SELECT id, status FROM browser_images "
        "WHERE tenant_id = $1 AND base_image = $2 AND status IN ('pending', 'building') "
        "AND COALESCE(runtime, 'standard_chrome') = 'standard_chrome' "
        "ORDER BY created_at DESC LIMIT 1",
        user.tenant_id,
        base_image,
    )
    if existing_build:
        raise HTTPException(409, "This version is already being built.")

    image_id = str(uuid.uuid4())
    image_tag = _image_tag_for(ver_tag, image_id)
    await pool.execute(
        "INSERT INTO browser_images (id, tenant_id, chrome_major, chrome_version, base_image, image_tag, status) "
        "VALUES ($1, $2, $3, $4, $5, $6, 'pending')",
        image_id, user.tenant_id, major, "", base_image, image_tag,
    )

    asyncio.create_task(
        _do_build(
            image_id,
            user.tenant_id,
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
    reject_browser_images_api()
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT bi.id, COALESCE(bi.runtime, 'standard_chrome') AS runtime, COALESCE(bi.name, '') AS name, "
        "       bi.chrome_major, bi.chrome_version, bi.base_image, bi.image_tag, "
        "       bi.status, bi.build_log, bi.created_at, "
        "       COALESCE(sc_id.cnt, sc_chrome.cnt, 0) as session_count "
        "FROM browser_images bi "
        "LEFT JOIN ( "
        "    SELECT tenant_id, browser_image_id, COUNT(*) as cnt "
        "    FROM sessions "
        "    WHERE browser_image_id IS NOT NULL AND browser_image_id != '' "
        "    GROUP BY tenant_id, browser_image_id "
        ") sc_id ON bi.tenant_id = sc_id.tenant_id AND bi.id = sc_id.browser_image_id "
        "LEFT JOIN ( "
        "    SELECT tenant_id, chrome_version, COUNT(*) as cnt "
        "    FROM sessions "
        "    WHERE (browser_image_id IS NULL OR browser_image_id = '') "
        "      AND chrome_version IS NOT NULL AND chrome_version != '' "
        "    GROUP BY tenant_id, chrome_version "
        ") sc_chrome ON bi.tenant_id = sc_chrome.tenant_id "
        "   AND bi.chrome_version = sc_chrome.chrome_version "
        "   AND COALESCE(bi.runtime, 'standard_chrome') = 'standard_chrome' "
        "WHERE bi.tenant_id = $1 "
        "ORDER BY COALESCE(bi.runtime, 'standard_chrome'), bi.chrome_major DESC, bi.created_at DESC",
        user.tenant_id,
    )
    standard_rows = [
        row for row in rows
        if (_row_value(row, "runtime", _RUNTIME_STANDARD) or _RUNTIME_STANDARD) == _RUNTIME_STANDARD
    ]
    cloak_rows = [
        row for row in rows
        if (_row_value(row, "runtime", _RUNTIME_STANDARD) or _RUNTIME_STANDARD) == _RUNTIME_CLOAK
    ]
    canonical_rows = _canonical_image_rows(standard_rows)
    legacy_cloak_session_count = await pool.fetchval(
        "SELECT COUNT(*) FROM sessions "
        "WHERE tenant_id = $1 AND browser_runtime = 'cloak_chromium' "
        "AND (browser_image_id IS NULL OR browser_image_id = '')",
        user.tenant_id,
    )
    runtime_images = [_browser_image_payload(r) for r in cloak_rows]
    legacy_cloak_image = await _legacy_cloak_runtime_image_payload(
        session_count=int(legacy_cloak_session_count or 0)
    )
    if legacy_cloak_image:
        runtime_images.append(legacy_cloak_image)
    return {
        "runtimeImages": runtime_images,
        "images": [_browser_image_payload(r) for r in canonical_rows] + runtime_images,
    }


@router.get("/api/browser-images/available-versions")
async def available_versions(
    refresh: bool = False,
    user: CurrentUser = Depends(get_current_user),
):
    reject_browser_images_api()
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
    reject_browser_images_api()
    pool = get_pool()
    if image_id == _CLOAK_LEGACY_IMAGE_ID:
        cnt = await pool.fetchval(
            "SELECT COUNT(*) FROM sessions "
            "WHERE tenant_id = $1 AND browser_runtime = 'cloak_chromium' "
            "AND (browser_image_id IS NULL OR browser_image_id = '')",
            user.tenant_id,
        )
        if cnt > 0:
            raise HTTPException(409, f"Image is in use by {cnt} session(s). Delete those sessions first.")

        try:
            await _run(f"docker rmi {shlex.quote(CLOAK_BROWSER_IMAGE_NAME)}", timeout=30)
        except Exception:
            pass
        return {"ok": True}

    row = await pool.fetchrow(
        "SELECT image_tag, chrome_version, COALESCE(runtime, 'standard_chrome') AS runtime "
        "FROM browser_images WHERE id = $1 AND tenant_id = $2",
        image_id, user.tenant_id,
    )
    if not row:
        raise HTTPException(404, "Image not found")

    if row["runtime"] == _RUNTIME_CLOAK:
        cnt = await pool.fetchval(
            "SELECT COUNT(*) FROM sessions WHERE tenant_id = $1 AND browser_image_id = $2",
            user.tenant_id, image_id,
        )
        if cnt > 0:
            raise HTTPException(409, f"Image is in use by {cnt} session(s). Delete those sessions first.")
    elif row["chrome_version"]:
        cnt = await pool.fetchval(
            "SELECT COUNT(*) FROM sessions "
            "WHERE tenant_id = $1 AND (browser_image_id = $2 OR chrome_version = $3)",
            user.tenant_id, image_id, row["chrome_version"],
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
    reject_browser_images_api()
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT id, chrome_major, chrome_version, image_tag FROM browser_images "
        "WHERE tenant_id = $1 AND status = 'ready' "
        "AND COALESCE(runtime, 'standard_chrome') = 'standard_chrome' "
        "ORDER BY chrome_major DESC LIMIT 1",
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
