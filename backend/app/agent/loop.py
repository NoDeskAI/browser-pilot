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
from app.config import get_max_steps
from app.i18n import t

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
    session_id: str | None = None
    locale: str = "zh"


async def agent_loop(params: AgentLoopParams) -> AsyncGenerator[SSEEvent, None]:
    messages = list(params.messages)
    cancel = params.cancel_event

    tool_ctx = ToolContext(cancel_event=cancel, session_id=params.session_id)

    loop_t0 = time.monotonic()
    total_tool_calls = 0
    completed_steps = 0
    max_steps = get_max_steps()
    _observed_urls: list[str] = []
    _cycle_limit = 3
    _cycle_hints_injected = 0
    _max_cycle_hints = 2

    for step in range(max_steps):
        if cancel.is_set():
            logger.info("cancelled before step %d", step + 1)
            break

        completed_steps = step + 1
        logger.info("step %d/%d", step + 1, max_steps)

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

            output_for_model = result.output
            image_block = None
            if isinstance(output_for_model, dict) and "_image" in output_for_model:
                img = output_for_model.pop("_image")
                image_block = {"type": "image", "source": {"type": "url", "url": img["url"]}}

            text_content = (
                json.dumps(output_for_model, ensure_ascii=False)
                if not isinstance(output_for_model, str)
                else output_for_model
            )
            if image_block:
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": result.tool_call_id,
                    "content": [image_block, {"type": "text", "text": text_content}],
                })
            else:
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": result.tool_call_id,
                    "content": text_content,
                })

            if result.tool_name == "browser_observe" and isinstance(result.output, dict):
                obs_url = result.output.get("url", "")
                if obs_url:
                    _observed_urls.append(obs_url)

        messages.append({"role": "user", "content": tool_results})

        if len(_observed_urls) >= _cycle_limit:
            recent = _observed_urls[-_cycle_limit:]
            if len(set(recent)) == 1:
                cycle_url = recent[0]
                _cycle_hints_injected += 1
                logger.warning("cycle detected (%d/%d): %s observed %d times", _cycle_hints_injected, _max_cycle_hints, cycle_url, _cycle_limit)

                if _cycle_hints_injected >= _max_cycle_hints:
                    logger.warning("max cycle hints reached, force stopping agent loop")
                    yield SSEEvent(type="text", content="\n\n" + t("cycle_stop", params.locale))
                    break

                cycle_image = None
                if params.session_id:
                    try:
                        from app.tools.browser.session import browser_session as _bs, wd_fetch as _wf
                        from app.file_store import get_store

                        async with _bs(params.session_id) as (sid, base):
                            b64 = await _wf(f"/session/{sid}/screenshot", base_url=base)
                        store = await get_store()
                        img_ref = await store.save(b64, params.session_id)
                        cycle_image = {"type": "image", "source": {"type": "url", "url": img_ref["url"]}}
                    except Exception:
                        cycle_image = None

                if cycle_image:
                    messages[-1]["content"].extend([
                        cycle_image,
                        {"type": "text", "text": (
                            f"[系统提示] 你已经连续 {_cycle_limit} 次在同一页面 ({cycle_url}) 上操作但没有效果。"
                            "这是当前页面的截图。请仔细观察截图中的视觉元素（弹窗、遮罩、验证码等），"
                            "尝试找到不同的操作方式。如果确实无法操作，告知用户并建议手动操作。"
                        )},
                    ])
                else:
                    messages[-1]["content"].append({"type": "text", "text": (
                        f"[系统提示] 你已经连续 {_cycle_limit} 次在同一页面 ({cycle_url}) 上操作但没有效果。"
                        "立即停止，直接回复用户说明你无法完成这个操作，建议用户手动操作。不要再调用任何工具。"
                    )})
                _observed_urls.clear()

    elapsed = time.monotonic() - loop_t0
    logger.info("agent done: %d steps, %d tool calls, %.1fs", completed_steps, total_tool_calls, elapsed)

    if params.session_id:
        from app.tools.browser import _nav_history
        _nav_history.pop(params.session_id, None)

    if completed_steps >= max_steps:
        yield SSEEvent(type="text", content="\n\n" + t("max_steps", params.locale, max_steps=max_steps))

    yield SSEEvent(type="done")


def _sanitize_for_sse(tool_name: str, output: Any) -> Any:
    if not output or not isinstance(output, dict):
        return output

    if tool_name == "browser_screenshot" and "_image" in output:
        output = {k: v for k, v in output.items() if k != "_image"}

    if tool_name == "browser_observe":
        raw_elements = output.get("elements") if isinstance(output.get("elements"), list) else []
        compact = []
        for el in raw_elements:
            item: dict[str, Any] = {
                "tag": el.get("tag", ""),
                "text": (el.get("text", "") or "")[:40],
                "x": el.get("x", 0),
                "y": el.get("y", 0),
            }
            attrs = el.get("attrs") or {}
            if attrs.get("id"):
                item["id"] = attrs["id"]
            if attrs.get("href"):
                item["href"] = attrs["href"][:60]
            if attrs.get("role"):
                item["role"] = attrs["role"]
            compact.append(item)
        return {
            "ok": True,
            "url": output.get("url"),
            "title": output.get("title"),
            "elementCount": len(raw_elements),
            "elements": compact,
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
