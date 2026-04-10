from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from app.agent.types import ToolContext, build_tool
from app.config import PROJECT_ROOT

MAX_OUTPUT_LINES = 200


class GrepInput(BaseModel):
    pattern: str = Field(description="Search pattern (regular expression)")
    path: str | None = Field(default=None, description="Search path (file or directory), defaults to project root")
    glob: str | None = Field(default=None, description='File glob filter, e.g. "*.ts", "*.{ts,tsx}"')
    context: int = Field(default=0, description="Number of context lines around each match")
    ignore_case: bool = Field(default=False, description="Case-insensitive search")
    max_results: int = Field(default=50, description="Maximum number of matches")


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
    description="Search file contents using ripgrep. Supports regex, context lines, and file type filtering. Path is relative to project root.",
    input_schema=GrepInput,
    execute=_execute,
    is_concurrency_safe=True,
)
