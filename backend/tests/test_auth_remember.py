import asyncio
import hashlib
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from app.auth import jwt as auth_jwt
from app.auth import routes as auth_routes


class FakePool:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.fetches = []
        self.executed = []

    async def fetchrow(self, *args):
        self.fetches.append(args)
        if not self.rows:
            return None
        return self.rows.pop(0)

    async def execute(self, *args):
        self.executed.append(args)
        return "OK"


def _user_row():
    return {
        "id": "user-1",
        "tenant_id": "tenant-1",
        "email": "user@example.com",
        "password_hash": "hash",
        "name": "User",
        "role": "admin",
        "is_active": True,
    }


def _cookie_headers(response: Response) -> list[str]:
    return [
        value.decode()
        for key, value in response.raw_headers
        if key.lower() == b"set-cookie"
    ]


def _request_with_remember_cookie(raw_token: str) -> Request:
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/auth/refresh",
        "headers": [(b"cookie", f"{auth_routes.REMEMBER_COOKIE_NAME}={raw_token}".encode())],
    }, receive)


def test_login_without_remember_me_does_not_set_cookie(monkeypatch):
    pool = FakePool([_user_row()])
    monkeypatch.setattr(auth_routes, "get_pool", lambda: pool)
    monkeypatch.setattr(auth_routes, "verify_password", lambda _raw, _hashed: True)

    response = Response()
    data = asyncio.run(auth_routes.login(
        auth_routes.LoginBody(email="user@example.com", password="secret"),
        response,
    ))

    assert data["access_token"]
    assert data["user"]["email"] == "user@example.com"
    assert _cookie_headers(response) == []
    assert pool.executed == []


def test_login_with_remember_me_stores_hash_and_sets_http_only_cookie(monkeypatch):
    pool = FakePool([_user_row()])
    monkeypatch.setattr(auth_routes, "get_pool", lambda: pool)
    monkeypatch.setattr(auth_routes, "verify_password", lambda _raw, _hashed: True)
    monkeypatch.setattr(auth_routes.secrets, "token_urlsafe", lambda _n: "remember-raw")
    monkeypatch.setattr(auth_routes.uuid, "uuid4", lambda: "remember-id")

    response = Response()
    asyncio.run(auth_routes.login(
        auth_routes.LoginBody(email="user@example.com", password="secret", rememberMe=True),
        response,
    ))

    assert pool.executed[0][1] == "remember-id"
    assert pool.executed[0][2] == "user-1"
    assert pool.executed[0][3] == "tenant-1"
    assert pool.executed[0][4] == hashlib.sha256(b"remember-raw").hexdigest()
    assert timedelta(days=6, hours=23) < pool.executed[0][5] - datetime.now(timezone.utc) <= timedelta(days=7)
    cookie = _cookie_headers(response)[0]
    assert f"{auth_routes.REMEMBER_COOKIE_NAME}=remember-raw" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Max-Age=604" in cookie


def test_refresh_rotates_remember_token_and_preserves_absolute_expiry(monkeypatch):
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    pool = FakePool([{
        "remember_token_id": "old-token-id",
        "expires_at": expires_at,
        "id": "user-1",
        "tenant_id": "tenant-1",
        "email": "user@example.com",
        "name": "User",
        "role": "admin",
    }])
    monkeypatch.setattr(auth_routes, "get_pool", lambda: pool)
    monkeypatch.setattr(auth_routes.secrets, "token_urlsafe", lambda _n: "new-raw")
    monkeypatch.setattr(auth_routes.uuid, "uuid4", lambda: "new-token-id")

    response = Response()
    data = asyncio.run(auth_routes.refresh(_request_with_remember_cookie("old-raw"), response))

    assert data["access_token"]
    assert pool.fetches[0][1] == hashlib.sha256(b"old-raw").hexdigest()
    assert pool.executed[0][1] == "old-token-id"
    assert pool.executed[1][1] == "new-token-id"
    assert pool.executed[1][4] == hashlib.sha256(b"new-raw").hexdigest()
    assert pool.executed[1][5] == expires_at
    assert f"{auth_routes.REMEMBER_COOKIE_NAME}=new-raw" in _cookie_headers(response)[0]


def test_refresh_rejects_invalid_remember_token_and_clears_cookie(monkeypatch):
    pool = FakePool([None])
    monkeypatch.setattr(auth_routes, "get_pool", lambda: pool)

    response = Response()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth_routes.refresh(_request_with_remember_cookie("stale"), response))

    assert exc.value.status_code == 401
    cookie = _cookie_headers(response)[0]
    assert f"{auth_routes.REMEMBER_COOKIE_NAME}=" in cookie
    assert "Max-Age=0" in cookie


def test_access_token_default_ttl_is_30_minutes(monkeypatch):
    monkeypatch.setattr(auth_jwt, "JWT_EXPIRE_MINUTES", 30)

    token = auth_jwt.create_access_token("user-1", "tenant-1", "admin")
    payload = jwt.decode(token, auth_jwt.JWT_SECRET, algorithms=["HS256"])
    expires_at = datetime.fromtimestamp(payload["exp"], timezone.utc)

    ttl = expires_at - datetime.now(timezone.utc)
    assert timedelta(minutes=29) < ttl <= timedelta(minutes=30)
