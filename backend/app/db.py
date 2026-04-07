from __future__ import annotations

import asyncio
import logging

import asyncpg

from app.config import DATABASE_URL

logger = logging.getLogger("db")

_pool: asyncpg.Pool | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS messages (
    id SERIAL PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    data JSONB NOT NULL,
    seq INTEGER NOT NULL,
    UNIQUE(session_id, seq)
);

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


async def init_db(max_retries: int = 10, retry_interval: float = 1.0) -> None:
    global _pool
    for attempt in range(1, max_retries + 1):
        try:
            _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
            async with _pool.acquire() as conn:
                await conn.execute(_SCHEMA)
            logger.info("Database ready (attempt %d)", attempt)
            return
        except (OSError, asyncpg.PostgresError) as exc:
            logger.warning("DB connect attempt %d/%d failed: %s", attempt, max_retries, exc)
            if _pool:
                await _pool.close()
                _pool = None
            if attempt == max_retries:
                raise
            await asyncio.sleep(retry_interval)


async def close_db() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("Database pool closed")


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialized — call init_db() first")
    return _pool
