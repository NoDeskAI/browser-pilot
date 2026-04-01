from __future__ import annotations

import json
from typing import Any

CHARS_PER_TOKEN_ESTIMATE = 3.5
MAX_CONTEXT_TOKENS = 120_000
COMPACT_TRIGGER_RATIO = 0.75
COMPACT_TRIGGER_TOKENS = MAX_CONTEXT_TOKENS * COMPACT_TRIGGER_RATIO


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text) / CHARS_PER_TOKEN_ESTIMATE + 0.99))


def _estimate_message_tokens(msg: dict[str, Any]) -> int:
    content = msg.get("content")
    if isinstance(content, str):
        return estimate_tokens(content) + 4
    if isinstance(content, list):
        total = 4
        for part in content:
            ptype = part.get("type", "")
            if ptype == "text":
                total += estimate_tokens(part.get("text", ""))
            elif ptype == "tool_use":
                total += estimate_tokens(json.dumps(part.get("input", {}))) + 20
            elif ptype == "tool_result":
                total += estimate_tokens(json.dumps(part.get("content", ""))) + 10
            else:
                total += 20
        return total
    return 20


def estimate_total_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(_estimate_message_tokens(m) for m in messages)


def should_compact(messages: list[dict[str, Any]]) -> bool:
    return estimate_total_tokens(messages) > COMPACT_TRIGGER_TOKENS


def _summarize_tool_result(tool_name: str, result: Any) -> Any:
    if not isinstance(result, dict):
        s = str(result)
        return s[:200] + "... (truncated)" if len(s) > 200 else result

    if tool_name == "browser_observe":
        elements = result.get("elements")
        return {
            "url": result.get("url"),
            "title": result.get("title"),
            "elementCount": len(elements) if isinstance(elements, list) else None,
            "visibleTextPreview": (result.get("visibleText") or "")[:200],
        }
    if tool_name == "bash":
        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
        return {
            "exitCode": result.get("exitCode"),
            "stdout": stdout[:300] + ("..." if len(stdout) > 300 else ""),
            "stderr": stderr[:200] + ("..." if len(stderr) > 200 else ""),
        }
    if tool_name == "file_read":
        content = result.get("content", "")
        return {
            "path": result.get("path"),
            "totalLines": result.get("totalLines"),
            "contentPreview": content[:300] + ("..." if len(content) > 300 else ""),
        }
    if tool_name == "grep":
        output = result.get("output", "")
        return {
            "matches": result.get("matches"),
            "outputPreview": output[:300] + ("..." if len(output) > 300 else ""),
        }

    s = json.dumps(result, ensure_ascii=False)
    if len(s) <= 500:
        return result
    return {"_summary": s[:400] + "... (truncated)"}


def _trim_old_tool_results(messages: list[dict[str, Any]], keep_recent: int = 6) -> list[dict[str, Any]]:
    if len(messages) <= keep_recent:
        return messages

    older = messages[:-keep_recent]
    recent = messages[-keep_recent:]

    trimmed: list[dict[str, Any]] = []
    for msg in older:
        content = msg.get("content")
        if msg.get("role") != "tool" or not isinstance(content, list):
            trimmed.append(msg)
            continue

        new_content = []
        for part in content:
            if part.get("type") != "tool_result":
                new_content.append(part)
                continue
            result_str = json.dumps(part.get("content", ""), ensure_ascii=False)
            if len(result_str) <= 500:
                new_content.append(part)
            else:
                raw = part.get("content")
                summarized = (
                    _summarize_tool_result(part.get("tool_use_id", ""), raw)
                    if isinstance(raw, dict)
                    else str(raw)[:200] + "... (truncated)"
                )
                new_content.append({**part, "content": summarized})
        trimmed.append({**msg, "content": new_content})

    return trimmed + recent


def compact_if_needed(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not should_compact(messages):
        return messages
    return _trim_old_tool_results(messages)
