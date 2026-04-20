"""Fingerprint pool & profile generator.

Dimensions are stored per-tenant in the ``fingerprint_pool`` DB table,
split into four groups (platform / gpu / hardware / screen).  At generation
time one entry per group is picked at random with cross-group compatibility
enforced via ``tags``.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from collections import defaultdict
from typing import Any

import httpx

from app.db import get_pool

logger = logging.getLogger("fingerprint")

_CHROME_VERSION = "136.0.7103.113"
_CHROME_MAJOR = _CHROME_VERSION.split(".")[0]

CHROME_VERSION = _CHROME_VERSION
CHROME_MAJOR = _CHROME_MAJOR

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

_WIN_UA = (
    f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    f"(KHTML, like Gecko) Chrome/{_CHROME_VERSION} Safari/537.36"
)
_WIN_AV = (
    f"5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    f"(KHTML, like Gecko) Chrome/{_CHROME_VERSION} Safari/537.36"
)
_MAC_UA = (
    f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    f"(KHTML, like Gecko) Chrome/{_CHROME_VERSION} Safari/537.36"
)
_MAC_AV = (
    f"5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    f"(KHTML, like Gecko) Chrome/{_CHROME_VERSION} Safari/537.36"
)

_DEFAULT_POOL: list[dict[str, Any]] = [
    # -- platform ----------------------------------------------------------
    {
        "group_name": "platform",
        "label": "Windows 10",
        "tags": ["windows"],
        "data": {
            "navigator": {"userAgent": _WIN_UA, "platform": "Win32", "appVersion": _WIN_AV},
            "clientHints": {
                "platform": "Windows", "platformVersion": "10.0.0",
                "architecture": "x86", "bitness": "64", "mobile": False, "wow64": False,
            },
        },
    },
    {
        "group_name": "platform",
        "label": "Windows 11",
        "tags": ["windows"],
        "data": {
            "navigator": {"userAgent": _WIN_UA, "platform": "Win32", "appVersion": _WIN_AV},
            "clientHints": {
                "platform": "Windows", "platformVersion": "15.0.0",
                "architecture": "x86", "bitness": "64", "mobile": False, "wow64": False,
            },
        },
    },
    {
        "group_name": "platform",
        "label": "macOS (Apple Silicon)",
        "tags": ["macos"],
        "data": {
            "navigator": {"userAgent": _MAC_UA, "platform": "MacIntel", "appVersion": _MAC_AV},
            "clientHints": {
                "platform": "macOS", "platformVersion": "14.5.0",
                "architecture": "arm", "bitness": "64", "mobile": False, "wow64": False,
            },
        },
    },
    {
        "group_name": "platform",
        "label": "macOS (Intel)",
        "tags": ["macos"],
        "data": {
            "navigator": {"userAgent": _MAC_UA, "platform": "MacIntel", "appVersion": _MAC_AV},
            "clientHints": {
                "platform": "macOS", "platformVersion": "13.6.0",
                "architecture": "x86", "bitness": "64", "mobile": False, "wow64": False,
            },
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
        },
    },
    {
        "group_name": "gpu",
        "label": "Intel UHD 770",
        "tags": ["windows"],
        "data": {
            "vendor": "Google Inc. (Intel)",
            "renderer": "ANGLE (Intel, Intel(R) UHD Graphics 770 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        },
    },
    {
        "group_name": "gpu",
        "label": "AMD RX 6700 XT",
        "tags": ["windows"],
        "data": {
            "vendor": "Google Inc. (AMD)",
            "renderer": "ANGLE (AMD, AMD Radeon RX 6700 XT Direct3D11 vs_5_0 ps_5_0, D3D11)",
        },
    },
    {
        "group_name": "gpu",
        "label": "NVIDIA RTX 4070",
        "tags": ["windows"],
        "data": {
            "vendor": "Google Inc. (NVIDIA)",
            "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        },
    },
    {
        "group_name": "gpu",
        "label": "Apple M1",
        "tags": ["macos"],
        "data": {
            "vendor": "Google Inc. (Apple)",
            "renderer": "ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)",
        },
    },
    {
        "group_name": "gpu",
        "label": "Apple M2 Pro",
        "tags": ["macos"],
        "data": {
            "vendor": "Google Inc. (Apple)",
            "renderer": "ANGLE (Apple, ANGLE Metal Renderer: Apple M2 Pro, Unspecified Version)",
        },
    },
    {
        "group_name": "gpu",
        "label": "Intel Iris Plus 645",
        "tags": ["macos"],
        "data": {
            "vendor": "Google Inc. (Intel)",
            "renderer": "ANGLE (Intel, Intel(R) Iris(TM) Plus Graphics 645, OpenGL 4.1)",
        },
    },
    # -- hardware ----------------------------------------------------------
    {"group_name": "hardware", "label": "4C / 8 GB", "tags": [], "data": {"hardwareConcurrency": 4, "deviceMemory": 8}},
    {"group_name": "hardware", "label": "8C / 8 GB", "tags": [], "data": {"hardwareConcurrency": 8, "deviceMemory": 8}},
    {"group_name": "hardware", "label": "8C / 16 GB", "tags": [], "data": {"hardwareConcurrency": 8, "deviceMemory": 16}},
    {"group_name": "hardware", "label": "12C / 16 GB", "tags": [], "data": {"hardwareConcurrency": 12, "deviceMemory": 16}},
    {"group_name": "hardware", "label": "16C / 32 GB", "tags": [], "data": {"hardwareConcurrency": 16, "deviceMemory": 32}},
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


async def generate_profile(tenant_id: str) -> dict[str, Any]:
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

    hardware_entry = secrets.choice(by_group["hardware"])

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

    return {
        "seed": secrets.randbelow(2**32),
        "navigator": {
            **p_data["navigator"],
            "hardwareConcurrency": h_data["hardwareConcurrency"],
            "deviceMemory": h_data["deviceMemory"],
            "languages": ["en-US", "en"],
            "language": "en-US",
            "maxTouchPoints": 0,
        },
        "screen": {
            "colorDepth": s_data["colorDepth"],
            "pixelDepth": s_data["pixelDepth"],
        },
        "devicePixelRatio": s_data["devicePixelRatio"],
        "webgl": {"vendor": g_data["vendor"], "renderer": g_data["renderer"]},
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
