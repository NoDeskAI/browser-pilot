from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from app.agent.types import ToolContext, build_tool
from app.config import PROJECT_ROOT

MAX_FILES = 200


class GlobInput(BaseModel):
    pattern: str = Field(description='glob 模式，如 "*.ts"、"**/test_*.py"、"src/**/*.vue"')
    path: str | None = Field(default=None, description="搜索起始路径，默认为项目根目录")


async def _execute(args: dict, ctx: ToolContext) -> dict:
    pattern = args["pattern"]
    search_path = args.get("path") or "."
    cmd = f"find {search_path} -path '*/{pattern}' -o -name '{pattern}' 2>/dev/null | head -{MAX_FILES + 1} | sort"

    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
    except asyncio.TimeoutError:
        return {"error": "glob timed out (10s)"}

    stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
    if not stdout:
        return {"files": [], "count": 0}

    files = [f for f in stdout.split("\n") if f]
    truncated = len(files) > MAX_FILES
    result = files[:MAX_FILES] if truncated else files

    out: dict = {"files": result, "count": len(result)}
    if truncated:
        out["truncated"] = True
        out["note"] = f"Showing first {MAX_FILES} of {len(files)}+ matches"
    return out


glob_tool = build_tool(
    name="glob",
    description="按文件名模式搜索文件。使用 find 搜索匹配的文件路径。路径相对于项目根目录。",
    input_schema=GlobInput,
    execute=_execute,
    is_concurrency_safe=True,
)
