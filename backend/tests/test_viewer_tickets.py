import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from app import agent_devices, rfb_proxy, viewer_tickets
from app.auth.dependencies import CurrentUser


def _user(user_id="user-1", *, api_token_id=None):
    return CurrentUser(
        id=user_id,
        tenant_id="tenant-1",
        email=f"{user_id}@example.com",
        name=user_id,
        role="member",
        created_at="2026-06-01T00:00:00Z",
        api_token_id=api_token_id,
    )


def _request():
    return SimpleNamespace(
        headers={"host": "localhost:8000", "user-agent": "pytest"},
        url=SimpleNamespace(scheme="http", netloc="localhost:8000"),
        client=SimpleNamespace(host="127.0.0.1"),
    )


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None


class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_exc):
        return None


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return FakeAcquire(self.conn)

    async def execute(self, *args):
        return await self.conn.execute(*args)


class FakeConn:
    def __init__(self):
        self.tickets = {}
        self.leases = {}

    def transaction(self):
        return FakeTransaction()

    async def execute(self, query, *args):
        sql = " ".join(query.lower().split())
        if "insert into session_viewer_tickets" in sql:
            ticket = {
                "id": args[0],
                "session_id": args[1],
                "tenant_id": args[2],
                "user_id": args[3],
                "operator_subject": args[4],
                "lease_id": args[5],
                "token_hash": args[6],
                "mode": args[7],
                "expires_at": args[8],
                "remote_addr": args[9],
                "user_agent": args[10],
                "consumed_at": None,
            }
            self.tickets[ticket["id"]] = ticket
            return "INSERT"
        return "OK"

    async def fetchrow(self, query, *args):
        sql = " ".join(query.lower().split())
        if "from session_viewer_tickets" in sql:
            token_hash, session_id = args
            return next(
                (
                    ticket
                    for ticket in self.tickets.values()
                    if ticket["token_hash"] == token_hash and ticket["session_id"] == session_id
                ),
                None,
            )
        if "from agent_device_leases" in sql:
            lease_id, session_id, operator = args
            lease = self.leases.get(lease_id)
            if not lease:
                return None
            if lease["session_id"] != session_id or lease["current_operator"] != operator or lease["status"] != "active":
                return None
            if lease["expires_at"] and lease["expires_at"] <= datetime.now(timezone.utc):
                return None
            return lease
        if "update session_viewer_tickets" in sql:
            ticket_id = args[0]
            ticket = self.tickets.get(ticket_id)
            if not ticket or ticket["consumed_at"] is not None:
                return None
            ticket["consumed_at"] = datetime.now(timezone.utc)
            return ticket
        return None


def _token_from(payload):
    parsed = urlparse(payload["viewerUrl"])
    return parse_qs(parsed.query)["ticket"][0]


def test_view_ticket_does_not_require_active_lease(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(viewer_tickets, "get_pool", lambda: FakePool(conn))
    user = _user()
    ctx = agent_devices.AgentDeviceActionContext(
        session_id="session-1",
        action="session.viewer.ticket.issue",
        actor="user:user-1",
        actor_owner_user_id="user-1",
        lease={},
        side_effect_level="internal",
    )

    payload = asyncio.run(
        viewer_tickets.issue_viewer_ticket(
            session_id="session-1",
            user=user,
            ctx=ctx,
            request=_request(),
            mode=viewer_tickets.VIEWER_MODE_VIEW,
        )
    )
    consumed = asyncio.run(
        viewer_tickets.consume_viewer_ticket(
            session_id="session-1",
            token=_token_from(payload),
            origin="http://localhost:9874",
        )
    )

    assert payload["mode"] == viewer_tickets.VIEWER_MODE_VIEW
    assert consumed.mode == viewer_tickets.VIEWER_MODE_VIEW
    assert consumed.lease_id is None


def test_control_ticket_still_requires_lease(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(viewer_tickets, "get_pool", lambda: FakePool(conn))
    user = _user()
    ctx = agent_devices.AgentDeviceActionContext(
        session_id="session-1",
        action="session.viewer.ticket.issue",
        actor="user:user-1",
        actor_owner_user_id="user-1",
        lease={},
        side_effect_level="internal",
    )

    with pytest.raises(viewer_tickets.ViewerTicketError) as exc:
        asyncio.run(
            viewer_tickets.issue_viewer_ticket(
                session_id="session-1",
                user=user,
                ctx=ctx,
                request=_request(),
                mode=viewer_tickets.VIEWER_MODE_CONTROL,
            )
        )

    assert exc.value.reason == "lease_required"


def test_view_only_rfb_filter_drops_input_messages():
    filt = rfb_proxy._RfbClientViewOnlyFilter()
    client_init = b"\x01"
    framebuffer_request = b"\x03\x01\x00\x00\x00\x00\x04\x00\x03\x00"
    key_event = b"\x04\x01\x00\x00\x00\x00\x00A"
    pointer_event = b"\x05\x01\x00\x10\x00\x20"
    set_encodings = b"\x02\x00\x00\x01\x00\x00\x00\x07"

    out = filt.feed(client_init + framebuffer_request + key_event + pointer_event + set_encodings)

    assert out == client_init + framebuffer_request + set_encodings
