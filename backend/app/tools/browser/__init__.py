from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel, Field

from app.agent.types import Tool, ToolContext, build_tool
from app.tools.browser.scripts import CLICK_ELEMENT_SCRIPT, OBSERVE_SCRIPT
from app.tools.browser.session import (
    KEY_MAP,
    browser_session,
    cdp_human_click,
    get_viewport_offset,
    human_key_actions,
    quick_observe,
    wd_fetch,
)

from urllib.parse import urlparse

logger = logging.getLogger("agent.browser")

_NO_SESSION = {"ok": False, "error": "No active session"}

_nav_history: dict[str, list[str]] = {}
_NAV_REPEAT_LIMIT = 2

_LOGIN_PATH_KEYWORDS = ("/user/", "/login", "/signin", "/auth/")
_QR_TEXT_KEYWORDS = ("扫码登录", "二维码", "QR")
_LOGIN_HINT = "当前页面是二维码登录页。如果用户要求的是展示二维码/到达登录页，任务已完成。立即停止操作，回复用户即可。"


def _maybe_add_login_hint(page: dict) -> dict:
    """If the page looks like a QR-code login page, append a hint for the LLM."""
    if not isinstance(page, dict):
        return page
    url = page.get("url", "")
    vt = page.get("visibleText", "")
    if any(k in url for k in _LOGIN_PATH_KEYWORDS) and any(k in vt for k in _QR_TEXT_KEYWORDS):
        page["hint"] = _LOGIN_HINT
    return page


class NavigateInput(BaseModel):
    url: str = Field(description="The full URL to navigate to")

class EmptyInput(BaseModel):
    pass

class SwitchTabInput(BaseModel):
    handle: str | None = Field(default=None, description="Target tab handle (from browser_list_tabs)")
    index: int | None = Field(default=None, description="Target tab index (0 = first, -1 = last)")
    closeCurrent: bool = Field(default=False, description="Whether to close the current tab before switching")

class ClickInput(BaseModel):
    x: int = Field(description="X coordinate to click")
    y: int = Field(description="Y coordinate to click")

class ClickElementInput(BaseModel):
    selector: str = Field(description='CSS selector, e.g. "#search-btn", "input[name=q]", "a.nav-link"')

class TypeInput(BaseModel):
    text: str = Field(description="Text to type")

class KeyInput(BaseModel):
    key: str = Field(description="Key name, e.g. Enter, Tab, Escape")

class ScrollInput(BaseModel):
    x: int = Field(default=640, description="Scroll origin X coordinate")
    y: int = Field(default=360, description="Scroll origin Y coordinate")
    deltaX: int = Field(default=0, description="Horizontal scroll amount")
    deltaY: int = Field(description="Vertical scroll amount (positive = down)")


async def _navigate(args: dict, ctx: ToolContext) -> dict:
    if not ctx.session_id:
        return _NO_SESSION
    target_url = args["url"]
    session_key = ctx.session_id

    history = _nav_history.setdefault(session_key, [])
    target_domain = urlparse(target_url).netloc
    recent_same = sum(1 for u in history[-_NAV_REPEAT_LIMIT:] if urlparse(u).netloc == target_domain)
    if recent_same >= _NAV_REPEAT_LIMIT:
        logger.warning("navigate blocked: %s already navigated to %s %d times", session_key, target_domain, recent_same)
        return {
            "ok": False,
            "error": f"已经导航到 {target_domain} {recent_same} 次，禁止重复导航。"
                     "页面已经在目标网站上。如果是登录页，请停止操作并告知用户手动登录。"
                     "使用 browser_observe 查看当前页面状态，不要再调用 browser_navigate。",
        }

    try:
        async with browser_session(ctx.session_id) as (sid, base):
            await wd_fetch(f"/session/{sid}/url", "POST", {"url": target_url}, timeout=60, base_url=base)
            await asyncio.sleep(1.5)
            page = await quick_observe(sid, base_url=base)
        history.append(target_url)
        if len(history) > 20:
            history[:] = history[-10:]
        return {"ok": True, "navigatedTo": target_url, "currentPage": _maybe_add_login_hint(page)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "hint": "Navigation failed. Selenium may be down. Try docker_start first."}


async def _list_tabs(args: dict, ctx: ToolContext) -> dict:
    if not ctx.session_id:
        return _NO_SESSION
    try:
        async with browser_session(ctx.session_id) as (sid, base):
            handles = await wd_fetch(f"/session/{sid}/window/handles", timeout=5, base_url=base)
            current_handle = await wd_fetch(f"/session/{sid}/window", timeout=5, base_url=base)
            tabs = []
            for h in handles:
                await wd_fetch(f"/session/{sid}/window", "POST", {"handle": h}, timeout=5, base_url=base)
                url = await wd_fetch(f"/session/{sid}/url", timeout=5, base_url=base)
                title = await wd_fetch(f"/session/{sid}/title", timeout=5, base_url=base)
                tabs.append({"handle": h, "url": url, "title": title, "active": h == current_handle})
            await wd_fetch(f"/session/{sid}/window", "POST", {"handle": current_handle}, timeout=5, base_url=base)
        return {"ok": True, "tabs": tabs, "count": len(tabs)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _switch_tab(args: dict, ctx: ToolContext) -> dict:
    if not ctx.session_id:
        return _NO_SESSION
    try:
        async with browser_session(ctx.session_id) as (sid, base):
            handles = await wd_fetch(f"/session/{sid}/window/handles", timeout=5, base_url=base)
            handle = args.get("handle")
            index = args.get("index")
            close_current = args.get("closeCurrent", False)

            if handle:
                if handle not in handles:
                    return {"ok": False, "error": f'Handle "{handle}" not found'}
                target = handle
            elif index is not None:
                idx = index if index >= 0 else len(handles) + index
                if idx < 0 or idx >= len(handles):
                    return {"ok": False, "error": f"Index {index} out of range ({len(handles)} tabs)"}
                target = handles[idx]
            else:
                return {"ok": False, "error": "Must provide handle or index"}

            if close_current:
                await wd_fetch(f"/session/{sid}/window", "DELETE", timeout=5, base_url=base)

            await wd_fetch(f"/session/{sid}/window", "POST", {"handle": target}, timeout=5, base_url=base)
            page = await quick_observe(sid, base_url=base)
        return {"ok": True, "switchedTo": target, "currentPage": _maybe_add_login_hint(page)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _observe(args: dict, ctx: ToolContext) -> dict:
    if not ctx.session_id:
        return _NO_SESSION
    try:
        async with browser_session(ctx.session_id) as (sid, base):
            result = await wd_fetch(f"/session/{sid}/execute/sync", "POST", {
                "script": OBSERVE_SCRIPT, "args": [],
            }, base_url=base)
            vp_offset = await get_viewport_offset(sid, base_url=base)
        if isinstance(result, dict):
            result["viewportOffset"] = vp_offset
        return _maybe_add_login_hint(result)
    except Exception as exc:
        return {"error": str(exc)}


async def _click(args: dict, ctx: ToolContext) -> dict:
    if not ctx.session_id:
        return _NO_SESSION
    try:
        async with browser_session(ctx.session_id) as (sid, base):
            x, y = args["x"], args["y"]
            handles_before = await wd_fetch(f"/session/{sid}/window/handles", timeout=5, base_url=base) or []
            await cdp_human_click(sid, x, y, base_url=base)
            await asyncio.sleep(0.8)
            handles_after = await wd_fetch(f"/session/{sid}/window/handles", timeout=5, base_url=base) or []
            new_tab = len(handles_after) > len(handles_before)
            if new_tab:
                logger.info("Click opened new tab (now %d tabs)", len(handles_after))
            page = await quick_observe(sid, base_url=base)
        return {"ok": True, "clickedAt": {"x": x, "y": y}, "newTabOpened": new_tab, "tabCount": len(handles_after), "currentPage": _maybe_add_login_hint(page)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _click_element(args: dict, ctx: ToolContext) -> dict:
    if not ctx.session_id:
        return _NO_SESSION
    selector = args["selector"]
    try:
        async with browser_session(ctx.session_id) as (sid, base):
            handles_before = await wd_fetch(f"/session/{sid}/window/handles", timeout=5, base_url=base) or []
            click_result = await wd_fetch(f"/session/{sid}/execute/sync", "POST", {
                "script": CLICK_ELEMENT_SCRIPT, "args": [selector],
            }, base_url=base)
            if not (click_result or {}).get("found"):
                raise RuntimeError(f'Element "{selector}" not found')
            await asyncio.sleep(0.8)
            handles_after = await wd_fetch(f"/session/{sid}/window/handles", timeout=5, base_url=base) or []
            new_tab = len(handles_after) > len(handles_before)
            if new_tab:
                logger.info("Click opened new tab (now %d tabs)", len(handles_after))
            page = await quick_observe(sid, base_url=base)
        return {"ok": True, "selector": selector, "clicked": click_result, "newTabOpened": new_tab, "tabCount": len(handles_after), "currentPage": _maybe_add_login_hint(page)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "hint": f'Element "{selector}" not found or not clickable'}


async def _type(args: dict, ctx: ToolContext) -> dict:
    if not ctx.session_id:
        return _NO_SESSION
    try:
        async with browser_session(ctx.session_id) as (sid, base):
            await wd_fetch(f"/session/{sid}/actions", "POST", {
                "actions": [{"type": "key", "id": "keyboard", "actions": human_key_actions(args["text"])}],
            }, base_url=base)
            await wd_fetch(f"/session/{sid}/actions", "DELETE", base_url=base)
            page = await quick_observe(sid, base_url=base)
        return {"ok": True, "typed": args["text"], "currentPage": page}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _key(args: dict, ctx: ToolContext) -> dict:
    if not ctx.session_id:
        return _NO_SESSION
    try:
        async with browser_session(ctx.session_id) as (sid, base):
            key_value = KEY_MAP.get(args["key"], args["key"])
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
        return {"ok": True, "key": args["key"], "currentPage": page}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _scroll(args: dict, ctx: ToolContext) -> dict:
    if not ctx.session_id:
        return _NO_SESSION
    try:
        async with browser_session(ctx.session_id) as (sid, base):
            await wd_fetch(f"/session/{sid}/actions", "POST", {
                "actions": [{
                    "type": "wheel", "id": "wheel",
                    "actions": [{
                        "type": "scroll",
                        "x": args.get("x", 640),
                        "y": args.get("y", 360),
                        "deltaX": args.get("deltaX", 0),
                        "deltaY": args["deltaY"],
                        "duration": 100,
                        "origin": "viewport",
                    }],
                }],
            }, base_url=base)
            await wd_fetch(f"/session/{sid}/actions", "DELETE", base_url=base)
        return {"ok": True, "deltaX": args.get("deltaX", 0), "deltaY": args["deltaY"]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _get_page_info(args: dict, ctx: ToolContext) -> dict:
    if not ctx.session_id:
        return _NO_SESSION
    try:
        async with browser_session(ctx.session_id) as (sid, base):
            url = await wd_fetch(f"/session/{sid}/url", base_url=base)
            title = await wd_fetch(f"/session/{sid}/title", base_url=base)
        return {"url": url, "title": title}
    except Exception as exc:
        return {"error": str(exc)}


async def _screenshot(args: dict, ctx: ToolContext) -> dict:
    if not ctx.session_id:
        return _NO_SESSION
    try:
        async with browser_session(ctx.session_id) as (sid, base):
            b64 = await wd_fetch(f"/session/{sid}/screenshot", base_url=base)
            page = await quick_observe(sid, base_url=base)
        from app.file_store import get_store

        store = await get_store()
        image_ref = await store.save(b64, ctx.session_id)
        return {"ok": True, "_image": image_ref, "currentPage": _maybe_add_login_hint(page)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


browser_tools: list[Tool] = [
    build_tool(name="browser_navigate", description="Navigate to a URL in the current tab of the remote browser", input_schema=NavigateInput, execute=_navigate),
    build_tool(name="browser_list_tabs", description="List all open tabs in the remote browser with handle and basic info for each", input_schema=EmptyInput, execute=_list_tabs, is_concurrency_safe=True),
    build_tool(name="browser_switch_tab", description="Switch to a tab by handle or index. Optionally close the current tab before switching.", input_schema=SwitchTabInput, execute=_switch_tab),
    build_tool(name="browser_observe", description='Observe the current page: get URL, title, visible text, and all visible interactive elements (including shadow DOM and same-origin iframes) with coordinates. Use this to "see" the page.', input_schema=EmptyInput, execute=_observe, is_concurrency_safe=True),
    build_tool(name="browser_click", description="Click at specific coordinates on the remote browser page (coordinates available from browser_observe). Reports if a new tab was opened.", input_schema=ClickInput, execute=_click),
    build_tool(name="browser_click_element", description="Find and click an element by CSS selector, supporting shadow DOM and same-origin iframes. Reports if a new tab was opened.", input_schema=ClickElementInput, execute=_click_element),
    build_tool(name="browser_type", description="Type text into the currently focused input field in the remote browser", input_schema=TypeInput, execute=_type),
    build_tool(name="browser_key", description="Press a keyboard key in the remote browser, e.g. Enter, Tab, Escape, Backspace", input_schema=KeyInput, execute=_key),
    build_tool(name="browser_scroll", description="Scroll the remote browser page", input_schema=ScrollInput, execute=_scroll),
    build_tool(name="browser_get_page_info", description="Get the current page URL and title of the remote browser", input_schema=EmptyInput, execute=_get_page_info, is_concurrency_safe=True),
    build_tool(
        name="browser_screenshot",
        description="Take a screenshot of the current browser page. Returns a visual image of what the user sees. Use as a fallback when observe-based actions fail or when you need to understand visual elements (images, charts, captchas, complex layouts) that DOM observation cannot capture.",
        input_schema=EmptyInput,
        execute=_screenshot,
        is_concurrency_safe=True,
    ),
]
