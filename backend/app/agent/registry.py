from __future__ import annotations

from typing import Any

from app.agent.types import Tool


def find_tool(tools: list[Tool], name: str) -> Tool | None:
    for t in tools:
        if t.name == name:
            return t
    return None


def build_anthropic_tools(tools: list[Tool]) -> list[dict[str, Any]]:
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema.model_json_schema(),
        }
        for t in tools
    ]


def build_openai_tools(tools: list[Tool]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema.model_json_schema(),
            },
        }
        for t in tools
    ]
