from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketDisconnect, WebSocketState

from app.auth.dependencies import CurrentUser, get_current_user
from app.config import JWT_SECRET
from app.db import get_pool

logger = logging.getLogger("browser_lite")
router = APIRouter(prefix="/api/browser-lite", tags=["browser-lite"])

PAIRING_TTL_SECONDS = 10 * 60
COMMAND_TIMEOUT_SECONDS = 45
TASK_SPACE_METHODS = frozenset({
    "createTab", "listTabs", "listTaskSpaces", "deleteSpaces", "listProfiles", "snapshot",
    "createTaskSpace", "claimTaskSpace", "closeTaskSpace", "useTaskSpace", "handOffTaskSpace",
    "takeOverTaskSpace", "completeTaskSpace", "markTaskSpaceError", "setAgentTaskState",
    "getBrowserVersion", "sendCDPMessage", "animationHighlightMouseToPosition",
})
PAIRING_RATE_LIMIT_WINDOW_SECONDS = 60
PAIRING_RATE_LIMIT_ATTEMPTS = 30
_pairing_attempts: dict[str, list[float]] = {}


def _hash_secret(value: str, purpose: str) -> str:
    return hmac.new(JWT_SECRET.encode(), f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else None)


def _check_pairing_rate_limit(request: Request) -> None:
    client = request.client.host if request.client else "unknown"
    now = time.monotonic()
    cutoff = now - PAIRING_RATE_LIMIT_WINDOW_SECONDS
    attempts = [attempt for attempt in _pairing_attempts.get(client, []) if attempt >= cutoff]
    if len(attempts) >= PAIRING_RATE_LIMIT_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many Browser Lite pairing attempts")
    attempts.append(now)
    _pairing_attempts[client] = attempts


class PairingBody(BaseModel):
    pairingCode: str = Field(min_length=10, max_length=10, pattern=r"^\d{10}$")
    displayName: str = Field(default="Browser Lite node", min_length=1, max_length=120)
    platform: str = Field(default="", max_length=40)
    architecture: str = Field(default="", max_length=40)
    appVersion: str = Field(default="", max_length=40)
    chromiumVersion: str = Field(default="", max_length=80)
    capabilities: list[str] = Field(default_factory=list, max_length=30)


@dataclass
class NodeConnection:
    node_id: str
    websocket: WebSocket
    connected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    pending: dict[str, asyncio.Future] = field(default_factory=dict)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_connections: dict[str, NodeConnection] = {}


class BrowserLiteNodeError(RuntimeError):
    def __init__(self, message: str, *, error_code: str | None = None, details: Any = None):
        super().__init__(message)
        self.error_code = error_code
        self.details = details


class TaskSpaceBody(BaseModel):
    sessionId: str
    method: str = Field(min_length=1, max_length=80)
    args: list[Any] = Field(default_factory=list, max_length=20)


def node_is_online(node_id: str | None) -> bool:
    connection = _connections.get(str(node_id or ""))
    return bool(connection and connection.websocket.client_state == WebSocketState.CONNECTED)


async def node_online(node_id: str | None) -> bool:
    if node_is_online(node_id):
        return True
    row = await get_pool().fetchrow(
        """
        SELECT 1 FROM browser_lite_nodes
        WHERE id = $1 AND status = 'online'
          AND last_seen_at > NOW() - INTERVAL '45 seconds'
        """,
        str(node_id or ""),
    )
    return bool(row)


async def _set_node_status(node_id: str, status: str) -> None:
    await get_pool().execute(
        "UPDATE browser_lite_nodes SET status = $1, last_seen_at = NOW(), updated_at = NOW() WHERE id = $2",
        status,
        node_id,
    )


async def _set_node_offline(connection: NodeConnection) -> None:
    await get_pool().execute(
        """
        UPDATE browser_lite_nodes
        SET status = 'offline', updated_at = NOW()
        WHERE id = $1 AND last_seen_at <= $2
        """,
        connection.node_id,
        connection.connected_at,
    )


async def _send_local_command(
    node_id: str,
    *,
    action: str,
    instance_id: str,
    payload: dict[str, Any] | None = None,
    timeout: float = COMMAND_TIMEOUT_SECONDS,
) -> Any:
    connection = _connections.get(node_id)
    if not connection or connection.websocket.client_state != WebSocketState.CONNECTED:
        raise RuntimeError("Browser Lite node is offline")
    request_id = secrets.token_urlsafe(18)
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    connection.pending[request_id] = future
    message = {
        "type": "request",
        "requestId": request_id,
        "action": action,
        "instanceId": instance_id,
        **(payload or {}),
    }
    try:
        async with connection.send_lock:
            await connection.websocket.send_json(message)
        response = await asyncio.wait_for(future, timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise RuntimeError(f"Browser Lite node request timed out: {action}") from exc
    finally:
        connection.pending.pop(request_id, None)
    if not response.get("ok"):
        raise BrowserLiteNodeError(
            response.get("error") or f"Browser Lite node request failed: {action}",
            error_code=response.get("errorCode"),
            details=response.get("details"),
        )
    return response.get("result")


async def _queue_node_command(
    node_id: str,
    *,
    action: str,
    instance_id: str,
    payload: dict[str, Any] | None,
    timeout: float,
) -> Any:
    if not await node_online(node_id):
        raise RuntimeError("Browser Lite node is offline")
    command_id = f"blc_{secrets.token_urlsafe(16)}"
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=timeout)
    await get_pool().execute(
        """
        INSERT INTO browser_lite_node_commands
            (id, node_id, instance_id, action, payload, expires_at)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6)
        """,
        command_id,
        node_id,
        instance_id,
        action,
        payload or {},
        expires_at,
    )
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        row = await get_pool().fetchrow(
            "SELECT status, response, error FROM browser_lite_node_commands WHERE id = $1",
            command_id,
        )
        if row and row["status"] == "completed":
            return row["response"]
        if row and row["status"] == "failed":
            raise RuntimeError(row["error"] or f"Browser Lite node request failed: {action}")
        await asyncio.sleep(0.1)
    await get_pool().execute(
        "UPDATE browser_lite_node_commands SET status = 'expired', updated_at = NOW() WHERE id = $1 AND status IN ('pending', 'dispatched')",
        command_id,
    )
    raise RuntimeError(f"Browser Lite node request timed out: {action}")


async def send_node_command(
    node_id: str,
    *,
    action: str,
    instance_id: str,
    payload: dict[str, Any] | None = None,
    timeout: float = COMMAND_TIMEOUT_SECONDS,
) -> Any:
    if node_is_online(node_id):
        return await _send_local_command(
            node_id,
            action=action,
            instance_id=instance_id,
            payload=payload,
            timeout=timeout,
        )
    return await _queue_node_command(
        node_id,
        action=action,
        instance_id=instance_id,
        payload=payload,
        timeout=timeout,
    )


async def _command_pump(connection: NodeConnection) -> None:
    pool = get_pool()
    await pool.execute(
        """
        UPDATE browser_lite_node_commands
        SET status = CASE WHEN expires_at > NOW() THEN 'failed' ELSE 'expired' END,
            error = CASE WHEN expires_at > NOW() THEN 'Browser Lite node reconnected; command outcome is unknown' ELSE error END,
            updated_at = NOW()
        WHERE node_id = $1 AND status = 'dispatched'
        """,
        connection.node_id,
    )
    while _connections.get(connection.node_id) is connection:
        row = None
        async with pool.acquire() as database_connection:
            async with database_connection.transaction():
                row = await database_connection.fetchrow(
                    """
                    SELECT id, instance_id, action, payload
                    FROM browser_lite_node_commands
                    WHERE node_id = $1 AND status = 'pending' AND expires_at > NOW()
                    ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1
                    """,
                    connection.node_id,
                )
                if row:
                    await database_connection.execute(
                        "UPDATE browser_lite_node_commands SET status = 'dispatched', updated_at = NOW() WHERE id = $1",
                        row["id"],
                    )
        if not row:
            await asyncio.sleep(0.1)
            continue
        try:
            result = await _send_local_command(
                connection.node_id,
                action=row["action"],
                instance_id=row["instance_id"],
                payload=dict(row["payload"] or {}),
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
            await pool.execute(
                """
                UPDATE browser_lite_node_commands
                SET status = 'completed', response = $2::jsonb, updated_at = NOW()
                WHERE id = $1
                """,
                row["id"],
                result,
            )
        except Exception as exc:
            await pool.execute(
                """
                UPDATE browser_lite_node_commands
                SET status = 'failed', error = $2, updated_at = NOW()
                WHERE id = $1
                """,
                row["id"],
                str(exc)[:1000],
            )


async def session_node(session_id: str) -> dict[str, Any]:
    row = await get_pool().fetchrow(
        """
        SELECT s.browser_lite_node_id, n.tenant_id, n.display_name
        FROM sessions s
        LEFT JOIN browser_lite_nodes n ON n.id = s.browser_lite_node_id
        WHERE s.id = $1 AND COALESCE(s.browser_runtime, 'standard_chrome') = 'browser_lite'
        """,
        session_id,
    )
    if not row or not row["browser_lite_node_id"]:
        raise RuntimeError("Browser Lite session has no assigned node")
    return dict(row)


async def session_command(
    session_id: str,
    action: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float = COMMAND_TIMEOUT_SECONDS,
) -> Any:
    node = await session_node(session_id)
    return await send_node_command(
        node["browser_lite_node_id"],
        action=action,
        instance_id=session_id,
        payload=payload,
        timeout=timeout,
    )


async def webdriver_request(
    session_id: str,
    path: str,
    method: str = "GET",
    body: Any = None,
    *,
    timeout: float = 30,
) -> dict[str, Any]:
    result = await session_command(
        session_id,
        "webdriver",
        payload={"request": {"path": path, "method": method, "body": body}},
        timeout=timeout + 5,
    )
    raw_body = result.get("body", "") if isinstance(result, dict) else ""
    try:
        data = json.loads(raw_body or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Browser Lite returned non-JSON WebDriver response for {path}") from exc
    return {
        "status": int(result.get("status", 500)),
        "headers": result.get("headers") or {},
        "data": data,
    }


async def task_space_command(
    session_id: str,
    method: str,
    *args: Any,
    timeout: float = COMMAND_TIMEOUT_SECONDS,
) -> Any:
    if method not in TASK_SPACE_METHODS:
        raise ValueError(f"Unknown Browser Lite task-space method: {method}")
    return await session_command(
        session_id,
        "task_space",
        payload={"method": method, "args": list(args)},
        timeout=timeout,
    )


@router.post("/task-spaces/command")
async def task_space_api(body: TaskSpaceBody, user: CurrentUser = Depends(get_current_user)):
    node = await session_node(body.sessionId)
    if node.get("tenant_id") != user.tenant_id:
        raise HTTPException(status_code=404, detail="Browser Lite session not found")
    try:
        return await task_space_command(body.sessionId, body.method, *body.args)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except BrowserLiteNodeError as exc:
        raise HTTPException(
            status_code=409 if exc.error_code in {"EGO_TASK_SPACE_USER_IN_CONTROL", "EGO_TASK_SPACE_INACTIVE"} else 502,
            detail={"message": str(exc), "errorCode": exc.error_code, "details": exc.details},
        ) from exc


async def session_is_browser_lite(session_id: str) -> bool:
    row = await get_pool().fetchrow(
        "SELECT COALESCE(browser_runtime, 'standard_chrome') AS browser_runtime FROM sessions WHERE id = $1",
        session_id,
    )
    return bool(row and row["browser_runtime"] == "browser_lite")


@router.post("/pairing-codes")
async def create_pairing_code(user: CurrentUser = Depends(get_current_user)):
    code = f"{secrets.randbelow(10_000_000_000):010d}"
    code_id = f"blp_{secrets.token_urlsafe(12)}"
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=PAIRING_TTL_SECONDS)
    await get_pool().execute(
        """
        INSERT INTO browser_lite_pairing_codes (id, tenant_id, code_hash, expires_at, created_by)
        VALUES ($1, $2, $3, $4, $5)
        """,
        code_id,
        user.tenant_id,
        _hash_secret(code, "pair"),
        expires_at,
        user.id,
    )
    return {"pairingCode": code, "expiresAt": expires_at.isoformat(), "ttlSeconds": PAIRING_TTL_SECONDS}


@router.post("/pair")
async def pair_node(body: PairingBody, request: Request):
    _check_pairing_rate_limit(request)
    pool = get_pool()
    async with pool.acquire() as connection:
        async with connection.transaction():
            row = await connection.fetchrow(
                """
                SELECT id, tenant_id, created_by
                FROM browser_lite_pairing_codes
                WHERE code_hash = $1 AND used_at IS NULL AND expires_at > NOW()
                ORDER BY created_at DESC LIMIT 1 FOR UPDATE
                """,
                _hash_secret(body.pairingCode.strip(), "pair"),
            )
            if not row:
                raise HTTPException(status_code=401, detail="Pairing code is invalid or expired")
            await connection.execute(
                "UPDATE browser_lite_pairing_codes SET used_at = NOW() WHERE id = $1",
                row["id"],
            )
            node_id = f"bln_{secrets.token_urlsafe(12)}"
            token = secrets.token_urlsafe(48)
            await connection.execute(
                """
                INSERT INTO browser_lite_nodes
                    (id, tenant_id, display_name, token_hash, platform, architecture,
                     app_version, chromium_version, capabilities, status, created_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, 'offline', $10)
                """,
                node_id,
                row["tenant_id"],
                body.displayName.strip(),
                _hash_secret(token, "node"),
                body.platform,
                body.architecture,
                body.appVersion,
                body.chromiumVersion,
                body.capabilities,
                row["created_by"],
            )
    return {"nodeId": node_id, "token": token, "displayName": body.displayName.strip()}


@router.get("/nodes")
async def list_nodes(user: CurrentUser = Depends(get_current_user)):
    rows = await get_pool().fetch(
        """
        SELECT id, display_name, platform, architecture, app_version, chromium_version,
               capabilities, status, last_seen_at, created_at
        FROM browser_lite_nodes WHERE tenant_id = $1 ORDER BY created_at DESC
        """,
        user.tenant_id,
    )
    return {
        "nodes": [
            {
                "id": row["id"],
                "displayName": row["display_name"],
                "platform": row["platform"],
                "architecture": row["architecture"],
                "appVersion": row["app_version"],
                "chromiumVersion": row["chromium_version"],
                "capabilities": list(row["capabilities"] or []),
                "status": "online" if (
                    node_is_online(row["id"])
                    or (row["status"] == "online" and row["last_seen_at"] and row["last_seen_at"] > datetime.now(timezone.utc) - timedelta(seconds=45))
                ) else "offline",
                "lastSeenAt": _iso(row["last_seen_at"]),
                "createdAt": _iso(row["created_at"]),
            }
            for row in rows
        ]
    }


@router.post("/nodes/{node_id}/disconnect")
async def disconnect_node(node_id: str, request: Request):
    authorization = request.headers.get("authorization") or ""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Missing Browser Lite node token")
    row = await get_pool().fetchrow(
        "SELECT id FROM browser_lite_nodes WHERE id = $1 AND token_hash = $2",
        node_id,
        _hash_secret(token, "node"),
    )
    if not row:
        raise HTTPException(status_code=401, detail="Invalid Browser Lite node token")
    await get_pool().execute(
        """
        UPDATE browser_lite_nodes
        SET token_hash = $1, status = 'offline', updated_at = NOW()
        WHERE id = $2
        """,
        _hash_secret(secrets.token_urlsafe(48), "node"),
        node_id,
    )
    connection = _connections.get(node_id)
    if connection and connection.websocket.client_state == WebSocketState.CONNECTED:
        await connection.websocket.close(code=1000)
    return {"ok": True}


@router.websocket("/nodes/connect")
async def connect_node(websocket: WebSocket):
    node_id = websocket.query_params.get("nodeId") or ""
    await websocket.accept()
    try:
        auth_message = await asyncio.wait_for(websocket.receive_json(), timeout=10)
    except Exception:
        await websocket.close(code=1008)
        return
    token = str(auth_message.get("token") or "") if auth_message.get("type") == "auth" else ""
    row = await get_pool().fetchrow(
        "SELECT id FROM browser_lite_nodes WHERE id = $1 AND token_hash = $2",
        node_id,
        _hash_secret(token, "node"),
    )
    if not row:
        await websocket.close(code=1008)
        return
    await websocket.send_json({"type": "auth_ok"})
    previous = _connections.pop(node_id, None)
    if previous and previous.websocket.client_state == WebSocketState.CONNECTED:
        await previous.websocket.close(code=1012)
    connection = NodeConnection(node_id=node_id, websocket=websocket)
    _connections[node_id] = connection
    await _set_node_status(node_id, "online")
    command_pump = asyncio.create_task(_command_pump(connection))
    try:
        while True:
            message = await websocket.receive_json()
            message_type = message.get("type")
            if message_type == "response" and message.get("requestId"):
                pending = connection.pending.get(message["requestId"])
                if pending and not pending.done():
                    pending.set_result(message)
            elif message_type in {"heartbeat", "hello"}:
                await _set_node_status(node_id, "online")
                if message_type == "hello":
                    capabilities = message.get("capabilities")
                    if not isinstance(capabilities, list) or not all(isinstance(value, str) for value in capabilities):
                        capabilities = []
                    capabilities = capabilities[:30]
                    await get_pool().execute(
                        """
                        UPDATE browser_lite_nodes
                        SET app_version = $1, chromium_version = $2,
                            capabilities = CASE WHEN $3::jsonb = '[]'::jsonb THEN capabilities ELSE $3::jsonb END,
                            updated_at = NOW()
                        WHERE id = $4
                        """,
                        str(message.get("appVersion") or ""),
                        str(message.get("chromiumVersion") or ""),
                        capabilities,
                        node_id,
                    )
    except WebSocketDisconnect:
        pass
    finally:
        command_pump.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await command_pump
        if _connections.get(node_id) is connection:
            _connections.pop(node_id, None)
            await _set_node_offline(connection)
        for pending in connection.pending.values():
            if not pending.done():
                pending.set_exception(RuntimeError("Browser Lite node disconnected"))
