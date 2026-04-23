"""Fingerprint pool & profile generator.

Dimensions are stored per-tenant in the ``fingerprint_pool`` DB table,
split into four groups (platform / gpu / hardware / screen).  At generation
time one entry per group is picked at random with cross-group compatibility
enforced via ``tags``.
"""

from __future__ import annotations

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

_WIN_FONTS = [
    "Arial", "Times New Roman", "Courier New", "Georgia", "Verdana",
    "Tahoma", "Trebuchet MS", "Impact", "DejaVu Sans", "DejaVu Serif",
    "DejaVu Sans Mono", "Liberation Sans", "Liberation Serif",
    "Liberation Mono", "WenQuanYi Zen Hei",
]
_MAC_FONTS = [
    "Arial", "Times New Roman", "Courier New", "Georgia", "Verdana",
    "DejaVu Sans", "DejaVu Serif", "DejaVu Sans Mono",
    "Liberation Sans", "Liberation Serif", "Liberation Mono",
    "WenQuanYi Zen Hei", "IPAGothic",
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
        "SELECT group_name, data, tags FROM fingerprint_pool "
        "WHERE tenant_id = $1 AND enabled = true",
        tenant_id,
    )

    by_group: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_group[r["group_name"]].append({"data": r["data"], "tags": set(r["tags"] or [])})

    for g in _REQUIRED_GROUPS:
        if not by_group.get(g):
            raise PoolEmptyError(g)

    platform_entry = secrets.choice(by_group["platform"])
    platform_tags = platform_entry["tags"]

    compatible_gpus = [e for e in by_group["gpu"] if e["tags"] & platform_tags]
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

    return {
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
        "fonts": p_data.get("fonts", []),
        "timezone": "UTC",
        "clientHints": p_data["clientHints"],
    }


# ---------------------------------------------------------------------------
# Timezone resolution (unchanged)
# ---------------------------------------------------------------------------

_TZ_API = "http://ip-api.com/json?fields=timezone"
_TZ_TIMEOUT = 5.0


async def resolve_timezone(proxy_url: str | None) -> str:
    """Resolve IANA timezone from egress IP by querying ip-api.com through the proxy."""
    try:
        kwargs: dict[str, Any] = {"timeout": _TZ_TIMEOUT}
        if proxy_url:
            kwargs["proxy"] = proxy_url
        async with httpx.AsyncClient(**kwargs) as client:
            resp = await client.get(_TZ_API)
            tz = resp.json().get("timezone")
            if tz:
                logger.info("Resolved timezone: %s (proxy=%s)", tz, proxy_url or "direct")
                return tz
    except Exception as exc:
        logger.warning("Failed to resolve timezone (proxy=%s): %s", proxy_url or "direct", exc)
    return "UTC"
