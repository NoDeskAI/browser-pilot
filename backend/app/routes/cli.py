from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse

router = APIRouter()

_CLI_DIST = Path(__file__).resolve().parent.parent.parent / "cli-dist"
_CLI_TEMPLATE = Path(__file__).resolve().parent.parent / "cli_template.sh"


def _find_wheel() -> Path | None:
    if not _CLI_DIST.is_dir():
        return None
    wheels = sorted(_CLI_DIST.glob("*.whl"))
    return wheels[-1] if wheels else None


def get_cli_install_info(base_url: str) -> dict:
    """Return install commands for site-info."""
    from ..config import CLI_COMMAND_NAME, CLI_INSTALL_COMMAND

    shell_cmd = f"curl -fsSL {base_url}/api/cli/install | bash"
    wheel = _find_wheel()
    python_cmd = (
        f"pip install {base_url}/api/cli/{wheel.name}" if wheel else CLI_INSTALL_COMMAND
    )
    return {"shell": shell_cmd, "python": python_cmd}


@router.get("/api/cli/install")
async def install_script(request: Request):
    """One-liner: curl -fsSL https://host/api/cli/install | bash"""
    from ..config import CLI_COMMAND_NAME

    base = str(request.base_url).rstrip("/")
    script = f"""#!/usr/bin/env bash
set -e

CLI_NAME="{CLI_COMMAND_NAME}"
INSTALL_DIR="${{HOME}}/.local/bin"

echo "Installing $CLI_NAME ..."
mkdir -p "$INSTALL_DIR"
curl -fsSL "{base}/api/cli/nwb" -o "$INSTALL_DIR/$CLI_NAME"
chmod +x "$INSTALL_DIR/$CLI_NAME"

if ! echo "$PATH" | tr ':' '\\n' | grep -q "^$INSTALL_DIR$"; then
  echo ""
  echo "Add to your shell profile:"
  echo "  export PATH=\\"$INSTALL_DIR:\\$PATH\\""
  echo ""
fi

echo "Done! Run '$CLI_NAME session list' to verify."
"""
    return PlainTextResponse(script, media_type="text/x-shellscript")


@router.get("/api/cli/nwb")
async def cli_script(request: Request):
    """Serve the bash CLI with API URL and CLI name baked in."""
    from ..config import CLI_COMMAND_NAME

    if not _CLI_TEMPLATE.is_file():
        raise HTTPException(500, "CLI template not found")

    base = str(request.base_url).rstrip("/")
    content = _CLI_TEMPLATE.read_text()
    content = content.replace("{{API_URL}}", base)
    content = content.replace("{{CLI_NAME}}", CLI_COMMAND_NAME)
    return PlainTextResponse(content, media_type="text/x-shellscript")


@router.get("/api/cli/{filename}")
async def serve_cli_wheel(filename: str):
    """Serve pre-built Python CLI wheel for pip install."""
    if not filename.endswith(".whl"):
        raise HTTPException(404, "Not found")
    path = _CLI_DIST / filename
    if not path.is_file():
        raise HTTPException(404, "CLI package not available (dev mode?)")
    return FileResponse(path, media_type="application/zip", filename=filename)
