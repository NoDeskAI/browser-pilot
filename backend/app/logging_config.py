from __future__ import annotations

import logging
import os
import sys


class _ColorFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: "\033[90m",     # gray
        logging.INFO: "\033[32m",      # green
        logging.WARNING: "\033[33m",   # yellow
        logging.ERROR: "\033[31m",     # red
        logging.CRITICAL: "\033[1;31m",  # bold red
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelno, "")
        ts = self.formatTime(record, "%H:%M:%S")
        name = record.name.split(".")[-1] if "." in record.name else record.name
        msg = record.getMessage()
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        exc = f"\n{record.exc_text}" if record.exc_text else ""
        return f"{color}[{ts} {name}]{self.RESET} {msg}{exc}"


def setup_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_ColorFormatter())

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    for noisy in ("httpx", "httpcore", "openai", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
