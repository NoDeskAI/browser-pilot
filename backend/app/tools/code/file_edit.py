from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from app.agent.types import ToolContext, build_tool
from app.config import PROJECT_ROOT


class FileEditInput(BaseModel):
    path: str = Field(description="File path")
    old_string: str = Field(description="Original string to replace (must match exactly)")
    new_string: str = Field(description="New string to replace with")
    replace_all: bool = Field(default=False, description="Whether to replace all occurrences")


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
    description="Edit a file by exact string replacement. old_string must be unique in the file (unless replace_all is set). Path is relative to project root.",
    input_schema=FileEditInput,
    execute=_execute,
)
