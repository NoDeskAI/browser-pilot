from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import asyncpg

from app.config import require_database_url

logger = logging.getLogger("db")

_pool: asyncpg.Pool | None = None
_db_ready = False


def _run_migrations() -> None:
    from alembic.config import Config
    from alembic import command

    cfg = Config(str(Path(__file__).parent.parent / "alembic.ini"))
    cfg.attributes["skip_logger_setup"] = True
    command.upgrade(cfg, "head")


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def init_db() -> None:
    global _pool, _db_ready
    database_url = require_database_url()
    backoff = 1.0
    attempt = 0
    while True:
        attempt += 1
        try:
            await asyncio.to_thread(_run_migrations)
            _pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5, init=_init_connection)
            _db_ready = True
            logger.info("Database ready (attempt %d)", attempt)
            return
        except Exception as exc:
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
