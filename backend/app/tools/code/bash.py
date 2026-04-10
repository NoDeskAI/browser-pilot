from __future__ import annotations

import asyncio
import os

from pydantic import BaseModel, Field

from app.agent.types import ToolContext, build_tool
from app.config import BASH_DEFAULT_TIMEOUT_MS, BASH_MAX_TIMEOUT_MS, MAX_OUTPUT_CHARS, PROJECT_ROOT


class BashInput(BaseModel):
    command: str = Field(description="Shell command to execute")
    timeout: int = Field(default=BASH_DEFAULT_TIMEOUT_MS, description="Timeout in milliseconds, default 30000")
    cwd: str | None = Field(default=None, description="Working directory, defaults to project root")


async def _execute(args: dict, ctx: ToolContext) -> dict:
    command = args["command"]
    timeout_ms = min(args.get("timeout", BASH_DEFAULT_TIMEOUT_MS), BASH_MAX_TIMEOUT_MS)
    cwd = args.get("cwd") or str(PROJECT_ROOT)

    env = {**os.environ, "FORCE_COLOR": "0"}

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_ms / 1000
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"exitCode": -1, "stdout": "", "stderr": "", "killed": True, "signal": "SIGKILL"}

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        exit_code = proc.returncode or 0

        if len(stdout) > MAX_OUTPUT_CHARS:
            stdout = stdout[:MAX_OUTPUT_CHARS] + f"\n... (truncated, {len(stdout)} total chars)"
        if len(stderr) > MAX_OUTPUT_CHARS:
            stderr = stderr[:MAX_OUTPUT_CHARS] + f"\n... (truncated, {len(stderr)} total chars)"

        return {"exitCode": exit_code, "stdout": stdout, "stderr": stderr}

    except Exception as exc:
        return {"exitCode": -1, "stdout": "", "stderr": str(exc)}


bash_tool = build_tool(
    name="bash",
    description="Execute a shell command on the server. Use for system commands, installing dependencies, checking process status, etc. Output is truncated after 10000 characters.",
    input_schema=BashInput,
    execute=_execute,
)
