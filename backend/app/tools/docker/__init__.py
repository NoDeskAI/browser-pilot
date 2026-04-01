from __future__ import annotations

import asyncio
import json
import logging

from pydantic import BaseModel, Field

from app.agent.types import Tool, ToolContext, build_tool
from app.config import PROJECT_ROOT

logger = logging.getLogger("agent.docker")

SERVICE_MAP: dict[str, list[str]] = {
    "selenium": ["selenium"],
    "dom-diff": ["dom-diff-proxy"],
    "rrweb": ["rrweb-proxy"],
}


async def _docker_compose(args_str: str, timeout: float = 120, cancel_event: asyncio.Event | None = None) -> tuple[str, str]:
    cmd = f"docker compose {args_str}"
    logger.info("exec: %s", cmd)

    proc = await asyncio.create_subprocess_shell(
        cmd,
        cwd=str(PROJECT_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"docker compose timed out ({timeout}s)")

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    if proc.returncode and proc.returncode != 0:
        raise RuntimeError(stderr[:300] or f"docker compose exited {proc.returncode}")
    return stdout, stderr


async def _images_exist(services: list[str]) -> bool:
    """检查所有 service 的镜像是否已构建。检查失败时返回 False（保守走长超时）。"""
    try:
        for svc in services:
            proc = await asyncio.create_subprocess_shell(
                f"docker compose images -q {svc}",
                cwd=str(PROJECT_ROOT),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            if not stdout.strip():
                return False
        return True
    except Exception:
        return False


async def _get_statuses() -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        stdout, _ = await _docker_compose("ps -a --format json", timeout=15)
        for line in stdout.strip().splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
                result[obj["Service"]] = obj["State"]
            except (json.JSONDecodeError, KeyError):
                try:
                    arr = json.loads(stdout)
                    if isinstance(arr, list):
                        for obj in arr:
                            result[obj["Service"]] = obj["State"]
                except Exception:
                    pass
    except Exception:
        pass
    return result


class EmptyInput(BaseModel):
    pass

class SolutionInput(BaseModel):
    solutionId: str = Field(description="方案 ID")


async def _docker_status(args: dict, ctx: ToolContext) -> dict:
    try:
        return {"statuses": await _get_statuses()}
    except Exception as exc:
        return {"error": str(exc)}


async def _docker_start(args: dict, ctx: ToolContext) -> dict:
    solution_id = args["solutionId"]
    services = SERVICE_MAP.get(solution_id)
    if not services:
        return {"ok": False, "error": f"未知方案: {solution_id}"}
    try:
        logger.info("start [%s]: %s", solution_id, ", ".join(services))
        has_images = await _images_exist(services)
        timeout = 60 if has_images else 600
        await _docker_compose(f"up -d {' '.join(services)}", timeout=timeout)
        return {"ok": True, "solutionId": solution_id, "services": services}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}


async def _docker_stop(args: dict, ctx: ToolContext) -> dict:
    solution_id = args["solutionId"]
    services = SERVICE_MAP.get(solution_id)
    if not services:
        return {"ok": False, "error": f"未知方案: {solution_id}"}
    try:
        logger.info("stop [%s]: %s", solution_id, ", ".join(services))
        await _docker_compose(f"stop {' '.join(services)}", timeout=60)
        return {"ok": True, "solutionId": solution_id, "services": services}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}


docker_tools: list[Tool] = [
    build_tool(name="docker_status", description="查询所有 Docker 容器的运行状态", input_schema=EmptyInput, execute=_docker_status, is_concurrency_safe=True),
    build_tool(name="docker_start", description="启动指定方案的 Docker 服务。可用 id: selenium", input_schema=SolutionInput, execute=_docker_start),
    build_tool(name="docker_stop", description="停止指定方案的 Docker 服务", input_schema=SolutionInput, execute=_docker_stop),
]
