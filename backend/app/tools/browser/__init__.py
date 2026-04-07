from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel, Field

from app.agent.types import Tool, ToolContext, build_tool
from app.tools.browser.scripts import CLICK_ELEMENT_SCRIPT, OBSERVE_SCRIPT
from app.tools.browser.session import (
    KEY_MAP,
    browser_session,
    human_click_actions,
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
    url: str = Field(description="要访问的完整 URL")

class EmptyInput(BaseModel):
    pass

class SwitchTabInput(BaseModel):
    handle: str | None = Field(default=None, description="目标标签页的 handle（从 browser_list_tabs 获取）")
    index: int | None = Field(default=None, description="目标标签页索引（0 为第一个，-1 为最后一个）")
    closeCurrent: bool = Field(default=False, description="切换前是否关闭当前标签页")

class ClickInput(BaseModel):
    x: int = Field(description="点击的 X 坐标")
    y: int = Field(description="点击的 Y 坐标")

class ClickElementInput(BaseModel):
    selector: str = Field(description='CSS 选择器，如 "#search-btn", "input[name=q]", "a.nav-link"')

class TypeInput(BaseModel):
    text: str = Field(description="要输入的文本")

class KeyInput(BaseModel):
    key: str = Field(description="按键名称，如 Enter、Tab、Escape")

class ScrollInput(BaseModel):
    x: int = Field(default=640, description="滚动起始 X 坐标")
    y: int = Field(default=360, description="滚动起始 Y 坐标")
    deltaX: int = Field(default=0, description="水平滚动量")
    deltaY: int = Field(description="垂直滚动量（正值向下）")


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
            await wd_fetch(f"/session/{sid}/actions", "POST", {
                "actions": [{
                    "type": "pointer", "id": "mouse",
                    "parameters": {"pointerType": "mouse"},
                    "actions": human_click_actions(x, y),
                }],
            }, base_url=base)
            await wd_fetch(f"/session/{sid}/actions", "DELETE", base_url=base)
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


browser_tools: list[Tool] = [
    build_tool(name="browser_navigate", description="在远程浏览器当前标签页中导航到指定 URL", input_schema=NavigateInput, execute=_navigate),
    build_tool(name="browser_list_tabs", description="列出远程浏览器当前打开的所有标签页，返回每个标签页的 handle 和基本信息", input_schema=EmptyInput, execute=_list_tabs, is_concurrency_safe=True),
    build_tool(name="browser_switch_tab", description="切换到指定标签页（通过 handle 或索引）。可选关闭当前标签页后再切换。", input_schema=SwitchTabInput, execute=_switch_tab),
    build_tool(name="browser_observe", description='观察当前页面：获取 URL、标题、可见文本、所有可见的交互元素（含 shadow DOM 和同源 iframe）及其坐标。用这个来"看"页面。', input_schema=EmptyInput, execute=_observe, is_concurrency_safe=True),
    build_tool(name="browser_click", description="在远程浏览器页面上点击指定坐标（可从 browser_observe 获取元素坐标）。如果点击导致新标签页打开，会在返回值中提示。", input_schema=ClickInput, execute=_click),
    build_tool(name="browser_click_element", description="通过 CSS 选择器查找并点击元素，支持 shadow DOM 和同源 iframe。如果点击导致新标签页打开，会在返回值中提示。", input_schema=ClickElementInput, execute=_click_element),
    build_tool(name="browser_type", description="在远程浏览器中输入文本（在当前聚焦的输入框中）", input_schema=TypeInput, execute=_type),
    build_tool(name="browser_key", description="在远程浏览器中按下键盘按键，如 Enter、Tab、Escape、Backspace 等", input_schema=KeyInput, execute=_key),
    build_tool(name="browser_scroll", description="在远程浏览器页面上滚动", input_schema=ScrollInput, execute=_scroll),
    build_tool(name="browser_get_page_info", description="获取远程浏览器当前页面的 URL 和标题", input_schema=EmptyInput, execute=_get_page_info, is_concurrency_safe=True),
]
