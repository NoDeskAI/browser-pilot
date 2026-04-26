import asyncio
from types import SimpleNamespace

from app import container
from app.routes import sessions


def _network(timezone="Europe/Berlin", country_code="DE"):
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
        "dnsServers": ["1.1.1.1", "8.8.8.8"],
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

    async def fake_run(cmd, timeout=30):
        commands.append(cmd)
        return "container-id", "", 0

    monkeypatch.setattr(container, "_run", fake_run)

    asyncio.run(
        container.create_container(
            "session-1",
            fingerprint_profile={"network": {"dnsServers": ["223.5.5.5", "bad", "119.29.29.29"]}},
            image_name="browser-pilot:test",
        )
    )

    assert "--dns 223.5.5.5" in commands[0]
    assert "--dns 119.29.29.29" in commands[0]
    assert "bad" not in commands[0]
