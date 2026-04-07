from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Literal

import anthropic
import openai

logger = logging.getLogger("agent.model")


@dataclass
class StreamChunk:
    type: Literal["text", "tool_use_start", "tool_use_delta", "tool_use_end", "done", "error"]
    text: str = ""
    tool_call_id: str = ""
    tool_name: str = ""
    partial_json: str = ""
    error_message: str = ""


async def stream_model(
    provider: str,
    base_url: str,
    api_key: str,
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> AsyncGenerator[StreamChunk, None]:
    if provider == "anthropic":
        async for chunk in _stream_anthropic(base_url, api_key, model, system, messages, tools):
            yield chunk
    else:
        async for chunk in _stream_openai(base_url, api_key, model, system, messages, tools):
            yield chunk


# ---------------------------------------------------------------------------
# Anthropic streaming
# ---------------------------------------------------------------------------

async def _stream_anthropic(
    base_url: str,
    api_key: str,
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> AsyncGenerator[StreamChunk, None]:
    url = base_url.rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"

    client = anthropic.AsyncAnthropic(base_url=url, api_key=api_key)

    try:
        async with client.messages.stream(
            model=model,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=8192,
        ) as stream:
            async for event in stream:
                if event.type == "content_block_start":
                    block = event.content_block
                    if block.type == "tool_use":
                        yield StreamChunk(
                            type="tool_use_start",
                            tool_call_id=block.id,
                            tool_name=block.name,
                        )
                elif event.type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        yield StreamChunk(type="text", text=delta.text)
                    elif delta.type == "input_json_delta":
                        yield StreamChunk(
                            type="tool_use_delta",
                            partial_json=delta.partial_json,
                        )
                elif event.type == "content_block_stop":
                    yield StreamChunk(type="tool_use_end")
    except Exception as exc:
        logger.error("Anthropic stream error: %s", exc, exc_info=True)
        yield StreamChunk(type="error", error_message=str(exc))


# ---------------------------------------------------------------------------
# OpenAI-compatible streaming
# ---------------------------------------------------------------------------

async def _stream_openai(
    base_url: str,
    api_key: str,
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> AsyncGenerator[StreamChunk, None]:
    client = openai.AsyncOpenAI(base_url=base_url.rstrip("/"), api_key=api_key)

    oai_messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        oai_messages.extend(_convert_message_to_openai(m))

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": oai_messages,
        "stream": True,
    }
    if tools:
        kwargs["tools"] = tools

    try:
        stream = await client.chat.completions.create(**kwargs)

        current_tool_calls: dict[int, dict[str, str]] = {}

        async for chunk in stream:
            choice = chunk.choices[0] if chunk.choices else None
            if not choice:
                continue
            delta = choice.delta

            if delta.content:
                yield StreamChunk(type="text", text=delta.content)

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in current_tool_calls:
                        current_tool_calls[idx] = {
                            "id": tc.id or "",
                            "name": tc.function.name if tc.function and tc.function.name else "",
                            "arguments": "",
                        }
                        if current_tool_calls[idx]["id"]:
                            yield StreamChunk(
                                type="tool_use_start",
                                tool_call_id=current_tool_calls[idx]["id"],
                                tool_name=current_tool_calls[idx]["name"],
                            )
                    if tc.function and tc.function.arguments:
                        current_tool_calls[idx]["arguments"] += tc.function.arguments
                        yield StreamChunk(
                            type="tool_use_delta",
                            partial_json=tc.function.arguments,
                        )

            if choice.finish_reason == "tool_calls":
                for _idx, info in sorted(current_tool_calls.items()):
                    yield StreamChunk(type="tool_use_end")
                current_tool_calls.clear()

    except Exception as exc:
        logger.error("OpenAI stream error: %s", exc, exc_info=True)
        yield StreamChunk(type="error", error_message=str(exc))


def _convert_message_to_openai(msg: dict[str, Any]) -> list[dict[str, Any]]:
    role = msg.get("role", "user")
    content = msg.get("content")

    if role == "assistant" and isinstance(content, list):
        text_parts = []
        tool_calls = []
        for part in content:
            if part.get("type") == "text":
                text_parts.append(part.get("text", ""))
            elif part.get("type") == "tool_use":
                tool_calls.append({
                    "id": part["id"],
                    "type": "function",
                    "function": {
                        "name": part["name"],
                        "arguments": json.dumps(part.get("input", {})),
                    },
                })
        result: dict[str, Any] = {"role": "assistant"}
        if text_parts:
            result["content"] = "\n".join(text_parts)
        if tool_calls:
            result["tool_calls"] = tool_calls
        return [result]

    if isinstance(content, list):
        tool_results = [p for p in content if p.get("type") == "tool_result"]
        if tool_results:
            return [
                {
                    "role": "tool",
                    "tool_call_id": p.get("tool_use_id", ""),
                    "content": json.dumps(p.get("content", ""), ensure_ascii=False)
                    if not isinstance(p.get("content"), str)
                    else p.get("content", ""),
                }
                for p in tool_results
            ]
        texts = [p.get("text", "") for p in content if p.get("type") == "text"]
        return [{"role": role, "content": "\n".join(texts) if texts else ""}]

    return [{"role": role, "content": content or ""}]
