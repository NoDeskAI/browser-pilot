from __future__ import annotations

import asyncio
import base64
import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app import agent_devices
from app.auth.dependencies import CurrentUser, get_session_aware_user, verify_session_access
from app.auto_name import maybe_auto_name
from app.container import ensure_localhost_bridge_for_url
from app.db import get_pool
from app.tools.browser.scripts import CLICK_ELEMENT_SCRIPT, OBSERVE_SCRIPT
from app.tools.browser.session import (
    KEY_MAP,
    browser_session,
    cdp_human_click,
    human_key_actions,
    quick_observe,
    wd_fetch,
)
from app.tools.vision.ui_detector import attach_dom_hints, build_mixed_candidates, ui_detector

logger = logging.getLogger("routes.browser")

VIEWPORT_METRICS_SCRIPT = """
return {
  width: window.innerWidth || document.documentElement.clientWidth || 0,
  height: window.innerHeight || document.documentElement.clientHeight || 0,
  devicePixelRatio: window.devicePixelRatio || 1
};
"""


async def _update_session_page(session_id: str, url: str | None, title: str | None) -> None:
    try:
        pool = get_pool()
        await pool.execute(
            "UPDATE sessions SET current_url = $1, current_title = $2, updated_at = NOW() WHERE id = $3",
            url, title, session_id,
        )
    except Exception:
        pass
router = APIRouter()


class SessionBody(BaseModel):
    sessionId: str
    mode: str = "dom"
    maxCandidates: int = Field(default=180, ge=1, le=500)
    threshold: float = Field(default=0.01, ge=0.0, le=1.0)
    includeScreenshot: bool = False
    includeAnnotatedScreenshot: bool = False


class NavigateBody(BaseModel):
    sessionId: str
    url: str


class ClickBody(BaseModel):
    sessionId: str
    x: int
    y: int


class ClickElementBody(BaseModel):
    sessionId: str
    selector: str


class TypeBody(BaseModel):
    sessionId: str
    text: str


class KeyBody(BaseModel):
    sessionId: str
    key: str


class ScrollBody(BaseModel):
    sessionId: str
    deltaY: int
    x: int = 640
    y: int = 360
    deltaX: int = 0


class SwitchTabBody(BaseModel):
    sessionId: str
    handle: str | None = None
    index: int | None = None
    closeCurrent: bool = False


def mix_needs_vision_fallback(result: object) -> bool:
    if not isinstance(result, dict):
        return True
    return not bool(result.get("elements"))


# ---------------------------------------------------------------------------
# Navigate
# ---------------------------------------------------------------------------

@router.post("/api/browser/navigate")
async def api_navigate(body: NavigateBody, user: CurrentUser = Depends(get_session_aware_user)):
    await verify_session_access(body.sessionId, user)
    ctx, rejected = await agent_devices.begin_compatible_action(
        body.sessionId, user, action="browser.navigate", side_effect_level="external"
    )
    if rejected:
        return rejected
    try:
        localhost_bridge = await ensure_localhost_bridge_for_url(body.sessionId, body.url)
        async with browser_session(body.sessionId) as (sid, base):
            await wd_fetch(
                f"/session/{sid}/url", "POST",
                {"url": body.url}, timeout=60, base_url=base,
            )
            await asyncio.sleep(1.5)
            page = await quick_observe(sid, base_url=base)
        asyncio.create_task(_update_session_page(body.sessionId, page.get("url"), page.get("title")))
        asyncio.create_task(maybe_auto_name(body.sessionId, page.get("url", ""), page.get("title", "")))
        return await agent_devices.complete_compatible_action(
            ctx,
            {"ok": True, "navigatedTo": body.url, "currentPage": page, "localhostBridge": localhost_bridge},
            summary=f"Navigated browser to {body.url}",
            details={"url": body.url},
        )
    except Exception as exc:
        logger.error("navigate failed: %s", exc)
        return await agent_devices.fail_compatible_action(ctx, str(exc))


# ---------------------------------------------------------------------------
# Current page info
# ---------------------------------------------------------------------------

@router.get("/api/browser/current")
async def api_current(sessionId: str = Query(...), user: CurrentUser = Depends(get_session_aware_user)):
    await verify_session_access(sessionId, user)
    ctx, rejected = await agent_devices.begin_compatible_action(
        sessionId, user, action="browser.current", side_effect_level="none"
    )
    if rejected:
        return rejected
    try:
        async with browser_session(sessionId) as (sid, base):
            url = await wd_fetch(f"/session/{sid}/url", timeout=5, base_url=base)
            title = await wd_fetch(f"/session/{sid}/title", timeout=5, base_url=base)
        return await agent_devices.complete_compatible_action(
            ctx,
            {"ok": True, "url": url, "title": title},
            summary="Read current browser page",
            retry_safety="safe",
        )
    except Exception as exc:
        return await agent_devices.fail_compatible_action(ctx, str(exc), retry_safety="safe")


# ---------------------------------------------------------------------------
# Observe
# ---------------------------------------------------------------------------

@router.post("/api/browser/observe")
async def api_observe(body: SessionBody, user: CurrentUser = Depends(get_session_aware_user)):
    await verify_session_access(body.sessionId, user)
    ctx, rejected = await agent_devices.begin_compatible_action(
        body.sessionId, user, action="browser.observe", side_effect_level="none"
    )
    if rejected:
        return rejected
    try:
        mode = (body.mode or "dom").strip().lower()
        if mode not in {"dom", "vision", "mix"}:
            return await agent_devices.complete_compatible_action(
                ctx,
                {"ok": False, "error": 'observe mode must be one of: "dom", "vision", "mix"'},
                status="failed",
                summary="Browser observe rejected invalid mode",
                retry_safety="safe",
                next_step="fix_request",
            )

        async with browser_session(body.sessionId) as (sid, base):
            result = {}
            screenshot_base64 = None
            viewport_metrics = None
            vision_result = None

            if mode in {"dom", "mix"}:
                result = await wd_fetch(
                    f"/session/{sid}/execute/sync", "POST",
                    {"script": OBSERVE_SCRIPT, "args": []},
                    base_url=base,
                )

            needs_vision = mode == "vision"
            if mode == "mix":
                needs_vision = mix_needs_vision_fallback(result)

            if needs_vision or body.includeScreenshot:
                screenshot_base64 = await wd_fetch(f"/session/{sid}/screenshot", base_url=base)
                viewport_metrics = await wd_fetch(
                    f"/session/{sid}/execute/sync", "POST",
                    {"script": VIEWPORT_METRICS_SCRIPT, "args": []},
                    timeout=5,
                    base_url=base,
                )

            if mode == "vision":
                url = await wd_fetch(f"/session/{sid}/url", timeout=5, base_url=base)
                title = await wd_fetch(f"/session/{sid}/title", timeout=5, base_url=base)
                vision_result = await asyncio.to_thread(
                    ui_detector.detect_base64,
                    screenshot_base64,
                    max_candidates=body.maxCandidates,
                    threshold=body.threshold,
                    include_annotated=body.includeAnnotatedScreenshot,
                    click_viewport=viewport_metrics,
                )
                result = {
                    "url": url,
                    "title": title,
                    "mode": mode,
                    "viewport": vision_result.viewport,
                    "visionCandidates": vision_result.candidates,
                    "visionGroups": vision_result.groups,
                    "visionFrame": vision_result.vision_frame,
                    "trace": vision_result.trace,
                }
                if vision_result.annotated_screenshot:
                    result["annotatedScreenshot"] = vision_result.annotated_screenshot

            if mode == "mix":
                elements = (result or {}).get("elements", []) if isinstance(result, dict) else []
                if needs_vision:
                    vision_result = await asyncio.to_thread(
                        ui_detector.detect_base64,
                        screenshot_base64,
                        max_candidates=body.maxCandidates,
                        threshold=body.threshold,
                        include_annotated=body.includeAnnotatedScreenshot,
                        click_viewport=viewport_metrics,
                    )
                    vision_candidates = attach_dom_hints(vision_result.candidates, elements)
                    mixed_candidates = build_mixed_candidates(
                        elements=elements,
                        vision_candidates=vision_candidates,
                        vision_groups=vision_result.groups,
                        max_candidates=body.maxCandidates,
                    )
                    result = {
                        **(result if isinstance(result, dict) else {}),
                        "mode": mode,
                        "viewport": vision_result.viewport,
                        "visionCandidates": vision_candidates,
                        "visionGroups": vision_result.groups,
                        "visionFrame": vision_result.vision_frame,
                        "mixedCandidates": mixed_candidates,
                        "trace": {
                            **vision_result.trace,
                            "mixed_count": len(mixed_candidates),
                            "mixed_vision_supplement_count": len(
                                [
                                    item
                                    for item in mixed_candidates
                                    if str(item.get("kind", "")).startswith("vision_supplement")
                                ]
                            ),
                            "mix_strategy": "dom_then_vision_fallback",
                            "vision_fallback_used": True,
                            "dom_element_count": len(elements),
                        },
                    }
                    if vision_result.annotated_screenshot:
                        result["annotatedScreenshot"] = vision_result.annotated_screenshot
                else:
                    mixed_candidates = build_mixed_candidates(
                        elements=elements,
                        vision_candidates=[],
                        vision_groups=[],
                        max_candidates=body.maxCandidates,
                    )
                    result = {
                        **(result if isinstance(result, dict) else {}),
                        "mode": mode,
                        "visionCandidates": [],
                        "visionGroups": [],
                        "mixedCandidates": mixed_candidates,
                        "trace": {
                            "mix_strategy": "dom_then_vision_fallback",
                            "vision_fallback_used": False,
                            "vision_skipped_reason": "dom_observe_succeeded",
                            "dom_element_count": len(elements),
                            "mixed_count": len(mixed_candidates),
                            "mixed_vision_supplement_count": 0,
                        },
                    }

            if mode == "dom" and isinstance(result, dict):
                result["mode"] = mode

            if body.includeScreenshot and screenshot_base64 and isinstance(result, dict):
                result["screenshot"] = screenshot_base64
        if isinstance(result, dict):
            asyncio.create_task(_update_session_page(body.sessionId, result.get("url"), result.get("title")))
        return await agent_devices.complete_compatible_action(
            ctx,
            {"ok": True, **(result if isinstance(result, dict) else {})},
            summary=f"Observed browser page with {mode} mode",
            retry_safety="safe",
        )
    except Exception as exc:
        return await agent_devices.fail_compatible_action(ctx, str(exc), retry_safety="safe")


# ---------------------------------------------------------------------------
# Click (coordinates)
# ---------------------------------------------------------------------------

@router.post("/api/browser/click")
async def api_click(body: ClickBody, user: CurrentUser = Depends(get_session_aware_user)):
    await verify_session_access(body.sessionId, user)
    ctx, rejected = await agent_devices.begin_compatible_action(
        body.sessionId, user, action="browser.click", side_effect_level="external"
    )
    if rejected:
        return rejected
    try:
        async with browser_session(body.sessionId) as (sid, base):
            x, y = body.x, body.y
            handles_before = await wd_fetch(
                f"/session/{sid}/window/handles", timeout=5, base_url=base,
            ) or []
            await cdp_human_click(sid, x, y, base_url=base)
            await asyncio.sleep(0.8)
            handles_after = await wd_fetch(
                f"/session/{sid}/window/handles", timeout=5, base_url=base,
            ) or []
            new_tab = len(handles_after) > len(handles_before)
            page = await quick_observe(sid, base_url=base)
        asyncio.create_task(_update_session_page(body.sessionId, page.get("url"), page.get("title")))
        return await agent_devices.complete_compatible_action(
            ctx,
            {
                "ok": True,
                "clickedAt": {"x": x, "y": y},
                "newTabOpened": new_tab,
                "tabCount": len(handles_after),
                "currentPage": page,
            },
            summary=f"Clicked browser coordinates {x},{y}",
            details={"x": x, "y": y, "newTabOpened": new_tab},
        )
    except Exception as exc:
        return await agent_devices.fail_compatible_action(ctx, str(exc))


# ---------------------------------------------------------------------------
# Click element (CSS selector)
# ---------------------------------------------------------------------------

@router.post("/api/browser/click-element")
async def api_click_element(body: ClickElementBody, user: CurrentUser = Depends(get_session_aware_user)):
    await verify_session_access(body.sessionId, user)
    ctx, rejected = await agent_devices.begin_compatible_action(
        body.sessionId, user, action="browser.click_element", side_effect_level="external"
    )
    if rejected:
        return rejected
    try:
        async with browser_session(body.sessionId) as (sid, base):
            handles_before = await wd_fetch(
                f"/session/{sid}/window/handles", timeout=5, base_url=base,
            ) or []
            click_result = await wd_fetch(
                f"/session/{sid}/execute/sync", "POST",
                {"script": CLICK_ELEMENT_SCRIPT, "args": [body.selector]},
                base_url=base,
            )
            if not (click_result or {}).get("found"):
                return await agent_devices.complete_compatible_action(
                    ctx,
                    {"ok": False, "error": f'Element "{body.selector}" not found'},
                    status="failed",
                    summary=f'Element "{body.selector}" not found',
                    details={"selector": body.selector},
                    retry_safety="safe",
                    next_step="observe",
                )
            await asyncio.sleep(0.8)
            handles_after = await wd_fetch(
                f"/session/{sid}/window/handles", timeout=5, base_url=base,
            ) or []
            new_tab = len(handles_after) > len(handles_before)
            page = await quick_observe(sid, base_url=base)
        asyncio.create_task(_update_session_page(body.sessionId, page.get("url"), page.get("title")))
        return await agent_devices.complete_compatible_action(
            ctx,
            {
                "ok": True,
                "selector": body.selector,
                "clicked": click_result,
                "newTabOpened": new_tab,
                "tabCount": len(handles_after),
                "currentPage": page,
            },
            summary=f'Clicked browser element "{body.selector}"',
            details={"selector": body.selector, "newTabOpened": new_tab},
        )
    except Exception as exc:
        return await agent_devices.fail_compatible_action(ctx, str(exc))


# ---------------------------------------------------------------------------
# Type text
# ---------------------------------------------------------------------------

@router.post("/api/browser/type")
async def api_type(body: TypeBody, user: CurrentUser = Depends(get_session_aware_user)):
    await verify_session_access(body.sessionId, user)
    ctx, rejected = await agent_devices.begin_compatible_action(
        body.sessionId, user, action="browser.type", side_effect_level="external"
    )
    if rejected:
        return rejected
    try:
        async with browser_session(body.sessionId) as (sid, base):
            await wd_fetch(f"/session/{sid}/actions", "POST", {
                "actions": [{
                    "type": "key", "id": "keyboard",
                    "actions": human_key_actions(body.text),
                }],
            }, base_url=base)
            await wd_fetch(f"/session/{sid}/actions", "DELETE", base_url=base)
            page = await quick_observe(sid, base_url=base)
        asyncio.create_task(_update_session_page(body.sessionId, page.get("url"), page.get("title")))
        return await agent_devices.complete_compatible_action(
            ctx,
            {"ok": True, "typed": body.text, "currentPage": page},
            summary="Typed text into browser",
            details={"textLength": len(body.text)},
        )
    except Exception as exc:
        return await agent_devices.fail_compatible_action(ctx, str(exc))


# ---------------------------------------------------------------------------
# Key press
# ---------------------------------------------------------------------------

@router.post("/api/browser/key")
async def api_key(body: KeyBody, user: CurrentUser = Depends(get_session_aware_user)):
    await verify_session_access(body.sessionId, user)
    ctx, rejected = await agent_devices.begin_compatible_action(
        body.sessionId, user, action="browser.key", side_effect_level="external"
    )
    if rejected:
        return rejected
    try:
        async with browser_session(body.sessionId) as (sid, base):
            key_value = KEY_MAP.get(body.key, body.key)
            await wd_fetch(f"/session/{sid}/actions", "POST", {
                "actions": [{
                    "type": "key", "id": "keyboard",
                    "actions": [
                        {"type": "keyDown", "value": key_value},
                        {"type": "keyUp", "value": key_value},
                    ],
                }],
            }, base_url=base)
            await wd_fetch(f"/session/{sid}/actions", "DELETE", base_url=base)
            await asyncio.sleep(0.5)
            page = await quick_observe(sid, base_url=base)
        asyncio.create_task(_update_session_page(body.sessionId, page.get("url"), page.get("title")))
        return await agent_devices.complete_compatible_action(
            ctx,
            {"ok": True, "key": body.key, "currentPage": page},
            summary=f"Pressed browser key {body.key}",
            details={"key": body.key},
        )
    except Exception as exc:
        return await agent_devices.fail_compatible_action(ctx, str(exc))


# ---------------------------------------------------------------------------
# Scroll
# ---------------------------------------------------------------------------

@router.post("/api/browser/scroll")
async def api_scroll(body: ScrollBody, user: CurrentUser = Depends(get_session_aware_user)):
    await verify_session_access(body.sessionId, user)
    ctx, rejected = await agent_devices.begin_compatible_action(
        body.sessionId, user, action="browser.scroll", side_effect_level="external"
    )
    if rejected:
        return rejected
    try:
        async with browser_session(body.sessionId) as (sid, base):
            await wd_fetch(f"/session/{sid}/actions", "POST", {
                "actions": [{
                    "type": "wheel", "id": "wheel",
                    "actions": [{
                        "type": "scroll",
                        "x": body.x,
                        "y": body.y,
                        "deltaX": body.deltaX,
                        "deltaY": body.deltaY,
                        "duration": 100,
                        "origin": "viewport",
                    }],
                }],
            }, base_url=base)
            await wd_fetch(f"/session/{sid}/actions", "DELETE", base_url=base)
        return await agent_devices.complete_compatible_action(
            ctx,
            {"ok": True, "deltaX": body.deltaX, "deltaY": body.deltaY},
            summary=f"Scrolled browser by {body.deltaX},{body.deltaY}",
            details={"deltaX": body.deltaX, "deltaY": body.deltaY},
        )
    except Exception as exc:
        return await agent_devices.fail_compatible_action(ctx, str(exc))


# ---------------------------------------------------------------------------
# List tabs
# ---------------------------------------------------------------------------

@router.get("/api/browser/tabs")
async def api_tabs(sessionId: str = Query(...), user: CurrentUser = Depends(get_session_aware_user)):
    await verify_session_access(sessionId, user)
    ctx, rejected = await agent_devices.begin_compatible_action(
        sessionId, user, action="browser.tabs", side_effect_level="none"
    )
    if rejected:
        return rejected
    try:
        async with browser_session(sessionId) as (sid, base):
            handles = await wd_fetch(
                f"/session/{sid}/window/handles", timeout=5, base_url=base,
            )
            current_handle = await wd_fetch(
                f"/session/{sid}/window", timeout=5, base_url=base,
            )
            tabs = []
            for h in handles:
                await wd_fetch(
                    f"/session/{sid}/window", "POST",
                    {"handle": h}, timeout=5, base_url=base,
                )
                url = await wd_fetch(f"/session/{sid}/url", timeout=5, base_url=base)
                title = await wd_fetch(f"/session/{sid}/title", timeout=5, base_url=base)
                tabs.append({"handle": h, "url": url, "title": title, "active": h == current_handle})
            await wd_fetch(
                f"/session/{sid}/window", "POST",
                {"handle": current_handle}, timeout=5, base_url=base,
            )
        return await agent_devices.complete_compatible_action(
            ctx,
            {"ok": True, "tabs": tabs, "count": len(tabs)},
            summary=f"Listed {len(tabs)} browser tabs",
            retry_safety="safe",
        )
    except Exception as exc:
        return await agent_devices.fail_compatible_action(ctx, str(exc), retry_safety="safe")


# ---------------------------------------------------------------------------
# Switch tab
# ---------------------------------------------------------------------------

@router.post("/api/browser/switch-tab")
async def api_switch_tab(body: SwitchTabBody, user: CurrentUser = Depends(get_session_aware_user)):
    await verify_session_access(body.sessionId, user)
    ctx, rejected = await agent_devices.begin_compatible_action(
        body.sessionId, user, action="browser.switch_tab", side_effect_level="external"
    )
    if rejected:
        return rejected
    try:
        async with browser_session(body.sessionId) as (sid, base):
            handles = await wd_fetch(
                f"/session/{sid}/window/handles", timeout=5, base_url=base,
            )
            if body.handle:
                if body.handle not in handles:
                    return await agent_devices.complete_compatible_action(
                        ctx,
                        {"ok": False, "error": f'Handle "{body.handle}" not found'},
                        status="failed",
                        summary=f'Browser tab handle "{body.handle}" not found',
                        retry_safety="safe",
                        next_step="tabs",
                    )
                target = body.handle
            elif body.index is not None:
                idx = body.index if body.index >= 0 else len(handles) + body.index
                if idx < 0 or idx >= len(handles):
                    return await agent_devices.complete_compatible_action(
                        ctx,
                        {"ok": False, "error": f"Index {body.index} out of range ({len(handles)} tabs)"},
                        status="failed",
                        summary=f"Browser tab index {body.index} out of range",
                        retry_safety="safe",
                        next_step="tabs",
                    )
                target = handles[idx]
            else:
                return await agent_devices.complete_compatible_action(
                    ctx,
                    {"ok": False, "error": "Must provide handle or index"},
                    status="failed",
                    summary="Browser tab switch missing target",
                    retry_safety="safe",
                    next_step="fix_request",
                )

            if body.closeCurrent:
                await wd_fetch(f"/session/{sid}/window", "DELETE", timeout=5, base_url=base)
            await wd_fetch(
                f"/session/{sid}/window", "POST",
                {"handle": target}, timeout=5, base_url=base,
            )
            page = await quick_observe(sid, base_url=base)
        asyncio.create_task(_update_session_page(body.sessionId, page.get("url"), page.get("title")))
        return await agent_devices.complete_compatible_action(
            ctx,
            {"ok": True, "switchedTo": target, "currentPage": page},
            summary="Switched browser tab",
            details={"target": target, "closeCurrent": body.closeCurrent},
        )
    except Exception as exc:
        return await agent_devices.fail_compatible_action(ctx, str(exc))


# ---------------------------------------------------------------------------
# Screenshot
# ---------------------------------------------------------------------------

@router.get("/api/browser/screenshot")
async def api_screenshot(
    sessionId: str = Query(...),
    includeBase64: bool = Query(False),
    user: CurrentUser = Depends(get_session_aware_user),
):
    await verify_session_access(sessionId, user)
    ctx, rejected = await agent_devices.begin_compatible_action(
        sessionId, user, action="browser.screenshot", side_effect_level="internal"
    )
    if rejected:
        return rejected
    try:
        async with browser_session(sessionId) as (sid, base):
            b64 = await wd_fetch(f"/session/{sid}/screenshot", base_url=base)
        from app.file_service import save_bytes

        file = await save_bytes(
            session_id=sessionId,
            source="screenshot",
            data=base64.b64decode(b64),
            filename="screenshot.png",
            content_type="image/png",
        )
        return await agent_devices.complete_compatible_action(
            ctx,
            {"ok": True, "file": file, "screenshot": b64 if includeBase64 is True else None},
            summary="Captured browser screenshot",
            evidence_refs=[{"type": "session_file", "id": file.get("id"), "url": file.get("url")}],
            retry_safety="safe",
        )
    except Exception as exc:
        return await agent_devices.fail_compatible_action(ctx, str(exc), retry_safety="safe")
