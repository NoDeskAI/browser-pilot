import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.auth.dependencies import get_current_user
from app.routes import network_egress as routes
from app import network_egress


def profile():
    return dict(id="egress-1", tenant_id="tenant-1", name="Office", type="clash",
                status="healthy", proxy_url="", config_ref="private/path",
                config_text="proxies: []", health_error="", last_checked_at=None,
                password="DO_NOT_RETURN", username="DO_NOT_RETURN")


@pytest.mark.parametrize("role,expected", [("admin", 200), ("superadmin", 200), ("member", 403), (None, 401)])
def test_detail_permissions_and_private_fields(monkeypatch, role, expected):
    app = FastAPI()
    app.include_router(routes.router)
    if role:
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(role=role, tenant_id="tenant-1")
    fetch = AsyncMock(return_value=profile())
    monkeypatch.setattr(routes, "fetch_egress_for_tenant", fetch)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.get("/api/network-egress/egress-1")

    response = asyncio.run(run())
    assert response.status_code == expected
    if expected == 200:
        fetch.assert_awaited_once_with("tenant-1", "egress-1")
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["profile"]["configText"] == "proxies: []"
        assert "DO_NOT_RETURN" not in response.text
        assert "private/path" not in response.text
    else:
        fetch.assert_not_awaited()


def test_missing_or_other_tenant_profile_returns_404(monkeypatch):
    monkeypatch.setattr(routes, "fetch_egress_for_tenant", AsyncMock(side_effect=routes.EgressError("not found")))
    with pytest.raises(routes.HTTPException) as exc:
        asyncio.run(routes.get_network_egress("other", routes.Response(), SimpleNamespace(tenant_id="tenant-1")))
    assert exc.value.status_code == 404


@pytest.mark.parametrize("body,changes_config", [({"name": "Renamed"}, False), ({"configUrl": "https://config.example/clash.yaml"}, True)])
def test_rename_keeps_config_and_url_replacement_invalidates_gateway(monkeypatch, body, changes_config):
    row = profile()
    monkeypatch.setattr(routes, "fetch_egress_for_tenant", AsyncMock(return_value=row))
    monkeypatch.setattr(routes, "assert_managed_network_egress_supported", lambda *_: None)
    resolve = AsyncMock(return_value="new config")
    write = AsyncMock(return_value="new/path")
    remove = AsyncMock()
    pool = SimpleNamespace(fetchrow=AsyncMock(return_value=row))
    monkeypatch.setattr(routes, "resolve_config_text", resolve)
    monkeypatch.setattr(routes, "write_config_ref", write)
    monkeypatch.setattr(routes, "remove_managed_egress", remove)
    monkeypatch.setattr(routes, "get_pool", lambda: pool)
    asyncio.run(routes.update_network_egress("egress-1", routes.EgressUpdateBody(**body), SimpleNamespace(tenant_id="tenant-1")))
    if changes_config:
        remove.assert_awaited_once_with("egress-1")
        resolve.assert_awaited_once_with(None, body["configUrl"])
    else:
        remove.assert_not_awaited()
        write.assert_not_awaited()
        assert pool.fetchrow.call_args.args[3:6] == (row["config_ref"], row["config_text"], row["status"])
        assert pool.fetchrow.call_args.args[8] is False


def test_openvpn_blank_credentials_preserve_existing_auth_file(monkeypatch, tmp_path):
    monkeypatch.setattr(network_egress, "_egress_dir", lambda *_: tmp_path)
    auth = tmp_path / "auth.txt"
    auth.write_text("old-user\nold-password\n")
    asyncio.run(network_egress.write_config_ref("tenant", "egress", "openvpn", "client\ndev tun"))
    assert auth.read_text() == "old-user\nold-password\n"
    asyncio.run(network_egress.write_config_ref("tenant", "egress", "openvpn", "client\ndev tun", "new-user", "new-password"))
    assert auth.read_text() == "new-user\nnew-password\n"
