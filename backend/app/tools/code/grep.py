from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from app.agent.types import ToolContext, build_tool
from app.config import PROJECT_ROOT

MAX_OUTPUT_LINES = 200


class GrepInput(BaseModel):
    pattern: str = Field(description="搜索模式（正则表达式）")
    path: str | None = Field(default=None, description="搜索路径（文件或目录），默认为项目根目录")
    glob: str | None = Field(default=None, description='文件 glob 过滤，如 "*.ts"、"*.{ts,tsx}"')
    context: int = Field(default=0, description="显示匹配行的上下文行数")
    ignore_case: bool = Field(default=False, description="是否忽略大小写")
    max_results: int = Field(default=50, description="最大匹配数")


async def _execute(args: dict, ctx: ToolContext) -> dict:
    pattern = args["pattern"]
    search_path = args.get("path")
    glob_pattern = args.get("glob")
    context_lines = args.get("context", 0)
    ignore_case = args.get("ignore_case", False)
    max_results = args.get("max_results", 50)

    cmd_parts = ["rg", "--no-heading", "--line-number", "--color=never"]
    if ignore_case:
        cmd_parts.append("-i")
    if context_lines and context_lines > 0:
        cmd_parts.append(f"-C{context_lines}")
    if glob_pattern:
        cmd_parts.append(f"--glob={glob_pattern}")
    cmd_parts.append(f"-m{max_results}")
    cmd_parts.append("--")
    cmd_parts.append(pattern)
    if search_path:
        cmd_parts.append(search_path)

    cmd = " ".join(cmd_parts)

    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=15)
    except asyncio.TimeoutError:
        return {"error": "grep timed out (15s)"}

    stdout = stdout_bytes.decode("utf-8", errors="replace").strip()

    if proc.returncode != 0 and not stdout:
        if proc.returncode == 1:
            return {"matches": 0, "output": "No matches found."}
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        return {"error": stderr[:300]}

    lines = stdout.split("\n")
    total_matches = len(lines)
    truncated = total_matches > MAX_OUTPUT_LINES
    output = "\n".join(lines[:MAX_OUTPUT_LINES]) + (
        f"\n... ({total_matches} total lines, showing first {MAX_OUTPUT_LINES})" if truncated else ""
    )

    return {"matches": total_matches, "output": output}


grep_tool = build_tool(
    name="grep",
    description="使用 ripgrep 搜索文件内容。支持正则表达式、上下文行、文件类型过滤。路径相对于项目根目录。",
    input_schema=GrepInput,
    execute=_execute,
    is_concurrency_safe=True,
)
