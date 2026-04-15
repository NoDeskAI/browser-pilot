from __future__ import annotations

import asyncio
import logging

import asyncpg

from app.config import DATABASE_URL

logger = logging.getLogger("db")

_pool: asyncpg.Pool | None = None
_db_ready = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    current_url TEXT,
    current_title TEXT
);

ALTER TABLE sessions ADD COLUMN IF NOT EXISTS current_url TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS current_title TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS device_preset TEXT DEFAULT 'desktop-1280x800';
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS proxy_url TEXT DEFAULT '';

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


async def init_db() -> None:
    global _pool, _db_ready
    backoff = 1.0
    attempt = 0
    while True:
        attempt += 1
        try:
            _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
            async with _pool.acquire() as conn:
                await conn.execute(_SCHEMA)
            _db_ready = True
            logger.info("Database ready (attempt %d)", attempt)
            return
        except (OSError, asyncpg.PostgresError) as exc:
            logger.warning("Waiting for database (attempt %d, next retry %.0fs): %s", attempt, backoff, exc)
            if _pool:
                await _pool.close()
                _pool = None
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


async def close_db() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("Database pool closed")


def is_ready() -> bool:
    return _db_ready


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialized — call init_db() first")
    return _pool
