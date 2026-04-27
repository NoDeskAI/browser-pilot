import asyncio
from pathlib import Path

import pytest

from app import fingerprint


class FakePool:
    async def fetchval(self, *args):
        return 1

    async def fetch(self, *args):
        return [
            {
                "group_name": "platform",
                "label": "Windows 10",
                "tags": ["windows"],
                "data": {
                    "navigator": {
                        "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                        "platform": "Win32",
                        "appVersion": "5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    },
                    "clientHints": {
                        "platform": "Windows",
                        "platformVersion": "10.0.0",
                        "architecture": "x86",
                        "bitness": "64",
                        "mobile": False,
                        "wow64": False,
                    },
                    "fonts": ["Arial", "DejaVu Sans", "Liberation Sans", "WenQuanYi Zen Hei"],
                },
            },
            {
                "group_name": "gpu",
                "label": "NVIDIA RTX 4070",
                "tags": ["windows"],
                "data": {
                    "vendor": "Google Inc. (NVIDIA)",
                    "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0, D3D11)",
                    "webglParams": {"maxTextureSize": 32768},
                },
            },
            {
                "group_name": "gpu",
                "label": "Intel UHD 770",
                "tags": ["windows"],
                "data": {
                    "vendor": "Google Inc. (Intel)",
                    "renderer": "ANGLE (Intel, Intel(R) UHD Graphics 770 Direct3D11 vs_5_0 ps_5_0, D3D11)",
                    "webglParams": {"maxTextureSize": 16384},
                },
            },
            {
                "group_name": "hardware",
                "label": "Win i7 8C / 16GB",
                "tags": ["windows"],
                "data": {
                    "hardwareConcurrency": 8,
                    "deviceMemory": 8,
                    "audio": {"sampleRate": 48000},
                    "connection": {"effectiveType": "4g", "rtt": 50, "downlink": 10, "saveData": False},
                },
            },
            {
                "group_name": "screen",
                "label": "24-bit / DPR 1",
                "tags": ["windows"],
                "data": {"colorDepth": 24, "pixelDepth": 24, "devicePixelRatio": 1},
            },
        ]


def test_windows_like_profile_hides_linux_fonts_and_keeps_language(monkeypatch):
    fingerprint.clear_seeded_cache("tenant-1")
    monkeypatch.setattr(fingerprint, "get_pool", lambda: FakePool())

    profile = asyncio.run(
        fingerprint.generate_profile(
            "tenant-1",
            browser_lang="zh-CN",
            chrome_version="147.0.7727.101",
        )
    )

    assert profile["profileFamily"] == fingerprint.PROFILE_FAMILY_WINDOWS_LIKE
    assert profile["navigator"]["platform"] == "Win32"
    assert "Windows NT 10.0" in profile["navigator"]["userAgent"]
    assert "Chrome/147.0.7727.101" in profile["navigator"]["userAgent"]
    assert profile["clientHints"]["platform"] == "Windows"
    assert profile["clientHints"]["fullVersion"] == "147.0.7727.101"
    assert profile["clientHints"]["fullVersionList"][0]["version"] == "147.0.7727.101"
    assert profile["navigator"]["languages"] == ["zh-CN", "zh", "en"]
    assert profile["fonts"] == profile["fontPolicy"]["exposedFonts"]
    assert profile["fontPolicy"]["mode"] == "windows-compatible-allowlist"
    assert "Microsoft YaHei" in profile["fonts"]
    assert "SimSun" in profile["fonts"]
    assert all(
        not font.startswith(("DejaVu", "Liberation", "Noto", "Nimbus", "WenQuanYi", "IPAGothic"))
        for font in profile["fonts"]
    )
    assert "Intel(R) UHD Graphics 770" in profile["webgl"]["renderer"]
    assert profile["warnings"]


def test_user_agent_metadata_follows_profile_client_hints():
    mac_profile = {
        "chromeVersion": "147.0.7727.101",
        "navigator": {
            "platform": "MacIntel",
            "languages": ["fr-FR", "fr", "en"],
        },
        "clientHints": {
            "platform": "macOS",
            "platformVersion": "14.5.0",
            "architecture": "arm",
            "bitness": "64",
            "mobile": False,
            "wow64": False,
        },
    }

    metadata = fingerprint.user_agent_metadata(mac_profile)

    assert metadata["platform"] == "macOS"
    assert metadata["platformVersion"] == "14.5.0"
    assert metadata["architecture"] == "arm"
    assert metadata["fullVersion"] == "147.0.7727.101"

    mac_profile["navigator"]["languages"] = ["ja-JP", "ja", "en"]
    metadata_after_language_change = fingerprint.user_agent_metadata(mac_profile)
    assert metadata_after_language_change["platform"] == "macOS"


def test_accept_language_uses_browser_lang_without_changing_platform():
    from app.tools.browser.session import _build_accept_language

    profile = {
        "chromeVersion": "147.0.7727.101",
        "navigator": {"platform": "Win32", "languages": ["de-DE", "de", "en"]},
        "clientHints": {
            "platform": "Windows",
            "platformVersion": "10.0.0",
            "architecture": "x86",
            "bitness": "64",
            "mobile": False,
            "wow64": False,
        },
    }

    assert _build_accept_language(profile["navigator"]["languages"]) == "de-DE,de;q=0.9,en;q=0.8"
    assert fingerprint.user_agent_metadata(profile)["platform"] == "Windows"


def test_generate_profile_requires_windows_platform(monkeypatch):
    class MacOnlyPool(FakePool):
        async def fetch(self, *args):
            rows = await super().fetch(*args)
            rows[0] = {
                "group_name": "platform",
                "label": "macOS",
                "tags": ["macos"],
                "data": rows[0]["data"],
            }
            return rows

    fingerprint.clear_seeded_cache("tenant-2")
    monkeypatch.setattr(fingerprint, "get_pool", lambda: MacOnlyPool())

    with pytest.raises(fingerprint.PoolEmptyError) as exc:
        asyncio.run(fingerprint.generate_profile("tenant-2"))
    assert exc.value.group == "platform"


def test_browser_fontconfig_and_stealth_use_windows_allowlist():
    root = Path(__file__).resolve().parents[2]
    fontconfig = (root / "services/selenium-chrome/browser-fontconfig.conf").read_text()
    legacy_fontconfig = (root / "services/selenium-chrome/fonts-local.conf").read_text()
    start = (root / "services/selenium-chrome/start-browser.sh").read_text()
    dockerfile = (root / "services/selenium-chrome/Dockerfile").read_text()
    stealth = (root / "services/selenium-chrome/stealth-ext/stealth.js").read_text()

    assert "FONTCONFIG_FILE=/opt/browser-fontconfig/fonts.conf" in start
    assert "/tmp/fingerprint-profile.json" in start
    assert "cdp-fingerprint-agent.py" in dockerfile
    assert "cdp-fingerprint-agent.conf" in dockerfile
    assert "<dir>/usr/share/fonts/truetype/croscore</dir>" in fontconfig
    assert "<dir>/usr/share/fonts/opentype/noto</dir>" in fontconfig
    assert "<family>Arial</family><prefer><family>Arimo</family>" in fontconfig
    assert "<string>Microsoft YaHei</string>" in fontconfig
    assert "<string>SimSun</string>" in fontconfig
    assert "/usr/share/fonts/truetype/dejavu/*" in fontconfig
    assert "/usr/share/fonts/truetype/liberation*/*" in fontconfig
    assert "/usr/share/fonts/truetype/noto/*" in fontconfig
    assert "DejaVu" not in legacy_fontconfig
    assert "Liberation" not in legacy_fontconfig
    assert "fontPolicy" in stealth
    assert "hiddenFamilies" in stealth
