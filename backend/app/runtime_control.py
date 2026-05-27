from __future__ import annotations

import asyncio
import logging
import shlex

import httpx

from app.config import (
    BROWSER_RUNTIME_COMMAND_MAX_TIMEOUT,
    BROWSER_RUNTIME_CONTROL_TOKEN,
    BROWSER_RUNTIME_CONTROL_URL,
)

logger = logging.getLogger("runtime_control")

_ALLOWED_DOCKER_SUBCOMMANDS = {
    "build",
    "exec",
    "image",
    "inspect",
    "network",
    "pause",
    "port",
    "ps",
    "pull",
    "rm",
    "rmi",
    "run",
    "start",
    "stop",
    "unpause",
    "volume",
}
_HOST_SHELL_CONTROL_CHARS = set(";&|<>")


class RuntimeCommandRejected(ValueError):
    pass


def runtime_control_enabled() -> bool:
    return bool(BROWSER_RUNTIME_CONTROL_URL.strip())


def _bounded_timeout(timeout: float) -> float:
    try:
        value = float(timeout)
    except (TypeError, ValueError):
        value = 30.0
    return max(1.0, min(value, float(BROWSER_RUNTIME_COMMAND_MAX_TIMEOUT)))


def _split_host_command(cmd: str) -> list[str]:
    lexer = shlex.shlex(cmd, posix=True, punctuation_chars=";&|<>")
    lexer.whitespace_split = True
    return list(lexer)


def _has_unquoted_command_substitution(cmd: str) -> bool:
    in_single = False
    in_double = False
    escaped = False
    for idx, ch in enumerate(cmd):
        if escaped:
            escaped = False
            continue
        if ch == "\\" and not in_single:
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            continue
        if in_single:
            continue
        if ch == "`" or (ch == "$" and idx + 1 < len(cmd) and cmd[idx + 1] == "("):
            return True
    return False


def validate_runtime_command(cmd: str) -> None:
    """Validate the narrow command surface accepted by the runtime worker."""
    stripped = str(cmd or "").strip()
    if not stripped:
        raise RuntimeCommandRejected("runtime command is empty")
    if "\n" in stripped or "\r" in stripped:
        raise RuntimeCommandRejected("host shell line breaks are not allowed")
    try:
        parts = _split_host_command(stripped)
    except ValueError as exc:
        raise RuntimeCommandRejected(f"runtime command is invalid: {exc}") from exc

    if not parts or parts[0] != "docker":
        raise RuntimeCommandRejected("runtime worker only accepts docker commands")
    if any(token and set(token) <= _HOST_SHELL_CONTROL_CHARS for token in parts[2:]):
        raise RuntimeCommandRejected("host shell control operators are not allowed")
    if _has_unquoted_command_substitution(stripped):
        raise RuntimeCommandRejected("host shell command substitution is not allowed")

    if len(parts) < 2 or parts[1] not in _ALLOWED_DOCKER_SUBCOMMANDS:
        subcommand = parts[1] if len(parts) > 1 else ""
        raise RuntimeCommandRejected(f"docker subcommand is not allowed: {subcommand}")


async def run_local_command(cmd: str, timeout: float = 30) -> tuple[str, str, int]:
    timeout = _bounded_timeout(timeout)
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return "", f"timeout after {timeout:g}s", -1
    return (
        stdout_b.decode("utf-8", errors="replace").strip(),
        stderr_b.decode("utf-8", errors="replace").strip(),
        proc.returncode or 0,
    )


async def run_runtime_command(cmd: str, timeout: float = 30) -> tuple[str, str, int]:
    if not runtime_control_enabled():
        return await run_local_command(cmd, timeout=timeout)

    if not BROWSER_RUNTIME_CONTROL_TOKEN:
        return "", "BROWSER_RUNTIME_CONTROL_TOKEN is required when BROWSER_RUNTIME_CONTROL_URL is set", -1

    timeout = _bounded_timeout(timeout)
    url = BROWSER_RUNTIME_CONTROL_URL.rstrip("/") + "/internal/runtime/command"
    try:
        async with httpx.AsyncClient(timeout=timeout + 5, trust_env=False) as client:
            resp = await client.post(
                url,
                json={"cmd": cmd, "timeout": timeout},
                headers={"Authorization": f"Bearer {BROWSER_RUNTIME_CONTROL_TOKEN}"},
            )
    except httpx.HTTPError as exc:
        logger.warning("Runtime worker request failed: %s", exc)
        return "", f"runtime worker unavailable: {exc}", -1

    if resp.status_code != 200:
        return "", f"runtime worker rejected command ({resp.status_code}): {resp.text[:300]}", -1

    data = resp.json()
    return (
        str(data.get("stdout") or ""),
        str(data.get("stderr") or ""),
        int(data.get("returncode") or 0),
    )
