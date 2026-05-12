from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException

from app.db import get_pool

FILE_CAPTURE_PURPOSE = "file_capture"
RUNTIME_TOKEN_PREFIX = "bpr_"
RUNTIME_TOKEN_DAYS = 90
HEARTBEAT_STALE_SECONDS = 90


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


async def issue_file_capture_token(session_id: str) -> str:
    pool = get_pool()
    row = await pool.fetchrow("SELECT tenant_id FROM sessions WHERE id = $1", session_id)
    if not row:
        raise HTTPException(404, "Session not found")
    raw_token = f"{RUNTIME_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    token_hash = _hash_token(raw_token)
    expires_at = datetime.now(timezone.utc) + timedelta(days=RUNTIME_TOKEN_DAYS)
    await pool.execute(
        """
        UPDATE session_runtime_tokens
        SET revoked_at = NOW()
        WHERE session_id = $1 AND purpose = $2 AND revoked_at IS NULL
        """,
        session_id,
        FILE_CAPTURE_PURPOSE,
    )
    await pool.execute(
        """
        INSERT INTO session_runtime_tokens (
            id, session_id, tenant_id, purpose, token_hash, expires_at
        )
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        str(uuid.uuid4()),
        session_id,
        row["tenant_id"],
        FILE_CAPTURE_PURPOSE,
        token_hash,
        expires_at,
    )
    await mark_file_capture_status(session_id, "unavailable", "file_capture_agent_unavailable")
    return raw_token


async def revoke_file_capture_tokens(session_id: str) -> None:
    pool = get_pool()
    await pool.execute(
        """
        UPDATE session_runtime_tokens
        SET revoked_at = NOW()
        WHERE session_id = $1 AND purpose = $2 AND revoked_at IS NULL
        """,
        session_id,
        FILE_CAPTURE_PURPOSE,
    )


async def verify_file_capture_token(session_id: str, raw_token: str) -> dict[str, Any]:
    if not raw_token or not raw_token.startswith(RUNTIME_TOKEN_PREFIX):
        raise HTTPException(401, "Invalid runtime token")
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT id, tenant_id
        FROM session_runtime_tokens
        WHERE session_id = $1
          AND purpose = $2
          AND token_hash = $3
          AND revoked_at IS NULL
          AND (expires_at IS NULL OR expires_at > NOW())
        """,
        session_id,
        FILE_CAPTURE_PURPOSE,
        _hash_token(raw_token),
    )
    if not row:
        raise HTTPException(401, "Invalid runtime token")
    await pool.execute(
        "UPDATE session_runtime_tokens SET last_used_at = NOW() WHERE id = $1",
        row["id"],
    )
    return {"tenant_id": row["tenant_id"]}


async def mark_file_capture_status(
    session_id: str,
    status: str,
    error: str | None = None,
    *,
    heartbeat: bool = False,
) -> None:
    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO session_runtime_status (
            session_id, purpose, status, last_heartbeat_at, last_error, updated_at
        )
        VALUES ($1, $2, $3, CASE WHEN $4 THEN NOW() ELSE NULL END, $5, NOW())
        ON CONFLICT (session_id, purpose) DO UPDATE SET
            status = EXCLUDED.status,
            last_heartbeat_at = COALESCE(EXCLUDED.last_heartbeat_at, session_runtime_status.last_heartbeat_at),
            last_error = EXCLUDED.last_error,
            updated_at = NOW()
        """,
        session_id,
        FILE_CAPTURE_PURPOSE,
        status,
        heartbeat,
        error or "",
    )


async def heartbeat_file_capture(session_id: str, status: str = "running", error: str | None = None) -> None:
    normalized = status if status in {"running", "degraded", "unavailable"} else "running"
    await mark_file_capture_status(session_id, normalized, error, heartbeat=True)


async def get_file_capture_status(session_id: str, *, container_status: str | None = None) -> dict[str, Any]:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT status, last_heartbeat_at, last_error, updated_at
        FROM session_runtime_status
        WHERE session_id = $1 AND purpose = $2
        """,
        session_id,
        FILE_CAPTURE_PURPOSE,
    )
    warnings: list[str] = []
    if not row:
        status = "unavailable" if container_status == "running" else "stopped"
        if container_status == "running":
            warnings.append("file_capture_agent_unavailable")
        return {
            "status": status,
            "lastHeartbeatAt": None,
            "lastError": "file_capture_agent_unavailable" if warnings else "",
            "warnings": warnings,
        }

    status = row["status"] or "unavailable"
    last_heartbeat = row["last_heartbeat_at"]
    if container_status and container_status != "running":
        status = "stopped"
    elif last_heartbeat:
        age = (datetime.now(timezone.utc) - last_heartbeat).total_seconds()
        if age > HEARTBEAT_STALE_SECONDS:
            status = "unavailable"
            warnings.append("file_capture_agent_unavailable")
    elif container_status == "running":
        status = "unavailable"
        warnings.append("file_capture_agent_unavailable")

    last_error = row["last_error"] or ""
    if last_error and last_error not in warnings:
        warnings.append(last_error)
    return {
        "status": status,
        "lastHeartbeatAt": last_heartbeat.isoformat() if last_heartbeat else None,
        "lastError": last_error,
        "warnings": warnings,
    }
