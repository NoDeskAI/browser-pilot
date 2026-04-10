from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from app.agent.types import ToolContext, build_tool
from app.config import PROJECT_ROOT

MAX_FILE_SIZE = 256 * 1024


class FileReadInput(BaseModel):
    path: str = Field(description="File path (relative to project root or absolute)")
    offset: int | None = Field(default=None, description="Start reading from this line (1-based), defaults to the beginning")
    limit: int | None = Field(default=None, description="Number of lines to read, defaults to all")


async def _execute(args: dict, ctx: ToolContext) -> dict:
    file_path = args["path"]
    offset = args.get("offset")
    limit = args.get("limit")

    try:
        resolved = Path(file_path) if Path(file_path).is_absolute() else PROJECT_ROOT / file_path

        if not resolved.is_file():
            return {"error": f"Not a file: {file_path}"}

        size = resolved.stat().st_size
        if size > MAX_FILE_SIZE:
            return {"error": f"File too large ({size // 1024} KB, max {MAX_FILE_SIZE // 1024} KB). Use offset/limit to read a portion."}

        raw = resolved.read_text(encoding="utf-8")
        all_lines = raw.split("\n")
        total_lines = len(all_lines)

        start_line = max(1, offset or 1)
        end_line = min(start_line + limit - 1, total_lines) if limit else total_lines
        lines = all_lines[start_line - 1 : end_line]

        numbered = "\n".join(f"{str(start_line + i).rjust(6)}|{line}" for i, line in enumerate(lines))

        return {
            "path": file_path,
            "totalLines": total_lines,
            "startLine": start_line,
            "endLine": end_line,
            "content": numbered,
        }
    except FileNotFoundError:
        return {"error": f"File not found: {file_path}"}
    except Exception as exc:
        return {"error": str(exc)}


file_read_tool = build_tool(
    name="file_read",
    description="Read file contents. Supports specifying start line and line limit. Path is relative to project root.",
    input_schema=FileReadInput,
    execute=_execute,
    is_concurrency_safe=True,
)
