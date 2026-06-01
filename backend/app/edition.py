from __future__ import annotations

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


def register_ee(app: FastAPI) -> None:
    if EDITION != "ee":
        return
    ensure_project_root_importable()
    from ee.backend import register_routes, register_middleware
    register_middleware(app)
    register_routes(app)
    logger.info("EE edition loaded")


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
    hooks = _load_ee_hooks()
    checker = getattr(hooks, "assert_tenant_runtime_allowed", None) if hooks else None
    if callable(checker):
        await checker(tenant_id, exclude_session_id=exclude_session_id)
