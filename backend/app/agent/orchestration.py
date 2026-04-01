from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import AsyncGenerator

from app.agent.registry import find_tool
from app.agent.types import PendingToolCall, Tool, ToolCallResult, ToolContext

logger = logging.getLogger("agent.orchestration")


@dataclass
class _Batch:
    concurrent: bool
    calls: list[PendingToolCall]


def _partition_tool_calls(tool_calls: list[PendingToolCall], tools: list[Tool]) -> list[_Batch]:
    batches: list[_Batch] = []
    current: _Batch | None = None

    for tc in tool_calls:
        tool = find_tool(tools, tc.tool_name)
        safe = tool.is_concurrency_safe if tool else False

        if current is not None and current.concurrent == safe:
            current.calls.append(tc)
        else:
            current = _Batch(concurrent=safe, calls=[tc])
            batches.append(current)

    return batches


async def _execute_one(tc: PendingToolCall, tools: list[Tool], ctx: ToolContext) -> ToolCallResult:
    tool = find_tool(tools, tc.tool_name)
    if not tool:
        return ToolCallResult(
            tool_call_id=tc.tool_call_id,
            tool_name=tc.tool_name,
            output={"error": f"Unknown tool: {tc.tool_name}"},
        )
    try:
        validated = tool.input_schema.model_validate(tc.args)
        output = await tool.execute(validated.model_dump(), ctx)
        return ToolCallResult(tool_call_id=tc.tool_call_id, tool_name=tc.tool_name, output=output)
    except Exception as exc:
        logger.exception("Tool %s failed", tc.tool_name)
        return ToolCallResult(
            tool_call_id=tc.tool_call_id,
            tool_name=tc.tool_name,
            output={"error": str(exc)},
        )


async def execute_tool_calls(
    tool_calls: list[PendingToolCall],
    tools: list[Tool],
    ctx: ToolContext,
) -> AsyncGenerator[ToolCallResult, None]:
    for batch in _partition_tool_calls(tool_calls, tools):
        if batch.concurrent:
            results = await asyncio.gather(
                *[_execute_one(tc, tools, ctx) for tc in batch.calls]
            )
            for r in results:
                yield r
        else:
            for tc in batch.calls:
                yield await _execute_one(tc, tools, ctx)
