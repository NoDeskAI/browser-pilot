import asyncio
import hashlib
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from app.auth import jwt as auth_jwt
from app.auth import routes as auth_routes
from app.routes import users as users_routes


class FakePool:
    def __init__(self, rows=None, fetch_values=None):
        self.rows = list(rows or [])
        self.fetch_values = list(fetch_values or [])
        self.fetches = []
        self.fetch_values_calls = []
        self.executed = []

    async def fetchrow(self, *args):
        self.fetches.append(args)
        if not self.rows:
            return None
        return self.rows.pop(0)

    async def fetch(self, *args):
        self.fetches.append(args)
        if not self.rows:
            return []
        row = self.rows.pop(0)
        if isinstance(row, list):
            return row
        return [row] if row else []

    async def fetchval(self, *args):
        self.fetch_values_calls.append(args)
        if not self.fetch_values:
            return None
        return self.fetch_values.pop(0)

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


def _request(path: str = "/api/auth/register", host: str = "203.0.113.10") -> Request:
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request({
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [],
        "client": (host, 45678),
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


def test_login_with_invited_email_in_multiple_tenants_requires_tenant_slug(monkeypatch):
    tenant_a = {
        **_user_row(),
        "tenant_name": "Tenant A",
        "tenant_slug": "tenant-a",
    }
    tenant_b = {
        **_user_row(),
        "id": "user-2",
        "tenant_id": "tenant-2",
        "tenant_name": "Tenant B",
        "tenant_slug": "tenant-b",
    }
    pool = FakePool([[tenant_a, tenant_b]])
    monkeypatch.setattr(auth_routes, "get_pool", lambda: pool)
    monkeypatch.setattr(auth_routes, "verify_password", lambda _raw, _hashed: True)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth_routes.login(
            auth_routes.LoginBody(email="USER@example.com", password="secret"),
            Response(),
        ))

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "multiple_tenants"
    assert exc.value.detail["tenants"] == [
        {"name": "Tenant A", "slug": "tenant-a"},
        {"name": "Tenant B", "slug": "tenant-b"},
    ]


def test_login_with_tenant_slug_selects_invited_tenant(monkeypatch):
    row = {
        **_user_row(),
        "tenant_name": "Tenant B",
        "tenant_slug": "tenant-b",
    }
    pool = FakePool([row])
    monkeypatch.setattr(auth_routes, "get_pool", lambda: pool)
    monkeypatch.setattr(auth_routes, "verify_password", lambda _raw, _hashed: True)

    data = asyncio.run(auth_routes.login(
        auth_routes.LoginBody(email="user@example.com", password="secret", tenantSlug="tenant-b"),
        Response(),
    ))

    assert data["user"]["tenantId"] == "tenant-1"
    assert "AND t.slug = $2" in pool.fetches[0][0]
    assert pool.fetches[0][2] == "tenant-b"


def test_register_creates_tenant_superadmin_and_token(monkeypatch):
    auth_routes._register_rate_attempts.clear()
    pool = FakePool(fetch_values=[None, None])
    hook_calls = []

    async def after_setup(**kwargs):
        hook_calls.append(kwargs)

    monkeypatch.setattr(auth_routes, "get_pool", lambda: pool)
    monkeypatch.setattr(auth_routes, "hash_password", lambda raw: f"hash:{raw}")
    monkeypatch.setattr(auth_routes, "after_tenant_setup", after_setup)
    monkeypatch.setattr(auth_routes.uuid, "uuid4", lambda: "fixed-id")

    response = Response()
    data = asyncio.run(auth_routes.register(
        auth_routes.RegisterBody(
            tenantName="Acme Inc",
            name="Alice",
            email="Alice@Example.com",
            password="secret1",
        ),
        _request(),
        response,
    ))

    assert data["access_token"]
    assert data["user"] == {
        "id": "fixed-id",
        "email": "alice@example.com",
        "name": "Alice",
        "role": "superadmin",
        "tenantId": "fixed-id",
    }
    assert pool.executed[0][0].startswith("INSERT INTO tenants")
    assert pool.executed[0][1:] == ("fixed-id", "Acme Inc", "acme-inc")
    assert pool.executed[1][0].startswith("INSERT INTO users")
    assert pool.executed[1][1:] == ("fixed-id", "fixed-id", "alice@example.com", "hash:secret1", "Alice")
    assert hook_calls == [{"tenant_id": "fixed-id", "user_id": "fixed-id"}]


def test_register_rejects_existing_email_globally(monkeypatch):
    auth_routes._register_rate_attempts.clear()
    pool = FakePool(fetch_values=[1])
    monkeypatch.setattr(auth_routes, "get_pool", lambda: pool)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth_routes.register(
            auth_routes.RegisterBody(
                tenantName="Acme Inc",
                name="Alice",
                email="alice@example.com",
                password="secret1",
            ),
            _request(host="203.0.113.11"),
            Response(),
        ))

    assert exc.value.status_code == 409
    assert exc.value.detail == "email_already_registered"
    assert pool.executed == []


def test_register_rate_limit(monkeypatch):
    auth_routes._register_rate_attempts.clear()
    monkeypatch.setattr(auth_routes, "REGISTER_RATE_LIMIT_BY_IP", 1)
    pool = FakePool(fetch_values=[None, None])

    async def after_setup(**_kwargs):
        return None

    monkeypatch.setattr(auth_routes, "get_pool", lambda: pool)
    monkeypatch.setattr(auth_routes, "after_tenant_setup", after_setup)

    asyncio.run(auth_routes.register(
        auth_routes.RegisterBody(
            tenantName="Acme Inc",
            name="Alice",
            email="alice@example.com",
            password="secret1",
        ),
        _request(host="203.0.113.12"),
        Response(),
    ))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth_routes.register(
            auth_routes.RegisterBody(
                tenantName="Beta Inc",
                name="Bob",
                email="bob@example.com",
                password="secret1",
            ),
            _request(host="203.0.113.12"),
            Response(),
        ))
    assert exc.value.status_code == 429


def test_invite_existing_email_in_other_tenant_is_allowed(monkeypatch):
    pool = FakePool(fetch_values=[None])
    monkeypatch.setattr(users_routes, "get_pool", lambda: pool)
    monkeypatch.setattr(users_routes, "hash_password", lambda raw: f"hash:{raw}")
    monkeypatch.setattr(users_routes.uuid, "uuid4", lambda: "invited-user")

    data = asyncio.run(users_routes.create_user(
        users_routes.InviteUserBody(
            email="Shared@Example.com",
            name="Shared User",
            password="secret1",
            role="member",
        ),
        user=auth_routes.CurrentUser(
            id="admin-1",
            tenant_id="tenant-1",
            email="admin@example.com",
            name="Admin",
            role="admin",
            created_at="now",
            session_scope=None,
        ),
    ))

    assert data["email"] == "shared@example.com"
    assert pool.fetch_values_calls[0][1:] == ("tenant-1", "shared@example.com")
    assert pool.executed[0][1:] == (
        "invited-user",
        "tenant-1",
        "shared@example.com",
        "hash:secret1",
        "Shared User",
        "member",
    )


def test_invite_duplicate_email_in_same_tenant_conflicts(monkeypatch):
    pool = FakePool(fetch_values=[1])
    monkeypatch.setattr(users_routes, "get_pool", lambda: pool)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(users_routes.create_user(
            users_routes.InviteUserBody(
                email="shared@example.com",
                name="Shared User",
                password="secret1",
                role="member",
            ),
            user=auth_routes.CurrentUser(
                id="admin-1",
                tenant_id="tenant-1",
                email="admin@example.com",
                name="Admin",
                role="admin",
                created_at="now",
                session_scope=None,
            ),
        ))

    assert exc.value.status_code == 409
    assert pool.executed == []


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
