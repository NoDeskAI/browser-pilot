import asyncio
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from app import file_capture


class FakePool:
    def __init__(self):
        self.sessions = {"session-1": {"tenant_id": "tenant-1"}}
        self.tokens = []
        self.status = {}
        self.executed = []

    async def fetchrow(self, query, *args):
        if "SELECT tenant_id FROM sessions" in query:
            return self.sessions.get(args[0])
        if "FROM session_runtime_tokens" in query:
            session_id, purpose, token_hash = args
            now = datetime.now(timezone.utc)
            for token in self.tokens:
                if (
                    token["session_id"] == session_id
                    and token["purpose"] == purpose
                    and token["token_hash"] == token_hash
                    and token.get("revoked_at") is None
                    and (token.get("expires_at") is None or token["expires_at"] > now)
                ):
                    return {"id": token["id"], "tenant_id": token["tenant_id"]}
            return None
        if "FROM session_runtime_status" in query:
            return self.status.get((args[0], args[1]))
        return None

    async def execute(self, query, *args):
        self.executed.append((query, args))
        if "INSERT INTO session_runtime_tokens" in query:
            (
                token_id,
                session_id,
                tenant_id,
                purpose,
                token_hash,
                expires_at,
            ) = args
            self.tokens.append({
                "id": token_id,
                "session_id": session_id,
                "tenant_id": tenant_id,
                "purpose": purpose,
                "token_hash": token_hash,
                "expires_at": expires_at,
                "revoked_at": None,
            })
        elif "UPDATE session_runtime_tokens" in query and "SET revoked_at" in query:
            session_id, purpose = args
            for token in self.tokens:
                if token["session_id"] == session_id and token["purpose"] == purpose and token.get("revoked_at") is None:
                    token["revoked_at"] = datetime.now(timezone.utc)
        elif "UPDATE session_runtime_tokens SET last_used_at" in query:
            token_id = args[0]
            for token in self.tokens:
                if token["id"] == token_id:
                    token["last_used_at"] = datetime.now(timezone.utc)
        elif "INSERT INTO session_runtime_status" in query:
            session_id, purpose, status, heartbeat, error = args
            existing = self.status.get((session_id, purpose), {})
            self.status[(session_id, purpose)] = {
                "status": status,
                "last_heartbeat_at": datetime.now(timezone.utc) if heartbeat else existing.get("last_heartbeat_at"),
                "last_error": error,
                "updated_at": datetime.now(timezone.utc),
            }
        return "OK"


def _hash(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def test_verify_file_capture_token_accepts_valid_runtime_token(monkeypatch):
    pool = FakePool()
    raw = "bpr_valid"
    pool.tokens.append({
        "id": "token-1",
        "session_id": "session-1",
        "tenant_id": "tenant-1",
        "purpose": file_capture.FILE_CAPTURE_PURPOSE,
        "token_hash": _hash(raw),
        "expires_at": datetime.now(timezone.utc) + timedelta(days=1),
        "revoked_at": None,
    })
    monkeypatch.setattr(file_capture, "get_pool", lambda: pool)

    result = asyncio.run(file_capture.verify_file_capture_token("session-1", raw))

    assert result == {"tenant_id": "tenant-1"}
    assert pool.tokens[0]["last_used_at"] is not None


@pytest.mark.parametrize(
    ("session_id", "raw", "expires_delta", "revoked"),
    [
        ("session-2", "bpr_valid", timedelta(days=1), False),
        ("session-1", "bpr_valid", timedelta(seconds=-1), False),
        ("session-1", "bpr_valid", timedelta(days=1), True),
        ("session-1", "user_api_token", timedelta(days=1), False),
    ],
)
def test_verify_file_capture_token_rejects_cross_session_expired_revoked_and_user_tokens(
    monkeypatch,
    session_id,
    raw,
    expires_delta,
    revoked,
):
    pool = FakePool()
    pool.tokens.append({
        "id": "token-1",
        "session_id": "session-1",
        "tenant_id": "tenant-1",
        "purpose": file_capture.FILE_CAPTURE_PURPOSE,
        "token_hash": _hash("bpr_valid"),
        "expires_at": datetime.now(timezone.utc) + expires_delta,
        "revoked_at": datetime.now(timezone.utc) if revoked else None,
    })
    monkeypatch.setattr(file_capture, "get_pool", lambda: pool)

    with pytest.raises(file_capture.HTTPException) as exc:
        asyncio.run(file_capture.verify_file_capture_token(session_id, raw))

    assert exc.value.status_code == 401


def test_file_capture_status_marks_running_heartbeat_stale_as_unavailable(monkeypatch):
    pool = FakePool()
    pool.status[("session-1", file_capture.FILE_CAPTURE_PURPOSE)] = {
        "status": "running",
        "last_heartbeat_at": datetime.now(timezone.utc) - timedelta(seconds=file_capture.HEARTBEAT_STALE_SECONDS + 5),
        "last_error": "",
        "updated_at": datetime.now(timezone.utc),
    }
    monkeypatch.setattr(file_capture, "get_pool", lambda: pool)

    result = asyncio.run(file_capture.get_file_capture_status("session-1", container_status="running"))

    assert result["status"] == "unavailable"
    assert "file_capture_agent_unavailable" in result["warnings"]
