from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any

import asyncpg
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app import config
from app.config import require_database_url

logger = logging.getLogger("db")

_pool: asyncpg.Pool | None = None
_MIGRATION_LOCK_ID = 4242420317


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class MigrationInfo:
    current_revision: str = ""
    target_revision: str = ""
    pending_revisions: list[str] = field(default_factory=list)
    current_revision_after: str = ""


@dataclass
class BootstrapState:
    status: str = "waiting_database"
    current_revision: str = ""
    target_revision: str = ""
    pending_revisions: list[str] = field(default_factory=list)
    error: str = ""
    attempt: int = 0
    updated_at: str = field(default_factory=lambda: _utc_now())


class IncompatibleSchemaError(RuntimeError):
    def __init__(self, message: str, info: MigrationInfo | None = None):
        super().__init__(message)
        self.info = info or MigrationInfo()


class MigrationExecutionError(RuntimeError):
    def __init__(self, message: str, info: MigrationInfo | None = None):
        super().__init__(message)
        self.info = info or MigrationInfo()


_bootstrap_state = BootstrapState()


def _set_bootstrap_state(
    status: str,
    *,
    current_revision: str | None = None,
    target_revision: str | None = None,
    pending_revisions: list[str] | None = None,
    error: str = "",
    attempt: int | None = None,
) -> None:
    global _bootstrap_state
    _bootstrap_state = BootstrapState(
        status=status,
        current_revision=_bootstrap_state.current_revision if current_revision is None else current_revision,
        target_revision=_bootstrap_state.target_revision if target_revision is None else target_revision,
        pending_revisions=list(_bootstrap_state.pending_revisions if pending_revisions is None else pending_revisions),
        error=error,
        attempt=_bootstrap_state.attempt if attempt is None else attempt,
    )


def get_bootstrap_state() -> dict[str, Any]:
    return {
        "status": _bootstrap_state.status,
        "currentRevision": _bootstrap_state.current_revision,
        "targetRevision": _bootstrap_state.target_revision,
        "pendingRevisions": list(_bootstrap_state.pending_revisions),
        "error": _bootstrap_state.error,
        "attempt": _bootstrap_state.attempt,
        "updatedAt": _bootstrap_state.updated_at,
    }


def _alembic_config() -> Config:
    backend_root = Path(__file__).parent.parent
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.attributes["skip_logger_setup"] = True
    return cfg


def _async_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return database_url


def _script_directory(cfg: Config) -> ScriptDirectory:
    return ScriptDirectory.from_config(cfg)


def _target_revision(script: ScriptDirectory) -> str:
    return script.get_current_head()


def _pending_revisions(script: ScriptDirectory, current_revision: str, target_revision: str) -> list[str]:
    if current_revision == target_revision:
        return []
    lower = current_revision or "base"
    return [rev.revision for rev in reversed(list(script.iterate_revisions(target_revision, lower)))]


def _revision_label(revision: str) -> str:
    return revision or "base"


def _collect_migration_info(connection) -> MigrationInfo:
    cfg = _alembic_config()
    script = _script_directory(cfg)
    target = _target_revision(script)
    current = MigrationContext.configure(connection).get_current_revision() or ""
    current_known = True
    if current:
        try:
            current_known = script.get_revision(current) is not None
        except Exception:
            current_known = False
    if current and not current_known:
        info = MigrationInfo(current_revision=current, target_revision=target)
        raise IncompatibleSchemaError(
            f"Database schema revision '{current}' is not known by this Browser Pilot version. "
            "Upgrade the application image or restore a backup matching this version.",
            info,
        )
    return MigrationInfo(
        current_revision=current,
        target_revision=target,
        pending_revisions=_pending_revisions(script, current, target),
        current_revision_after=current,
    )


def _upgrade_with_connection(connection) -> MigrationInfo:
    info = _collect_migration_info(connection)
    _set_bootstrap_state(
        "migrating",
        current_revision=info.current_revision,
        target_revision=info.target_revision,
        pending_revisions=info.pending_revisions,
    )
    if not info.pending_revisions:
        logger.info("Database schema is current (revision=%s)", _revision_label(info.current_revision))
        return info

    logger.info(
        "Database migration starting current=%s target=%s pending=%s",
        _revision_label(info.current_revision),
        _revision_label(info.target_revision),
        info.pending_revisions,
    )
    cfg = _alembic_config()
    cfg.attributes["connection"] = connection
    try:
        command.upgrade(cfg, "head")
    except Exception as exc:
        logger.error(
            "Database migration failed current=%s target=%s pending=%s: %s",
            _revision_label(info.current_revision),
            _revision_label(info.target_revision),
            info.pending_revisions,
            exc,
        )
        raise MigrationExecutionError(str(exc), info) from exc
    info.current_revision_after = MigrationContext.configure(connection).get_current_revision() or ""
    logger.info(
        "Database migration completed from=%s to=%s applied=%s",
        _revision_label(info.current_revision),
        _revision_label(info.current_revision_after),
        info.pending_revisions,
    )
    return info


async def _run_migrations(database_url: str) -> MigrationInfo:
    engine = create_async_engine(_async_database_url(database_url), poolclass=NullPool)
    info = MigrationInfo()
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT pg_advisory_lock(:lock_id)"), {"lock_id": _MIGRATION_LOCK_ID})
            await connection.commit()
            try:
                _set_bootstrap_state("migrating", error="")
                info = await connection.run_sync(_upgrade_with_connection)
            except IncompatibleSchemaError:
                raise
            except MigrationExecutionError:
                raise
            except Exception as exc:
                raise MigrationExecutionError(str(exc), info) from exc
            finally:
                try:
                    await connection.execute(
                        text("SELECT pg_advisory_unlock(:lock_id)"),
                        {"lock_id": _MIGRATION_LOCK_ID},
                    )
                    await connection.commit()
                except Exception as exc:
                    logger.warning("Failed to release migration advisory lock: %s", exc)
            return info
    finally:
        await engine.dispose()


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def init_db() -> None:
    database_url = require_database_url()
    backoff = 1.0
    attempt = 0
    while True:
        attempt += 1
        result = await _attempt_init(database_url, attempt)
        if result == "ready" or result == "blocked":
            return
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 30.0)


async def _attempt_init(database_url: str, attempt: int) -> str:
    global _pool
    target = ""
    try:
        target = _target_revision(_script_directory(_alembic_config()))
    except Exception:
        logger.exception("Failed to inspect Alembic migration target")
    _set_bootstrap_state("waiting_database", target_revision=target, error="", attempt=attempt)
    try:
        info = await _run_migrations(database_url)
        _pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5, init=_init_connection)
        await _ensure_default_storage_config()
        current = info.current_revision_after or info.current_revision
        _set_bootstrap_state(
            "ready",
            current_revision=current,
            target_revision=info.target_revision,
            pending_revisions=[],
            attempt=attempt,
        )
        logger.info("Database ready (attempt %d)", attempt)
        return "ready"
    except IncompatibleSchemaError as exc:
        _set_bootstrap_state(
            "incompatible_schema",
            current_revision=exc.info.current_revision,
            target_revision=exc.info.target_revision or target,
            pending_revisions=[],
            error=str(exc),
            attempt=attempt,
        )
        logger.error("Database schema is incompatible with this application version: %s", exc)
        return "blocked"
    except MigrationExecutionError as exc:
        _set_bootstrap_state(
            "migration_failed",
            current_revision=exc.info.current_revision,
            target_revision=exc.info.target_revision or target,
            pending_revisions=exc.info.pending_revisions,
            error=str(exc),
            attempt=attempt,
        )
        logger.error(
            "Database migration failed current=%s target=%s pending=%s: %s",
            _revision_label(exc.info.current_revision),
            _revision_label(exc.info.target_revision or target),
            exc.info.pending_revisions,
            exc,
        )
        return "blocked"
    except Exception as exc:
        _set_bootstrap_state("waiting_database", target_revision=target, error=str(exc), attempt=attempt)
        logger.warning("Waiting for database (attempt %d): %s", attempt, exc)
        if _pool:
            await _pool.close()
            _pool = None
        return "retry"


def _default_s3_storage_config() -> dict[str, Any] | None:
    if not config.BUNDLED_S3_STORAGE_BOOTSTRAP:
        return None
    missing = [
        key
        for key, value in {
            "BUNDLED_S3_ACCESS_KEY": config.BUNDLED_S3_ACCESS_KEY,
            "BUNDLED_S3_SECRET_KEY": config.BUNDLED_S3_SECRET_KEY,
            "BUNDLED_S3_BUCKET": config.BUNDLED_S3_BUCKET,
        }.items()
        if not value
    ]
    if missing:
        logger.warning("Skipping default S3 storage bootstrap; missing %s", missing)
        return None
    return {
        "storage": "s3",
        "s3Bucket": config.BUNDLED_S3_BUCKET,
        "s3Region": config.BUNDLED_S3_REGION,
        "s3AccessKey": config.BUNDLED_S3_ACCESS_KEY,
        "s3SecretKey": config.BUNDLED_S3_SECRET_KEY,
        "s3Endpoint": config.BUNDLED_S3_ENDPOINT,
        "s3Presign": True,
        "s3PresignExpires": 3600,
    }


def _storage_config_needs_default_s3_repair(storage_config: dict[str, Any]) -> bool:
    if not storage_config:
        return True
    mode = storage_config.get("storage")
    if not mode:
        return True
    if mode == "builtin":
        return False
    if mode != "s3":
        return False
    required = (
        "s3Bucket",
        "s3Region",
        "s3AccessKey",
        "s3SecretKey",
        "s3Endpoint",
    )
    return any(not str(storage_config.get(field) or "").strip() for field in required)


async def _ensure_default_storage_config() -> None:
    if _pool is None:
        return
    storage_config = _default_s3_storage_config()
    if not storage_config:
        return
    try:
        row = await _pool.fetchrow(
            "SELECT value FROM app_state WHERE key = $1",
            "storage_config",
        )
        action = "initialized"
        if row:
            value = row.get("value") if hasattr(row, "get") else row["value"]
            existing = value if isinstance(value, dict) else json.loads(value or "{}")
            if not _storage_config_needs_default_s3_repair(existing):
                return
            action = "repaired"
        await _pool.execute(
            """INSERT INTO app_state (key, value) VALUES ($1, $2)
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
            "storage_config",
            json.dumps(storage_config, ensure_ascii=False),
        )
        logger.info(
            "Default S3 storage config %s bucket=%s endpoint=%s",
            action,
            storage_config["s3Bucket"],
            storage_config["s3Endpoint"],
        )
    except Exception as exc:
        logger.warning("Failed to initialize default S3 storage config: %s", exc)


async def close_db() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("Database pool closed")


def is_ready() -> bool:
    return _pool is not None and _bootstrap_state.status == "ready"


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialized — call init_db() first")
    return _pool
