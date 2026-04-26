from __future__ import annotations

import json
import logging
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth.dependencies import CurrentUser, get_current_user, get_session_aware_user, require_role, verify_session_access
from app.container import (
    ensure_container_running,
    exec_in_container,
    get_all_container_statuses,
    get_container_ports,
    pause_container,
    recreate_container,
    remove_container,
    stop_container,
)
from app.db import get_pool
from app.device_presets import DEVICE_PRESETS, DEFAULT_PRESET, get_preset
from app.fingerprint import PoolEmptyError, generate_profile, resolve_timezone, resolve_timezone_via_container

logger = logging.getLogger("routes.sessions")
router = APIRouter()


async def _verify_session_tenant(session_id: str, user: CurrentUser) -> None:
    """Raise 404 if session doesn't belong to the user's tenant."""
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT tenant_id FROM sessions WHERE id = $1", session_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row["tenant_id"] and row["tenant_id"] != user.tenant_id:
        raise HTTPException(status_code=404, detail="Session not found")


class CreateSessionBody(BaseModel):
    name: str = "新会话"
    devicePreset: str = DEFAULT_PRESET
    proxyUrl: str = ""
    browserLang: str = "zh-CN"
    chromeVersion: str | None = None


class UpdateSessionBody(BaseModel):
    name: str


class DevicePresetBody(BaseModel):
    preset: str


class ProxyBody(BaseModel):
    proxyUrl: str = ""


class FingerprintActionBody(BaseModel):
    action: str = "regenerate"


class AppStateBody(BaseModel):
    value: str

_VALID_PROXY_SCHEMES = ("http://", "https://", "socks4://", "socks5://")


async def _resolve_session_image(session_id: str) -> str | None:
    """Look up the image_tag for a session from browser_images."""
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT chrome_version, tenant_id FROM sessions WHERE id = $1", session_id,
    )
    if not row:
        return None
    if row["chrome_version"] and row["tenant_id"]:
        img_row = await pool.fetchrow(
            "SELECT image_tag FROM browser_images WHERE tenant_id = $1 AND chrome_version = $2 AND status = 'ready' LIMIT 1",
            row["tenant_id"], row["chrome_version"],
        )
        if img_row:
            return img_row["image_tag"]
    if row["tenant_id"]:
        img_row = await pool.fetchrow(
            "SELECT image_tag FROM browser_images WHERE tenant_id = $1 AND status = 'ready' ORDER BY chrome_major DESC LIMIT 1",
            row["tenant_id"],
        )
        if img_row:
            return img_row["image_tag"]
    return None


# -----------------------------------------------------------------------
# Sessions CRUD
# -----------------------------------------------------------------------

@router.get("/api/sessions")
async def list_sessions(user: CurrentUser = Depends(get_current_user)):
    pool = get_pool()
    if user.role in ("superadmin", "admin"):
        rows = await pool.fetch("""
            SELECT id, name, created_at, updated_at, current_url, current_title,
                   device_preset, proxy_url, user_id, fingerprint_profile, browser_lang
            FROM sessions WHERE tenant_id = $1
            ORDER BY updated_at DESC
        """, user.tenant_id)
    else:
        rows = await pool.fetch("""
            SELECT id, name, created_at, updated_at, current_url, current_title,
                   device_preset, proxy_url, user_id, fingerprint_profile, browser_lang
            FROM sessions WHERE tenant_id = $1 AND user_id = $2
            ORDER BY updated_at DESC
        """, user.tenant_id, user.id)

    all_statuses = await get_all_container_statuses()

    result = []
    for r in rows:
        sid = r["id"]
        sid_prefix = sid[:12]
        container_status = all_statuses.get(sid_prefix, "not_found")

        fp = r["fingerprint_profile"]
        if fp is None:
            try:
                fp = await generate_profile(user.tenant_id, browser_lang=r["browser_lang"] or "zh-CN")
                await pool.execute(
                    "UPDATE sessions SET fingerprint_profile = $1::jsonb WHERE id = $2",
                    fp, sid,
                )
            except PoolEmptyError:
                pass

        entry: dict = {
            "id": sid,
            "name": r["name"],
            "createdAt": r["created_at"].isoformat(),
            "updatedAt": r["updated_at"].isoformat(),
            "currentUrl": r["current_url"] or "",
            "currentTitle": r["current_title"] or "",
            "containerStatus": container_status,
            "devicePreset": r["device_preset"] or DEFAULT_PRESET,
            "proxyUrl": r["proxy_url"] or "",
            "fingerprintProfile": fp,
            "browserLang": r["browser_lang"] or "zh-CN",
        }

        if container_status == "running":
            try:
                ports = await get_container_ports(sid)
                entry["ports"] = ports
            except Exception:
                entry["ports"] = None
        else:
            entry["ports"] = None

        result.append(entry)
    return {"sessions": result}


@router.post("/api/sessions")
async def create_session(body: CreateSessionBody, user: CurrentUser = Depends(get_current_user)):
    pool = get_pool()
    session_id = str(uuid.uuid4())
    preset_id = body.devicePreset if body.devicePreset in DEVICE_PRESETS else DEFAULT_PRESET
    safe_lang = re.sub(r"[^a-zA-Z0-9_-]", "", body.browserLang or "zh-CN") or "zh-CN"

    resolved_chrome_version: str | None = None
    resolved_image_tag: str | None = None
    if body.chromeVersion:
        row_img = await pool.fetchrow(
            "SELECT chrome_version, image_tag FROM browser_images WHERE tenant_id = $1 AND chrome_major = $2 AND status = 'ready' LIMIT 1",
            user.tenant_id, int(body.chromeVersion.split(".")[0]),
        )
        if row_img:
            resolved_chrome_version = row_img["chrome_version"]
            resolved_image_tag = row_img["image_tag"]
    else:
        row_img = await pool.fetchrow(
            "SELECT chrome_version, image_tag FROM browser_images WHERE tenant_id = $1 AND status = 'ready' ORDER BY chrome_major DESC LIMIT 1",
            user.tenant_id,
        )
        if row_img:
            resolved_chrome_version = row_img["chrome_version"]
            resolved_image_tag = row_img["image_tag"]
        else:
            raise HTTPException(422, "No browser images available. Please build one first in Settings > Browser Images.")

    tz = "UTC"
    if resolved_image_tag:
        tz = await resolve_timezone_via_container(body.proxyUrl or None, resolved_image_tag)

    try:
        fp_profile = await generate_profile(
            user.tenant_id,
            browser_lang=safe_lang,
            chrome_version=resolved_chrome_version,
        )
    except PoolEmptyError as exc:
        raise HTTPException(422, f"Fingerprint pool group '{exc.group}' has no enabled entries") from exc
    fp_profile["timezone"] = tz

    preset_data = get_preset(preset_id)
    fp_profile["screen"]["width"] = preset_data["width"]
    fp_profile["screen"]["height"] = preset_data["height"]

    await pool.execute(
        "INSERT INTO sessions (id, name, device_preset, proxy_url, tenant_id, user_id, fingerprint_profile, browser_lang, chrome_version) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9)",
        session_id, body.name, preset_id, body.proxyUrl, user.tenant_id, user.id, fp_profile, safe_lang, resolved_chrome_version,
    )
    logger.info("Session created: %s (%s) preset=%s lang=%s chrome=%s", session_id, body.name, preset_id, safe_lang, resolved_chrome_version or "default")
    return {"id": session_id, "name": body.name, "devicePreset": preset_id, "proxyUrl": body.proxyUrl, "fingerprintProfile": fp_profile, "browserLang": safe_lang, "chromeVersion": resolved_chrome_version}


@router.get("/api/sessions/{session_id}")
async def get_session(session_id: str, user: CurrentUser = Depends(get_session_aware_user)):
    await verify_session_access(session_id, user)
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT id, name, created_at, updated_at, current_url, current_title, device_preset, proxy_url, fingerprint_profile, browser_lang FROM sessions WHERE id = $1",
        session_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    fp = row["fingerprint_profile"]
    if fp is None:
        try:
            fp = await generate_profile(user.tenant_id, browser_lang=row["browser_lang"] or "zh-CN")
            await pool.execute(
                "UPDATE sessions SET fingerprint_profile = $1::jsonb WHERE id = $2",
                fp, session_id,
            )
        except PoolEmptyError:
            pass
    return {
        "id": row["id"],
        "name": row["name"],
        "createdAt": row["created_at"].isoformat(),
        "updatedAt": row["updated_at"].isoformat(),
        "currentUrl": row["current_url"] or "",
        "currentTitle": row["current_title"] or "",
        "devicePreset": row["device_preset"] or DEFAULT_PRESET,
        "proxyUrl": row["proxy_url"] or "",
        "fingerprintProfile": fp,
        "browserLang": row["browser_lang"] or "zh-CN",
    }


@router.patch("/api/sessions/{session_id}")
async def update_session(session_id: str, body: UpdateSessionBody, user: CurrentUser = Depends(get_current_user)):
    await _verify_session_tenant(session_id, user)
    pool = get_pool()
    await pool.execute(
        "UPDATE sessions SET name = $1, updated_at = NOW() WHERE id = $2",
        body.name, session_id,
    )
    return {"ok": True}


@router.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str, user: CurrentUser = Depends(get_current_user)):
    await _verify_session_tenant(session_id, user)
    pool = get_pool()
    await remove_container(session_id)
    await pool.execute("DELETE FROM sessions WHERE id = $1", session_id)
    logger.info("Session deleted: %s", session_id)
    return {"ok": True}


# -----------------------------------------------------------------------
# Container start / stop
# -----------------------------------------------------------------------

@router.post("/api/sessions/{session_id}/container/start")
async def start_session_container(session_id: str, user: CurrentUser = Depends(get_session_aware_user)):
    await verify_session_access(session_id, user)
    try:
        ports = await ensure_container_running(session_id)
        return {"ok": True, "ports": ports}
    except Exception as exc:
        logger.error("Container start failed for %s: %s", session_id, exc)
        return {"ok": False, "error": str(exc)}


@router.post("/api/sessions/{session_id}/container/stop")
async def stop_session_container(session_id: str, user: CurrentUser = Depends(get_session_aware_user)):
    await verify_session_access(session_id, user)
    try:
        await stop_container(session_id)
        return {"ok": True}
    except Exception as exc:
        logger.error("Container stop failed for %s: %s", session_id, exc)
        return {"ok": False, "error": str(exc)}


@router.post("/api/sessions/{session_id}/container/pause")
async def pause_session_container(session_id: str, user: CurrentUser = Depends(get_session_aware_user)):
    await verify_session_access(session_id, user)
    try:
        await pause_container(session_id)
        return {"ok": True}
    except Exception as exc:
        logger.error("Container pause failed for %s: %s", session_id, exc)
        return {"ok": False, "error": str(exc)}


@router.post("/api/sessions/{session_id}/container/unpause")
async def unpause_session_container(session_id: str, user: CurrentUser = Depends(get_session_aware_user)):
    await verify_session_access(session_id, user)
    try:
        ports = await ensure_container_running(session_id)
        return {"ok": True, "ports": ports}
    except Exception as exc:
        logger.error("Container unpause failed for %s: %s", session_id, exc)
        return {"ok": False, "error": str(exc)}


# -----------------------------------------------------------------------
# Device preset & proxy
# -----------------------------------------------------------------------

@router.get("/api/device-presets")
async def list_device_presets(_user: CurrentUser = Depends(get_current_user)):
    presets = []
    for pid, p in DEVICE_PRESETS.items():
        entry = {"id": pid, "label": p["label"], "category": p["category"], "width": p["width"], "height": p["height"]}
        if "dpr" in p:
            entry["dpr"] = p["dpr"]
        if p.get("default"):
            entry["default"] = True
        presets.append(entry)
    return {"presets": presets}


@router.post("/api/sessions/{session_id}/device-preset")
async def change_device_preset(session_id: str, body: DevicePresetBody, user: CurrentUser = Depends(get_current_user)):
    await _verify_session_tenant(session_id, user)
    if body.preset not in DEVICE_PRESETS:
        return {"ok": False, "error": f"Unknown preset: {body.preset}"}
    pool = get_pool()
    row = await pool.fetchrow("SELECT proxy_url, fingerprint_profile, browser_lang FROM sessions WHERE id = $1", session_id)
    if not row:
        return {"ok": False, "error": "Session not found"}
    await pool.execute(
        "UPDATE sessions SET device_preset = $1, updated_at = NOW() WHERE id = $2",
        body.preset, session_id,
    )
    preset_data = get_preset(body.preset)
    proxy = row["proxy_url"] or None
    fp_profile = row["fingerprint_profile"]
    image_name = await _resolve_session_image(session_id)
    fp_ua = fp_profile.get("navigator", {}).get("userAgent") if isinstance(fp_profile, dict) else None
    from app.tools.browser.session import invalidate_session_cache
    invalidate_session_cache(session_id)
    ports = await recreate_container(
        session_id,
        width=preset_data["width"],
        height=preset_data["height"],
        user_agent=fp_ua,
        proxy=proxy,
        fingerprint_profile=fp_profile,
        browser_lang=row["browser_lang"] or "zh-CN",
        image_name=image_name,
    )
    return {"ok": True, "ports": ports, "devicePreset": body.preset}


@router.post("/api/sessions/{session_id}/proxy")
async def change_proxy(session_id: str, body: ProxyBody, user: CurrentUser = Depends(get_current_user)):
    await _verify_session_tenant(session_id, user)
    proxy_url = body.proxyUrl.strip()
    if proxy_url and not proxy_url.startswith(_VALID_PROXY_SCHEMES):
        return {"ok": False, "error": "Proxy URL must start with http://, https://, socks4://, or socks5://"}
    pool = get_pool()
    row = await pool.fetchrow("SELECT device_preset, fingerprint_profile, browser_lang FROM sessions WHERE id = $1", session_id)
    if not row:
        return {"ok": False, "error": "Session not found"}
    fp_profile = row["fingerprint_profile"] or {}
    image_name = await _resolve_session_image(session_id)
    if image_name:
        tz = await resolve_timezone_via_container(proxy_url or None, image_name)
    else:
        tz = await resolve_timezone(proxy_url or None)
    fp_profile["timezone"] = tz
    await pool.execute(
        "UPDATE sessions SET proxy_url = $1, fingerprint_profile = $2::jsonb, updated_at = NOW() WHERE id = $3",
        proxy_url, fp_profile, session_id,
    )
    preset_data = get_preset(row["device_preset"] or DEFAULT_PRESET)
    fp_ua = fp_profile.get("navigator", {}).get("userAgent") if isinstance(fp_profile, dict) else None
    from app.tools.browser.session import invalidate_session_cache
    invalidate_session_cache(session_id)
    ports = await recreate_container(
        session_id,
        width=preset_data["width"],
        height=preset_data["height"],
        user_agent=fp_ua,
        proxy=proxy_url or None,
        fingerprint_profile=fp_profile,
        browser_lang=row["browser_lang"] or "zh-CN",
        image_name=image_name,
    )
    return {"ok": True, "ports": ports, "proxyUrl": proxy_url}


@router.post("/api/sessions/{session_id}/fingerprint")
async def regenerate_fingerprint(session_id: str, body: FingerprintActionBody, user: CurrentUser = Depends(get_current_user)):
    await _verify_session_tenant(session_id, user)
    pool = get_pool()
    row = await pool.fetchrow("SELECT device_preset, proxy_url, browser_lang, chrome_version FROM sessions WHERE id = $1", session_id)
    if not row:
        return {"ok": False, "error": "Session not found"}
    proxy = row["proxy_url"] or None
    try:
        fp_profile = await generate_profile(
            user.tenant_id,
            browser_lang=row["browser_lang"] or "zh-CN",
            chrome_version=row["chrome_version"],
        )
    except PoolEmptyError as exc:
        return {"ok": False, "error": f"Pool group '{exc.group}' has no enabled entries"}
    image_name = await _resolve_session_image(session_id)
    if image_name:
        fp_profile["timezone"] = await resolve_timezone_via_container(proxy, image_name)
    else:
        fp_profile["timezone"] = await resolve_timezone(proxy)
    preset_data = get_preset(row["device_preset"] or DEFAULT_PRESET)
    fp_profile["screen"]["width"] = preset_data["width"]
    fp_profile["screen"]["height"] = preset_data["height"]
    await pool.execute(
        "UPDATE sessions SET fingerprint_profile = $1::jsonb, updated_at = NOW() WHERE id = $2",
        fp_profile, session_id,
    )
    from app.tools.browser.session import invalidate_session_cache
    invalidate_session_cache(session_id)

    fp_ua = fp_profile.get("navigator", {}).get("userAgent") if isinstance(fp_profile, dict) else None

    ports = await recreate_container(
        session_id,
        width=preset_data["width"],
        height=preset_data["height"],
        user_agent=fp_ua,
        proxy=proxy,
        fingerprint_profile=fp_profile,
        browser_lang=row["browser_lang"] or "zh-CN",
        image_name=image_name,
    )
    return {"ok": True, "ports": ports, "fingerprintProfile": fp_profile}


# -----------------------------------------------------------------------
# Container logs
# -----------------------------------------------------------------------

@router.get("/api/sessions/{session_id}/logs")
async def get_session_logs(session_id: str, tail: int = 200, log_type: str | None = None, user: CurrentUser = Depends(get_session_aware_user)):
    await verify_session_access(session_id, user)
    try:
        stdout = await exec_in_container(
            session_id, f"tail -n {min(tail, 1000)} /tmp/cdp-events.jsonl"
        )
    except RuntimeError:
        return {"logs": []}
    lines = []
    for raw in stdout.splitlines():
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if log_type and entry.get("type") != log_type:
            continue
        lines.append(entry)
    return {"logs": lines}


# -----------------------------------------------------------------------
# Site info (deployment config exposed to frontend)
# -----------------------------------------------------------------------

@router.get("/api/site-info")
async def get_site_info(request: Request):
    from ..config import APP_TITLE, CLI_COMMAND_NAME, EDITION
    from .cli import get_cli_install_info

    pool = get_pool()
    user_count = await pool.fetchval("SELECT COUNT(*) FROM users")

    base = str(request.base_url).rstrip("/")
    cli_info = get_cli_install_info(base)
    return {
        "appTitle": APP_TITLE,
        "edition": EDITION,
        "setupComplete": user_count > 0,
        "features": {
            "sso": EDITION == "ee",
            "multiTenantManagement": EDITION == "ee",
        },
        "cliCommandName": CLI_COMMAND_NAME,
        "cliInstallCommand": cli_info["shell"],
        "cliPythonInstallCommand": cli_info["python"],
    }


# App state (key-value)
# -----------------------------------------------------------------------

@router.get("/api/app-state/{key}")
async def get_app_state(key: str, _user: CurrentUser = Depends(get_current_user)):
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT value FROM app_state WHERE key = $1", key,
    )
    if row is None:
        return {"value": None}
    return {"value": row["value"]}


@router.put("/api/app-state/{key}")
async def set_app_state(key: str, body: AppStateBody, _user: CurrentUser = Depends(require_role(["superadmin", "admin"]))):
    pool = get_pool()
    await pool.execute(
        """INSERT INTO app_state (key, value) VALUES ($1, $2)
           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
        key, body.value,
    )
    return {"ok": True}
