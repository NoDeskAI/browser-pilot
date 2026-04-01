from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncGenerator

from app.agent.context import compact_if_needed, estimate_total_tokens
from app.agent.model import StreamChunk, stream_model
from app.agent.orchestration import execute_tool_calls
from app.agent.registry import build_anthropic_tools, build_openai_tools
from app.agent.types import PendingToolCall, SSEEvent, Tool, ToolContext
from app.config import MAX_STEPS

logger = logging.getLogger("agent.loop")


@dataclass
class AgentLoopParams:
    provider: str
    base_url: str
    api_key: str
    model: str
    system_prompt: str
    messages: list[dict[str, Any]]
    tools: list[Tool]
    cancel_event: asyncio.Event


async def agent_loop(params: AgentLoopParams) -> AsyncGenerator[SSEEvent, None]:
    messages = list(params.messages)
    cancel = params.cancel_event

    tool_ctx = ToolContext(cancel_event=cancel)

    loop_t0 = time.monotonic()
    total_tool_calls = 0
    completed_steps = 0

    for step in range(MAX_STEPS):
        if cancel.is_set():
            logger.info("cancelled before step %d", step + 1)
            break

        completed_steps = step + 1
        logger.info("step %d/%d", step + 1, MAX_STEPS)

        prev_len = len(messages)
        messages = compact_if_needed(messages)
        if len(messages) != prev_len:
            logger.info("context compacted: ~%d tokens", estimate_total_tokens(messages))

        api_tools = (
            build_anthropic_tools(params.tools)
            if params.provider == "anthropic"
            else build_openai_tools(params.tools)
        )

        assistant_text = ""
        pending: list[PendingToolCall] = []
        current_tool_id = ""
        current_tool_name = ""
        current_tool_json = ""

        try:
            async for chunk in stream_model(
                provider=params.provider,
                base_url=params.base_url,
                api_key=params.api_key,
                model=params.model,
                system=params.system_prompt,
                messages=messages,
                tools=api_tools,
            ):
                if cancel.is_set():
                    logger.info("cancelled during step %d streaming", step + 1)
                    break

                if chunk.type == "text":
                    assistant_text += chunk.text
                    yield SSEEvent(type="text", content=chunk.text)

                elif chunk.type == "tool_use_start":
                    current_tool_id = chunk.tool_call_id
                    current_tool_name = chunk.tool_name
                    current_tool_json = ""

                elif chunk.type == "tool_use_delta":
                    current_tool_json += chunk.partial_json

                elif chunk.type == "tool_use_end":
                    if current_tool_id:
                        try:
                            args = json.loads(current_tool_json) if current_tool_json else {}
                        except json.JSONDecodeError:
                            args = {}
                        logger.info("tool-call: %s(%s)", current_tool_name, json.dumps(args, ensure_ascii=False)[:100])
                        tc = PendingToolCall(
                            tool_call_id=current_tool_id,
                            tool_name=current_tool_name,
                            args=args,
                        )
                        pending.append(tc)
                        yield SSEEvent(type="tool_call", id=tc.tool_call_id, name=tc.tool_name, args=args)
                        current_tool_id = ""
                        current_tool_name = ""
                        current_tool_json = ""

                elif chunk.type == "error":
                    logger.error("stream error: %s", chunk.error_message)
                    yield SSEEvent(type="error", message=chunk.error_message)

        except Exception as exc:
            if cancel.is_set():
                logger.info("cancelled during step %d (exception after cancel)", step + 1)
                break
            logger.error("step %d exception: %s", step + 1, exc, exc_info=True)
            yield SSEEvent(type="error", message=str(exc))
            break

        if not pending:
            break

        total_tool_calls += len(pending)

        assistant_content: list[dict[str, Any]] = []
        if assistant_text:
            assistant_content.append({"type": "text", "text": assistant_text})
        for tc in pending:
            assistant_content.append({
                "type": "tool_use",
                "id": tc.tool_call_id,
                "name": tc.tool_name,
                "input": tc.args,
            })
        messages.append({"role": "assistant", "content": assistant_content})

        tool_results: list[dict[str, Any]] = []
        async for result in execute_tool_calls(pending, params.tools, tool_ctx):
            ok = not (isinstance(result.output, dict) and (result.output.get("error") or result.output.get("ok") is False))
            logger.info("tool-result: %s -> %s", result.tool_name, "ok" if ok else "FAIL")

            yield SSEEvent(
                type="tool_result",
                id=result.tool_call_id,
                name=result.tool_name,
                result=_sanitize_for_sse(result.tool_name, result.output),
            )

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": result.tool_call_id,
                "content": json.dumps(result.output, ensure_ascii=False) if not isinstance(result.output, str) else result.output,
            })

        messages.append({"role": "user", "content": tool_results})

    elapsed = time.monotonic() - loop_t0
    logger.info("agent done: %d steps, %d tool calls, %.1fs", completed_steps, total_tool_calls, elapsed)

    yield SSEEvent(type="done")


def _sanitize_for_sse(tool_name: str, output: Any) -> Any:
    if not output or not isinstance(output, dict):
        return output

    if tool_name == "browser_observe":
        return {
            "ok": True,
            "url": output.get("url"),
            "title": output.get("title"),
            "elementCount": len(output["elements"]) if isinstance(output.get("elements"), list) else None,
        }

    if "currentPage" in output and isinstance(output["currentPage"], dict):
        cp = output["currentPage"]
        return {
            **output,
            "currentPage": {
                "url": cp.get("url"),
                "title": cp.get("title"),
                "elementCount": cp.get("elementCount"),
            },
        }

    return output
