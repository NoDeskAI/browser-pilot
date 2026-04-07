from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.tools.browser.scripts import CLICK_ELEMENT_SCRIPT, OBSERVE_SCRIPT
from app.tools.browser.session import (
    KEY_MAP,
    browser_session,
    human_click_actions,
    human_key_actions,
    quick_observe,
    wd_fetch,
)

logger = logging.getLogger("routes.browser")
router = APIRouter()


class SessionBody(BaseModel):
    sessionId: str


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


# ---------------------------------------------------------------------------
# Navigate
# ---------------------------------------------------------------------------

@router.post("/api/browser/navigate")
async def api_navigate(body: NavigateBody):
    try:
        async with browser_session(body.sessionId) as (sid, base):
            await wd_fetch(
                f"/session/{sid}/url", "POST",
                {"url": body.url}, timeout=60, base_url=base,
            )
            await asyncio.sleep(1.5)
            page = await quick_observe(sid, base_url=base)
        return {"ok": True, "navigatedTo": body.url, "currentPage": page}
    except Exception as exc:
        logger.error("navigate failed: %s", exc)
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Current page info
# ---------------------------------------------------------------------------

@router.get("/api/browser/current")
async def api_current(sessionId: str = Query(...)):
    try:
        async with browser_session(sessionId) as (sid, base):
            url = await wd_fetch(f"/session/{sid}/url", timeout=5, base_url=base)
            title = await wd_fetch(f"/session/{sid}/title", timeout=5, base_url=base)
        return {"ok": True, "url": url, "title": title}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Observe
# ---------------------------------------------------------------------------

@router.post("/api/browser/observe")
async def api_observe(body: SessionBody):
    try:
        async with browser_session(body.sessionId) as (sid, base):
            result = await wd_fetch(
                f"/session/{sid}/execute/sync", "POST",
                {"script": OBSERVE_SCRIPT, "args": []},
                base_url=base,
            )
        return {"ok": True, **(result if isinstance(result, dict) else {})}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Click (coordinates)
# ---------------------------------------------------------------------------

@router.post("/api/browser/click")
async def api_click(body: ClickBody):
    try:
        async with browser_session(body.sessionId) as (sid, base):
            x, y = body.x, body.y
            handles_before = await wd_fetch(
                f"/session/{sid}/window/handles", timeout=5, base_url=base,
            ) or []
            await wd_fetch(f"/session/{sid}/actions", "POST", {
                "actions": [{
                    "type": "pointer", "id": "mouse",
                    "parameters": {"pointerType": "mouse"},
                    "actions": human_click_actions(x, y),
                }],
            }, base_url=base)
            await wd_fetch(f"/session/{sid}/actions", "DELETE", base_url=base)
            await asyncio.sleep(0.8)
            handles_after = await wd_fetch(
                f"/session/{sid}/window/handles", timeout=5, base_url=base,
            ) or []
            new_tab = len(handles_after) > len(handles_before)
            page = await quick_observe(sid, base_url=base)
        return {
            "ok": True,
            "clickedAt": {"x": x, "y": y},
            "newTabOpened": new_tab,
            "tabCount": len(handles_after),
            "currentPage": page,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Click element (CSS selector)
# ---------------------------------------------------------------------------

@router.post("/api/browser/click-element")
async def api_click_element(body: ClickElementBody):
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
                return {"ok": False, "error": f'Element "{body.selector}" not found'}
            await asyncio.sleep(0.8)
            handles_after = await wd_fetch(
                f"/session/{sid}/window/handles", timeout=5, base_url=base,
            ) or []
            new_tab = len(handles_after) > len(handles_before)
            page = await quick_observe(sid, base_url=base)
        return {
            "ok": True,
            "selector": body.selector,
            "clicked": click_result,
            "newTabOpened": new_tab,
            "tabCount": len(handles_after),
            "currentPage": page,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Type text
# ---------------------------------------------------------------------------

@router.post("/api/browser/type")
async def api_type(body: TypeBody):
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
        return {"ok": True, "typed": body.text, "currentPage": page}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Key press
# ---------------------------------------------------------------------------

@router.post("/api/browser/key")
async def api_key(body: KeyBody):
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
        return {"ok": True, "key": body.key, "currentPage": page}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Scroll
# ---------------------------------------------------------------------------

@router.post("/api/browser/scroll")
async def api_scroll(body: ScrollBody):
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
        return {"ok": True, "deltaX": body.deltaX, "deltaY": body.deltaY}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# List tabs
# ---------------------------------------------------------------------------

@router.get("/api/browser/tabs")
async def api_tabs(sessionId: str = Query(...)):
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
        return {"ok": True, "tabs": tabs, "count": len(tabs)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Switch tab
# ---------------------------------------------------------------------------

@router.post("/api/browser/switch-tab")
async def api_switch_tab(body: SwitchTabBody):
    try:
        async with browser_session(body.sessionId) as (sid, base):
            handles = await wd_fetch(
                f"/session/{sid}/window/handles", timeout=5, base_url=base,
            )
            if body.handle:
                if body.handle not in handles:
                    return {"ok": False, "error": f'Handle "{body.handle}" not found'}
                target = body.handle
            elif body.index is not None:
                idx = body.index if body.index >= 0 else len(handles) + body.index
                if idx < 0 or idx >= len(handles):
                    return {"ok": False, "error": f"Index {body.index} out of range ({len(handles)} tabs)"}
                target = handles[idx]
            else:
                return {"ok": False, "error": "Must provide handle or index"}

            if body.closeCurrent:
                await wd_fetch(f"/session/{sid}/window", "DELETE", timeout=5, base_url=base)
            await wd_fetch(
                f"/session/{sid}/window", "POST",
                {"handle": target}, timeout=5, base_url=base,
            )
            page = await quick_observe(sid, base_url=base)
        return {"ok": True, "switchedTo": target, "currentPage": page}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Screenshot
# ---------------------------------------------------------------------------

@router.get("/api/browser/screenshot")
async def api_screenshot(sessionId: str = Query(...)):
    try:
        async with browser_session(sessionId) as (sid, base):
            b64 = await wd_fetch(f"/session/{sid}/screenshot", base_url=base)
        return {"ok": True, "screenshot": b64}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
