import asyncio
import json
from types import SimpleNamespace

import pytest

from app import container
from app.routes import sessions


@pytest.fixture(autouse=True)
def clear_container_network_state():
    container._NETWORK_PROFILE_CACHE.clear()
    container._BACKGROUND_NETWORK_TASKS.clear()
    yield
    container._NETWORK_PROFILE_CACHE.clear()
    container._BACKGROUND_NETWORK_TASKS.clear()


def _network(timezone="Europe/Berlin", country_code="DE"):
    dns = ["223.5.5.5", "119.29.29.29"] if country_code == "CN" else ["1.1.1.1", "8.8.8.8"]
    return {
        "ip": "203.0.113.8",
        "countryCode": country_code,
        "country": "Germany",
        "region": "Berlin",
        "city": "Berlin",
        "timezone": timezone,
        "asn": "AS64500",
        "isp": "Example ISP",
        "lat": 52.52,
        "lon": 13.405,
        "postal": "10115",
        "source": "test",
        "probedAt": "2026-04-27T00:00:00Z",
        "dnsServers": dns,
        "warnings": [],
    }


def _profile(browser_lang="zh-CN"):
    return {
        "timezone": "UTC",
        "screen": {"width": 1, "height": 1},
        "navigator": {"languages": [browser_lang, browser_lang.split("-")[0], "en"]},
    }


class FakePool:
    def __init__(self, rows):
        self.rows = list(rows)
        self.executed = []

    async def fetchrow(self, *args):
        if not self.rows:
            return None
        row = self.rows.pop(0)
        return row(*args) if callable(row) else row

    async def execute(self, *args):
        self.executed.append(args)
        return "OK"


def _user():
    return SimpleNamespace(id="user-1", tenant_id="tenant-1", role="admin")


def test_create_session_binds_network_timezone_without_changing_browser_lang(monkeypatch):
    pool = FakePool([
        {"chrome_version": "147.0.0.0", "image_tag": "browser-pilot:test"},
    ])
    monkeypatch.setattr(sessions, "get_pool", lambda: pool)
    monkeypatch.setattr(sessions.uuid, "uuid4", lambda: "session-1")

    async def fake_network(proxy_url, image_tag):
        assert proxy_url is None
        assert image_tag == "browser-pilot:test"
        return _network("America/New_York", "US")

    async def fake_generate_profile(tenant_id, browser_lang, chrome_version=None):
        assert tenant_id == "tenant-1"
        assert browser_lang == "fr-FR"
        assert chrome_version == "147.0.0.0"
        return _profile(browser_lang)

    monkeypatch.setattr(sessions, "_resolve_session_network", fake_network)
    monkeypatch.setattr(sessions, "generate_profile", fake_generate_profile)

    result = asyncio.run(
        sessions.create_session(
            sessions.CreateSessionBody(name="test", browserLang="fr-FR"),
            _user(),
        )
    )

    profile = result["fingerprintProfile"]
    assert result["browserLang"] == "fr-FR"
    assert profile["timezone"] == "America/New_York"
    assert profile["network"]["timezone"] == "America/New_York"
    assert profile["navigator"]["languages"] == ["fr-FR", "fr", "en"]


def test_create_session_uses_declared_network_without_container_probe(monkeypatch):
    pool = FakePool([
        {"chrome_version": "147.0.0.0", "image_tag": "browser-pilot:test"},
    ])
    calls = {}
    monkeypatch.setattr(sessions, "get_pool", lambda: pool)
    monkeypatch.setattr(sessions.uuid, "uuid4", lambda: "session-1")

    def fake_declared_network(proxy_url, image_tag):
        calls["proxy_url"] = proxy_url
        calls["image_tag"] = image_tag
        return _network("Asia/Shanghai", "CN")

    async def fake_generate_profile(tenant_id, browser_lang, chrome_version=None):
        return _profile(browser_lang)

    monkeypatch.setattr(sessions, "declared_network_profile", fake_declared_network)
    monkeypatch.setattr(sessions, "generate_profile", fake_generate_profile)

    result = asyncio.run(
        sessions.create_session(
            sessions.CreateSessionBody(name="test", browserLang="zh-CN"),
            _user(),
        )
    )

    assert calls == {"proxy_url": None, "image_tag": "browser-pilot:test"}
    assert result["fingerprintProfile"]["network"]["source"] == "test"
    assert result["fingerprintProfile"]["timezone"] == "Asia/Shanghai"


def test_create_session_prefers_exact_chrome_version(monkeypatch):
    pool = FakePool([
        {"chrome_version": "147.0.7727.60", "image_tag": "browser-pilot:147-60"},
    ])
    captured = {}
    monkeypatch.setattr(sessions, "get_pool", lambda: pool)
    monkeypatch.setattr(sessions.uuid, "uuid4", lambda: "session-1")

    async def fake_network(proxy_url, image_tag):
        captured["image_tag"] = image_tag
        return _network()

    async def fake_generate_profile(tenant_id, browser_lang, chrome_version=None):
        captured["chrome_version"] = chrome_version
        return _profile(browser_lang)

    monkeypatch.setattr(sessions, "_resolve_session_network", fake_network)
    monkeypatch.setattr(sessions, "generate_profile", fake_generate_profile)

    result = asyncio.run(
        sessions.create_session(
            sessions.CreateSessionBody(
                name="test",
                browserLang="zh-CN",
                chromeVersion="147.0.7727.60",
            ),
            _user(),
        )
    )

    assert result["chromeVersion"] == "147.0.7727.60"
    assert captured == {
        "image_tag": "browser-pilot:147-60",
        "chrome_version": "147.0.7727.60",
    }


def test_create_session_keeps_major_version_fallback(monkeypatch):
    pool = FakePool([
        {"chrome_version": "147.0.7727.55", "image_tag": "browser-pilot:147"},
    ])
    captured = {}
    monkeypatch.setattr(sessions, "get_pool", lambda: pool)
    monkeypatch.setattr(sessions.uuid, "uuid4", lambda: "session-1")

    async def fake_network(proxy_url, image_tag):
        captured["image_tag"] = image_tag
        return _network()

    async def fake_generate_profile(tenant_id, browser_lang, chrome_version=None):
        captured["chrome_version"] = chrome_version
        return _profile(browser_lang)

    monkeypatch.setattr(sessions, "_resolve_session_network", fake_network)
    monkeypatch.setattr(sessions, "generate_profile", fake_generate_profile)

    result = asyncio.run(
        sessions.create_session(
            sessions.CreateSessionBody(name="test", chromeVersion="147"),
            _user(),
        )
    )

    assert result["chromeVersion"] == "147.0.7727.55"
    assert captured == {
        "image_tag": "browser-pilot:147",
        "chrome_version": "147.0.7727.55",
    }


def test_change_proxy_refreshes_network_and_keeps_language(monkeypatch):
    profile = _profile("ja-JP")
    pool = FakePool([
        {"tenant_id": "tenant-1"},
        {"device_preset": "desktop", "fingerprint_profile": profile, "browser_lang": "ja-JP"},
    ])
    captured = {}
    monkeypatch.setattr(sessions, "get_pool", lambda: pool)
    monkeypatch.setattr(sessions, "_resolve_session_image", lambda session_id: asyncio.sleep(0, "browser-pilot:test"))

    async def fake_network(proxy_url, image_tag):
        assert proxy_url == "http://proxy.example:8080"
        assert image_tag == "browser-pilot:test"
        return _network("Europe/Paris", "FR")

    async def fake_recreate_container(session_id, **kwargs):
        assert session_id == "session-1"
        captured.update(kwargs)
        return {"selenium_port": 4444, "vnc_port": 7900}

    monkeypatch.setattr(sessions, "_resolve_session_network", fake_network)
    monkeypatch.setattr(sessions, "recreate_container", fake_recreate_container)

    result = asyncio.run(
        sessions.change_proxy(
            "session-1",
            sessions.ProxyBody(proxyUrl="http://proxy.example:8080"),
            _user(),
        )
    )

    assert result["ok"] is True
    assert result["fingerprintProfile"]["timezone"] == "Europe/Paris"
    assert result["fingerprintProfile"]["network"]["countryCode"] == "FR"
    assert result["fingerprintProfile"]["navigator"]["languages"] == ["ja-JP", "ja", "en"]
    assert captured["proxy"] == "http://proxy.example:8080"
    assert captured["browser_lang"] == "ja-JP"


def test_regenerate_fingerprint_preserves_network_and_does_not_fallback_to_utc(monkeypatch):
    pool = FakePool([
        {"tenant_id": "tenant-1"},
        {
            "device_preset": "desktop",
            "proxy_url": "socks5://proxy.example:1080",
            "browser_lang": "es-ES",
            "chrome_version": "147.0.0.0",
        },
    ])
    monkeypatch.setattr(sessions, "get_pool", lambda: pool)
    monkeypatch.setattr(sessions, "_resolve_session_image", lambda session_id: asyncio.sleep(0, "browser-pilot:test"))

    async def fake_generate_profile(tenant_id, browser_lang, chrome_version=None):
        assert browser_lang == "es-ES"
        return _profile(browser_lang)

    async def fake_network(proxy_url, image_tag):
        assert proxy_url == "socks5://proxy.example:1080"
        return _network("Asia/Tokyo", "JP")

    async def fake_recreate_container(session_id, **kwargs):
        assert session_id == "session-1"
        return {"selenium_port": 4444, "vnc_port": 7900}

    monkeypatch.setattr(sessions, "generate_profile", fake_generate_profile)
    monkeypatch.setattr(sessions, "_resolve_session_network", fake_network)
    monkeypatch.setattr(sessions, "recreate_container", fake_recreate_container)

    result = asyncio.run(
        sessions.regenerate_fingerprint(
            "session-1",
            sessions.FingerprintActionBody(),
            _user(),
        )
    )

    profile = result["fingerprintProfile"]
    assert profile["timezone"] == "Asia/Tokyo"
    assert profile["network"]["timezone"] == "Asia/Tokyo"
    assert profile["timezone"] != "UTC"
    assert profile["navigator"]["languages"] == ["es-ES", "es", "en"]


def test_create_container_passes_profile_dns_to_docker(monkeypatch):
    commands = []
    ports = iter([55100, 55101])

    async def fake_run(cmd, timeout=30):
        commands.append(cmd)
        return "container-id", "", 0

    monkeypatch.setattr(container, "_run", fake_run)
    monkeypatch.setattr(container, "_find_free_port", lambda: next(ports))
    monkeypatch.setattr(container, "ensure_docker_network", lambda: asyncio.sleep(0))

    asyncio.run(
        container.create_container(
            "session-1",
            fingerprint_profile={"network": {"dnsServers": ["223.5.5.5", "bad", "119.29.29.29"]}},
            image_name="browser-pilot:test",
        )
    )

    assert "--network browser-pilot-net" in commands[0]
    assert "--dns 223.5.5.5" in commands[0]
    assert "--dns 119.29.29.29" in commands[0]
    assert "-p 55100:4444" in commands[0]
    assert "-p 55101:7900" in commands[0]
    assert "-p 0:" not in commands[0]
    assert "bad" not in commands[0]


def test_session_params_attach_declared_network_without_container_probe(monkeypatch):
    profile = _profile("zh-CN")
    pool = FakePool([
        {
            "device_preset": "desktop",
            "proxy_url": None,
            "fingerprint_profile": profile,
            "browser_lang": "zh-CN",
            "tenant_id": "tenant-1",
            "chrome_version": "147.0.0.0",
        },
        {"image_tag": "browser-pilot:test"},
    ])
    calls = {}
    monkeypatch.setattr(container, "get_pool", lambda: pool)

    def fake_declared_network(proxy_url, image_tag):
        calls["proxy_url"] = proxy_url
        calls["image_tag"] = image_tag
        return _network("Asia/Shanghai", "CN")

    async def fail_browser_network(*args, **kwargs):
        raise AssertionError("default session params must not probe through browser")

    monkeypatch.setattr(container, "declared_network_profile", fake_declared_network)
    monkeypatch.setattr(container, "resolve_network_via_browser", fail_browser_network)

    width, height, ua, proxy, fp_profile, lang, image_name = asyncio.run(
        container._session_container_params("session-1")
    )

    assert width == 1920
    assert height == 1080
    assert proxy is None
    assert lang == "zh-CN"
    assert image_name == "browser-pilot:test"
    assert calls == {"proxy_url": None, "image_tag": "browser-pilot:test"}
    assert fp_profile["timezone"] == "Asia/Shanghai"
    assert pool.executed


def test_ensure_container_running_default_path_does_not_probe(monkeypatch):
    calls = []
    profile = _profile("zh-CN")
    profile["network"] = _network("Asia/Shanghai", "CN")
    profile["timezone"] = "Asia/Shanghai"

    async def fake_status(session_id):
        return "not_found"

    async def fake_params(session_id):
        return 1920, 1080, "UA", None, profile, "zh-CN", "browser-pilot:test"

    async def fake_create(session_id, **kwargs):
        calls.append(("create", kwargs))

    async def fake_ports(session_id):
        return {"selenium_port": 4444, "vnc_port": 7900}

    async def fake_wait(port):
        calls.append(("wait", port))

    async def fail_reconcile(*args, **kwargs):
        raise AssertionError("container/start must not reconcile network by default")

    async def fail_browser_network(*args, **kwargs):
        raise AssertionError("container/start must not run browser network probe")

    def fail_background(*args, **kwargs):
        raise AssertionError("container/start must not schedule background network consensus")

    monkeypatch.setattr(container, "get_container_status", fake_status)
    monkeypatch.setattr(container, "_session_container_params", fake_params)
    monkeypatch.setattr(container, "create_container", fake_create)
    monkeypatch.setattr(container, "get_container_ports", fake_ports)
    monkeypatch.setattr(container, "_wait_grid_ready", fake_wait)
    monkeypatch.setattr(container, "reconcile_browser_network_profile", fail_reconcile)
    monkeypatch.setattr(container, "resolve_network_via_browser", fail_browser_network)
    monkeypatch.setattr(container, "_schedule_background_network_consensus", fail_background)

    ports = asyncio.run(container.ensure_container_running("session-1"))

    assert ports == {"selenium_port": 4444, "vnc_port": 7900}
    assert calls[0][0] == "create"
    assert calls[1] == ("wait", 4444)


def test_recreate_container_default_path_does_not_probe(monkeypatch):
    calls = []

    async def fake_stop(session_id):
        calls.append("stop")

    async def fake_remove(session_id, keep_volume=False):
        calls.append(("remove", keep_volume))

    async def fake_create(session_id, **kwargs):
        calls.append(("create", kwargs))

    async def fake_ports(session_id):
        return {"selenium_port": 4444, "vnc_port": 7900}

    async def fake_wait(port):
        calls.append(("wait", port))

    async def fail_reconcile(*args, **kwargs):
        raise AssertionError("recreate_container must not reconcile network by default")

    monkeypatch.setattr(container, "stop_container", fake_stop)
    monkeypatch.setattr(container, "remove_container", fake_remove)
    monkeypatch.setattr(container, "create_container", fake_create)
    monkeypatch.setattr(container, "get_container_ports", fake_ports)
    monkeypatch.setattr(container, "_wait_grid_ready", fake_wait)
    monkeypatch.setattr(container, "reconcile_browser_network_profile", fail_reconcile)

    ports = asyncio.run(
        container.recreate_container(
            "session-1",
            width=1920,
            height=1080,
            fingerprint_profile={"network": _network("Asia/Shanghai", "CN")},
        )
    )

    assert ports == {"selenium_port": 4444, "vnc_port": 7900}
    assert calls == [
        "stop",
        ("remove", True),
        ("create", {
            "width": 1920,
            "height": 1080,
            "user_agent": None,
            "proxy": None,
            "fingerprint_profile": {"network": _network("Asia/Shanghai", "CN")},
            "browser_lang": "zh-CN",
            "image_name": None,
        }),
        ("wait", 4444),
    ]


def test_browser_network_reconcile_overrides_profile_and_recreates_for_dns(monkeypatch):
    profile = _profile("zh-CN")
    profile["network"] = _network("Asia/Singapore", "SG")
    profile["timezone"] = "Asia/Singapore"
    pool = FakePool([])
    captured = {}
    monkeypatch.setattr(container, "get_pool", lambda: pool)

    async def fake_browser_network(ports, **kwargs):
        assert ports == {"selenium_port": 4444, "vnc_port": 7900}
        assert kwargs["session_id"] == "session-1"
        network = _network("Asia/Shanghai", "CN")
        network.update({
            "ip": "115.198.151.18",
            "country": "China",
            "region": "Zhejiang",
            "city": "Hangzhou",
            "source": "browser:ip-api.com",
            "observedVia": "browser",
        })
        return network

    async def fake_recreate_container(session_id, **kwargs):
        assert session_id == "session-1"
        captured.update(kwargs)
        return {"selenium_port": 5555, "vnc_port": 7901}

    monkeypatch.setattr(container, "resolve_network_via_browser", fake_browser_network)
    monkeypatch.setattr(container, "recreate_container", fake_recreate_container)
    monkeypatch.setattr(container, "_schedule_background_network_consensus", lambda *args, **kwargs: None)

    ports, updated = asyncio.run(
        container.reconcile_browser_network_profile(
            "session-1",
            {"selenium_port": 4444, "vnc_port": 7900},
            width=1920,
            height=1080,
            user_agent="UA",
            proxy=None,
            fingerprint_profile=profile,
            browser_lang="zh-CN",
            image_name="browser-pilot:test",
        )
    )

    assert ports == {"selenium_port": 5555, "vnc_port": 7901}
    assert updated["timezone"] == "Asia/Shanghai"
    assert updated["network"]["ip"] == "115.198.151.18"
    assert updated["network"]["countryCode"] == "CN"
    assert updated["network"]["dnsServers"] == ["223.5.5.5", "119.29.29.29"]
    assert updated["network"]["observedVia"] == "browser"
    assert any("network_profile_reconciled" in w for w in updated["runtimeWarnings"])
    assert captured["fingerprint_profile"]["network"]["dnsServers"] == ["223.5.5.5", "119.29.29.29"]
    assert captured["reconcile_network"] is False
    assert pool.executed


def test_browser_network_reconcile_does_not_recreate_when_dns_unchanged(monkeypatch):
    profile = _profile("en-US")
    profile["network"] = _network("Europe/Berlin", "DE")
    profile["timezone"] = "Europe/Berlin"
    pool = FakePool([])
    monkeypatch.setattr(container, "get_pool", lambda: pool)

    async def fake_browser_network(ports, **kwargs):
        assert kwargs["session_id"] == "session-1"
        network = _network("Europe/Paris", "FR")
        network["observedVia"] = "browser"
        network["source"] = "browser:ip-api.com"
        return network

    async def fail_recreate_container(*args, **kwargs):
        raise AssertionError("DNS did not change, container should not be recreated")

    synced = {}

    async def fake_sync_profile(session_id, fingerprint_profile, restart_agent=True):
        synced["session_id"] = session_id
        synced["fingerprint_profile"] = fingerprint_profile
        synced["restart_agent"] = restart_agent

    monkeypatch.setattr(container, "resolve_network_via_browser", fake_browser_network)
    monkeypatch.setattr(container, "recreate_container", fail_recreate_container)
    monkeypatch.setattr(container, "sync_fingerprint_profile_to_container", fake_sync_profile)
    monkeypatch.setattr(container, "_schedule_background_network_consensus", lambda *args, **kwargs: None)

    ports, updated = asyncio.run(
        container.reconcile_browser_network_profile(
            "session-1",
            {"selenium_port": 4444, "vnc_port": 7900},
            width=1920,
            height=1080,
            user_agent=None,
            proxy=None,
            fingerprint_profile=profile,
            browser_lang="en-US",
            image_name="browser-pilot:test",
        )
    )

    assert ports == {"selenium_port": 4444, "vnc_port": 7900}
    assert updated["timezone"] == "Europe/Paris"
    assert updated["network"]["dnsServers"] == ["1.1.1.1", "8.8.8.8"]
    assert updated["navigator"]["languages"] == ["en-US", "en", "en"]
    assert any("network_profile_reconciled" in w for w in updated["runtimeWarnings"])
    assert synced["session_id"] == "session-1"
    assert synced["fingerprint_profile"]["network"]["observedVia"] == "browser"


def test_hidden_fast_probe_uses_neutral_results_without_webdriver_navigation(monkeypatch):
    calls = []

    async def fake_hidden_probe(session_id, *, mode, timeout):
        calls.append({"session_id": session_id, "mode": mode, "timeout": timeout})
        return [
            {
                "kind": "observed_geo",
                "ipSource": "api.ipify.org",
                "geoSource": "ipwho.is",
                "ok": True,
                "text": json.dumps({
                    "ip": "115.198.151.18",
                    "success": True,
                    "country": "China",
                    "country_code": "CN",
                    "region": "Zhejiang",
                    "city": "Hangzhou",
                    "latitude": "30.2943",
                    "longitude": "120.1663",
                    "timezone": {"id": "Asia/Shanghai"},
                    "connection": {"asn": 4134, "isp": "Chinanet"},
                }),
            },
            {
                "kind": "direct",
                "source": "api.ip.sb",
                "ok": True,
                "text": json.dumps({
                    "ip": "115.198.151.18",
                    "country": "China",
                    "country_code": "CN",
                    "region": "Zhejiang",
                    "city": "Hangzhou",
                    "latitude": "30.2943",
                    "longitude": "120.1663",
                    "timezone": "Asia/Shanghai",
                    "asn": "4134",
                    "isp": "Chinanet",
                }),
            },
        ]

    monkeypatch.setattr(container, "_run_hidden_browser_probe", fake_hidden_probe)

    network = asyncio.run(
        container._resolve_hidden_browser_network(
            "session-1",
            mode="fast",
            timeout=3,
            warnings=[],
        )
    )

    assert network is not None
    assert network["ip"] == "115.198.151.18"
    assert network["countryCode"] == "CN"
    assert network["timezone"] == "Asia/Shanghai"
    assert network["dnsServers"] == ["223.5.5.5", "119.29.29.29"]
    assert network["source"] == "browser:api.ipify.org+ipwho.is"
    assert network["observedVia"] == "browser-hidden-cdp"
    assert network["observedIpSource"] == "api.ipify.org"
    assert network["confidence"] == "high"
    assert network["probeMode"] == "fast"
    assert calls == [{"session_id": "session-1", "mode": "fast", "timeout": 3}]
    assert len(container._browser_probe_tasks("fast")["observed"]) == 1
    assert len(container._browser_probe_tasks("fast")["direct"]) == 1


def test_neutral_container_fallback_returns_cn_dns(monkeypatch):
    async def fake_run(cmd, timeout=30):
        assert "docker exec bp-session-1" in cmd
        payload = [
            {
                "source": "freeipapi.com",
                "ok": True,
                "text": json.dumps({
                    "ipAddress": "115.198.151.18",
                    "countryName": "China",
                    "countryCode": "CN",
                    "regionName": "Zhejiang",
                    "cityName": "Hangzhou",
                    "latitude": "30.2943",
                    "longitude": "120.1663",
                    "timeZones": ["Asia/Shanghai"],
                    "asn": "4134",
                    "asnOrganization": "Chinanet",
                }),
            },
            {
                "source": "ip.guide",
                "ok": True,
                "text": json.dumps({
                    "ip": "115.198.151.18",
                    "network": {
                        "autonomous_system": {
                            "asn": 4134,
                            "organization": "Chinanet",
                            "country": "CN",
                        },
                    },
                    "location": {
                        "country": "China",
                        "city": "Hangzhou",
                        "timezone": "Asia/Shanghai",
                        "latitude": 30.2943,
                        "longitude": 120.1663,
                    },
                }),
            },
        ]
        return json.dumps(payload), "", 0

    monkeypatch.setattr(container, "_run", fake_run)

    network = asyncio.run(
        container._resolve_neutral_network_via_container("session-1", warnings=["browser probe failed"])
    )

    assert network is not None
    assert network["source"] == "container:freeipapi.com"
    assert network["observedVia"] == "container-fallback-neutral"
    assert network["countryCode"] == "CN"
    assert network["timezone"] == "Asia/Shanghai"
    assert network["dnsServers"] == ["223.5.5.5", "119.29.29.29"]
    assert network["confidence"] == "high"
    assert "browser probe failed" in network["warnings"]


def test_fast_probe_failure_marks_pending_without_blocking(monkeypatch):
    profile = _profile("zh-CN")
    profile["network"] = _network("Asia/Singapore", "SG")
    profile["timezone"] = "Asia/Singapore"
    pool = FakePool([])
    scheduled = {}
    monkeypatch.setattr(container, "get_pool", lambda: pool)

    async def fake_browser_network(ports, **kwargs):
        assert kwargs["mode"] == "fast"
        return {"source": "unresolved", "warnings": ["timeout"]}

    def fake_schedule(session_id, ports, *, cache_key):
        scheduled["session_id"] = session_id
        scheduled["ports"] = ports
        scheduled["cache_key"] = cache_key

    monkeypatch.setattr(container, "resolve_network_via_browser", fake_browser_network)
    monkeypatch.setattr(container, "_schedule_background_network_consensus", fake_schedule)

    ports, updated = asyncio.run(
        container.reconcile_browser_network_profile(
            "session-1",
            {"selenium_port": 4444, "vnc_port": 7900},
            width=1920,
            height=1080,
            user_agent=None,
            proxy=None,
            fingerprint_profile=profile,
            browser_lang="zh-CN",
            image_name="browser-pilot:test",
        )
    )

    assert ports == {"selenium_port": 4444, "vnc_port": 7900}
    assert updated["network"]["timezone"] == "Asia/Singapore"
    assert any("network_probe_pending" in w for w in updated["runtimeWarnings"])
    assert scheduled["session_id"] == "session-1"
    assert pool.executed


def test_network_profile_cache_skips_fast_probe(monkeypatch):
    profile = _profile("zh-CN")
    profile["network"] = _network("Asia/Singapore", "SG")
    profile["timezone"] = "Asia/Singapore"
    pool = FakePool([])
    monkeypatch.setattr(container, "get_pool", lambda: pool)
    monkeypatch.setattr(container, "_schedule_background_network_consensus", lambda *args, **kwargs: None)
    synced = {}

    async def fail_browser_network(*args, **kwargs):
        raise AssertionError("cache hit should avoid the blocking fast probe")

    async def fake_sync_profile(session_id, fingerprint_profile, restart_agent=True):
        synced["profile"] = fingerprint_profile

    cached_network = _network("Europe/Paris", "FR")
    cached_network["observedVia"] = "browser-hidden-cdp"
    cache_key = container._network_cache_key(
        image_name="browser-pilot:test",
        proxy=None,
        fingerprint_profile=profile,
    )
    container._NETWORK_PROFILE_CACHE.clear()
    container._store_network_profile_cache(cache_key, cached_network)

    monkeypatch.setattr(container, "resolve_network_via_browser", fail_browser_network)
    monkeypatch.setattr(container, "sync_fingerprint_profile_to_container", fake_sync_profile)

    ports, updated = asyncio.run(
        container.reconcile_browser_network_profile(
            "session-1",
            {"selenium_port": 4444, "vnc_port": 7900},
            width=1920,
            height=1080,
            user_agent=None,
            proxy=None,
            fingerprint_profile=profile,
            browser_lang="zh-CN",
            image_name="browser-pilot:test",
        )
    )

    assert ports == {"selenium_port": 4444, "vnc_port": 7900}
    assert updated["timezone"] == "Europe/Paris"
    assert updated["network"]["cacheHit"] is True
    assert updated["network"]["probeStatus"] == "cached"
    assert synced["profile"]["network"]["countryCode"] == "FR"


def test_background_dns_change_sets_restart_warning_without_recreate(monkeypatch):
    profile = _profile("zh-CN")
    profile["network"] = _network("Asia/Singapore", "SG")
    profile["timezone"] = "Asia/Singapore"
    pool = FakePool([{"fingerprint_profile": profile}])
    synced = {}
    monkeypatch.setattr(container, "get_pool", lambda: pool)

    async def fake_browser_network(ports, **kwargs):
        assert kwargs["mode"] == "deep"
        network = _network("Asia/Shanghai", "CN")
        network["observedVia"] = "browser-hidden-cdp"
        network["source"] = "browser:api.ipify.org+ipwho.is"
        return network

    async def fake_sync_profile(session_id, fingerprint_profile, restart_agent=True):
        synced["session_id"] = session_id
        synced["profile"] = fingerprint_profile

    monkeypatch.setattr(container, "resolve_network_via_browser", fake_browser_network)
    monkeypatch.setattr(container, "sync_fingerprint_profile_to_container", fake_sync_profile)

    cache_key = container._network_cache_key(
        image_name="browser-pilot:test",
        proxy=None,
        fingerprint_profile=profile,
    )
    asyncio.run(
        container._background_network_consensus(
            "session-1",
            {"selenium_port": 4444, "vnc_port": 7900},
            cache_key=cache_key,
        )
    )

    saved_profile = pool.executed[-1][1]
    assert saved_profile["network"]["countryCode"] == "CN"
    assert saved_profile["network"]["dnsServers"] == ["223.5.5.5", "119.29.29.29"]
    assert any("network_probe_background_reconciled" in w for w in saved_profile["runtimeWarnings"])
    assert any("dns_recreate_required" in w for w in saved_profile["runtimeWarnings"])
    assert synced["session_id"] == "session-1"
