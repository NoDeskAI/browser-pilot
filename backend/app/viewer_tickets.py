from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from fastapi import Request

from app import agent_devices
from app.auth.dependencies import CurrentUser
from app.config import VIEWER_TICKET_TTL_SECONDS, origin_allowed
from app.db import get_pool

VIEWER_MODE_CONTROL = "control"
VIEWER_MODE_VIEW = "view"
VIEWER_MODES = {VIEWER_MODE_CONTROL, VIEWER_MODE_VIEW}


class ViewerTicketError(Exception):
    def __init__(self, message: str, *, reason: str = "viewer_ticket_invalid", status_code: int = 403):
        super().__init__(message)
        self.message = message
        self.reason = reason
        self.status_code = status_code


@dataclass
class ConsumedViewerTicket:
    id: str
    session_id: str
    tenant_id: str | None
    user_id: str | None
    operator_subject: str
    lease_id: str | None
    mode: str
    remote_addr: str | None
    user_agent: str | None

    @property
    def lease(self) -> dict[str, Any]:
        return {
            "lease_id": self.lease_id,
            "id": self.lease_id,
            "tenant_id": self.tenant_id,
            "current_operator": self.operator_subject,
            "operator_subject": self.operator_subject,
        }


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _client_host(request: Request) -> str | None:
    return request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip() or (
        request.client.host if request.client else None
    )


def _viewer_url(request: Request, session_id: str, token: str) -> str:
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    scheme = forwarded_proto or request.url.scheme
    ws_scheme = "wss" if scheme == "https" else "ws"
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{ws_scheme}://{host}/api/sessions/{quote(session_id)}/vnc?ticket={quote(token)}"


async def issue_viewer_ticket(
    *,
    session_id: str,
    user: CurrentUser,
    ctx: agent_devices.AgentDeviceActionContext,
    request: Request,
    mode: str = VIEWER_MODE_CONTROL,
) -> dict[str, Any]:
    if mode not in VIEWER_MODES:
        raise ViewerTicketError("Only control and view viewer tickets are supported", reason="unsupported_viewer_mode", status_code=422)
    lease_id = (ctx.lease or {}).get("lease_id") or (ctx.lease or {}).get("id")
    if mode == VIEWER_MODE_CONTROL and not lease_id:
        raise ViewerTicketError("Viewer ticket requires an active lease", reason="lease_required", status_code=409)

    token = secrets.token_urlsafe(32)
    ticket_id = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=VIEWER_TICKET_TTL_SECONDS)
    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO session_viewer_tickets (
            id, session_id, tenant_id, user_id, operator_subject, lease_id,
            token_hash, mode, expires_at, remote_addr, user_agent
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        """,
        ticket_id,
        session_id,
        user.tenant_id,
        user.id,
        ctx.actor,
        lease_id,
        _token_hash(token),
        mode,
        expires_at,
        _client_host(request),
        request.headers.get("user-agent", "")[:500] or None,
    )
    return {
        "ok": True,
        "viewerUrl": _viewer_url(request, session_id, token),
        "expiresAt": expires_at.isoformat(),
        "ttlSeconds": VIEWER_TICKET_TTL_SECONDS,
        "mode": mode,
    }


async def consume_viewer_ticket(
    *,
    session_id: str,
    token: str | None,
    origin: str | None,
) -> ConsumedViewerTicket:
    if not origin_allowed(origin):
        raise ViewerTicketError("Origin is not allowed", reason="origin_not_allowed", status_code=403)
    if not token:
        raise ViewerTicketError("Viewer ticket is required", reason="ticket_required", status_code=401)

    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            ticket = await conn.fetchrow(
                """
                SELECT *
                FROM session_viewer_tickets
                WHERE token_hash = $1
                  AND session_id = $2
                FOR UPDATE
                """,
                _token_hash(token),
                session_id,
            )
            if not ticket:
                raise ViewerTicketError("Viewer ticket is invalid", reason="ticket_invalid", status_code=403)
            if ticket["mode"] not in VIEWER_MODES:
                raise ViewerTicketError("Viewer ticket mode is unsupported", reason="unsupported_viewer_mode", status_code=422)
            if ticket["consumed_at"] is not None:
                raise ViewerTicketError("Viewer ticket has already been consumed", reason="ticket_consumed", status_code=403)
            if ticket["expires_at"] <= datetime.now(timezone.utc):
                raise ViewerTicketError("Viewer ticket has expired", reason="ticket_expired", status_code=403)

            if ticket["mode"] == VIEWER_MODE_CONTROL:
                lease = await conn.fetchrow(
                    """
                    SELECT id, session_id, tenant_id, current_operator, status, expires_at
                    FROM agent_device_leases
                    WHERE id = $1
                      AND device_instance_id = $2
                      AND status = 'active'
                      AND current_operator = $3
                      AND (expires_at IS NULL OR expires_at > NOW())
                    FOR UPDATE
                    """,
                    ticket["lease_id"],
                    session_id,
                    ticket["operator_subject"],
                )
                if not lease:
                    raise ViewerTicketError("Viewer lease is no longer active", reason="lease_inactive", status_code=409)
                if ticket["tenant_id"] and lease["tenant_id"] and ticket["tenant_id"] != lease["tenant_id"]:
                    raise ViewerTicketError("Viewer ticket tenant mismatch", reason="tenant_mismatch", status_code=403)

            consumed = await conn.fetchrow(
                """
                UPDATE session_viewer_tickets
                SET consumed_at = NOW()
                WHERE id = $1
                  AND consumed_at IS NULL
                RETURNING *
                """,
                ticket["id"],
            )
            if not consumed:
                raise ViewerTicketError("Viewer ticket has already been consumed", reason="ticket_consumed", status_code=403)

    return ConsumedViewerTicket(
        id=consumed["id"],
        session_id=consumed["session_id"],
        tenant_id=consumed["tenant_id"],
        user_id=consumed["user_id"],
        operator_subject=consumed["operator_subject"],
        lease_id=consumed["lease_id"],
        mode=consumed["mode"],
        remote_addr=consumed["remote_addr"],
        user_agent=consumed["user_agent"],
    )
