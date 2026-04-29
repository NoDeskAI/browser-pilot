from __future__ import annotations

import importlib
import logging
import os
import secrets
import sys
from pathlib import Path

PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", str(Path(__file__).resolve().parents[2])))
_ENV_FILE = PROJECT_ROOT / ".env"

_config_logger = logging.getLogger("config")


def _env(key: str, default: str) -> str:
    """Read from .env file first (hot-reload), fall back to os env then default."""
    if _ENV_FILE.is_file():
        for line in _ENV_FILE.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            k, _, v = stripped.partition("=")
            if k.strip() == key:
                return v.strip()
    return os.getenv(key, default)


DATABASE_URL = _env("DATABASE_URL", "")


def require_database_url() -> str:
    if DATABASE_URL:
        return DATABASE_URL
    raise RuntimeError(
        "DATABASE_URL is not set. Copy .env.example to .env and set "
        "DATABASE_URL, POSTGRES_USER, POSTGRES_PASSWORD, and POSTGRES_DB before starting Browser Pilot."
    )


DOCKER_HOST_ADDR = _env("DOCKER_HOST_ADDR", "localhost")
API_BASE_URL = _env("API_BASE_URL", "http://localhost:8000")

APP_TITLE = _env("APP_TITLE", "Browser Pilot")
CLI_COMMAND_NAME = _env("CLI_COMMAND_NAME", "bpilot")
CONTAINER_PREFIX = _env("CONTAINER_PREFIX", "bp")

# --- Declared network profile ---

DEFAULT_NETWORK_COUNTRY_CODE = _env("DEFAULT_NETWORK_COUNTRY_CODE", "")
DEFAULT_NETWORK_COUNTRY = _env("DEFAULT_NETWORK_COUNTRY", "")
DEFAULT_NETWORK_REGION = _env("DEFAULT_NETWORK_REGION", "")
DEFAULT_NETWORK_CITY = _env("DEFAULT_NETWORK_CITY", "")
DEFAULT_NETWORK_TIMEZONE = _env("DEFAULT_NETWORK_TIMEZONE", "")
DEFAULT_NETWORK_DNS_SERVERS = _env("DEFAULT_NETWORK_DNS_SERVERS", "")

# --- Network egress services ---

NETWORK_EGRESS_DOCKER_NETWORK = _env("NETWORK_EGRESS_DOCKER_NETWORK", "browser-pilot-net")
NETWORK_EGRESS_CONFIG_DIR = _env("NETWORK_EGRESS_CONFIG_DIR", str(PROJECT_ROOT / "data" / "network-egress"))
NETWORK_EGRESS_CLASH_IMAGE = _env("NETWORK_EGRESS_CLASH_IMAGE", "ghcr.io/metacubex/mihomo:latest")
NETWORK_EGRESS_CLASH_PROXY_PORT = int(_env("NETWORK_EGRESS_CLASH_PROXY_PORT", "7890"))
NETWORK_EGRESS_OPENVPN_IMAGE = _env("NETWORK_EGRESS_OPENVPN_IMAGE", "browser-pilot-openvpn-egress:latest")
NETWORK_EGRESS_OPENVPN_PROXY_PORT = int(_env("NETWORK_EGRESS_OPENVPN_PROXY_PORT", "8888"))

# --- Auth ---

JWT_EXPIRE_MINUTES = int(_env("JWT_EXPIRE_MINUTES", "1440"))


def _resolve_jwt_secret() -> str:
    val = _env("JWT_SECRET", "")
    if val:
        return val
    val = secrets.token_urlsafe(48)
    try:
        with open(_ENV_FILE, "a") as f:
            f.write(f"\nJWT_SECRET={val}\n")
        _config_logger.warning(
            "JWT_SECRET was not set — auto-generated and written to %s. "
            "Set it explicitly in production.",
            _ENV_FILE,
        )
    except OSError:
        _config_logger.warning(
            "JWT_SECRET was not set and .env is not writable — using ephemeral secret. "
            "Tokens will be invalidated on restart.",
        )
    return val


JWT_SECRET = _resolve_jwt_secret()

# --- Edition detection ---


def _detect_edition() -> str:
    """CE or EE, detected at startup."""
    env = _env("EDITION", "").lower()
    if env in ("ce", "ee"):
        return env
    ee_init = PROJECT_ROOT / "ee" / "backend" / "__init__.py"
    if not ee_init.is_file():
        return "ce"
    root = str(PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        importlib.import_module("ee.backend")
        return "ee"
    except ImportError:
        return "ce"


EDITION = _detect_edition()
