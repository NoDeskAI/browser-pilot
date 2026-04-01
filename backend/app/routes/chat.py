from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent.loop import AgentLoopParams, agent_loop
from app.agent.prompt import SYSTEM_PROMPT
from app.tools.browser import browser_tools
from app.tools.code import code_tools
from app.tools.docker import docker_tools

logger = logging.getLogger("agent.routes.chat")
router = APIRouter()

ALL_TOOLS = [*browser_tools, *docker_tools, *code_tools]


class ChatRequest(BaseModel):
    messages: list[dict[str, Any]]
    apiKey: str
    baseUrl: str | None = None
    model: str | None = None
    apiType: str | None = None


@router.post("/api/ai/chat")
async def chat(body: ChatRequest, request: Request):
    if not body.apiKey:
        return {"error": "请先配置 API Key"}
    if not body.messages:
        return {"error": "消息不能为空"}

    provider = body.apiType or "openai"
    base_url = body.baseUrl or "https://api.openai.com/v1"
    model_name = body.model or ("claude-sonnet-4-20250514" if provider == "anthropic" else "gpt-4o-mini")

    core_messages: list[dict[str, Any]] = []
    for m in body.messages:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content", "")
        if not isinstance(content, str):
            import json
            content = json.dumps(content, ensure_ascii=False)
        core_messages.append({"role": role, "content": content})

    logger.info("Agent loop start, model=%s, provider=%s", model_name, provider)

    cancel_event = asyncio.Event()

    params = AgentLoopParams(
        provider=provider,
        base_url=base_url,
        api_key=body.apiKey,
        model=model_name,
        system_prompt=SYSTEM_PROMPT,
        messages=core_messages,
        tools=ALL_TOOLS,
        cancel_event=cancel_event,
    )

    async def event_stream():
        try:
            async for event in agent_loop(params):
                if await request.is_disconnected():
                    cancel_event.set()
                    break
                yield f"data: {event.to_json()}\n\n"
        except Exception as exc:
            logger.exception("Agent loop fatal error")
            import json
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
