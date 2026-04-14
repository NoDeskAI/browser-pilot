from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

from app.db import get_pool

_DEFAULT_NAMES = {"新会话", "New Session"}
logger = logging.getLogger("auto_name")


async def maybe_auto_name(session_id: str, url: str, title: str) -> None:
    """Auto-name a session on first navigation. Skips if already renamed."""
    try:
        pool = get_pool()
        row = await pool.fetchrow("SELECT name FROM sessions WHERE id = $1", session_id)
        if not row or row["name"] not in _DEFAULT_NAMES:
            return

        new_name = await _generate_name(url, title)
        if new_name:
            await pool.execute(
                "UPDATE sessions SET name = $1, updated_at = NOW() WHERE id = $2",
                new_name, session_id,
            )
            logger.info("Auto-named session %s -> %s", session_id[:8], new_name)
    except Exception as exc:
        logger.warning("Auto-name failed for %s: %s", session_id[:8], exc)


async def _generate_name(url: str, title: str) -> str | None:
    api_key = os.getenv("OPENAI_API_KEY", "")
    if api_key:
        try:
            return await _llm_name(api_key, url, title)
        except Exception as exc:
            logger.warning("LLM naming failed, falling back: %s", exc)

    if title and title.strip():
        return title.strip()[:30]
    hostname = urlparse(url).hostname
    return hostname if hostname else None


async def _llm_name(api_key: str, url: str, title: str) -> str:
    import openai

    client = openai.AsyncOpenAI(api_key=api_key)
    resp = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{
            "role": "user",
            "content": (
                f"为一个浏览器会话起一个简短名称（不超过20字），"
                f"当前页面: URL={url}, 标题={title}。只输出名称，不要引号。"
            ),
        }],
        max_tokens=30,
    )
    return resp.choices[0].message.content.strip()[:30]
