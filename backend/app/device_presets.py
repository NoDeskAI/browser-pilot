from __future__ import annotations

DEVICE_PRESETS: dict[str, dict] = {
    # ── Desktop ──────────────────────────────────────────────────────────
    "desktop-1920x1080": {
        "width": 1920,
        "height": 1080,
        "category": "desktop",
        "label": "1920\u00d71080 Full HD",
        "default": True,
    },
    "desktop-1600x900": {
        "width": 1600,
        "height": 900,
        "category": "desktop",
        "label": "1600\u00d7900 HD+",
    },
    "desktop-1440x900": {
        "width": 1440,
        "height": 900,
        "category": "desktop",
        "label": "1440\u00d7900 WXGA+",
    },
    "desktop-1366x768": {
        "width": 1366,
        "height": 768,
        "category": "desktop",
        "label": "1366\u00d7768 HD",
    },
    "desktop-1280x800": {
        "width": 1280,
        "height": 800,
        "category": "desktop",
        "label": "1280\u00d7800 WXGA",
    },
    "desktop-1280x720": {
        "width": 1280,
        "height": 720,
        "category": "desktop",
        "label": "1280\u00d7720 720p",
    },
    # ── Mobile ───────────────────────────────────────────────────────────
    "iphone-16-pro-max": {
        "width": 440,
        "height": 956,
        "dpr": 3,
        "category": "mobile",
        "label": "iPhone 16 Pro Max",
        "user_agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/18.0 Mobile/15E148 Safari/604.1"
        ),
    },
    "iphone-16": {
        "width": 393,
        "height": 852,
        "dpr": 3,
        "category": "mobile",
        "label": "iPhone 16",
        "user_agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/18.0 Mobile/15E148 Safari/604.1"
        ),
    },
    "iphone-se": {
        "width": 375,
        "height": 667,
        "dpr": 2,
        "category": "mobile",
        "label": "iPhone SE",
        "user_agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/18.0 Mobile/15E148 Safari/604.1"
        ),
    },
    "ipad-air": {
        "width": 820,
        "height": 1180,
        "dpr": 2,
        "category": "mobile",
        "label": "iPad Air",
        "user_agent": (
            "Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/18.0 Mobile/15E148 Safari/604.1"
        ),
    },
    "galaxy-s24": {
        "width": 412,
        "height": 915,
        "dpr": 3,
        "category": "mobile",
        "label": "Samsung Galaxy S24",
        "user_agent": (
            "Mozilla/5.0 (Linux; Android 14; SM-S921B) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Mobile Safari/537.36"
        ),
    },
    "pixel-8": {
        "width": 412,
        "height": 915,
        "dpr": 2.625,
        "category": "mobile",
        "label": "Google Pixel 8",
        "user_agent": (
            "Mozilla/5.0 (Linux; Android 14; Pixel 8) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Mobile Safari/537.36"
        ),
    },
}

DEFAULT_PRESET = "desktop-1920x1080"


def get_preset(preset_id: str) -> dict:
    return DEVICE_PRESETS.get(preset_id, DEVICE_PRESETS[DEFAULT_PRESET])
