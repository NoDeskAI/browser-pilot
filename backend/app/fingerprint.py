"""Fingerprint pool & profile generator.

Dimensions are stored per-tenant in the ``fingerprint_pool`` DB table,
split into four groups (platform / gpu / hardware / screen).  At generation
time one entry per group is picked at random with cross-group compatibility
enforced via ``tags``.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import re
import secrets
import uuid
from collections import defaultdict
from typing import Any

import httpx

from app.db import get_pool

logger = logging.getLogger("fingerprint")

DEFAULT_CHROME_VERSION = "124.0.6367.78"
DEFAULT_CHROME_MAJOR = DEFAULT_CHROME_VERSION.split(".")[0]

CHROME_VERSION = DEFAULT_CHROME_VERSION
CHROME_MAJOR = DEFAULT_CHROME_MAJOR

# ---------------------------------------------------------------------------
# Domain exception
# ---------------------------------------------------------------------------

class PoolEmptyError(Exception):
    """Raised when a required dimension group has no enabled entries."""

    def __init__(self, group: str):
        self.group = group
        super().__init__(f"No enabled entries in group '{group}'")


# ---------------------------------------------------------------------------
# Default pool seed data (decomposed into 4 groups)
# ---------------------------------------------------------------------------

def _win_ua(ver: str) -> str:
    return f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver} Safari/537.36"

def _win_av(ver: str) -> str:
    return f"5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver} Safari/537.36"

def _mac_ua(ver: str) -> str:
    return f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver} Safari/537.36"

def _mac_av(ver: str) -> str:
    return f"5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver} Safari/537.36"

PROFILE_FAMILY_WINDOWS_LIKE = "windows-like-linux-container"

_WINDOWS_COMPATIBLE_FONTS = [
    "Arial",
    "Calibri",
    "Cambria",
    "Consolas",
    "Courier New",
    "Georgia",
    "Segoe UI",
    "Tahoma",
    "Times New Roman",
    "Trebuchet MS",
    "Verdana",
    "Microsoft YaHei",
    "Microsoft YaHei UI",
    "SimSun",
    "NSimSun",
    "SimHei",
]
_HIDDEN_LINUX_FONT_FAMILIES = [
    "DejaVu",
    "Liberation",
    "Noto",
    "Nimbus",
    "WenQuanYi",
    "IPAGothic",
    "IPAPGothic",
    "Ubuntu",
    "Cantarell",
    "Droid Sans",
]
_WINDOWS_LIKE_GPU_LABELS = {"NVIDIA RTX 3060", "Intel UHD 770", "AMD RX 6700 XT"}

_WIN_FONTS = list(_WINDOWS_COMPATIBLE_FONTS)
_MAC_FONTS = [
    "Arial", "Times New Roman", "Courier New", "Georgia", "Verdana",
    "Helvetica Neue", "Helvetica", "Menlo", "Monaco", "Avenir",
    "Avenir Next", "Futura", "Gill Sans", "Optima", "Palatino",
]

_DEFAULT_POOL: list[dict[str, Any]] = [
    # -- platform ----------------------------------------------------------
    {
        "group_name": "platform",
        "label": "Windows 10",
        "tags": ["windows"],
        "data": {
            "navigator": {"userAgent": _win_ua(DEFAULT_CHROME_VERSION), "platform": "Win32", "appVersion": _win_av(DEFAULT_CHROME_VERSION)},
            "clientHints": {
                "platform": "Windows", "platformVersion": "10.0.0",
                "architecture": "x86", "bitness": "64", "mobile": False, "wow64": False,
            },
            "fonts": _WIN_FONTS,
        },
    },
    {
        "group_name": "platform",
        "label": "Windows 11",
        "tags": ["windows"],
        "data": {
            "navigator": {"userAgent": _win_ua(DEFAULT_CHROME_VERSION), "platform": "Win32", "appVersion": _win_av(DEFAULT_CHROME_VERSION)},
            "clientHints": {
                "platform": "Windows", "platformVersion": "15.0.0",
                "architecture": "x86", "bitness": "64", "mobile": False, "wow64": False,
            },
            "fonts": _WIN_FONTS,
        },
    },
    {
        "group_name": "platform",
        "label": "macOS (Apple Silicon)",
        "tags": ["macos"],
        "data": {
            "navigator": {"userAgent": _mac_ua(DEFAULT_CHROME_VERSION), "platform": "MacIntel", "appVersion": _mac_av(DEFAULT_CHROME_VERSION)},
            "clientHints": {
                "platform": "macOS", "platformVersion": "14.5.0",
                "architecture": "arm", "bitness": "64", "mobile": False, "wow64": False,
            },
            "fonts": _MAC_FONTS,
        },
    },
    {
        "group_name": "platform",
        "label": "macOS (Intel)",
        "tags": ["macos"],
        "data": {
            "navigator": {"userAgent": _mac_ua(DEFAULT_CHROME_VERSION), "platform": "MacIntel", "appVersion": _mac_av(DEFAULT_CHROME_VERSION)},
            "clientHints": {
                "platform": "macOS", "platformVersion": "13.6.0",
                "architecture": "x86", "bitness": "64", "mobile": False, "wow64": False,
            },
            "fonts": _MAC_FONTS,
        },
    },
    # -- gpu ---------------------------------------------------------------
    {
        "group_name": "gpu",
        "label": "NVIDIA RTX 3060",
        "tags": ["windows"],
        "data": {
            "vendor": "Google Inc. (NVIDIA)",
            "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
            "webglParams": {
                "maxTextureSize": 16384, "maxRenderbufferSize": 16384,
                "maxViewportDims": [32768, 32768], "maxVertexAttribs": 16,
                "maxVaryingVectors": 30, "maxVertexUniformVectors": 4096,
                "maxFragmentUniformVectors": 1024, "maxTextureImageUnits": 16,
                "maxCombinedTextureImageUnits": 80, "maxVertexTextureImageUnits": 32,
                "aliasedLineWidthRange": [1, 1], "aliasedPointSizeRange": [1, 1024],
            },
        },
    },
    {
        "group_name": "gpu",
        "label": "Intel UHD 770",
        "tags": ["windows"],
        "data": {
            "vendor": "Google Inc. (Intel)",
            "renderer": "ANGLE (Intel, Intel(R) UHD Graphics 770 Direct3D11 vs_5_0 ps_5_0, D3D11)",
            "webglParams": {
                "maxTextureSize": 16384, "maxRenderbufferSize": 16384,
                "maxViewportDims": [16384, 16384], "maxVertexAttribs": 16,
                "maxVaryingVectors": 16, "maxVertexUniformVectors": 1024,
                "maxFragmentUniformVectors": 1024, "maxTextureImageUnits": 16,
                "maxCombinedTextureImageUnits": 48, "maxVertexTextureImageUnits": 16,
                "aliasedLineWidthRange": [1, 1], "aliasedPointSizeRange": [1, 1024],
            },
        },
    },
    {
        "group_name": "gpu",
        "label": "AMD RX 6700 XT",
        "tags": ["windows"],
        "data": {
            "vendor": "Google Inc. (AMD)",
            "renderer": "ANGLE (AMD, AMD Radeon RX 6700 XT Direct3D11 vs_5_0 ps_5_0, D3D11)",
            "webglParams": {
                "maxTextureSize": 16384, "maxRenderbufferSize": 16384,
                "maxViewportDims": [32768, 32768], "maxVertexAttribs": 16,
                "maxVaryingVectors": 32, "maxVertexUniformVectors": 4096,
                "maxFragmentUniformVectors": 1024, "maxTextureImageUnits": 16,
                "maxCombinedTextureImageUnits": 80, "maxVertexTextureImageUnits": 32,
                "aliasedLineWidthRange": [1, 1], "aliasedPointSizeRange": [1, 1024],
            },
        },
    },
    {
        "group_name": "gpu",
        "label": "NVIDIA RTX 4070",
        "tags": ["windows"],
        "data": {
            "vendor": "Google Inc. (NVIDIA)",
            "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0, D3D11)",
            "webglParams": {
                "maxTextureSize": 32768, "maxRenderbufferSize": 32768,
                "maxViewportDims": [32768, 32768], "maxVertexAttribs": 16,
                "maxVaryingVectors": 30, "maxVertexUniformVectors": 4096,
                "maxFragmentUniformVectors": 1024, "maxTextureImageUnits": 16,
                "maxCombinedTextureImageUnits": 80, "maxVertexTextureImageUnits": 32,
                "aliasedLineWidthRange": [1, 1], "aliasedPointSizeRange": [1, 1024],
            },
        },
    },
    {
        "group_name": "gpu",
        "label": "Apple M1",
        "tags": ["macos"],
        "data": {
            "vendor": "Google Inc. (Apple)",
            "renderer": "ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)",
            "webglParams": {
                "maxTextureSize": 16384, "maxRenderbufferSize": 16384,
                "maxViewportDims": [16384, 16384], "maxVertexAttribs": 16,
                "maxVaryingVectors": 15, "maxVertexUniformVectors": 256,
                "maxFragmentUniformVectors": 224, "maxTextureImageUnits": 16,
                "maxCombinedTextureImageUnits": 32, "maxVertexTextureImageUnits": 16,
                "aliasedLineWidthRange": [1, 1], "aliasedPointSizeRange": [1, 511],
            },
        },
    },
    {
        "group_name": "gpu",
        "label": "Apple M2 Pro",
        "tags": ["macos"],
        "data": {
            "vendor": "Google Inc. (Apple)",
            "renderer": "ANGLE (Apple, ANGLE Metal Renderer: Apple M2 Pro, Unspecified Version)",
            "webglParams": {
                "maxTextureSize": 16384, "maxRenderbufferSize": 16384,
                "maxViewportDims": [16384, 16384], "maxVertexAttribs": 16,
                "maxVaryingVectors": 15, "maxVertexUniformVectors": 256,
                "maxFragmentUniformVectors": 224, "maxTextureImageUnits": 16,
                "maxCombinedTextureImageUnits": 32, "maxVertexTextureImageUnits": 16,
                "aliasedLineWidthRange": [1, 1], "aliasedPointSizeRange": [1, 511],
            },
        },
    },
    {
        "group_name": "gpu",
        "label": "Intel Iris Plus 645",
        "tags": ["macos"],
        "data": {
            "vendor": "Google Inc. (Intel)",
            "renderer": "ANGLE (Intel, Intel(R) Iris(TM) Plus Graphics 645, OpenGL 4.1)",
            "webglParams": {
                "maxTextureSize": 16384, "maxRenderbufferSize": 16384,
                "maxViewportDims": [16384, 16384], "maxVertexAttribs": 16,
                "maxVaryingVectors": 16, "maxVertexUniformVectors": 1024,
                "maxFragmentUniformVectors": 1024, "maxTextureImageUnits": 16,
                "maxCombinedTextureImageUnits": 48, "maxVertexTextureImageUnits": 16,
                "aliasedLineWidthRange": [1, 1], "aliasedPointSizeRange": [1, 255],
            },
        },
    },
    # -- hardware ----------------------------------------------------------
    # deviceMemory: Chrome caps at 8 (valid: 0.25, 0.5, 1, 2, 4, 8)
    {"group_name": "hardware", "label": "Mac M1 8C / 8GB", "tags": ["macos"], "data": {
        "hardwareConcurrency": 8, "deviceMemory": 8,
        "audio": {"sampleRate": 48000, "maxChannelCount": 2, "channelCount": 2, "baseLatency": 0.005333, "outputLatency": 0.021333},
        "connection": {"effectiveType": "4g", "rtt": 50, "downlink": 10, "saveData": False},
    }},
    {"group_name": "hardware", "label": "Mac M2P 12C / 16GB", "tags": ["macos"], "data": {
        "hardwareConcurrency": 12, "deviceMemory": 8,
        "audio": {"sampleRate": 48000, "maxChannelCount": 2, "channelCount": 2, "baseLatency": 0.005333, "outputLatency": 0.021333},
        "connection": {"effectiveType": "4g", "rtt": 50, "downlink": 10, "saveData": False},
    }},
    {"group_name": "hardware", "label": "Win i5 4C / 8GB", "tags": ["windows"], "data": {
        "hardwareConcurrency": 4, "deviceMemory": 8,
        "audio": {"sampleRate": 48000, "maxChannelCount": 6, "channelCount": 2, "baseLatency": 0.01, "outputLatency": 0.04},
        "connection": {"effectiveType": "4g", "rtt": 50, "downlink": 10, "saveData": False},
    }},
    {"group_name": "hardware", "label": "Win i7 8C / 16GB", "tags": ["windows"], "data": {
        "hardwareConcurrency": 8, "deviceMemory": 8,
        "audio": {"sampleRate": 48000, "maxChannelCount": 6, "channelCount": 2, "baseLatency": 0.01, "outputLatency": 0.04},
        "connection": {"effectiveType": "4g", "rtt": 50, "downlink": 10, "saveData": False},
    }},
    {"group_name": "hardware", "label": "Win i9 16C / 32GB", "tags": ["windows"], "data": {
        "hardwareConcurrency": 16, "deviceMemory": 8,
        "audio": {"sampleRate": 48000, "maxChannelCount": 6, "channelCount": 2, "baseLatency": 0.01, "outputLatency": 0.04},
        "connection": {"effectiveType": "4g", "rtt": 50, "downlink": 10, "saveData": False},
    }},
    # -- screen ------------------------------------------------------------
    {"group_name": "screen", "label": "24-bit / DPR 1", "tags": ["windows"], "data": {"colorDepth": 24, "pixelDepth": 24, "devicePixelRatio": 1}},
    {"group_name": "screen", "label": "30-bit Retina / DPR 2", "tags": ["macos"], "data": {"colorDepth": 30, "pixelDepth": 30, "devicePixelRatio": 2}},
    {"group_name": "screen", "label": "24-bit Retina / DPR 2", "tags": ["macos"], "data": {"colorDepth": 24, "pixelDepth": 24, "devicePixelRatio": 2}},
]


def _font_policy() -> dict[str, Any]:
    return {
        "mode": "windows-compatible-allowlist",
        "exposedFonts": list(_WINDOWS_COMPATIBLE_FONTS),
        "hiddenFamilies": list(_HIDDEN_LINUX_FONT_FAMILIES),
    }


def _windows_like_warnings() -> list[str]:
    return [
        "Windows-like profile is running on a Linux container; TLS/JA3 and low-level OS signals may still reveal the runtime.",
    ]


def chrome_brands(chrome_version: str | None) -> list[dict[str, str]]:
    major = (chrome_version or DEFAULT_CHROME_VERSION).split(".")[0]
    return [
        {"brand": "Chromium", "version": major},
        {"brand": "Google Chrome", "version": major},
        {"brand": "Not=A?Brand", "version": "99"},
    ]


def chrome_full_version_list(chrome_version: str | None) -> list[dict[str, str]]:
    version = chrome_version or DEFAULT_CHROME_VERSION
    return [
        {"brand": "Chromium", "version": version},
        {"brand": "Google Chrome", "version": version},
        {"brand": "Not=A?Brand", "version": "99.0.0.0"},
    ]


def _platform_from_navigator(nav_platform: str | None) -> str:
    value = str(nav_platform or "")
    if value.startswith("Win"):
        return "Windows"
    if value.startswith("Mac"):
        return "macOS"
    if value.startswith("Linux"):
        return "Linux"
    return value


def complete_client_hints(profile: dict[str, Any]) -> dict[str, Any]:
    """Return full UA-CH metadata derived from one fingerprint profile.

    The platform fields intentionally come from ``fingerprintProfile.clientHints``
    (or, as a last resort, navigator.platform) so request headers follow the
    selected profile instead of a baked-in OS.
    """
    chrome_version = str(profile.get("chromeVersion") or DEFAULT_CHROME_VERSION)
    nav = profile.get("navigator") if isinstance(profile.get("navigator"), dict) else {}
    raw_hints = profile.get("clientHints") if isinstance(profile.get("clientHints"), dict) else {}
    hints = dict(raw_hints)

    hints.setdefault("platform", _platform_from_navigator(nav.get("platform")))
    hints.setdefault("platformVersion", "")
    hints.setdefault("architecture", "")
    hints.setdefault("bitness", "")
    hints.setdefault("model", "")
    hints["mobile"] = bool(hints.get("mobile", False))
    hints["wow64"] = bool(hints.get("wow64", False))

    if not isinstance(hints.get("brands"), list) or not hints["brands"]:
        hints["brands"] = chrome_brands(chrome_version)
    if not isinstance(hints.get("fullVersionList"), list) or not hints["fullVersionList"]:
        hints["fullVersionList"] = chrome_full_version_list(chrome_version)
    hints.setdefault("fullVersion", chrome_version)
    hints.setdefault("uaFullVersion", hints["fullVersion"])
    return hints


def user_agent_metadata(profile: dict[str, Any]) -> dict[str, Any]:
    hints = complete_client_hints(profile)
    return {
        "brands": hints["brands"],
        "fullVersionList": hints["fullVersionList"],
        "fullVersion": hints["fullVersion"],
        "platform": hints["platform"],
        "platformVersion": hints["platformVersion"],
        "architecture": hints["architecture"],
        "model": hints.get("model", ""),
        "mobile": hints["mobile"],
        "bitness": hints["bitness"],
        "wow64": hints["wow64"],
    }

# ---------------------------------------------------------------------------
# Pool seeding
# ---------------------------------------------------------------------------

_seeded_tenants: set[str] = set()


async def _ensure_pool_seeded(tenant_id: str) -> None:
    """Seed default pool entries for a tenant if none exist yet."""
    if tenant_id in _seeded_tenants:
        return
    pool = get_pool()
    count = await pool.fetchval(
        "SELECT COUNT(*) FROM fingerprint_pool WHERE tenant_id = $1",
        tenant_id,
    )
    if count == 0:
        for entry in _DEFAULT_POOL:
            await pool.execute(
                "INSERT INTO fingerprint_pool (id, tenant_id, group_name, label, data, tags) "
                "VALUES ($1, $2, $3, $4, $5::jsonb, $6) "
                "ON CONFLICT (tenant_id, group_name, label) DO NOTHING",
                str(uuid.uuid4()),
                tenant_id,
                entry["group_name"],
                entry["label"],
                entry["data"],
                entry["tags"],
            )
        logger.info("Seeded %d default pool entries for tenant %s", len(_DEFAULT_POOL), tenant_id)

    _seeded_tenants.add(tenant_id)


def clear_seeded_cache(tenant_id: str | None = None) -> None:
    """Clear the in-memory seeded cache.  Called by the reset API."""
    if tenant_id:
        _seeded_tenants.discard(tenant_id)
    else:
        _seeded_tenants.clear()


# ---------------------------------------------------------------------------
# Profile generation
# ---------------------------------------------------------------------------

_REQUIRED_GROUPS = ("platform", "gpu", "hardware", "screen")


async def generate_profile(
    tenant_id: str,
    *,
    browser_lang: str = "en-US",
    chrome_version: str | None = None,
) -> dict[str, Any]:
    """Build a random fingerprint profile from the tenant's pool entries."""
    await _ensure_pool_seeded(tenant_id)
    pool = get_pool()

    rows = await pool.fetch(
        "SELECT group_name, label, data, tags FROM fingerprint_pool "
        "WHERE tenant_id = $1 AND enabled = true",
        tenant_id,
    )

    by_group: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_group[r["group_name"]].append({
            "label": r["label"],
            "data": r["data"],
            "tags": set(r["tags"] or []),
        })

    for g in _REQUIRED_GROUPS:
        if not by_group.get(g):
            raise PoolEmptyError(g)

    windows_platforms = [e for e in by_group["platform"] if "windows" in e["tags"]]
    if not windows_platforms:
        raise PoolEmptyError("platform")
    platform_entry = secrets.choice(windows_platforms)
    platform_tags = platform_entry["tags"]

    compatible_gpus = [e for e in by_group["gpu"] if e["tags"] & platform_tags]
    preferred_gpus = [e for e in compatible_gpus if e["label"] in _WINDOWS_LIKE_GPU_LABELS]
    if preferred_gpus:
        compatible_gpus = preferred_gpus
    if not compatible_gpus:
        raise PoolEmptyError("gpu")
    gpu_entry = secrets.choice(compatible_gpus)

    compatible_hw = [
        e for e in by_group["hardware"]
        if not e["tags"] or (e["tags"] & platform_tags)
    ]
    if not compatible_hw:
        raise PoolEmptyError("hardware")
    hardware_entry = secrets.choice(compatible_hw)

    compatible_screens = [
        e for e in by_group["screen"]
        if not e["tags"] or (e["tags"] & platform_tags)
    ]
    if not compatible_screens:
        raise PoolEmptyError("screen")
    screen_entry = secrets.choice(compatible_screens)

    p_data = platform_entry["data"]
    g_data = gpu_entry["data"]
    h_data = hardware_entry["data"]
    s_data = screen_entry["data"]

    ver = chrome_version or DEFAULT_CHROME_VERSION

    nav = {**p_data["navigator"]}
    old_ua = nav.get("userAgent", "")
    nav["userAgent"] = re.sub(r"Chrome/[\d.]+", f"Chrome/{ver}", old_ua) if old_ua else _win_ua(ver)
    old_av = nav.get("appVersion", "")
    nav["appVersion"] = re.sub(r"Chrome/[\d.]+", f"Chrome/{ver}", old_av) if old_av else _win_av(ver)

    lang_primary = browser_lang.split(",")[0].strip() if browser_lang else "en-US"
    lang_base = lang_primary.split("-")[0]
    languages = [lang_primary]
    if lang_base != lang_primary:
        languages.append(lang_base)
    if lang_primary != "en" and lang_base != "en":
        languages.append("en")

    nav.update({
        "hardwareConcurrency": h_data["hardwareConcurrency"],
        "deviceMemory": h_data["deviceMemory"],
        "languages": languages,
        "language": lang_primary,
        "maxTouchPoints": 0,
    })

    profile = {
        "seed": secrets.randbelow(2**32),
        "chromeVersion": ver,
        "navigator": nav,
        "screen": {
            "colorDepth": s_data["colorDepth"],
            "pixelDepth": s_data["pixelDepth"],
        },
        "devicePixelRatio": s_data["devicePixelRatio"],
        "webgl": {
            "vendor": g_data["vendor"],
            "renderer": g_data["renderer"],
            "params": g_data.get("webglParams", {}),
        },
        "audio": h_data.get("audio", {}),
        "connection": h_data.get("connection", {}),
        "fonts": list(_WINDOWS_COMPATIBLE_FONTS),
        "profileFamily": PROFILE_FAMILY_WINDOWS_LIKE,
        "fontPolicy": _font_policy(),
        "warnings": _windows_like_warnings(),
        "timezone": "UTC",
        "clientHints": dict(p_data["clientHints"]),
    }
    profile["clientHints"] = complete_client_hints(profile)
    return profile


# ---------------------------------------------------------------------------
# Timezone resolution (unchanged)
# ---------------------------------------------------------------------------

_TZ_APIS = [
    ("ip-api.com", "http://ip-api.com/json?fields=timezone", lambda d: d.get("timezone")),
    ("ipinfo.io", "http://ipinfo.io/json", lambda d: d.get("timezone")),
]
_TZ_TIMEOUT = 5.0

_NETWORK_TIMEOUT = 8
_DNS_CN = ["223.5.5.5", "119.29.29.29"]
_DNS_GLOBAL = ["1.1.1.1", "8.8.8.8"]


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_country_code(value: Any) -> str:
    return str(value or "").strip().upper()[:2]


def _dns_servers_for_country(country_code: str) -> list[str]:
    return list(_DNS_CN if _clean_country_code(country_code) == "CN" else _DNS_GLOBAL)


def _normal_network(
    *,
    source: str,
    ip: Any = None,
    country_code: Any = None,
    country: Any = None,
    region: Any = None,
    city: Any = None,
    timezone: Any = None,
    asn: Any = None,
    isp: Any = None,
    lat: Any = None,
    lon: Any = None,
    postal: Any = None,
    warnings: list[str] | None = None,
) -> dict[str, Any] | None:
    tz = str(timezone or "").strip()
    ip_value = str(ip or "").strip()
    if not ip_value or "/" not in tz:
        return None

    code = _clean_country_code(country_code)
    asn_value = str(asn or "").strip()
    if asn_value and asn_value.isdigit():
        asn_value = f"AS{asn_value}"

    return {
        "ip": ip_value,
        "countryCode": code,
        "country": str(country or "").strip(),
        "region": str(region or "").strip(),
        "city": str(city or "").strip(),
        "timezone": tz,
        "asn": asn_value,
        "isp": str(isp or "").strip(),
        "lat": _to_float(lat),
        "lon": _to_float(lon),
        "postal": str(postal or "").strip(),
        "source": source,
        "probedAt": _utc_now(),
        "dnsServers": _dns_servers_for_country(code),
        "warnings": warnings or [],
    }


def normalize_network_probe(source: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize one IP geo provider response into the session network profile."""
    if source == "ipwho.is":
        if data.get("success") is False:
            return None
        connection = data.get("connection") if isinstance(data.get("connection"), dict) else {}
        timezone = data.get("timezone") if isinstance(data.get("timezone"), dict) else {}
        return _normal_network(
            source=source,
            ip=data.get("ip"),
            country_code=data.get("country_code"),
            country=data.get("country"),
            region=data.get("region"),
            city=data.get("city"),
            timezone=timezone.get("id") or data.get("timezone"),
            asn=connection.get("asn"),
            isp=connection.get("isp") or connection.get("org"),
            lat=data.get("latitude"),
            lon=data.get("longitude"),
            postal=data.get("postal"),
        )

    if source == "api.ip.sb":
        return _normal_network(
            source=source,
            ip=data.get("ip"),
            country_code=data.get("country_code"),
            country=data.get("country"),
            region=data.get("region"),
            city=data.get("city"),
            timezone=data.get("timezone"),
            asn=data.get("asn"),
            isp=data.get("isp") or data.get("organization") or data.get("asn_organization"),
            lat=data.get("latitude"),
            lon=data.get("longitude"),
            postal=data.get("postal_code") or data.get("postal"),
        )

    if source == "freeipapi.com":
        timezones = data.get("timeZones") if isinstance(data.get("timeZones"), list) else []
        return _normal_network(
            source=source,
            ip=data.get("ipAddress"),
            country_code=data.get("countryCode"),
            country=data.get("countryName"),
            region=data.get("regionName") or data.get("regionCode"),
            city=data.get("cityName"),
            timezone=timezones[0] if timezones else data.get("timeZone"),
            asn=data.get("asn"),
            isp=data.get("asnOrganization"),
            lat=data.get("latitude"),
            lon=data.get("longitude"),
            postal=data.get("zipCode"),
        )

    if source == "ip.guide":
        network = data.get("network") if isinstance(data.get("network"), dict) else {}
        autonomous_system = (
            network.get("autonomous_system")
            if isinstance(network.get("autonomous_system"), dict)
            else {}
        )
        location = data.get("location") if isinstance(data.get("location"), dict) else {}
        return _normal_network(
            source=source,
            ip=data.get("ip"),
            country_code=autonomous_system.get("country"),
            country=location.get("country"),
            region=location.get("region"),
            city=location.get("city"),
            timezone=location.get("timezone"),
            asn=autonomous_system.get("asn"),
            isp=autonomous_system.get("organization") or autonomous_system.get("name"),
            lat=location.get("latitude"),
            lon=location.get("longitude"),
            postal=location.get("postal"),
        )

    if source == "ip-api.com":
        if data.get("status") and data.get("status") != "success":
            return None
        return _normal_network(
            source=source,
            ip=data.get("query"),
            country_code=data.get("countryCode"),
            country=data.get("country"),
            region=data.get("regionName") or data.get("region"),
            city=data.get("city"),
            timezone=data.get("timezone"),
            asn=data.get("as"),
            isp=data.get("isp") or data.get("org"),
            lat=data.get("lat"),
            lon=data.get("lon"),
            postal=data.get("zip"),
        )

    if source == "ip234.in":
        return _normal_network(
            source=source,
            ip=data.get("ip"),
            country_code=data.get("country_code"),
            country=data.get("country"),
            region=data.get("region"),
            city=data.get("city"),
            timezone=data.get("timezone"),
            asn=data.get("asn"),
            isp=data.get("organization") or data.get("isp"),
            lat=data.get("latitude"),
            lon=data.get("longitude"),
            postal=data.get("postal"),
        )

    if source == "ipinfo.io":
        lat = lon = None
        if isinstance(data.get("loc"), str) and "," in data["loc"]:
            lat_s, lon_s = data["loc"].split(",", 1)
            lat, lon = lat_s, lon_s
        org = str(data.get("org") or "")
        asn = org.split(" ", 1)[0] if org.startswith("AS") else data.get("asn")
        return _normal_network(
            source=source,
            ip=data.get("ip"),
            country_code=data.get("country"),
            country=data.get("country"),
            region=data.get("region"),
            city=data.get("city"),
            timezone=data.get("timezone"),
            asn=asn,
            isp=org,
            lat=lat,
            lon=lon,
            postal=data.get("postal"),
        )

    return None


def failed_network_profile(reason: str, warnings: list[str] | None = None) -> dict[str, Any]:
    all_warnings = list(warnings or [])
    all_warnings.append(reason)
    return {
        "ip": "",
        "countryCode": "",
        "country": "",
        "region": "",
        "city": "",
        "timezone": "UTC",
        "asn": "",
        "isp": "",
        "lat": None,
        "lon": None,
        "postal": "",
        "source": "unresolved",
        "probedAt": _utc_now(),
        "dnsServers": list(_DNS_GLOBAL),
        "warnings": all_warnings,
    }


def attach_network_profile(profile: dict[str, Any], network: dict[str, Any]) -> dict[str, Any]:
    """Bind network-derived timezone/DNS metadata to an existing fingerprint profile."""
    normalized = dict(network or failed_network_profile("network probe did not return data"))
    normalized.setdefault("warnings", [])
    normalized["dnsServers"] = normalized.get("dnsServers") or _dns_servers_for_country(
        normalized.get("countryCode", "")
    )
    tz = normalized.get("timezone") if isinstance(normalized.get("timezone"), str) else None
    if not tz or "/" not in tz:
        tz = "UTC"
        normalized["timezone"] = tz
        normalized["warnings"].append("network timezone missing or invalid; using UTC")
    profile["network"] = normalized
    profile["timezone"] = tz
    return profile


def _system_timezone() -> str:
    """Return the host's local IANA timezone, e.g. 'Asia/Shanghai'."""
    import datetime
    try:
        local = datetime.datetime.now().astimezone().tzinfo
        if hasattr(local, "key"):
            return local.key
        name = str(local)
        if "/" in name:
            return name
    except Exception:
        pass
    return "UTC"


async def resolve_timezone(proxy_url: str | None) -> str:
    """Resolve IANA timezone from egress IP (host-side, used as fallback).

    Chain: ip-api.com -> ipinfo.io -> system local timezone (when no proxy).
    """
    kwargs: dict[str, Any] = {"timeout": _TZ_TIMEOUT}
    if proxy_url:
        kwargs["proxy"] = proxy_url

    for name, url, extract in _TZ_APIS:
        try:
            async with httpx.AsyncClient(**kwargs) as client:
                resp = await client.get(url)
                tz = extract(resp.json())
                if tz:
                    logger.info("Resolved timezone via %s: %s (proxy=%s)", name, tz, proxy_url or "direct")
                    return tz
        except Exception as exc:
            logger.warning("Timezone API %s failed (proxy=%s): %s", name, proxy_url or "direct", exc)

    if not proxy_url:
        tz = _system_timezone()
        logger.info("Using system timezone: %s", tz)
        return tz

    logger.warning("All timezone APIs failed, falling back to UTC")
    return "UTC"


async def resolve_timezone_via_container(proxy_url: str | None, image_tag: str) -> str:
    """Backward-compatible timezone wrapper around the container network profile."""
    return (await resolve_network_via_container(proxy_url, image_tag)).get("timezone", "UTC")


async def resolve_network_via_container(proxy_url: str | None, image_tag: str) -> dict[str, Any]:
    """Resolve the browser session network profile from the same container path.

    This intentionally does not fall back to the host network. If all probes fail,
    the caller still receives an explicit UTC profile with warnings.
    """
    import json as _json
    import shlex

    apis = [
        (
            "ip-api.com",
            "http://ip-api.com/json?fields=status,message,country,countryCode,regionName,city,zip,lat,lon,timezone,isp,org,as,query",
        ),
        ("ip234.in", "https://ip234.in/ip.json"),
        ("ipinfo.io", "https://ipinfo.io/json"),
    ]
    warnings: list[str] = []

    for api_name, url in apis:
        curl = f"curl -sS --max-time {_NETWORK_TIMEOUT}"
        if proxy_url:
            curl += f" --proxy {shlex.quote(proxy_url)}"
        curl += f" {shlex.quote(url)}"

        cmd = f"docker run --rm {shlex.quote(image_tag)} sh -c {shlex.quote(curl)}"
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=_NETWORK_TIMEOUT + 15
            )
            stdout = stdout_b.decode("utf-8", errors="replace").strip()
            stderr = stderr_b.decode("utf-8", errors="replace").strip()
            if proc.returncode != 0:
                warnings.append(f"{api_name} probe failed: {stderr[:160] or 'curl failed'}")
                continue
            try:
                data = _json.loads(stdout)
            except Exception:
                warnings.append(f"{api_name} returned non-JSON response: {stdout[:160]}")
                continue
            network = normalize_network_probe(api_name, data)
            if network:
                network["warnings"] = warnings
                logger.info(
                    "Resolved network via container %s (%s): ip=%s tz=%s dns=%s proxy=%s",
                    api_name,
                    image_tag,
                    network.get("ip"),
                    network.get("timezone"),
                    ",".join(network.get("dnsServers", [])),
                    proxy_url or "direct",
                )
                return network
            warnings.append(f"{api_name} response missing usable IP/timezone")
        except asyncio.TimeoutError:
            warnings.append(f"{api_name} probe timed out")
        except Exception as exc:
            warnings.append(f"{api_name} probe failed: {exc}")

    logger.warning("Container network probes failed for image=%s proxy=%s", image_tag, proxy_url or "direct")
    return failed_network_profile("all container network probes failed", warnings)
