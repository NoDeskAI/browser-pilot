from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_CMD_NAME = Path(sys.argv[0]).stem if sys.argv and sys.argv[0] else "nwb"
if _CMD_NAME in ("__main__", "main", "python", "python3", "py"):
    _CMD_NAME = "nwb"

CONFIG_DIR = Path(os.getenv("NWB_CONFIG_DIR", str(Path.home() / f".{_CMD_NAME}")))
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULTS = {
    "api_url": "http://localhost:8000",
    "active_session": "",
}


def _ensure_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return {**DEFAULTS, **json.load(f)}
    return dict(DEFAULTS)


def save(cfg: dict) -> None:
    _ensure_dir()
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def get(key: str) -> str:
    return load().get(key, "")


def set_key(key: str, value: str) -> None:
    cfg = load()
    cfg[key] = value
    save(cfg)
