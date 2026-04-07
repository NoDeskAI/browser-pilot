from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.container import ensure_container_running
from app.tools.browser.scripts import OBSERVE_SCRIPT

logger = logging.getLogger("agent.browser")

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=30.0)
    return _client


@dataclass
class BrowserSession:
    wd_session_id: str | None = None
    selenium_base: str = ""
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_sessions: dict[str, BrowserSession] = {}


async def wd_fetch(
    url_path: str,
    method: str = "GET",
    body: Any = None,
    timeout: float = 30.0,
    *,
    base_url: str = "",
) -> Any:
    if not base_url:
        raise RuntimeError("wd_fetch requires base_url (no global SELENIUM_BASE)")
    client = _get_client()
    url = f"{base_url}{url_path}"
    kwargs: dict[str, Any] = {"headers": {"Content-Type": "application/json"}}
    if body is not None:
        kwargs["content"] = json.dumps(body)

    try:
        resp = await client.request(method, url, timeout=timeout, **kwargs)
        data = resp.json()
    except httpx.TimeoutException:
        raise RuntimeError(f"WebDriver timeout ({timeout}s): {url_path}")
    except Exception as exc:
        raise RuntimeError(f"WebDriver request failed: {exc}") from exc

    value = data.get("value", data)
    if isinstance(value, dict) and value.get("error"):
        raise RuntimeError(f"WebDriver {value['error']}: {value.get('message', '')}")
    return value


async def _cleanup_stale_session(base: str, sid: str) -> None:
    try:
        client = _get_client()
        await client.delete(f"{base}/session/{sid}", timeout=5)
        logger.info("Cleaned up stale session: %s", sid)
    except Exception:
        pass


async def _find_existing_session(base: str) -> str | None:
    try:
        client = _get_client()
        resp = await client.get(f"{base}/status", timeout=5)
        status = resp.json()
        for node in status.get("value", {}).get("nodes", []):
            for slot in node.get("slots", []):
                sid = (slot.get("session") or {}).get("sessionId")
                if sid:
                    return sid
    except Exception:
        pass
    return None


async def _cdp(sid: str, cmd: str, params: dict | None = None, *, base_url: str) -> None:
    await wd_fetch(f"/session/{sid}/goog/cdp/execute", "POST", {
        "cmd": cmd, "params": params or {},
    }, timeout=5, base_url=base_url)


async def _inject_stealth(sid: str, *, base_url: str) -> None:
    try:
        await _cdp(sid, "Page.addScriptToEvaluateOnNewDocument", {"source": STEALTH_SCRIPT}, base_url=base_url)
        logger.info("Stealth: addScriptToEvaluateOnNewDocument OK")
    except Exception as exc:
        logger.warning("Stealth: CDP inject failed (%s), will rely on execute_script", exc)

    try:
        await wd_fetch(f"/session/{sid}/execute/sync", "POST", {
            "script": f"return {STEALTH_SCRIPT}", "args": [],
        }, timeout=5, base_url=base_url)
    except Exception:
        pass

    try:
        ua_resp = await wd_fetch(f"/session/{sid}/execute/sync", "POST", {
            "script": "return navigator.userAgent", "args": [],
        }, timeout=5, base_url=base_url)
        if isinstance(ua_resp, str):
            clean_ua = (
                ua_resp
                .replace("HeadlessChrome", "Chrome")
                .replace("Headless", "")
            )
            if "Chrome/" not in clean_ua:
                clean_ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.7680.153 Safari/537.36"
            await _cdp(sid, "Network.setUserAgentOverride", {
                "userAgent": clean_ua,
                "acceptLanguage": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
                "platform": "Linux x86_64",
                "userAgentMetadata": {
                    "brands": [
                        {"brand": "Chromium", "version": "146"},
                        {"brand": "Google Chrome", "version": "146"},
                        {"brand": "Not=A?Brand", "version": "99"},
                    ],
                    "fullVersionList": [
                        {"brand": "Chromium", "version": "146.0.7680.153"},
                        {"brand": "Google Chrome", "version": "146.0.7680.153"},
                        {"brand": "Not=A?Brand", "version": "99.0.0.0"},
                    ],
                    "fullVersion": "146.0.7680.153",
                    "platform": "Linux",
                    "platformVersion": "6.5.0",
                    "architecture": "x86",
                    "model": "",
                    "mobile": False,
                    "bitness": "64",
                    "wow64": False,
                },
            }, base_url=base_url)
            logger.info("Stealth: UA + client hints cleaned")
    except Exception:
        pass

    try:
        await _cdp(sid, "Page.setBypassCSP", {"enabled": True}, base_url=base_url)
    except Exception:
        pass

    try:
        await _cdp(sid, "Emulation.setTimezoneOverride", {"timezoneId": "Asia/Shanghai"}, base_url=base_url)
        logger.info("Stealth: timezone -> Asia/Shanghai")
    except Exception:
        pass

    try:
        await _cdp(sid, "Page.addScriptToEvaluateOnNewDocument", {
            "source": (
                "Object.defineProperty(Navigator.prototype,'webdriver',"
                "{get:()=>false,configurable:true,enumerable:true});"
            ),
        }, base_url=base_url)
    except Exception:
        pass


async def _ensure_session_impl(bs: BrowserSession) -> str:
    base = bs.selenium_base

    if bs.wd_session_id:
        try:
            await wd_fetch(f"/session/{bs.wd_session_id}/url", timeout=5, base_url=base)
            return bs.wd_session_id
        except Exception:
            bs.wd_session_id = None

    existing = await _find_existing_session(base)
    if existing:
        logger.info("Found existing session on %s: %s, reusing", base, existing)
        bs.wd_session_id = existing
        try:
            await wd_fetch(f"/session/{bs.wd_session_id}/url", timeout=5, base_url=base)
            return bs.wd_session_id
        except Exception:
            logger.info("Existing session dead, cleaning up")
            await _cleanup_stale_session(base, existing)
            bs.wd_session_id = None

    logger.info("Creating WebDriver session (attach to existing Chrome) on %s...", base)
    client = _get_client()
    resp = await client.post(
        f"{base}/session",
        json={
            "capabilities": {
                "alwaysMatch": {
                    "browserName": "chrome",
                    "goog:chromeOptions": {
                        "debuggerAddress": "localhost:9222",
                    },
                },
            },
        },
        timeout=15,
    )
    data = resp.json()
    bs.wd_session_id = data["value"]["sessionId"]
    logger.info("WebDriver session attached: %s", bs.wd_session_id)
    return bs.wd_session_id


async def ensure_session(chat_session_id: str) -> tuple[str, str]:
    """Returns (webdriver_session_id, selenium_base_url)."""
    if chat_session_id not in _sessions:
        _sessions[chat_session_id] = BrowserSession()

    bs = _sessions[chat_session_id]

    ports = await ensure_container_running(chat_session_id)
    bs.selenium_base = f"http://localhost:{ports['selenium_port']}"

    async with bs.lock:
        sid = await _ensure_session_impl(bs)
    return sid, bs.selenium_base


async def switch_to_latest_tab(sid: str, *, base_url: str) -> None:
    try:
        handles = await wd_fetch(f"/session/{sid}/window/handles", timeout=5, base_url=base_url)
        if handles and len(handles) > 1:
            current = await wd_fetch(f"/session/{sid}/window", timeout=5, base_url=base_url)
            latest = handles[-1]
            if current != latest:
                logger.info("Switching from tab %s to latest tab %s (%d tabs open)", current, latest, len(handles))
                await wd_fetch(f"/session/{sid}/window", "POST", {"handle": latest}, timeout=5, base_url=base_url)
    except Exception as exc:
        logger.warning("switchToLatestTab failed: %s", exc)


async def close_other_tabs(sid: str, *, base_url: str) -> None:
    try:
        handles = await wd_fetch(f"/session/{sid}/window/handles", timeout=5, base_url=base_url)
        if handles and len(handles) > 1:
            current = await wd_fetch(f"/session/{sid}/window", timeout=5, base_url=base_url)
            for h in handles:
                if h != current:
                    await wd_fetch(f"/session/{sid}/window", "POST", {"handle": h}, timeout=5, base_url=base_url)
                    await wd_fetch(f"/session/{sid}/window", "DELETE", timeout=5, base_url=base_url)
            await wd_fetch(f"/session/{sid}/window", "POST", {"handle": current}, timeout=5, base_url=base_url)
            logger.info("Closed %d extra tab(s)", len(handles) - 1)
    except Exception as exc:
        logger.warning("closeOtherTabs failed: %s", exc)


async def quick_observe(sid: str, *, base_url: str) -> dict:
    try:
        result = await wd_fetch(f"/session/{sid}/execute/sync", "POST", {
            "script": OBSERVE_SCRIPT,
            "args": [],
        }, timeout=10, base_url=base_url)
        return {
            "url": (result or {}).get("url", ""),
            "title": (result or {}).get("title", ""),
            "elementCount": len((result or {}).get("elements", [])),
        }
    except Exception:
        return {"url": "(observe failed)", "title": "", "elementCount": 0}


def human_key_actions(text: str) -> list[dict]:
    actions: list[dict] = []
    for ch in text:
        if actions:
            actions.append({"type": "pause", "duration": 30 + random.randint(0, 90)})
        actions.append({"type": "keyDown", "value": ch})
        actions.append({"type": "pause", "duration": 8 + random.randint(0, 25)})
        actions.append({"type": "keyUp", "value": ch})
    return actions


def human_click_actions(x: int, y: int) -> list[dict]:
    steps = 3 + random.randint(0, 3)
    start_x = x + random.randint(-60, 60)
    start_y = y + random.randint(-60, 60)
    cp_x = (start_x + x) / 2 + random.randint(-20, 20)
    cp_y = (start_y + y) / 2 + random.randint(-20, 20)

    actions: list[dict] = []
    for i in range(steps + 1):
        t = i / steps
        px = (1 - t) ** 2 * start_x + 2 * (1 - t) * t * cp_x + t**2 * x
        py = (1 - t) ** 2 * start_y + 2 * (1 - t) * t * cp_y + t**2 * y
        actions.append({
            "type": "pointerMove",
            "duration": 15 + random.randint(0, 35),
            "x": max(0, round(px)),
            "y": max(0, round(py)),
            "origin": "viewport",
        })
    actions.append({
        "type": "pointerMove", "duration": 10,
        "x": x + random.randint(-1, 1),
        "y": y + random.randint(-1, 1),
        "origin": "viewport",
    })
    actions.append({"type": "pause", "duration": 15 + random.randint(0, 50)})
    actions.append({"type": "pointerDown", "button": 0})
    actions.append({"type": "pause", "duration": 30 + random.randint(0, 60)})
    actions.append({"type": "pointerUp", "button": 0})
    return actions


async def cleanup_session(chat_session_id: str) -> None:
    """Delete the WebDriver session to release ChromeDriver from the container."""
    bs = _sessions.get(chat_session_id)
    if not bs:
        return

    sid = bs.wd_session_id
    base = bs.selenium_base
    if sid and base:
        try:
            client = _get_client()
            await client.delete(f"{base}/session/{sid}", timeout=5)
            logger.info("Cleaned up WebDriver session %s for %s", sid, chat_session_id)
        except Exception as exc:
            logger.debug("Session cleanup failed (may already be gone): %s", exc)
        bs.wd_session_id = None


class _BrowserSessionCtx:
    """Context manager: create session on enter, destroy on exit.

    Minimizes the time ChromeDriver holds a CDP connection to Chrome,
    preventing anti-bot systems from detecting the debugger.
    """

    __slots__ = ("_chat_id", "_sid", "_base")

    def __init__(self, chat_session_id: str):
        self._chat_id = chat_session_id
        self._sid = ""
        self._base = ""

    async def __aenter__(self) -> tuple[str, str]:
        self._sid, self._base = await ensure_session(self._chat_id)
        return self._sid, self._base

    async def __aexit__(self, *exc: object) -> None:
        await cleanup_session(self._chat_id)


def browser_session(chat_session_id: str) -> _BrowserSessionCtx:
    """Usage: async with browser_session(id) as (sid, base): ..."""
    return _BrowserSessionCtx(chat_session_id)


KEY_MAP: dict[str, str] = {
    "Enter": "\uE007", "Tab": "\uE004", "Escape": "\uE00C", "Backspace": "\uE003",
    "Delete": "\uE017", "Space": "\uE00D",
    "ArrowUp": "\uE013", "ArrowDown": "\uE014", "ArrowLeft": "\uE012", "ArrowRight": "\uE011",
    "Home": "\uE011", "End": "\uE010", "PageUp": "\uE00E", "PageDown": "\uE00F",
}
