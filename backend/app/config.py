from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", str(Path(__file__).resolve().parents[2])))
_ENV_FILE = PROJECT_ROOT / ".env"


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


def get_max_steps() -> int:
    return int(_env("MAX_STEPS", "100"))


DATABASE_URL = _env("DATABASE_URL", "postgresql://nodeskpane:nodeskpane@localhost:5432/nodeskpane")
CLI_INSTALL_COMMAND = _env("CLI_INSTALL_COMMAND", "pip install nwb-cli")
DOCKER_HOST_ADDR = _env("DOCKER_HOST_ADDR", "localhost")
API_BASE_URL = _env("API_BASE_URL", "http://localhost:8000")
MAX_OUTPUT_CHARS = 10_000
BASH_DEFAULT_TIMEOUT_MS = 30_000
BASH_MAX_TIMEOUT_MS = 120_000

APP_TITLE = _env("APP_TITLE", "Remote Browser Playground")
APP_AGENT_NAME = _env("APP_AGENT_NAME", "NoDeskPane Agent")
CLI_COMMAND_NAME = _env("CLI_COMMAND_NAME", "nwb")
CONTAINER_PREFIX = _env("CONTAINER_PREFIX", "ndb")
SELENIUM_IMAGE_NAME = _env("SELENIUM_IMAGE_NAME", "no-window-browser-selenium")
