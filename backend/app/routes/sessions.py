from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.container import (
    ensure_container_running,
    exec_in_container,
    get_all_container_statuses,
    get_container_ports,
    pause_container,
    remove_container,
    stop_container,
)
from app.db import get_pool

logger = logging.getLogger("routes.sessions")
router = APIRouter()


class CreateSessionBody(BaseModel):
    name: str = "新会话"


class UpdateSessionBody(BaseModel):
    name: str


class AppStateBody(BaseModel):
    value: str


# -----------------------------------------------------------------------
# Sessions CRUD
# -----------------------------------------------------------------------

@router.get("/api/sessions")
async def list_sessions():
    pool = get_pool()
    rows = await pool.fetch("""
        SELECT id, name, created_at, updated_at, current_url, current_title
        FROM sessions
        ORDER BY updated_at DESC
    """)

    all_statuses = await get_all_container_statuses()

    result = []
    for r in rows:
        sid = r["id"]
        sid_prefix = sid[:12]
        container_status = all_statuses.get(sid_prefix, "not_found")

        entry: dict = {
            "id": sid,
            "name": r["name"],
            "createdAt": r["created_at"].isoformat(),
            "updatedAt": r["updated_at"].isoformat(),
            "currentUrl": r["current_url"] or "",
            "currentTitle": r["current_title"] or "",
            "containerStatus": container_status,
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
async def create_session(body: CreateSessionBody):
    pool = get_pool()
    session_id = str(uuid.uuid4())
    await pool.execute(
        "INSERT INTO sessions (id, name) VALUES ($1, $2)",
        session_id, body.name,
    )
    logger.info("Session created: %s (%s)", session_id, body.name)
    return {"id": session_id, "name": body.name}


@router.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT id, name, created_at, updated_at, current_url, current_title FROM sessions WHERE id = $1",
        session_id,
    )
    if not row:
        return {"error": "not found"}
    return {
        "id": row["id"],
        "name": row["name"],
        "createdAt": row["created_at"].isoformat(),
        "updatedAt": row["updated_at"].isoformat(),
        "currentUrl": row["current_url"] or "",
        "currentTitle": row["current_title"] or "",
    }


@router.patch("/api/sessions/{session_id}")
async def update_session(session_id: str, body: UpdateSessionBody):
    pool = get_pool()
    await pool.execute(
        "UPDATE sessions SET name = $1, updated_at = NOW() WHERE id = $2",
        body.name, session_id,
    )
    return {"ok": True}


@router.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    pool = get_pool()
    await remove_container(session_id)
    await pool.execute("DELETE FROM sessions WHERE id = $1", session_id)
    logger.info("Session deleted: %s", session_id)
    return {"ok": True}


# -----------------------------------------------------------------------
# Container start / stop
# -----------------------------------------------------------------------

@router.post("/api/sessions/{session_id}/container/start")
async def start_session_container(session_id: str):
    try:
        ports = await ensure_container_running(session_id)
        return {"ok": True, "ports": ports}
    except Exception as exc:
        logger.error("Container start failed for %s: %s", session_id, exc)
        return {"ok": False, "error": str(exc)}


@router.post("/api/sessions/{session_id}/container/stop")
async def stop_session_container(session_id: str):
    try:
        await stop_container(session_id)
        return {"ok": True}
    except Exception as exc:
        logger.error("Container stop failed for %s: %s", session_id, exc)
        return {"ok": False, "error": str(exc)}


@router.post("/api/sessions/{session_id}/container/pause")
async def pause_session_container(session_id: str):
    try:
        await pause_container(session_id)
        return {"ok": True}
    except Exception as exc:
        logger.error("Container pause failed for %s: %s", session_id, exc)
        return {"ok": False, "error": str(exc)}


@router.post("/api/sessions/{session_id}/container/unpause")
async def unpause_session_container(session_id: str):
    try:
        ports = await ensure_container_running(session_id)
        return {"ok": True, "ports": ports}
    except Exception as exc:
        logger.error("Container unpause failed for %s: %s", session_id, exc)
        return {"ok": False, "error": str(exc)}


# -----------------------------------------------------------------------
# Container logs
# -----------------------------------------------------------------------

@router.get("/api/sessions/{session_id}/logs")
async def get_session_logs(session_id: str, tail: int = 200, log_type: str | None = None):
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
    from ..config import APP_TITLE, APP_AGENT_NAME, CLI_COMMAND_NAME
    from .cli import get_cli_install_info

    base = str(request.base_url).rstrip("/")
    cli_info = get_cli_install_info(base)
    return {
        "appTitle": APP_TITLE,
        "agentName": APP_AGENT_NAME,
        "cliCommandName": CLI_COMMAND_NAME,
        "cliInstallCommand": cli_info["shell"],
        "cliPythonInstallCommand": cli_info["python"],
    }


# App state (key-value)
# -----------------------------------------------------------------------

@router.get("/api/app-state/{key}")
async def get_app_state(key: str):
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT value FROM app_state WHERE key = $1", key,
    )
    if row is None:
        return {"value": None}
    return {"value": row["value"]}


@router.put("/api/app-state/{key}")
async def set_app_state(key: str, body: AppStateBody):
    pool = get_pool()
    await pool.execute(
        """INSERT INTO app_state (key, value) VALUES ($1, $2)
           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
        key, body.value,
    )
    return {"ok": True}
