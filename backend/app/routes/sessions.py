from __future__ import annotations

import json
import logging
import re
import secrets
import shlex
import string
import asyncio
import contextlib
import time
from datetime import datetime, timezone
from typing import Any, Literal

import asyncpg
import websockets
from fastapi import APIRouter, Body, Depends, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.websockets import WebSocketDisconnect, WebSocketState

from app import db
from app import agent_devices
from app import rfb_proxy
from app import viewer_tickets
from app.auth.dependencies import CurrentUser, get_current_user, get_session_aware_user, require_role, verify_session_access
from app.runtime_provider import (
    BROWSER_RUNTIME_CLOAK,
    BROWSER_RUNTIME_STANDARD,
    ensure_container_running,
    exec_in_container,
    get_all_container_statuses,
    get_container_status,
    pause_container,
    recreate_container,
    remove_container,
    resolve_vnc_websocket_url,
    resolve_network_via_browser,
    session_vnc_password,
    sync_fingerprint_profile_to_container,
    stop_container,
)
from app.config import CLOAK_BROWSER_IMAGE_NAME, EE_SAAS_MODE
from app.db import get_pool
from app.device_presets import DEVICE_PRESETS, DEFAULT_PRESET, get_preset
from app.download_watcher import configure_download_behavior, start_download_watcher, stop_download_watcher
from app.file_capture import get_file_capture_status, mark_file_capture_status
from app.fingerprint import (
    PoolEmptyError,
    attach_network_profile,
    declared_network_profile,
    generate_profile,
)
from app.network_egress import (
    EgressError,
    EffectiveEgress,
    resolve_egress,
)
from app.platform_control import assert_tenant_runtime_allowed
from app.runtime_control import run_runtime_command

logger = logging.getLogger("routes.sessions")
router = APIRouter()

SESSION_ID_LENGTH = 12
SESSION_ID_RETRY_LIMIT = 5
SESSION_ID_FIRST_CHARS = string.ascii_lowercase
SESSION_ID_CHARS = string.ascii_lowercase + string.digits


def _generate_session_id(length: int = SESSION_ID_LENGTH) -> str:
    if length < 1:
        raise ValueError("session id length must be at least 1")
    first = secrets.choice(SESSION_ID_FIRST_CHARS)
    rest = "".join(secrets.choice(SESSION_ID_CHARS) for _ in range(length - 1))
    return f"{first}{rest}"


def _row_value(row, key: str, default=None):
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


async def _activate_file_capture(session_id: str) -> None:
    start_download_watcher(session_id)
    try:
        await configure_download_behavior(session_id)
    except Exception as exc:
        logger.warning("Download behavior configuration failed for %s: %s", session_id, exc)


async def _file_capture_payload(session_id: str, *, container_status: str | None = None) -> dict[str, Any]:
    try:
        return await get_file_capture_status(session_id, container_status=container_status)
    except Exception as exc:
        return {
            "status": "unavailable",
            "lastHeartbeatAt": None,
            "lastError": str(exc),
            "warnings": ["file_capture_status_unavailable"],
        }


async def _verify_session_tenant(session_id: str, user: CurrentUser) -> None:
    """Raise 404 if session doesn't belong to the user's tenant."""
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT tenant_id, user_id FROM sessions WHERE id = $1", session_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row["tenant_id"] and row["tenant_id"] != user.tenant_id:
        raise HTTPException(status_code=404, detail="Session not found")
    if user.role == "member" and _row_value(row, "user_id") != user.id:
        raise HTTPException(status_code=404, detail="Session not found")


def _operator_subject_for_user(user: CurrentUser) -> str:
    if getattr(user, "api_token_id", None):
        return f"token:{user.api_token_id}"
    return f"user:{user.id}"


async def _record_viewer_rejection(
    session_id: str,
    *,
    actor: str,
    actor_owner_user_id: str | None,
    lease: dict[str, Any] | None = None,
    summary: str,
    error: str,
) -> None:
    if not db.is_ready():
        return
    with contextlib.suppress(Exception):
        await agent_devices.record_action_event(
            session_id=session_id,
            actor=actor,
            actor_owner_user_id=actor_owner_user_id,
            lease=lease,
            action="session.viewer.connect",
            outcome="rejected",
            side_effect_level="none",
            summary=summary,
            details={"reason": error},
            error=error,
        )


class CreateSessionBody(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = "新会话"
    devicePreset: str = DEFAULT_PRESET
    proxyUrl: str = ""
    networkEgressId: str | None = None
    browserLang: str = "zh-CN"
    chromeVersion: str | None = None
    browserRuntime: Literal["standard_chrome", "cloak_chromium"] = "standard_chrome"


class UpdateSessionBody(BaseModel):
    name: str


class DeleteSessionBody(BaseModel):
    fileDeleteMode: Literal["none", "selected", "all"] = "none"
    deleteFileIds: list[str] = Field(default_factory=list)


class DevicePresetBody(BaseModel):
    preset: str


class SessionNetworkEgressBody(BaseModel):
    networkEgressId: str | None = None


class NetworkProfileOverrideBody(BaseModel):
    network: dict[str, Any]


class FingerprintActionBody(BaseModel):
    action: str = "regenerate"


class ViewerTicketBody(BaseModel):
    mode: Literal["control", "view"] = "control"


class AppStateBody(BaseModel):
    value: str

def _row_get(row, key: str, default=None):
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _is_full_chrome_version(ver: str) -> bool:
    return bool(re.fullmatch(r"\d+\.\d+\.\d+\.\d+", ver.strip()))


def _chrome_major(ver: str) -> int:
    try:
        return int(ver.strip().split(".")[0])
    except (AttributeError, TypeError, ValueError) as exc:
        raise HTTPException(422, "Invalid Chrome version.") from exc


def _reject_saas_runtime_image_selection(body: CreateSessionBody) -> None:
    if not EE_SAAS_MODE:
        return
    extra = body.model_extra or {}
    fields_set = set(getattr(body, "model_fields_set", set()) or set())
    forbidden_fields = {
        "image",
        "imageTag",
        "imageDigest",
        "imageRef",
        "imageName",
        "runtimeImage",
        "customImage",
    }
    if "chromeVersion" in fields_set or any(field in extra for field in forbidden_fields):
        raise HTTPException(status_code=403, detail="runtime_image_not_allowed")


async def _resolve_browser_image(pool, tenant_id: str, requested: str | None):
    if requested:
        raw = requested.strip()
        if _is_full_chrome_version(raw):
            row = await pool.fetchrow(
                "SELECT chrome_version, image_tag FROM browser_images "
                "WHERE tenant_id = $1 AND chrome_version = $2 AND status = 'ready' "
                "ORDER BY CASE WHEN image_tag LIKE '%-fpagent' THEN 1 ELSE 0 END, created_at DESC LIMIT 1",
                tenant_id,
                raw,
            )
            if row:
                return row
        row = await pool.fetchrow(
            "SELECT chrome_version, image_tag FROM browser_images "
            "WHERE tenant_id = $1 AND chrome_major = $2 AND status = 'ready' "
            "ORDER BY CASE WHEN image_tag LIKE '%-fpagent' THEN 1 ELSE 0 END, created_at DESC LIMIT 1",
            tenant_id,
            _chrome_major(raw),
        )
        if row:
            return row
        raise HTTPException(422, f"Chrome {raw} is not available. Please build it first in Settings > Browser Images.")

    row = await pool.fetchrow(
        "SELECT chrome_version, image_tag FROM browser_images "
        "WHERE tenant_id = $1 AND status = 'ready' "
        "ORDER BY chrome_major DESC, CASE WHEN image_tag LIKE '%-fpagent' THEN 1 ELSE 0 END, created_at DESC LIMIT 1",
        tenant_id,
    )
    if row:
        return row
    raise HTTPException(422, "No browser images available. Please build one first in Settings > Browser Images.")


async def _ensure_cloak_runtime_image() -> None:
    _, stderr, rc = await run_runtime_command(
        f"docker image inspect {shlex.quote(CLOAK_BROWSER_IMAGE_NAME)}",
        timeout=20,
    )
    if rc != 0:
        detail = (
            "Cloak Chromium runtime image is not built. "
            "Open Settings > Browser Images and build the Cloak Chromium runtime image first."
        )
        if stderr:
            detail += f" Docker said: {stderr[:160]}"
        raise HTTPException(422, detail)


def _egress_payload(effective: EffectiveEgress) -> dict:
    return {
        "networkEgressId": effective.id,
        "networkEgressName": effective.name,
        "networkEgressType": effective.type,
        "networkEgressStatus": effective.status,
        "networkEgressProxyUrl": effective.proxy_url,
        "networkEgressHealthError": effective.health_error,
    }


def _egress_payload_from_row(row) -> dict:
    egress_id = _row_get(row, "network_egress_id")
    if not egress_id:
        return {
            "networkEgressId": None,
            "networkEgressName": "Direct",
            "networkEgressType": "direct",
            "networkEgressStatus": "healthy",
            "networkEgressProxyUrl": "",
            "networkEgressHealthError": "",
        }
    return {
        "networkEgressId": egress_id,
        "networkEgressName": _row_get(row, "network_egress_name", "") or "",
        "networkEgressType": _row_get(row, "network_egress_type", "") or "",
        "networkEgressStatus": _row_get(row, "network_egress_status", "unchecked") or "unchecked",
        "networkEgressProxyUrl": _row_get(row, "proxy_url", "") or "",
        "networkEgressHealthError": _row_get(row, "network_egress_health_error", "") or "",
    }


def _session_proxy_url(row) -> str:
    if not _row_get(row, "network_egress_id"):
        return ""
    return _row_get(row, "proxy_url", "") or ""


def _iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _active_lease_payload_from_row(row) -> dict[str, Any] | None:
    lease_id = _row_get(row, "lease_id")
    if not lease_id:
        return None
    current_operator = _row_get(row, "current_operator", "") or ""
    operator_type = "unknown"
    operator_name = None
    if current_operator.startswith("token:"):
        operator_type = "api_token"
        operator_name = _row_get(row, "lease_token_name")
    elif current_operator.startswith("user:"):
        operator_type = "user"
        operator_name = _row_get(row, "lease_owner_name") or _row_get(row, "lease_owner_email")
    elif current_operator.startswith("runtime:file_capture:"):
        operator_type = "runtime_file_capture"
    elif current_operator.startswith("system:"):
        operator_type = "system"

    return {
        "id": lease_id,
        "leaseId": lease_id,
        "leaseMode": _row_get(row, "lease_mode"),
        "taskId": _row_get(row, "lease_task_id"),
        "currentOperator": current_operator,
        "operatorOwnerUserId": _row_get(row, "operator_owner_user_id"),
        "operatorType": operator_type,
        "operatorName": operator_name,
        "expiresAt": _iso_or_none(_row_get(row, "lease_expires_at")),
        "updatedAt": _iso_or_none(_row_get(row, "lease_updated_at")),
    }


async def _resolve_session_network(proxy_url: str | None, image_tag: str | None) -> dict:
    return declared_network_profile(proxy_url, image_tag)


async def _resolve_session_image(session_id: str) -> str | None:
    """Look up the image_tag for a session from browser_images."""
    if EE_SAAS_MODE:
        return None
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT chrome_version, tenant_id, COALESCE(browser_runtime, 'standard_chrome') AS browser_runtime FROM sessions WHERE id = $1", session_id,
    )
    if not row:
        return None
    if _row_get(row, "browser_runtime", BROWSER_RUNTIME_STANDARD) == BROWSER_RUNTIME_CLOAK:
        return CLOAK_BROWSER_IMAGE_NAME
    if row["chrome_version"] and row["tenant_id"]:
        img_row = await pool.fetchrow(
            "SELECT image_tag FROM browser_images "
            "WHERE tenant_id = $1 AND chrome_version = $2 AND status = 'ready' "
            "ORDER BY CASE WHEN image_tag LIKE '%-fpagent' THEN 1 ELSE 0 END, created_at DESC LIMIT 1",
            row["tenant_id"], row["chrome_version"],
        )
        if img_row:
            return img_row["image_tag"]
    if row["tenant_id"]:
        img_row = await pool.fetchrow(
            "SELECT image_tag FROM browser_images "
            "WHERE tenant_id = $1 AND status = 'ready' "
            "ORDER BY chrome_major DESC, CASE WHEN image_tag LIKE '%-fpagent' THEN 1 ELSE 0 END, created_at DESC LIMIT 1",
            row["tenant_id"],
        )
        if img_row:
            return img_row["image_tag"]
    return None


def _unique_strings(values: list[object]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _add_profile_warning(profile: dict | None, warning: str) -> None:
    if not isinstance(profile, dict):
        return
    warnings = list(profile.get("runtimeWarnings") or [])
    warnings.append(warning)
    profile["runtimeWarnings"] = _unique_strings(warnings)


def _network_profile_is_unverified(profile: dict | None) -> bool:
    if not isinstance(profile, dict):
        return False
    network = profile.get("network") if isinstance(profile.get("network"), dict) else {}
    warning_values = [
        *(network.get("warnings") if isinstance(network.get("warnings"), list) else []),
        *(profile.get("runtimeWarnings") if isinstance(profile.get("runtimeWarnings"), list) else []),
    ]
    warnings = {str(w) for w in warning_values if str(w or "").strip()}
    return (
        "network_profile_unverified" in warnings
        or network.get("source") == "declared:unverified"
    )


def _apply_fingerprint_readiness(
    profile: dict | None,
    *,
    egress_type: str | None,
    egress_name: str | None = None,
) -> None:
    if not isinstance(profile, dict):
        return
    normalized_type = egress_type or "direct"
    unverified_network = _network_profile_is_unverified(profile)
    not_ready = unverified_network
    warnings: list[str] = []
    reason = ""
    if not_ready:
        reason = "direct_network_profile_unverified" if normalized_type == "direct" else "network_profile_unverified"
        warnings.append("fingerprint_not_ready_unverified_network")
        _add_profile_warning(profile, "fingerprint_not_ready_unverified_network")

    profile["fingerprintReady"] = not not_ready
    profile["readiness"] = {
        "ready": not not_ready,
        "status": "unverified_network" if not_ready else "ready",
        "reason": reason,
        "egressType": normalized_type,
        "egressName": egress_name or ("Direct" if normalized_type == "direct" else ""),
        "warnings": warnings,
    }


def _apply_egress_runtime_warnings(profile: dict | None, effective: EffectiveEgress) -> None:
    _apply_fingerprint_readiness(
        profile,
        egress_type=effective.type,
        egress_name=effective.name,
    )


def _apply_row_fingerprint_readiness(profile: dict | None, row) -> None:
    egress_id = _row_get(row, "network_egress_id")
    _apply_fingerprint_readiness(
        profile,
        egress_type=(_row_get(row, "network_egress_type") if egress_id else "direct"),
        egress_name=(_row_get(row, "network_egress_name") if egress_id else "Direct"),
    )


def _network_dns(network: dict | None) -> list[str]:
    if not isinstance(network, dict):
        return []
    servers = network.get("dnsServers")
    if not isinstance(servers, list):
        return []
    return [str(s) for s in servers if str(s or "").strip()]


def _stable_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _stable_network_payload(network: dict[str, Any]) -> dict[str, Any]:
    payload = dict(network or {})
    payload["lat"] = _stable_float(payload.get("lat"))
    payload["lon"] = _stable_float(payload.get("lon"))
    if not isinstance(payload.get("dnsServers"), list):
        payload["dnsServers"] = []
    payload["dnsServers"] = [str(s) for s in payload.get("dnsServers") if str(s or "").strip()]
    if not isinstance(payload.get("warnings"), list):
        payload["warnings"] = []
    payload["observedAt"] = payload.get("observedAt") or datetime.now(timezone.utc).isoformat()
    return payload


async def _save_fingerprint_profile(session_id: str, profile: dict) -> None:
    await get_pool().execute(
        "UPDATE sessions SET fingerprint_profile = $1::jsonb, updated_at = NOW() WHERE id = $2",
        profile,
        session_id,
    )
    try:
        await sync_fingerprint_profile_to_container(session_id, profile)
    except Exception as exc:
        logger.warning("Profile sync failed for %s: %s", session_id, exc)


async def _read_fingerprint_health(session_id: str) -> dict | None:
    try:
        raw = await exec_in_container(session_id, "cat /tmp/fingerprint-health.json", timeout=3)
        health = json.loads(raw)
        return health if isinstance(health, dict) else None
    except Exception as exc:
        return {
            "agent": "cdp-fingerprint-agent",
            "ok": False,
            "status": "unavailable",
            "warnings": [f"Fingerprint runtime health is unavailable: {exc}"],
        }


async def _with_runtime_health(
    session_id: str,
    fingerprint_profile: dict | None,
    *,
    container_status: str | None = None,
) -> dict | None:
    if not isinstance(fingerprint_profile, dict):
        return fingerprint_profile
    status = container_status or await get_container_status(session_id)
    if status != "running":
        return fingerprint_profile

    profile = dict(fingerprint_profile)
    try:
        row = await get_pool().fetchrow(
            "SELECT COALESCE(browser_runtime, 'standard_chrome') AS browser_runtime FROM sessions WHERE id = $1",
            session_id,
        )
        if _row_get(row, "browser_runtime", BROWSER_RUNTIME_STANDARD) == BROWSER_RUNTIME_CLOAK:
            profile["runtimeHealth"] = {
                "agent": "cloak_chromium",
                "ok": True,
                "status": "managed_by_cloak_runtime",
                "warnings": [],
            }
            return profile
    except Exception:
        pass

    health = await _read_fingerprint_health(session_id)
    if not health:
        return profile

    profile["runtimeHealth"] = health
    warnings = list(profile.get("runtimeWarnings") or [])
    warnings.extend(health.get("warnings") or [])
    if health.get("ok") is False and not health.get("warnings"):
        warnings.append("Fingerprint runtime injection health check failed.")
    profile["runtimeWarnings"] = _unique_strings(warnings)
    return profile


async def _with_runtime_health_wait(
    session_id: str,
    fingerprint_profile: dict | None,
    *,
    container_status: str | None = None,
    timeout: float = 5.0,
) -> dict | None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    last = await _with_runtime_health(session_id, fingerprint_profile, container_status=container_status)
    while loop.time() < deadline:
        health = (last or {}).get("runtimeHealth") if isinstance(last, dict) else None
        if isinstance(health, dict) and health.get("status") not in (None, "unavailable", "starting"):
            return last
        await asyncio.sleep(0.5)
        last = await _with_runtime_health(session_id, fingerprint_profile, container_status=container_status)
    return last


# -----------------------------------------------------------------------
# Sessions CRUD
# -----------------------------------------------------------------------

@router.get("/api/sessions")
async def list_sessions(user: CurrentUser = Depends(get_current_user)):
    pool = get_pool()
    if user.role in ("superadmin", "admin"):
        rows = await pool.fetch("""
            SELECT s.id, s.name, s.created_at, s.updated_at, s.current_url, s.current_title,
                   s.device_preset, s.proxy_url, s.network_egress_id, s.user_id,
                   COALESCE(s.browser_runtime, 'standard_chrome') AS browser_runtime,
                   s.fingerprint_profile, s.browser_lang,
                   e.name AS network_egress_name,
                   e.type AS network_egress_type,
                   e.status AS network_egress_status,
                   e.health_error AS network_egress_health_error,
                   l.id AS lease_id,
                   l.lease_mode,
                   l.task_id AS lease_task_id,
                   l.current_operator,
                   l.operator_owner_user_id,
                   l.expires_at AS lease_expires_at,
                   l.updated_at AS lease_updated_at,
                   tok.name AS lease_token_name,
                   ou.name AS lease_owner_name,
                   ou.email AS lease_owner_email
            FROM sessions s
            LEFT JOIN network_egress_profiles e ON e.id = s.network_egress_id
            LEFT JOIN agent_device_leases l
              ON l.device_instance_id = s.id
             AND l.status = 'active'
             AND (l.expires_at IS NULL OR l.expires_at > NOW())
            LEFT JOIN api_tokens tok ON l.current_operator = 'token:' || tok.id
            LEFT JOIN users ou ON l.operator_owner_user_id = ou.id
            WHERE s.tenant_id = $1
            ORDER BY s.updated_at DESC
        """, user.tenant_id)
    else:
        rows = await pool.fetch("""
            SELECT s.id, s.name, s.created_at, s.updated_at, s.current_url, s.current_title,
                   s.device_preset, s.proxy_url, s.network_egress_id, s.user_id,
                   COALESCE(s.browser_runtime, 'standard_chrome') AS browser_runtime,
                   s.fingerprint_profile, s.browser_lang,
                   e.name AS network_egress_name,
                   e.type AS network_egress_type,
                   e.status AS network_egress_status,
                   e.health_error AS network_egress_health_error,
                   l.id AS lease_id,
                   l.lease_mode,
                   l.task_id AS lease_task_id,
                   l.current_operator,
                   l.operator_owner_user_id,
                   l.expires_at AS lease_expires_at,
                   l.updated_at AS lease_updated_at,
                   tok.name AS lease_token_name,
                   ou.name AS lease_owner_name,
                   ou.email AS lease_owner_email
            FROM sessions s
            LEFT JOIN network_egress_profiles e ON e.id = s.network_egress_id
            LEFT JOIN agent_device_leases l
              ON l.device_instance_id = s.id
             AND l.status = 'active'
             AND (l.expires_at IS NULL OR l.expires_at > NOW())
            LEFT JOIN api_tokens tok ON l.current_operator = 'token:' || tok.id
            LEFT JOIN users ou ON l.operator_owner_user_id = ou.id
            WHERE s.tenant_id = $1 AND s.user_id = $2
            ORDER BY s.updated_at DESC
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

        egress_payload = _egress_payload_from_row(r)
        fp_response = await _with_runtime_health(sid, fp, container_status=container_status)
        _apply_fingerprint_readiness(
            fp_response,
            egress_type=egress_payload["networkEgressType"],
            egress_name=egress_payload["networkEgressName"],
        )

        entry: dict = {
            "id": sid,
            "name": r["name"],
            "createdAt": r["created_at"].isoformat(),
            "updatedAt": r["updated_at"].isoformat(),
            "currentUrl": r["current_url"] or "",
            "currentTitle": r["current_title"] or "",
            "containerStatus": container_status,
            "devicePreset": r["device_preset"] or DEFAULT_PRESET,
            "proxyUrl": _session_proxy_url(r),
            "fingerprintProfile": fp_response,
            "browserLang": r["browser_lang"] or "zh-CN",
            "browserRuntime": _row_get(r, "browser_runtime", BROWSER_RUNTIME_STANDARD) or BROWSER_RUNTIME_STANDARD,
            "activeLease": _active_lease_payload_from_row(r),
            "fileCapture": await _file_capture_payload(sid, container_status=container_status),
            **egress_payload,
        }

        result.append(entry)
    return {"sessions": result}


@router.post("/api/sessions")
async def create_session(body: CreateSessionBody, user: CurrentUser = Depends(get_current_user)):
    if body.proxyUrl.strip():
        raise HTTPException(422, "Manual HTTP/SOCKS proxy is no longer supported. Use a Clash or OpenVPN network egress profile.")
    _reject_saas_runtime_image_selection(body)

    pool = get_pool()
    await assert_tenant_runtime_allowed(user.tenant_id)
    preset_id = body.devicePreset if body.devicePreset in DEVICE_PRESETS else DEFAULT_PRESET
    safe_lang = re.sub(r"[^a-zA-Z0-9_-]", "", body.browserLang or "zh-CN") or "zh-CN"

    if EE_SAAS_MODE:
        resolved_chrome_version = None
        resolved_image_tag = None
    elif body.browserRuntime == BROWSER_RUNTIME_CLOAK:
        await _ensure_cloak_runtime_image()
        resolved_chrome_version = None
        resolved_image_tag = None
    else:
        row_img = await _resolve_browser_image(pool, user.tenant_id, body.chromeVersion)
        resolved_chrome_version = row_img["chrome_version"]
        resolved_image_tag = row_img["image_tag"]

    try:
        effective_egress = await resolve_egress(
            user.tenant_id,
            body.networkEgressId,
            "",
            ensure=False,
        )
    except EgressError as exc:
        raise HTTPException(422, str(exc)) from exc
    network_profile = await _resolve_session_network(effective_egress.proxy_url or None, resolved_image_tag)

    try:
        fp_profile = await generate_profile(
            user.tenant_id,
            browser_lang=safe_lang,
            chrome_version=resolved_chrome_version,
        )
    except PoolEmptyError as exc:
        raise HTTPException(422, f"Fingerprint pool group '{exc.group}' has no enabled entries") from exc
    attach_network_profile(fp_profile, network_profile)
    _apply_egress_runtime_warnings(fp_profile, effective_egress)

    preset_data = get_preset(preset_id)
    fp_profile["screen"]["width"] = preset_data["width"]
    fp_profile["screen"]["height"] = preset_data["height"]

    session_id = ""
    last_collision: Exception | None = None
    for attempt in range(SESSION_ID_RETRY_LIMIT):
        session_id = _generate_session_id()
        try:
            await pool.execute(
                """
                INSERT INTO sessions
                    (id, name, device_preset, proxy_url, network_egress_id, tenant_id, user_id,
                     fingerprint_profile, browser_lang, chrome_version, browser_runtime)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11)
                """,
                session_id,
                body.name,
                preset_id,
                effective_egress.proxy_url,
                effective_egress.id,
                user.tenant_id,
                user.id,
                fp_profile,
                safe_lang,
                resolved_chrome_version,
                body.browserRuntime,
            )
            break
        except asyncpg.exceptions.UniqueViolationError as exc:
            last_collision = exc
            logger.warning(
                "Session id collision while creating session, retrying (%s/%s)",
                attempt + 1,
                SESSION_ID_RETRY_LIMIT,
            )
    else:
        raise HTTPException(status_code=500, detail="Could not allocate session id") from last_collision

    logger.info("Session created: %s (%s) preset=%s lang=%s chrome=%s", session_id, body.name, preset_id, safe_lang, resolved_chrome_version or "default")
    return agent_devices.control_action_response(
        {
            "id": session_id,
            "name": body.name,
            "devicePreset": preset_id,
            "proxyUrl": effective_egress.proxy_url,
            "fingerprintProfile": fp_profile,
            "browserLang": safe_lang,
            "chromeVersion": resolved_chrome_version,
            "browserRuntime": body.browserRuntime,
            **_egress_payload(effective_egress),
        },
        device_id=session_id,
        user=user,
        lease=None,
        action="create_session",
        status="succeeded",
        audit_event_id=None,
        next_step="inspect_or_acquire_lease",
        state_changed=True,
    )


@router.post("/api/sessions/{session_id}/viewer-ticket")
async def create_viewer_ticket(
    session_id: str,
    request: Request,
    body: ViewerTicketBody | None = Body(None),
    user: CurrentUser = Depends(get_session_aware_user),
):
    await verify_session_access(session_id, user)
    mode = (body or ViewerTicketBody()).mode
    if mode == viewer_tickets.VIEWER_MODE_CONTROL:
        try:
            ctx = await agent_devices.require_active_lease(
                session_id,
                user,
                action="session.viewer.ticket.issue",
                side_effect_level="internal",
            )
        except agent_devices.AgentDeviceLeaseError as exc:
            await _record_viewer_rejection(
                session_id,
                actor=_operator_subject_for_user(user),
                actor_owner_user_id=user.id,
                lease=exc.lease,
                summary=exc.message,
                error=exc.reason,
            )
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    else:
        ctx = agent_devices.AgentDeviceActionContext(
            session_id=session_id,
            action="session.viewer.ticket.issue",
            actor=_operator_subject_for_user(user),
            actor_owner_user_id=user.id,
            lease={},
            side_effect_level="internal",
        )

    try:
        payload = await viewer_tickets.issue_viewer_ticket(
            session_id=session_id,
            user=user,
            ctx=ctx,
            request=request,
            mode=mode,
        )
    except viewer_tickets.ViewerTicketError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    audit_id = await agent_devices.record_action_event(
        session_id=session_id,
        actor=ctx.actor,
        actor_owner_user_id=ctx.actor_owner_user_id,
        lease=ctx.lease,
        action=ctx.action,
        outcome="succeeded",
        side_effect_level="internal",
        summary="Issued short-lived viewer ticket",
        details={
            "mode": payload["mode"],
            "expiresAt": payload["expiresAt"],
            "ttlSeconds": payload["ttlSeconds"],
        },
    )
    return agent_devices.control_action_response(
        payload,
        device_id=session_id,
        user=user,
        lease=ctx.lease,
        action=ctx.action,
        status="succeeded",
        audit_event_id=audit_id,
        next_step="connect_viewer",
        state_changed=False,
    )


@router.websocket("/api/sessions/{session_id}/vnc")
async def proxy_session_vnc(websocket: WebSocket, session_id: str):
    token = websocket.query_params.get("ticket")
    try:
        ticket = await viewer_tickets.consume_viewer_ticket(
            session_id=session_id,
            token=token,
            origin=websocket.headers.get("origin"),
        )
    except viewer_tickets.ViewerTicketError as exc:
        await _record_viewer_rejection(
            session_id,
            actor="viewer:ticket",
            actor_owner_user_id=None,
            summary=exc.message,
            error=exc.reason,
        )
        await websocket.close(code=1008)
        return
    except Exception as exc:
        logger.warning("Viewer ticket validation failed for %s: %s", session_id, exc)
        await websocket.close(code=1011)
        return

    await websocket.accept()
    started_at = time.monotonic()
    downstream_bytes = 0
    upstream_bytes = 0
    outcome = "succeeded"
    error_reason: str | None = None
    summary = "Viewer connection closed"
    try:
        upstream_url = await resolve_vnc_websocket_url(session_id)
        async with websockets.connect(upstream_url, max_size=None) as upstream:
            downstream_buffer, upstream_buffer = await rfb_proxy.authenticate_upstream_vnc(
                downstream=websocket,
                upstream=upstream,
                password=session_vnc_password(session_id),
            )
            downstream_bytes, upstream_bytes = await rfb_proxy.bridge_websockets(
                downstream=websocket,
                upstream=upstream,
                downstream_buffer=downstream_buffer,
                upstream_buffer=upstream_buffer,
                view_only=ticket.mode == viewer_tickets.VIEWER_MODE_VIEW,
            )
    except WebSocketDisconnect:
        summary = "Viewer disconnected"
    except Exception as exc:
        outcome = "failed"
        error_reason = type(exc).__name__
        summary = f"Viewer proxy failed: {exc}"
        logger.warning("Viewer proxy failed for %s: %s", session_id, exc)
    finally:
        duration_ms = int((time.monotonic() - started_at) * 1000)
        with contextlib.suppress(Exception):
            await agent_devices.record_action_event(
                session_id=session_id,
                actor=ticket.operator_subject,
                actor_owner_user_id=ticket.user_id,
                lease=ticket.lease,
                action="session.viewer.connect",
                outcome=outcome,
                side_effect_level="none" if ticket.mode == viewer_tickets.VIEWER_MODE_VIEW else "external",
                summary=summary,
                details={
                    "ticketId": ticket.id,
                    "mode": ticket.mode,
                    "viewOnly": ticket.mode == viewer_tickets.VIEWER_MODE_VIEW,
                    "durationMs": duration_ms,
                    "bytesFromViewer": downstream_bytes,
                    "bytesToViewer": upstream_bytes,
                    "remoteAddr": ticket.remote_addr,
                    "userAgent": ticket.user_agent,
                },
                error=error_reason,
            )
        if websocket.application_state == WebSocketState.CONNECTED:
            with contextlib.suppress(Exception):
                await websocket.close()


@router.get("/api/sessions/{session_id}")
async def get_session(session_id: str, user: CurrentUser = Depends(get_session_aware_user)):
    await verify_session_access(session_id, user)
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT s.id, s.name, s.created_at, s.updated_at, s.current_url, s.current_title,
               s.device_preset, s.proxy_url, s.network_egress_id, s.fingerprint_profile, s.browser_lang,
               COALESCE(s.browser_runtime, 'standard_chrome') AS browser_runtime,
               e.name AS network_egress_name,
               e.type AS network_egress_type,
               e.status AS network_egress_status,
               e.health_error AS network_egress_health_error
        FROM sessions s
        LEFT JOIN network_egress_profiles e ON e.id = s.network_egress_id
        WHERE s.id = $1
        """,
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
    egress_payload = _egress_payload_from_row(row)
    fp_response = await _with_runtime_health(session_id, fp)
    container_status = await get_container_status(session_id)
    _apply_fingerprint_readiness(
        fp_response,
        egress_type=egress_payload["networkEgressType"],
        egress_name=egress_payload["networkEgressName"],
    )
    return {
        "id": row["id"],
        "name": row["name"],
        "createdAt": row["created_at"].isoformat(),
        "updatedAt": row["updated_at"].isoformat(),
        "currentUrl": row["current_url"] or "",
        "currentTitle": row["current_title"] or "",
        "devicePreset": row["device_preset"] or DEFAULT_PRESET,
        "proxyUrl": _session_proxy_url(row),
        "fingerprintProfile": fp_response,
        "browserLang": row["browser_lang"] or "zh-CN",
        "browserRuntime": _row_get(row, "browser_runtime", BROWSER_RUNTIME_STANDARD) or BROWSER_RUNTIME_STANDARD,
        "fileCapture": await _file_capture_payload(session_id, container_status=container_status),
        **egress_payload,
    }


@router.get("/api/sessions/{session_id}/files")
async def list_session_files_route(
    session_id: str,
    user: CurrentUser = Depends(get_session_aware_user),
):
    await verify_session_access(session_id, user)
    from app.file_service import list_session_files

    files = await list_session_files(session_id)
    return {"files": files}


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
async def delete_session(
    session_id: str,
    body: DeleteSessionBody | None = Body(None),
    user: CurrentUser = Depends(get_current_user),
):
    await _verify_session_tenant(session_id, user)
    pool = get_pool()
    from app.file_service import handle_session_delete_files

    delete_body = body or DeleteSessionBody()
    file_result = await handle_session_delete_files(
        session_id,
        user,
        file_delete_mode=delete_body.fileDeleteMode,
        delete_file_ids=delete_body.deleteFileIds,
    )
    await stop_download_watcher(session_id)
    await remove_container(session_id)
    await pool.execute("DELETE FROM sessions WHERE id = $1", session_id)
    logger.info("Session deleted: %s", session_id)
    return {"ok": True, "files": file_result}


# -----------------------------------------------------------------------
# Container start / stop
# -----------------------------------------------------------------------

@router.post("/api/sessions/{session_id}/container/start")
async def start_session_container(session_id: str, user: CurrentUser = Depends(get_session_aware_user)):
    await verify_session_access(session_id, user)
    ctx = await agent_devices.begin_control_action(
        session_id, user, action="session.container.start", side_effect_level="internal"
    )
    try:
        await assert_tenant_runtime_allowed(user.tenant_id, exclude_session_id=session_id)
        await ensure_container_running(session_id)
        await _activate_file_capture(session_id)
        from app.tools.browser.session import ensure_homepage

        home_page = await ensure_homepage(session_id)
        pool = get_pool()
        row = await pool.fetchrow(
            """
            SELECT s.fingerprint_profile, s.network_egress_id,
                   e.name AS network_egress_name,
                   e.type AS network_egress_type
            FROM sessions s
            LEFT JOIN network_egress_profiles e ON e.id = s.network_egress_id
            WHERE s.id = $1
            """,
            session_id,
        )
        fp = await _with_runtime_health_wait(
            session_id,
            row["fingerprint_profile"] if row else None,
            container_status="running",
        )
        if row:
            _apply_row_fingerprint_readiness(fp, row)
        return await agent_devices.complete_compatible_action(
            ctx,
            {
                "ok": True,
                "homePage": home_page,
                "fingerprintProfile": fp,
                "fileCapture": await _file_capture_payload(session_id, container_status="running"),
            },
            summary="Started browser container",
            details={"homePage": home_page},
        )
    except Exception as exc:
        logger.error("Container start failed for %s: %s", session_id, exc)
        return await agent_devices.fail_compatible_action(ctx, str(exc))


@router.post("/api/sessions/{session_id}/container/stop")
async def stop_session_container(session_id: str, user: CurrentUser = Depends(get_session_aware_user)):
    await verify_session_access(session_id, user)
    ctx, rejected = await agent_devices.begin_compatible_action(
        session_id, user, action="session.container.stop", side_effect_level="external"
    )
    if rejected:
        return rejected
    try:
        await stop_download_watcher(session_id)
        await stop_container(session_id)
        await mark_file_capture_status(session_id, "unavailable", "container_stopped")
        return await agent_devices.complete_compatible_action(
            ctx,
            {"ok": True},
            summary="Stopped browser container",
        )
    except Exception as exc:
        logger.error("Container stop failed for %s: %s", session_id, exc)
        return await agent_devices.fail_compatible_action(ctx, str(exc))


@router.post("/api/sessions/{session_id}/container/pause")
async def pause_session_container(session_id: str, user: CurrentUser = Depends(get_session_aware_user)):
    await verify_session_access(session_id, user)
    ctx, rejected = await agent_devices.begin_compatible_action(
        session_id, user, action="session.container.pause", side_effect_level="external"
    )
    if rejected:
        return rejected
    try:
        await stop_download_watcher(session_id)
        await pause_container(session_id)
        await mark_file_capture_status(session_id, "unavailable", "container_paused")
        return await agent_devices.complete_compatible_action(
            ctx,
            {"ok": True},
            summary="Paused browser container",
        )
    except Exception as exc:
        logger.error("Container pause failed for %s: %s", session_id, exc)
        return await agent_devices.fail_compatible_action(ctx, str(exc))


@router.post("/api/sessions/{session_id}/container/unpause")
async def unpause_session_container(session_id: str, user: CurrentUser = Depends(get_session_aware_user)):
    await verify_session_access(session_id, user)
    ctx, rejected = await agent_devices.begin_compatible_action(
        session_id, user, action="session.container.unpause", side_effect_level="external"
    )
    if rejected:
        return rejected
    try:
        await assert_tenant_runtime_allowed(user.tenant_id, exclude_session_id=session_id)
        await ensure_container_running(session_id)
        await _activate_file_capture(session_id)
        pool = get_pool()
        row = await pool.fetchrow(
            """
            SELECT s.fingerprint_profile, s.network_egress_id,
                   e.name AS network_egress_name,
                   e.type AS network_egress_type
            FROM sessions s
            LEFT JOIN network_egress_profiles e ON e.id = s.network_egress_id
            WHERE s.id = $1
            """,
            session_id,
        )
        fp = await _with_runtime_health(
            session_id,
            row["fingerprint_profile"] if row else None,
            container_status="running",
        )
        if row:
            _apply_row_fingerprint_readiness(fp, row)
        return await agent_devices.complete_compatible_action(
            ctx,
            {
                "ok": True,
                "fingerprintProfile": fp,
                "fileCapture": await _file_capture_payload(session_id, container_status="running"),
            },
            summary="Unpaused browser container",
        )
    except Exception as exc:
        logger.error("Container unpause failed for %s: %s", session_id, exc)
        return await agent_devices.fail_compatible_action(ctx, str(exc))


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
    row = await pool.fetchrow(
        "SELECT proxy_url, network_egress_id, fingerprint_profile, browser_lang, tenant_id FROM sessions WHERE id = $1",
        session_id,
    )
    if not row:
        return {"ok": False, "error": "Session not found"}
    await pool.execute(
        "UPDATE sessions SET device_preset = $1, updated_at = NOW() WHERE id = $2",
        body.preset, session_id,
    )
    preset_data = get_preset(body.preset)
    try:
        effective_egress = await resolve_egress(
            _row_get(row, "tenant_id") or user.tenant_id,
            _row_get(row, "network_egress_id"),
            "",
            ensure=False,
        )
    except EgressError as exc:
        return {"ok": False, "error": str(exc)}
    proxy = effective_egress.proxy_url or None
    fp_profile = row["fingerprint_profile"]
    image_name = await _resolve_session_image(session_id)
    if isinstance(fp_profile, dict) and not fp_profile.get("network"):
        attach_network_profile(fp_profile, await _resolve_session_network(proxy, image_name))
    _apply_egress_runtime_warnings(fp_profile, effective_egress)
    if isinstance(fp_profile, dict):
        await pool.execute(
            "UPDATE sessions SET fingerprint_profile = $1::jsonb, updated_at = NOW() WHERE id = $2",
            fp_profile, session_id,
        )
    fp_ua = fp_profile.get("navigator", {}).get("userAgent") if isinstance(fp_profile, dict) else None
    from app.tools.browser.session import invalidate_session_cache
    invalidate_session_cache(session_id)
    await stop_download_watcher(session_id)
    await assert_tenant_runtime_allowed(_row_get(row, "tenant_id") or user.tenant_id, exclude_session_id=session_id)
    await recreate_container(
        session_id,
        width=preset_data["width"],
        height=preset_data["height"],
        user_agent=fp_ua,
        proxy=proxy,
        fingerprint_profile=fp_profile,
        browser_lang=row["browser_lang"] or "zh-CN",
        image_name=image_name,
    )
    await _activate_file_capture(session_id)
    fp_response = await _with_runtime_health(session_id, fp_profile)
    return {"ok": True, "devicePreset": body.preset, "fingerprintProfile": fp_response}


@router.post("/api/sessions/{session_id}/network-egress")
async def change_network_egress(
    session_id: str,
    body: SessionNetworkEgressBody,
    user: CurrentUser = Depends(get_current_user),
):
    await _verify_session_tenant(session_id, user)
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT device_preset, fingerprint_profile, browser_lang, tenant_id FROM sessions WHERE id = $1",
        session_id,
    )
    if not row:
        return {"ok": False, "error": "Session not found"}
    try:
        effective_egress = await resolve_egress(
            _row_get(row, "tenant_id") or user.tenant_id,
            body.networkEgressId,
            "",
            ensure=False,
        )
    except EgressError as exc:
        return {"ok": False, "error": str(exc)}

    fp_profile = row["fingerprint_profile"] or {}
    image_name = await _resolve_session_image(session_id)
    attach_network_profile(
        fp_profile,
        await _resolve_session_network(effective_egress.proxy_url or None, image_name),
    )
    _apply_egress_runtime_warnings(fp_profile, effective_egress)
    await pool.execute(
        """
        UPDATE sessions
        SET network_egress_id = $1, proxy_url = $2, fingerprint_profile = $3::jsonb, updated_at = NOW()
        WHERE id = $4
        """,
        effective_egress.id,
        effective_egress.proxy_url,
        fp_profile,
        session_id,
    )
    preset_data = get_preset(row["device_preset"] or DEFAULT_PRESET)
    fp_ua = fp_profile.get("navigator", {}).get("userAgent") if isinstance(fp_profile, dict) else None
    from app.tools.browser.session import invalidate_session_cache
    invalidate_session_cache(session_id)
    await stop_download_watcher(session_id)
    await assert_tenant_runtime_allowed(_row_get(row, "tenant_id") or user.tenant_id, exclude_session_id=session_id)
    await recreate_container(
        session_id,
        width=preset_data["width"],
        height=preset_data["height"],
        user_agent=fp_ua,
        proxy=effective_egress.proxy_url or None,
        fingerprint_profile=fp_profile,
        browser_lang=row["browser_lang"] or "zh-CN",
        image_name=image_name,
    )
    await _activate_file_capture(session_id)
    fp_response = await _with_runtime_health(session_id, fp_profile)
    return {
        "ok": True,
        "proxyUrl": effective_egress.proxy_url,
        "fingerprintProfile": fp_response,
        **_egress_payload(effective_egress),
    }


@router.post("/api/sessions/{session_id}/fingerprint")
async def regenerate_fingerprint(session_id: str, body: FingerprintActionBody, user: CurrentUser = Depends(get_current_user)):
    await _verify_session_tenant(session_id, user)
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT device_preset, proxy_url, network_egress_id, browser_lang, chrome_version, tenant_id, COALESCE(browser_runtime, 'standard_chrome') AS browser_runtime FROM sessions WHERE id = $1",
        session_id,
    )
    if not row:
        return {"ok": False, "error": "Session not found"}
    try:
        effective_egress = await resolve_egress(
            _row_get(row, "tenant_id") or user.tenant_id,
            _row_get(row, "network_egress_id"),
            "",
            ensure=False,
        )
    except EgressError as exc:
        return {"ok": False, "error": str(exc)}
    proxy = effective_egress.proxy_url or None
    try:
        fp_profile = await generate_profile(
            user.tenant_id,
            browser_lang=row["browser_lang"] or "zh-CN",
            chrome_version=None if _row_get(row, "browser_runtime", BROWSER_RUNTIME_STANDARD) == BROWSER_RUNTIME_CLOAK else row["chrome_version"],
        )
    except PoolEmptyError as exc:
        return {"ok": False, "error": f"Pool group '{exc.group}' has no enabled entries"}
    image_name = await _resolve_session_image(session_id)
    attach_network_profile(
        fp_profile,
        await _resolve_session_network(proxy, image_name),
    )
    _apply_egress_runtime_warnings(fp_profile, effective_egress)
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

    await stop_download_watcher(session_id)
    await assert_tenant_runtime_allowed(_row_get(row, "tenant_id") or user.tenant_id, exclude_session_id=session_id)
    await recreate_container(
        session_id,
        width=preset_data["width"],
        height=preset_data["height"],
        user_agent=fp_ua,
        proxy=proxy,
        fingerprint_profile=fp_profile,
        browser_lang=row["browser_lang"] or "zh-CN",
        image_name=image_name,
    )
    await _activate_file_capture(session_id)
    fp_response = await _with_runtime_health(session_id, fp_profile)
    return {
        "ok": True,
        "fingerprintProfile": fp_response,
        "proxyUrl": effective_egress.proxy_url,
        **_egress_payload(effective_egress),
    }


# -----------------------------------------------------------------------
# Explicit network profile observation and sync
# -----------------------------------------------------------------------

@router.post("/api/sessions/{session_id}/network-profile/refresh")
async def refresh_network_profile(session_id: str, user: CurrentUser = Depends(get_session_aware_user)):
    await verify_session_access(session_id, user)
    await assert_tenant_runtime_allowed(user.tenant_id, exclude_session_id=session_id)
    runtime_ports = await ensure_container_running(session_id)
    await _activate_file_capture(session_id)
    observed = await resolve_network_via_browser(runtime_ports, session_id=session_id, mode="deep")
    observed_payload = _stable_network_payload(observed)
    observed_payload["observedAt"] = datetime.now(timezone.utc).isoformat()

    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT s.fingerprint_profile, s.network_egress_id,
               e.name AS network_egress_name,
               e.type AS network_egress_type
        FROM sessions s
        LEFT JOIN network_egress_profiles e ON e.id = s.network_egress_id
        WHERE s.id = $1
        """,
        session_id,
    )
    if not row or not isinstance(row["fingerprint_profile"], dict):
        raise HTTPException(status_code=404, detail="Session not found")

    fp_profile = row["fingerprint_profile"]
    network = fp_profile.get("network") if isinstance(fp_profile.get("network"), dict) else {}
    network = dict(network)
    network["observed"] = observed_payload
    fp_profile["network"] = network
    if observed_payload.get("source") == "unresolved":
        _add_profile_warning(
            fp_profile,
            "network_profile_observation_failed: explicit network observation did not return a trusted profile.",
        )
    _apply_row_fingerprint_readiness(fp_profile, row)
    await _save_fingerprint_profile(session_id, fp_profile)
    fp_response = await _with_runtime_health(session_id, fp_profile)
    _apply_row_fingerprint_readiness(fp_response, row)
    return {
        "ok": True,
        "observedNetworkProfile": observed_payload,
        "fingerprintProfile": fp_response,
    }


@router.post("/api/sessions/{session_id}/network-profile/sync")
async def sync_observed_network_profile(session_id: str, user: CurrentUser = Depends(get_session_aware_user)):
    await verify_session_access(session_id, user)
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT s.fingerprint_profile, s.network_egress_id,
               e.name AS network_egress_name,
               e.type AS network_egress_type
        FROM sessions s
        LEFT JOIN network_egress_profiles e ON e.id = s.network_egress_id
        WHERE s.id = $1
        """,
        session_id,
    )
    if not row or not isinstance(row["fingerprint_profile"], dict):
        raise HTTPException(status_code=404, detail="Session not found")

    fp_profile = row["fingerprint_profile"]
    current_network = fp_profile.get("network") if isinstance(fp_profile.get("network"), dict) else {}
    observed = current_network.get("observed") if isinstance(current_network.get("observed"), dict) else None
    if not observed:
        return {"ok": False, "error": "No observed network profile is available. Refresh first."}
    if observed.get("source") == "unresolved":
        return {"ok": False, "error": "Observed network profile is unresolved. Refresh again before syncing."}

    old_dns = _network_dns(current_network)
    observed_payload = _stable_network_payload(observed)
    attach_network_profile(fp_profile, observed_payload)
    fp_profile["network"]["observed"] = observed_payload
    fp_profile["network"]["source"] = observed_payload.get("source") or "observed"
    new_dns = _network_dns(fp_profile.get("network"))
    restart_required = old_dns != new_dns
    if restart_required:
        _add_profile_warning(
            fp_profile,
            "dns_recreate_required: observed network DNS differs from the running container DNS; restart this session to apply it.",
        )
    _apply_row_fingerprint_readiness(fp_profile, row)
    await _save_fingerprint_profile(session_id, fp_profile)
    fp_response = await _with_runtime_health(session_id, fp_profile)
    _apply_row_fingerprint_readiness(fp_response, row)
    return {
        "ok": True,
        "restartRequired": restart_required,
        "fingerprintProfile": fp_response,
    }


@router.patch("/api/sessions/{session_id}/network-profile")
async def override_network_profile(
    session_id: str,
    body: NetworkProfileOverrideBody,
    user: CurrentUser = Depends(get_session_aware_user),
):
    await verify_session_access(session_id, user)
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT s.fingerprint_profile, s.network_egress_id,
               e.name AS network_egress_name,
               e.type AS network_egress_type
        FROM sessions s
        LEFT JOIN network_egress_profiles e ON e.id = s.network_egress_id
        WHERE s.id = $1
        """,
        session_id,
    )
    if not row or not isinstance(row["fingerprint_profile"], dict):
        raise HTTPException(status_code=404, detail="Session not found")

    fp_profile = row["fingerprint_profile"]
    current_network = fp_profile.get("network") if isinstance(fp_profile.get("network"), dict) else {}
    old_dns = _network_dns(current_network)
    observed = current_network.get("observed") if isinstance(current_network.get("observed"), dict) else None
    manual_network = _stable_network_payload({**current_network, **body.network})
    manual_network["source"] = "user_override"
    manual_network["observedVia"] = "user_override"
    if observed:
        manual_network["observed"] = observed
    warnings = list(manual_network.get("warnings") or [])
    warnings.append("network_profile_user_override")
    manual_network["warnings"] = _unique_strings(warnings)

    attach_network_profile(fp_profile, manual_network)
    if observed:
        fp_profile["network"]["observed"] = observed
    _add_profile_warning(
        fp_profile,
        "network_profile_user_override: manual network fingerprint fields do not change the real egress observed by target sites.",
    )
    new_dns = _network_dns(fp_profile.get("network"))
    restart_required = old_dns != new_dns
    if restart_required:
        _add_profile_warning(
            fp_profile,
            "dns_recreate_required: manual network DNS differs from the running container DNS; restart this session to apply it.",
        )

    _apply_row_fingerprint_readiness(fp_profile, row)
    await _save_fingerprint_profile(session_id, fp_profile)
    fp_response = await _with_runtime_health(session_id, fp_profile)
    _apply_row_fingerprint_readiness(fp_response, row)
    return {
        "ok": True,
        "restartRequired": restart_required,
        "fingerprintProfile": fp_response,
    }


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
    from ..config import APP_TITLE, CLI_COMMAND_NAME, EDITION, EE_SAAS_MODE, JWT_EXPIRE_MINUTES, REMEMBER_ME_DAYS
    from .cli import get_cli_install_info

    if not db.is_ready():
        state = db.get_bootstrap_state()
        return JSONResponse({"status": state["status"], "database": state}, status_code=503)

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
            "saasMode": EE_SAAS_MODE,
            "browserImages": not EE_SAAS_MODE,
        },
        "auth": {
            "accessTokenMinutes": JWT_EXPIRE_MINUTES,
            "rememberMeDays": REMEMBER_ME_DAYS,
        },
        "cliCommandName": CLI_COMMAND_NAME,
        "cliInstallCommand": cli_info["shell"],
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
