from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from pydantic import BaseModel

logger = logging.getLogger("agent")


class ToolContext:
    __slots__ = ("cancel_event", "log")

    def __init__(self, cancel_event: asyncio.Event, log: Callable[..., None] = logger.info):
        self.cancel_event = cancel_event
        self.log = log


@dataclass
class Tool:
    name: str
    description: str
    input_schema: type[BaseModel]
    execute: Callable[[dict, ToolContext], Awaitable[Any]]
    is_concurrency_safe: bool = False


def build_tool(**kwargs: Any) -> Tool:
    return Tool(**kwargs)


@dataclass
class PendingToolCall:
    tool_call_id: str
    tool_name: str
    args: dict[str, Any]


@dataclass
class ToolCallResult:
    tool_call_id: str
    tool_name: str
    output: Any


# ---------------------------------------------------------------------------
# SSE Events — serialised as JSON and sent over the wire
# ---------------------------------------------------------------------------

@dataclass
class SSEEvent:
    """Union-like event that maps 1:1 to the frontend's expected types."""

    type: str
    content: str | None = None
    id: str | None = None
    name: str | None = None
    args: dict[str, Any] | None = None
    result: Any = None
    message: str | None = None

    def to_json(self) -> str:
        d: dict[str, Any] = {"type": self.type}
        if self.content is not None:
            d["content"] = self.content
        if self.id is not None:
            d["id"] = self.id
        if self.name is not None:
            d["name"] = self.name
        if self.args is not None:
            d["args"] = self.args
        if self.result is not None:
            d["result"] = self.result
        if self.message is not None:
            d["message"] = self.message
        return json.dumps(d, ensure_ascii=False)
