from __future__ import annotations

import inspect
import logging
from typing import Any

from fastapi import FastAPI

from app.config import EDITION, ensure_project_root_importable

logger = logging.getLogger("edition")


def _load_ee_hooks():
    if EDITION != "ee":
        return None
    ensure_project_root_importable()
    try:
        from ee.backend import hooks
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if missing == "ee" or missing.startswith("ee."):
            logger.warning("EE hooks are unavailable: %s", missing)
            return None
        raise
    return hooks


async def _call_ee_hook(name: str, *args: Any, **kwargs: Any) -> Any:
    if EDITION != "ee":
        return None
    hooks = _load_ee_hooks()
    hook = getattr(hooks, name, None) if hooks else None
    if not callable(hook):
        return None
    result = hook(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def register_ee(app: FastAPI) -> None:
    if EDITION != "ee":
        return
    ensure_project_root_importable()
    from ee.backend import register_routes, register_middleware
    register_middleware(app)
    register_routes(app)
    logger.info("EE edition loaded")


async def start_ee_services(app: FastAPI) -> None:
    if EDITION != "ee":
        return
    ensure_project_root_importable()
    try:
        from ee.backend import start_services
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if missing == "ee" or missing.startswith("ee."):
            logger.warning("EE services are unavailable: %s", missing)
            return
        raise
    start_services(app)


async def stop_ee_services(app: FastAPI) -> None:
    if EDITION != "ee":
        return
    ensure_project_root_importable()
    try:
        from ee.backend import stop_services
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if missing == "ee" or missing.startswith("ee."):
            return
        raise
    result = stop_services(app)
    if inspect.isawaitable(result):
        await result


def ee_features() -> dict[str, Any]:
    hooks = _load_ee_hooks()
    if hooks is None:
        return {}
    features = getattr(hooks, "features", None)
    return dict(features()) if callable(features) else {}


def browser_images_enabled() -> bool:
    return ee_features().get("browserImages", True) is not False


def runtime_shell_commands_enabled() -> bool:
    return ee_features().get("runtimeShellTools", True) is not False


def reject_browser_images_api() -> None:
    hooks = _load_ee_hooks()
    reject = getattr(hooks, "reject_browser_images_api", None) if hooks else None
    if callable(reject):
        reject()


def reject_file_storage_api() -> None:
    hooks = _load_ee_hooks()
    reject = getattr(hooks, "reject_file_storage_api", None) if hooks else None
    if callable(reject):
        reject()


def reject_container_debug_api() -> None:
    hooks = _load_ee_hooks()
    reject = getattr(hooks, "reject_container_debug_api", None) if hooks else None
    if callable(reject):
        reject()


def reject_session_runtime_selection(body: Any) -> None:
    hooks = _load_ee_hooks()
    reject = getattr(hooks, "reject_session_runtime_selection", None) if hooks else None
    if callable(reject):
        reject(body)


async def assert_tenant_runtime_allowed(tenant_id: str, *, exclude_session_id: str | None = None) -> None:
    await _call_ee_hook("assert_tenant_runtime_allowed", tenant_id, exclude_session_id=exclude_session_id)


async def before_session_create(user: Any, body: Any) -> None:
    await _call_ee_hook("before_session_create", user, body)


async def after_session_created(user: Any, session_id: str, body: Any) -> None:
    await _call_ee_hook("after_session_created", user, session_id, body)


async def after_tenant_setup(*, tenant_id: str, user_id: str) -> None:
    await _call_ee_hook("after_tenant_setup", tenant_id=tenant_id, user_id=user_id)


async def before_session_runtime_start(user: Any, session_id: str, *, action: str) -> None:
    await _call_ee_hook("before_session_runtime_start", user, session_id, action=action)


async def after_session_runtime_started(user: Any, session_id: str, *, action: str) -> None:
    await _call_ee_hook("after_session_runtime_started", user, session_id, action=action)


async def after_session_runtime_start_failed(user: Any, session_id: str, *, action: str, error: Any) -> None:
    await _call_ee_hook("after_session_runtime_start_failed", user, session_id, action=action, error=error)


async def after_session_runtime_stopped(user: Any, session_id: str, *, action: str) -> None:
    await _call_ee_hook("after_session_runtime_stopped", user, session_id, action=action)


async def before_viewer_ticket_issue(user: Any, session_id: str, *, mode: str) -> None:
    await _call_ee_hook("before_viewer_ticket_issue", user, session_id, mode=mode)


async def after_viewer_stream(
    *,
    session_id: str,
    ticket: Any,
    outcome: str,
    duration_ms: int,
    bytes_from_viewer: int,
    bytes_to_viewer: int,
    audit_event_id: str | None,
    error: str | None,
) -> None:
    await _call_ee_hook(
        "after_viewer_stream",
        session_id=session_id,
        ticket=ticket,
        outcome=outcome,
        duration_ms=duration_ms,
        bytes_from_viewer=bytes_from_viewer,
        bytes_to_viewer=bytes_to_viewer,
        audit_event_id=audit_event_id,
        error=error,
    )


async def before_file_store(
    *,
    file_id: str,
    session_id: str,
    tenant_id: str | None,
    user_id: str | None,
    source: str,
    size_bytes: int,
) -> None:
    await _call_ee_hook(
        "before_file_store",
        file_id=file_id,
        session_id=session_id,
        tenant_id=tenant_id,
        user_id=user_id,
        source=source,
        size_bytes=size_bytes,
    )


async def after_file_stored(file: dict[str, Any]) -> None:
    await _call_ee_hook("after_file_stored", file)


async def after_file_store_failed(
    *,
    file_id: str,
    session_id: str,
    tenant_id: str | None,
    user_id: str | None,
    source: str,
    size_bytes: int,
    error: Any,
) -> None:
    await _call_ee_hook(
        "after_file_store_failed",
        file_id=file_id,
        session_id=session_id,
        tenant_id=tenant_id,
        user_id=user_id,
        source=source,
        size_bytes=size_bytes,
        error=error,
    )


async def after_file_deleted(
    *,
    file: Any,
    object_deleted: bool,
    record_deleted: bool,
) -> None:
    await _call_ee_hook(
        "after_file_deleted",
        file=file,
        object_deleted=object_deleted,
        record_deleted=record_deleted,
    )
