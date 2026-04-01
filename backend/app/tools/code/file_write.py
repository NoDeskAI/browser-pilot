from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from app.agent.types import ToolContext, build_tool
from app.config import PROJECT_ROOT


class FileWriteInput(BaseModel):
    path: str = Field(description="文件路径（相对于项目根目录或绝对路径）")
    content: str = Field(description="要写入的完整文件内容")


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
    description="写入文件内容（覆盖已有文件或创建新文件）。路径相对于项目根目录。会自动创建不存在的目录。",
    input_schema=FileWriteInput,
    execute=_execute,
)
