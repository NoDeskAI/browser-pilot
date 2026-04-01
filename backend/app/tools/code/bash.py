from __future__ import annotations

import asyncio
import os

from pydantic import BaseModel, Field

from app.agent.types import ToolContext, build_tool
from app.config import BASH_DEFAULT_TIMEOUT_MS, BASH_MAX_TIMEOUT_MS, MAX_OUTPUT_CHARS, PROJECT_ROOT


class BashInput(BaseModel):
    command: str = Field(description="要执行的 shell 命令")
    timeout: int = Field(default=BASH_DEFAULT_TIMEOUT_MS, description="超时时间（毫秒），默认 30000")
    cwd: str | None = Field(default=None, description="工作目录，默认为项目根目录")


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
    description="在服务器上执行 shell 命令。用于运行系统命令、安装依赖、查看进程状态等。输出超过 10000 字符会被截断。",
    input_schema=BashInput,
    execute=_execute,
)
