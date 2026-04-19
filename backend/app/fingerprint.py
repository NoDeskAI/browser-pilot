"""Fingerprint profile generator.

Each profile is a self-consistent hardware identity:
navigator, screen, WebGL, timezone, client-hints, and a noise seed.
"""

from __future__ import annotations

import secrets
from copy import deepcopy
from typing import Any

_CHROME_VERSION = "136.0.7103.113"
_CHROME_MAJOR = _CHROME_VERSION.split(".")[0]

_PRESETS: list[dict[str, Any]] = [
    {
        "label": "Windows 10 + NVIDIA RTX 3060",
        "navigator": {
            "userAgent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_CHROME_VERSION} Safari/537.36",
            "platform": "Win32",
            "appVersion": f"5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_CHROME_VERSION} Safari/537.36",
            "hardwareConcurrency": 12,
            "deviceMemory": 16,
            "languages": ["en-US", "en"],
            "language": "en-US",
            "maxTouchPoints": 0,
        },
        "screen": {"colorDepth": 24, "pixelDepth": 24},
        "devicePixelRatio": 1,
        "webgl": {
            "vendor": "Google Inc. (NVIDIA)",
            "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        },
        "timezone": "America/New_York",
        "clientHints": {
            "platform": "Windows",
            "platformVersion": "15.0.0",
            "architecture": "x86",
            "bitness": "64",
            "mobile": False,
            "wow64": False,
        },
    },
    {
        "label": "Windows 11 + Intel UHD 770",
        "navigator": {
            "userAgent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_CHROME_VERSION} Safari/537.36",
            "platform": "Win32",
            "appVersion": f"5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_CHROME_VERSION} Safari/537.36",
            "hardwareConcurrency": 8,
            "deviceMemory": 16,
            "languages": ["en-US", "en"],
            "language": "en-US",
            "maxTouchPoints": 0,
        },
        "screen": {"colorDepth": 24, "pixelDepth": 24},
        "devicePixelRatio": 1,
        "webgl": {
            "vendor": "Google Inc. (Intel)",
            "renderer": "ANGLE (Intel, Intel(R) UHD Graphics 770 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        },
        "timezone": "America/Chicago",
        "clientHints": {
            "platform": "Windows",
            "platformVersion": "15.0.0",
            "architecture": "x86",
            "bitness": "64",
            "mobile": False,
            "wow64": False,
        },
    },
    {
        "label": "Windows 10 + AMD RX 6700",
        "navigator": {
            "userAgent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_CHROME_VERSION} Safari/537.36",
            "platform": "Win32",
            "appVersion": f"5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_CHROME_VERSION} Safari/537.36",
            "hardwareConcurrency": 16,
            "deviceMemory": 32,
            "languages": ["en-US", "en"],
            "language": "en-US",
            "maxTouchPoints": 0,
        },
        "screen": {"colorDepth": 24, "pixelDepth": 24},
        "devicePixelRatio": 1,
        "webgl": {
            "vendor": "Google Inc. (AMD)",
            "renderer": "ANGLE (AMD, AMD Radeon RX 6700 XT Direct3D11 vs_5_0 ps_5_0, D3D11)",
        },
        "timezone": "America/Los_Angeles",
        "clientHints": {
            "platform": "Windows",
            "platformVersion": "10.0.0",
            "architecture": "x86",
            "bitness": "64",
            "mobile": False,
            "wow64": False,
        },
    },
    {
        "label": "macOS + Apple M1",
        "navigator": {
            "userAgent": f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_CHROME_VERSION} Safari/537.36",
            "platform": "MacIntel",
            "appVersion": f"5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_CHROME_VERSION} Safari/537.36",
            "hardwareConcurrency": 8,
            "deviceMemory": 8,
            "languages": ["en-US", "en"],
            "language": "en-US",
            "maxTouchPoints": 0,
        },
        "screen": {"colorDepth": 30, "pixelDepth": 30},
        "devicePixelRatio": 2,
        "webgl": {
            "vendor": "Google Inc. (Apple)",
            "renderer": "ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)",
        },
        "timezone": "America/New_York",
        "clientHints": {
            "platform": "macOS",
            "platformVersion": "14.5.0",
            "architecture": "arm",
            "bitness": "64",
            "mobile": False,
            "wow64": False,
        },
    },
    {
        "label": "macOS + Apple M2 Pro",
        "navigator": {
            "userAgent": f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_CHROME_VERSION} Safari/537.36",
            "platform": "MacIntel",
            "appVersion": f"5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_CHROME_VERSION} Safari/537.36",
            "hardwareConcurrency": 12,
            "deviceMemory": 16,
            "languages": ["en-US", "en"],
            "language": "en-US",
            "maxTouchPoints": 0,
        },
        "screen": {"colorDepth": 30, "pixelDepth": 30},
        "devicePixelRatio": 2,
        "webgl": {
            "vendor": "Google Inc. (Apple)",
            "renderer": "ANGLE (Apple, ANGLE Metal Renderer: Apple M2 Pro, Unspecified Version)",
        },
        "timezone": "America/Los_Angeles",
        "clientHints": {
            "platform": "macOS",
            "platformVersion": "15.1.0",
            "architecture": "arm",
            "bitness": "64",
            "mobile": False,
            "wow64": False,
        },
    },
    {
        "label": "macOS + Intel Iris Plus",
        "navigator": {
            "userAgent": f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_CHROME_VERSION} Safari/537.36",
            "platform": "MacIntel",
            "appVersion": f"5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_CHROME_VERSION} Safari/537.36",
            "hardwareConcurrency": 4,
            "deviceMemory": 8,
            "languages": ["en-US", "en"],
            "language": "en-US",
            "maxTouchPoints": 0,
        },
        "screen": {"colorDepth": 24, "pixelDepth": 24},
        "devicePixelRatio": 2,
        "webgl": {
            "vendor": "Google Inc. (Intel)",
            "renderer": "ANGLE (Intel, Intel(R) Iris(TM) Plus Graphics 645, OpenGL 4.1)",
        },
        "timezone": "Europe/London",
        "clientHints": {
            "platform": "macOS",
            "platformVersion": "13.6.0",
            "architecture": "x86",
            "bitness": "64",
            "mobile": False,
            "wow64": False,
        },
    },
    {
        "label": "Linux + Intel HD 630",
        "navigator": {
            "userAgent": f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_CHROME_VERSION} Safari/537.36",
            "platform": "Linux x86_64",
            "appVersion": f"5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_CHROME_VERSION} Safari/537.36",
            "hardwareConcurrency": 4,
            "deviceMemory": 8,
            "languages": ["en-US", "en"],
            "language": "en-US",
            "maxTouchPoints": 0,
        },
        "screen": {"colorDepth": 24, "pixelDepth": 24},
        "devicePixelRatio": 1,
        "webgl": {
            "vendor": "Google Inc. (Intel)",
            "renderer": "ANGLE (Intel, Mesa Intel(R) HD Graphics 630 (KBL GT2), OpenGL 4.6)",
        },
        "timezone": "Europe/Berlin",
        "clientHints": {
            "platform": "Linux",
            "platformVersion": "6.5.0",
            "architecture": "x86",
            "bitness": "64",
            "mobile": False,
            "wow64": False,
        },
    },
    {
        "label": "Linux + NVIDIA GTX 1650",
        "navigator": {
            "userAgent": f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_CHROME_VERSION} Safari/537.36",
            "platform": "Linux x86_64",
            "appVersion": f"5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_CHROME_VERSION} Safari/537.36",
            "hardwareConcurrency": 8,
            "deviceMemory": 16,
            "languages": ["en-US", "en"],
            "language": "en-US",
            "maxTouchPoints": 0,
        },
        "screen": {"colorDepth": 24, "pixelDepth": 24},
        "devicePixelRatio": 1,
        "webgl": {
            "vendor": "Google Inc. (NVIDIA)",
            "renderer": "ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 (/etc/vulkan/icd.d), OpenGL 4.6.0)",
        },
        "timezone": "Asia/Tokyo",
        "clientHints": {
            "platform": "Linux",
            "platformVersion": "6.8.0",
            "architecture": "x86",
            "bitness": "64",
            "mobile": False,
            "wow64": False,
        },
    },
    {
        "label": "Windows 11 + NVIDIA RTX 4070",
        "navigator": {
            "userAgent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_CHROME_VERSION} Safari/537.36",
            "platform": "Win32",
            "appVersion": f"5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_CHROME_VERSION} Safari/537.36",
            "hardwareConcurrency": 16,
            "deviceMemory": 32,
            "languages": ["en-US", "en"],
            "language": "en-US",
            "maxTouchPoints": 0,
        },
        "screen": {"colorDepth": 24, "pixelDepth": 24},
        "devicePixelRatio": 1,
        "webgl": {
            "vendor": "Google Inc. (NVIDIA)",
            "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        },
        "timezone": "Asia/Shanghai",
        "clientHints": {
            "platform": "Windows",
            "platformVersion": "15.0.0",
            "architecture": "x86",
            "bitness": "64",
            "mobile": False,
            "wow64": False,
        },
    },
    {
        "label": "Linux + Mesa llvmpipe (VM)",
        "navigator": {
            "userAgent": f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_CHROME_VERSION} Safari/537.36",
            "platform": "Linux x86_64",
            "appVersion": f"5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_CHROME_VERSION} Safari/537.36",
            "hardwareConcurrency": 2,
            "deviceMemory": 4,
            "languages": ["en-US", "en"],
            "language": "en-US",
            "maxTouchPoints": 0,
        },
        "screen": {"colorDepth": 24, "pixelDepth": 24},
        "devicePixelRatio": 1,
        "webgl": {
            "vendor": "Mesa",
            "renderer": "llvmpipe (LLVM 15.0.7, 256 bits)",
        },
        "timezone": "UTC",
        "clientHints": {
            "platform": "Linux",
            "platformVersion": "5.15.0",
            "architecture": "x86",
            "bitness": "64",
            "mobile": False,
            "wow64": False,
        },
    },
]

CHROME_VERSION = _CHROME_VERSION
CHROME_MAJOR = _CHROME_MAJOR


def generate_profile() -> dict[str, Any]:
    """Pick a random preset, assign a random noise seed, return a complete profile."""
    preset = deepcopy(secrets.choice(_PRESETS))
    profile: dict[str, Any] = {
        "seed": secrets.randbelow(2**32),
        "navigator": preset["navigator"],
        "screen": preset["screen"],
        "devicePixelRatio": preset["devicePixelRatio"],
        "webgl": preset["webgl"],
        "timezone": preset["timezone"],
        "clientHints": preset["clientHints"],
    }
    return profile
