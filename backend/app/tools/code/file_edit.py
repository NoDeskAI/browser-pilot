from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from app.agent.types import ToolContext, build_tool
from app.config import PROJECT_ROOT


class FileEditInput(BaseModel):
    path: str = Field(description="文件路径")
    old_string: str = Field(description="要被替换的原始字符串（必须精确匹配）")
    new_string: str = Field(description="替换后的新字符串")
    replace_all: bool = Field(default=False, description="是否替换所有匹配项")


async def _execute(args: dict, ctx: ToolContext) -> dict:
    file_path = args["path"]
    old_string = args["old_string"]
    new_string = args["new_string"]
    replace_all = args.get("replace_all", False)

    try:
        resolved = Path(file_path) if Path(file_path).is_absolute() else PROJECT_ROOT / file_path
        content = resolved.read_text(encoding="utf-8")

        if old_string not in content:
            return {"ok": False, "error": "old_string not found in file"}

        occurrences = content.count(old_string)
        if occurrences > 1 and not replace_all:
            return {"ok": False, "error": f"old_string has {occurrences} matches. Set replace_all=true or provide more context to make it unique."}

        updated = content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)
        resolved.write_text(updated, encoding="utf-8")

        return {"ok": True, "path": file_path, "replacements": occurrences if replace_all else 1}
    except FileNotFoundError:
        return {"ok": False, "error": f"File not found: {file_path}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


file_edit_tool = build_tool(
    name="file_edit",
    description="通过精确字符串匹配替换来编辑文件。old_string 必须在文件中唯一匹配（除非设置 replace_all）。路径相对于项目根目录。",
    input_schema=FileEditInput,
    execute=_execute,
)
