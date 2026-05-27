#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import base64
import json
import os
import uuid
from typing import Any

from aiohttp import web
from cloakbrowser import launch_persistent_context_async


SESSION_ID = "cloak-session"


class DriverState:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.context = None
        self.page = None
        self.cdp = None
        self.started = False
        self.handles = {}
        self.timeouts = {"script": 30000, "pageLoad": 60000, "implicit": 0}

    def reset_page_state(self) -> None:
        self.page = None
        self.cdp = None
        self.handles = {}

    def handle_for_page(self, page) -> str:
        for handle, known_page in self.handles.items():
            if known_page is page:
                return handle
        handle = f"page-{len(self.handles)}"
        self.handles[handle] = page
        return handle

    def open_pages(self) -> list[tuple[str, Any]]:
        if not self.context:
            return []
        for page in self.context.pages:
            if not page.is_closed():
                self.handle_for_page(page)
        return [(handle, page) for handle, page in self.handles.items() if not page.is_closed()]

    def remember_new_page(self, page) -> None:
        self.handle_for_page(page)
        self.page = page
        self.cdp = None

    async def ensure_started(self):
        async with self.lock:
            if self.started and self.page and not self.page.is_closed():
                return self.page

            width = int(os.getenv("SE_SCREEN_WIDTH", "1280") or "1280")
            height = int(os.getenv("SE_SCREEN_HEIGHT", "800") or "800")
            lang = os.getenv("BROWSER_LANG", "zh-CN") or "zh-CN"
            timezone = os.getenv("BROWSER_TIMEZONE") or None
            proxy = os.getenv("BROWSER_PROXY") or None
            user_agent = os.getenv("BROWSER_UA") or None
            seed = os.getenv("CLOAK_FINGERPRINT_SEED") or f"bp_{uuid.uuid4().hex[:24]}"
            profile_dir = os.getenv("CLOAK_PROFILE_DIR", "/home/seluser/chrome-data/cloak")
            os.makedirs(profile_dir, exist_ok=True)

            args = [
                f"--fingerprint={seed}",
                f"--window-size={width},{height}",
                "--remote-allow-origins=*",
            ]

            self.context = await launch_persistent_context_async(
                profile_dir,
                headless=False,
                humanize=True,
                human_preset=os.getenv("CLOAK_HUMAN_PRESET", "default"),
                proxy=proxy,
                locale=lang,
                timezone=timezone,
                user_agent=user_agent,
                viewport={"width": width, "height": height},
                args=args,
            )
            self.context.on("page", self.remember_new_page)
            pages = self.context.pages
            self.page = pages[0] if pages else await self.context.new_page()
            self.handle_for_page(self.page)
            self.cdp = await self.context.new_cdp_session(self.page)
            self.started = True
            return self.page

    async def active_page(self):
        page = await self.ensure_started()
        if page.is_closed():
            self.started = False
            return await self.ensure_started()
        return page

    async def active_cdp(self):
        await self.active_page()
        if self.cdp is None:
            self.cdp = await self.context.new_cdp_session(self.page)
        return self.cdp

    async def set_active_by_handle(self, handle: str) -> None:
        await self.ensure_started()
        pages = dict(self.open_pages())
        page = pages.get(handle)
        if page is None and handle.startswith("page-"):
            try:
                idx = int(handle.split("-", 1)[1])
            except (IndexError, ValueError) as exc:
                raise webdriver_bad_request("no such window", "Unknown window handle") from exc
            open_pages = [page for _, page in self.open_pages()]
            page = open_pages[idx] if 0 <= idx < len(open_pages) else None
        if page is None:
            raise webdriver_bad_request("no such window", "Unknown window handle")
        if page.is_closed():
            raise webdriver_bad_request("no such window", "Window handle not found")
        self.page = page
        self.cdp = await self.context.new_cdp_session(self.page)
        await self.page.bring_to_front()


state = DriverState()


@web.middleware
async def webdriver_error_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception as exc:
        return error(type(exc).__name__, str(exc), 500)


def ok(value: Any = None) -> web.Response:
    return web.json_response({"value": value})


def error(error_name: str, message: str, status: int = 500) -> web.Response:
    return web.json_response({"value": {"error": error_name, "message": message, "stacktrace": ""}}, status=status)


def webdriver_bad_request(error_name: str, message: str) -> web.HTTPBadRequest:
    return web.HTTPBadRequest(
        text=json.dumps({"value": {"error": error_name, "message": message, "stacktrace": ""}}),
        content_type="application/json",
    )


async def status(_request: web.Request) -> web.Response:
    session = {"sessionId": SESSION_ID} if state.started else None
    return ok({"ready": True, "message": "Browser Pilot Cloak driver ready", "nodes": [{"slots": [{"session": session}]}]})


async def create_session(_request: web.Request) -> web.Response:
    await state.ensure_started()
    return ok(
        {
            "sessionId": SESSION_ID,
            "capabilities": {
                "browserName": "chrome",
                "browserVersion": "cloak",
                "platformName": "linux",
                "acceptInsecureCerts": True,
                "setWindowRect": True,
            },
        }
    )


async def delete_session(_request: web.Request) -> web.Response:
    if state.context:
        await state.context.close()
    state.context = None
    state.reset_page_state()
    state.started = False
    return ok(None)


async def get_url(_request: web.Request) -> web.Response:
    page = await state.active_page()
    return ok(page.url)


async def set_url(request: web.Request) -> web.Response:
    body = await request.json()
    page = await state.active_page()
    await page.goto(body.get("url", "about:blank"), wait_until="domcontentloaded", timeout=60000)
    return ok(None)


async def refresh_page(_request: web.Request) -> web.Response:
    page = await state.active_page()
    await page.reload(wait_until="domcontentloaded", timeout=state.timeouts.get("pageLoad", 60000))
    return ok(None)


async def back_page(_request: web.Request) -> web.Response:
    page = await state.active_page()
    await page.go_back(wait_until="domcontentloaded", timeout=state.timeouts.get("pageLoad", 60000))
    return ok(None)


async def forward_page(_request: web.Request) -> web.Response:
    page = await state.active_page()
    await page.go_forward(wait_until="domcontentloaded", timeout=state.timeouts.get("pageLoad", 60000))
    return ok(None)


async def get_title(_request: web.Request) -> web.Response:
    page = await state.active_page()
    return ok(await page.title())


async def get_source(_request: web.Request) -> web.Response:
    page = await state.active_page()
    return ok(await page.content())


async def get_timeouts(_request: web.Request) -> web.Response:
    return ok(dict(state.timeouts))


async def set_timeouts(request: web.Request) -> web.Response:
    body = await request.json()
    for key in ("script", "pageLoad", "implicit"):
        if key in body and body[key] is not None:
            state.timeouts[key] = int(body[key])
    return ok(None)


async def get_cookies(_request: web.Request) -> web.Response:
    await state.ensure_started()
    return ok(await state.context.cookies())


async def add_cookie(request: web.Request) -> web.Response:
    body = await request.json()
    cookie = body.get("cookie") if isinstance(body, dict) else None
    if not isinstance(cookie, dict):
        return error("invalid argument", "Missing cookie", 400)
    await state.ensure_started()
    await state.context.add_cookies([cookie])
    return ok(None)


async def delete_all_cookies(_request: web.Request) -> web.Response:
    await state.ensure_started()
    await state.context.clear_cookies()
    return ok(None)


async def delete_cookie(request: web.Request) -> web.Response:
    name = request.match_info.get("name", "")
    await state.ensure_started()
    cookies = [cookie for cookie in await state.context.cookies() if cookie.get("name") != name]
    await state.context.clear_cookies()
    if cookies:
        await state.context.add_cookies(cookies)
    return ok(None)


async def execute_sync(request: web.Request) -> web.Response:
    body = await request.json()
    script = body.get("script") or ""
    args = body.get("args") or []
    page = await state.active_page()
    result = await page.evaluate(
        """([source, args]) => {
            const fn = new Function(...args.map((_, i) => `arg${i}`), source);
            return fn(...args);
        }""",
        [script, args],
    )
    return ok(result)


async def screenshot(_request: web.Request) -> web.Response:
    page = await state.active_page()
    data = await page.screenshot(type="png", full_page=False)
    return ok(base64.b64encode(data).decode("ascii"))


KEY_MAP = {
    "\ue007": "Enter",
    "\ue008": "Shift",
    "\ue009": "Control",
    "\ue00a": "Alt",
    "\ue00c": "Escape",
    "\ue003": "Backspace",
    "\ue004": "Tab",
    "\ue017": "ArrowDown",
    "\ue013": "ArrowUp",
    "\ue014": "ArrowRight",
    "\ue012": "ArrowLeft",
}


async def actions(request: web.Request) -> web.Response:
    body = await request.json()
    page = await state.active_page()
    for source in body.get("actions") or []:
        source_type = source.get("type")
        for action in source.get("actions") or []:
            typ = action.get("type")
            if typ == "pause":
                await asyncio.sleep(float(action.get("duration") or 0) / 1000)
            elif source_type == "key" and typ == "keyDown":
                value = KEY_MAP.get(action.get("value"), action.get("value", ""))
                if len(value) == 1:
                    await page.keyboard.insert_text(value)
                else:
                    await page.keyboard.press(value)
            elif source_type == "wheel" and typ == "scroll":
                await page.mouse.move(float(action.get("x", 0)), float(action.get("y", 0)))
                await page.mouse.wheel(float(action.get("deltaX", 0)), float(action.get("deltaY", 0)))
            elif source_type == "pointer" and typ == "pointerMove":
                await page.mouse.move(float(action.get("x", 0)), float(action.get("y", 0)))
            elif source_type == "pointer" and typ == "pointerDown":
                await page.mouse.down()
            elif source_type == "pointer" and typ == "pointerUp":
                await page.mouse.up()
    return ok(None)


async def release_actions(_request: web.Request) -> web.Response:
    return ok(None)


async def cdp_execute(request: web.Request) -> web.Response:
    body = await request.json()
    cmd = body.get("cmd")
    params = body.get("params") or {}
    if not cmd:
        return error("invalid argument", "Missing CDP cmd", 400)
    cdp = await state.active_cdp()
    result = await cdp.send(cmd, params)
    return ok(result)


async def window_handles(_request: web.Request) -> web.Response:
    await state.ensure_started()
    return ok([handle for handle, _ in state.open_pages()])


async def get_window(_request: web.Request) -> web.Response:
    await state.ensure_started()
    if state.page and not state.page.is_closed():
        return ok(state.handle_for_page(state.page))
    open_pages = state.open_pages()
    if not open_pages:
        state.page = await state.context.new_page()
        return ok(state.handle_for_page(state.page))
    state.page = open_pages[0][1]
    return ok(open_pages[0][0])


async def set_window(request: web.Request) -> web.Response:
    body = await request.json()
    await state.set_active_by_handle(body.get("handle", "page-0"))
    return ok(None)


async def close_window(_request: web.Request) -> web.Response:
    page = await state.active_page()
    await page.close()
    open_pages = state.open_pages()
    if open_pages:
        state.page = open_pages[0][1]
    else:
        state.page = await state.context.new_page()
        state.handle_for_page(state.page)
    state.cdp = await state.context.new_cdp_session(state.page)
    return ok([handle for handle, _ in state.open_pages()])


async def current_window_rect(_request: web.Request) -> web.Response:
    width = int(os.getenv("SE_SCREEN_WIDTH", "1280") or "1280")
    height = int(os.getenv("SE_SCREEN_HEIGHT", "800") or "800")
    page = await state.active_page()
    viewport = page.viewport_size or {}
    width = int(viewport.get("width") or width)
    height = int(viewport.get("height") or height)
    return ok({"x": 0, "y": 0, "width": width, "height": height})


async def set_window_rect(request: web.Request) -> web.Response:
    body = await request.json()
    page = await state.active_page()
    width = int(body.get("width") or os.getenv("SE_SCREEN_WIDTH", "1280") or "1280")
    height = int(body.get("height") or os.getenv("SE_SCREEN_HEIGHT", "800") or "800")
    await page.set_viewport_size({"width": max(320, width), "height": max(240, height)})
    return await current_window_rect(request)


def build_app() -> web.Application:
    app = web.Application(middlewares=[webdriver_error_middleware])
    app.router.add_get("/status", status)
    app.router.add_post("/session", create_session)
    app.router.add_delete("/session/{sid}", delete_session)
    app.router.add_get("/session/{sid}/url", get_url)
    app.router.add_post("/session/{sid}/url", set_url)
    app.router.add_post("/session/{sid}/refresh", refresh_page)
    app.router.add_post("/session/{sid}/back", back_page)
    app.router.add_post("/session/{sid}/forward", forward_page)
    app.router.add_get("/session/{sid}/title", get_title)
    app.router.add_get("/session/{sid}/source", get_source)
    app.router.add_get("/session/{sid}/timeouts", get_timeouts)
    app.router.add_post("/session/{sid}/timeouts", set_timeouts)
    app.router.add_get("/session/{sid}/cookie", get_cookies)
    app.router.add_post("/session/{sid}/cookie", add_cookie)
    app.router.add_delete("/session/{sid}/cookie", delete_all_cookies)
    app.router.add_delete("/session/{sid}/cookie/{name}", delete_cookie)
    app.router.add_post("/session/{sid}/execute/sync", execute_sync)
    app.router.add_get("/session/{sid}/screenshot", screenshot)
    app.router.add_post("/session/{sid}/actions", actions)
    app.router.add_delete("/session/{sid}/actions", release_actions)
    app.router.add_post("/session/{sid}/goog/cdp/execute", cdp_execute)
    app.router.add_get("/session/{sid}/window/handles", window_handles)
    app.router.add_get("/session/{sid}/window", get_window)
    app.router.add_post("/session/{sid}/window", set_window)
    app.router.add_delete("/session/{sid}/window", close_window)
    app.router.add_get("/session/{sid}/window/rect", current_window_rect)
    app.router.add_post("/session/{sid}/window/rect", set_window_rect)
    return app


if __name__ == "__main__":
    web.run_app(build_app(), host="0.0.0.0", port=4444)
