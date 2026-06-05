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


def _env_first(keys: tuple[str, ...], default: str) -> str:
    for key in keys:
        value = _env(key, "")
        if value:
            return value
    return default


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
BROWSER_RUNTIME_BACKEND_URL = _env("BROWSER_RUNTIME_BACKEND_URL", "http://host.docker.internal:8000")
BROWSER_RUNTIME_CONTROL_URL = _env("BROWSER_RUNTIME_CONTROL_URL", "")
BROWSER_RUNTIME_CONTROL_TOKEN = _env("BROWSER_RUNTIME_CONTROL_TOKEN", "")
BROWSER_RUNTIME_COMMAND_MAX_TIMEOUT = int(_env("BROWSER_RUNTIME_COMMAND_MAX_TIMEOUT", "3600"))
CLOAK_BROWSER_IMAGE_NAME = _env("CLOAK_BROWSER_IMAGE_NAME", "browser-pilot-cloak:latest")
BROWSER_RUNTIME_PROVIDER = _env("BROWSER_RUNTIME_PROVIDER", "docker").strip().lower()
BROWSER_RUNTIME_ACCESS_MODE = _env("BROWSER_RUNTIME_ACCESS_MODE", "private").strip().lower()
BROWSER_VNC_PASSWORD_SECRET = _env("BROWSER_VNC_PASSWORD_SECRET", "")
VIEWER_TICKET_TTL_SECONDS = int(_env("VIEWER_TICKET_TTL_SECONDS", "60"))
BROWSER_HOME_URL = _env("BROWSER_HOME_URL", "https://www.google.com/")
BP_LEGACY_DOCKER_DOWNLOAD_WATCHER = _env("BP_LEGACY_DOCKER_DOWNLOAD_WATCHER", "").lower() in {"1", "true", "yes", "on"}

APP_TITLE = _env("APP_TITLE", "Browser Pilot")
APP_ENV = _env("APP_ENV", "development").strip().lower()
APP_PUBLIC_ORIGINS = [
    part.strip().rstrip("/")
    for part in _env("APP_PUBLIC_ORIGINS", "").split(",")
    if part.strip()
]
CLI_COMMAND_NAME = _env("CLI_COMMAND_NAME", "bpilot")
CONTAINER_PREFIX = _env("CONTAINER_PREFIX", "bp")
BROWSER_GL_MODE = _env("BROWSER_GL_MODE", "auto")

# --- S3-compatible storage bootstrap ---

S3_STORAGE_BOOTSTRAP = _env_first(("S3_STORAGE_BOOTSTRAP", "MINIO_STORAGE_BOOTSTRAP"), "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
S3_ACCESS_KEY = _env_first(("S3_ACCESS_KEY", "MINIO_ROOT_USER"), "")
S3_SECRET_KEY = _env_first(("S3_SECRET_KEY", "MINIO_ROOT_PASSWORD"), "")
S3_BUCKET = _env_first(("S3_BUCKET", "MINIO_BUCKET"), "")
S3_ENDPOINT = _env_first(("S3_ENDPOINT", "MINIO_ENDPOINT"), "http://localhost:9000")
S3_PUBLIC_ENDPOINT = _env_first(("S3_PUBLIC_ENDPOINT", "MINIO_PUBLIC_ENDPOINT"), S3_ENDPOINT)
S3_REGION = _env_first(("S3_REGION", "MINIO_REGION"), "us-east-1")

# Legacy MinIO-named aliases are kept so existing deployments do not break.
MINIO_STORAGE_BOOTSTRAP = S3_STORAGE_BOOTSTRAP
MINIO_ROOT_USER = S3_ACCESS_KEY
MINIO_ROOT_PASSWORD = S3_SECRET_KEY
MINIO_BUCKET = S3_BUCKET
MINIO_ENDPOINT = S3_ENDPOINT
MINIO_PUBLIC_ENDPOINT = S3_PUBLIC_ENDPOINT
MINIO_REGION = S3_REGION

BUNDLED_S3_STORAGE_BOOTSTRAP = S3_STORAGE_BOOTSTRAP
BUNDLED_S3_ACCESS_KEY = S3_ACCESS_KEY
BUNDLED_S3_SECRET_KEY = S3_SECRET_KEY
BUNDLED_S3_BUCKET = S3_BUCKET
BUNDLED_S3_ENDPOINT = S3_ENDPOINT
BUNDLED_S3_PUBLIC_ENDPOINT = S3_PUBLIC_ENDPOINT
BUNDLED_S3_REGION = S3_REGION


def ensure_project_root_importable() -> None:
    root = str(PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


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

JWT_EXPIRE_MINUTES = int(_env("JWT_EXPIRE_MINUTES", "30"))
PLATFORM_JWT_EXPIRE_HOURS = int(_env("PLATFORM_JWT_EXPIRE_HOURS", "168"))
REMEMBER_ME_DAYS = int(_env("REMEMBER_ME_DAYS", "7"))


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


# --- Public deployment guardrails ---

FILE_DOWNLOAD_URL_TTL_SECONDS = int(_env("FILE_DOWNLOAD_URL_TTL_SECONDS", "300"))


def is_production() -> bool:
    return APP_ENV in {"prod", "production"}


def origin_allowed(origin: str | None) -> bool:
    if not APP_PUBLIC_ORIGINS:
        return not is_production()
    normalized = str(origin or "").strip().rstrip("/")
    return "*" in APP_PUBLIC_ORIGINS or normalized in APP_PUBLIC_ORIGINS


def validate_public_runtime_config() -> None:
    if BROWSER_RUNTIME_ACCESS_MODE not in {"private", "published"}:
        raise RuntimeError("BROWSER_RUNTIME_ACCESS_MODE must be private or published")
    if VIEWER_TICKET_TTL_SECONDS < 10 or VIEWER_TICKET_TTL_SECONDS > 300:
        raise RuntimeError("VIEWER_TICKET_TTL_SECONDS must be between 10 and 300")
    if FILE_DOWNLOAD_URL_TTL_SECONDS < 30 or FILE_DOWNLOAD_URL_TTL_SECONDS > 3600:
        raise RuntimeError("FILE_DOWNLOAD_URL_TTL_SECONDS must be between 30 and 3600")
    if not is_production():
        return
    if BROWSER_RUNTIME_ACCESS_MODE == "published":
        raise RuntimeError("BROWSER_RUNTIME_ACCESS_MODE=published is not allowed in production")
    if not APP_PUBLIC_ORIGINS:
        raise RuntimeError("APP_PUBLIC_ORIGINS must be set in production")
    if "*" in APP_PUBLIC_ORIGINS:
        raise RuntimeError("APP_PUBLIC_ORIGINS cannot be '*' in production")
    if not BROWSER_VNC_PASSWORD_SECRET:
        raise RuntimeError("BROWSER_VNC_PASSWORD_SECRET must be set in production")
    if BROWSER_RUNTIME_PROVIDER == "docker":
        if not BROWSER_RUNTIME_CONTROL_URL:
            raise RuntimeError("BROWSER_RUNTIME_CONTROL_URL must be set in production for docker runtime")
        if not BROWSER_RUNTIME_CONTROL_TOKEN:
            raise RuntimeError("BROWSER_RUNTIME_CONTROL_TOKEN must be set in production for docker runtime")

# --- Edition detection ---


def _detect_edition() -> str:
    """CE or EE, detected at startup."""
    env = _env("EDITION", "").lower()
    if env in ("ce", "ee"):
        if env == "ee":
            ensure_project_root_importable()
        return env
    ee_init = PROJECT_ROOT / "ee" / "backend" / "__init__.py"
    if not ee_init.is_file():
        return "ce"
    ensure_project_root_importable()
    try:
        importlib.import_module("ee.backend")
        return "ee"
    except ImportError:
        return "ce"


EDITION = _detect_edition()
