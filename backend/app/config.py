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
DOCKER_HOST_ADDR = _env("DOCKER_HOST_ADDR", "localhost")
MAX_OUTPUT_CHARS = 10_000
BASH_DEFAULT_TIMEOUT_MS = 30_000
BASH_MAX_TIMEOUT_MS = 120_000
