from __future__ import annotations

import logging

from pydantic import BaseModel

from app.agent.types import Tool, ToolContext, build_tool
from app.container import (
    ensure_container_running,
    get_container_ports,
    get_container_status,
    pause_container,
    stop_container,
)

logger = logging.getLogger("agent.docker")

_NO_SESSION = {"ok": False, "error": "No active session"}


class EmptyInput(BaseModel):
    pass


async def _docker_status(args: dict, ctx: ToolContext) -> dict:
    if not ctx.session_id:
        return _NO_SESSION
    try:
        status = await get_container_status(ctx.session_id)
        result: dict = {"ok": True, "containerStatus": status}
        if status == "running":
            try:
                ports = await get_container_ports(ctx.session_id)
                result["ports"] = ports
            except Exception:
                result["ports"] = None
        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _docker_start(args: dict, ctx: ToolContext) -> dict:
    if not ctx.session_id:
        return _NO_SESSION
    try:
        ports = await ensure_container_running(ctx.session_id)
        return {"ok": True, "ports": ports}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}


async def _docker_stop(args: dict, ctx: ToolContext) -> dict:
    if not ctx.session_id:
        return _NO_SESSION
    try:
        await stop_container(ctx.session_id)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}


async def _docker_pause(args: dict, ctx: ToolContext) -> dict:
    if not ctx.session_id:
        return _NO_SESSION
    try:
        await pause_container(ctx.session_id)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}


docker_tools: list[Tool] = [
    build_tool(name="docker_status", description="Query the browser container status for the current session", input_schema=EmptyInput, execute=_docker_status, is_concurrency_safe=True),
    build_tool(name="docker_start", description="Start the browser container for the current session", input_schema=EmptyInput, execute=_docker_start),
    build_tool(name="docker_stop", description="Stop the browser container for the current session", input_schema=EmptyInput, execute=_docker_stop),
    build_tool(name="docker_pause", description="Hibernate the browser container (freeze all processes, preserve full state)", input_schema=EmptyInput, execute=_docker_pause),
]
