from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from app.agent.types import ToolContext, build_tool
from app.config import PROJECT_ROOT


class FileWriteInput(BaseModel):
    path: str = Field(description="File path (relative to project root or absolute)")
    content: str = Field(description="Complete file content to write")


async def _execute(args: dict, ctx: ToolContext) -> dict:
    file_path = args["path"]
    content = args["content"]

    try:
        resolved = Path(file_path) if Path(file_path).is_absolute() else PROJECT_ROOT / file_path
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")

        line_count = content.count("\n") + 1
        return {"ok": True, "path": file_path, "lines": line_count, "bytes": len(content.encode("utf-8"))}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


file_write_tool = build_tool(
    name="file_write",
    description="Write file contents (overwrite existing or create new). Path is relative to project root. Missing directories are created automatically.",
    input_schema=FileWriteInput,
    execute=_execute,
)
