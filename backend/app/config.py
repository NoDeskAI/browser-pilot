from __future__ import annotations

import os
from pathlib import Path

SELENIUM_BASE = os.getenv("SELENIUM_BASE", "http://localhost:4444")

PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", str(Path(__file__).resolve().parents[2])))

MAX_STEPS = int(os.getenv("MAX_STEPS", "15"))

MAX_OUTPUT_CHARS = 10_000
BASH_DEFAULT_TIMEOUT_MS = 30_000
BASH_MAX_TIMEOUT_MS = 120_000
