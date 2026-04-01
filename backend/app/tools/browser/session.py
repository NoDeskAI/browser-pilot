from __future__ import annotations

import asyncio
import json
import logging
import math
import random
from typing import Any

import httpx

from app.config import SELENIUM_BASE
from app.tools.browser.scripts import OBSERVE_SCRIPT, STEALTH_SCRIPT

logger = logging.getLogger("agent.browser")

_session_id: str | None = None
_session_lock: asyncio.Lock = asyncio.Lock()

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=30.0)
    return _client


async def wd_fetch(url_path: str, method: str = "GET", body: Any = None, timeout: float = 30.0) -> Any:
    client = _get_client()
    url = f"{SELENIUM_BASE}{url_path}"
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


async def _cleanup_stale_session(sid: str) -> None:
    try:
        client = _get_client()
        await client.delete(f"{SELENIUM_BASE}/session/{sid}", timeout=5)
        logger.info("Cleaned up stale session: %s", sid)
    except Exception:
        pass


async def _find_existing_session() -> str | None:
    try:
        client = _get_client()
        resp = await client.get(f"{SELENIUM_BASE}/status", timeout=5)
        status = resp.json()
        for node in status.get("value", {}).get("nodes", []):
            for slot in node.get("slots", []):
                sid = (slot.get("session") or {}).get("sessionId")
                if sid:
                    return sid
    except Exception:
        pass
    return None


async def _inject_stealth(sid: str) -> None:
    try:
        await wd_fetch(f"/session/{sid}/goog/cdp/execute", "POST", {
            "cmd": "Page.addScriptToEvaluateOnNewDocument",
            "params": {"source": STEALTH_SCRIPT},
        }, timeout=5)
        logger.info("Stealth: addScriptToEvaluateOnNewDocument OK")
    except Exception as exc:
        logger.warning("Stealth: CDP inject failed (%s), will rely on execute_script", exc)

    try:
        await wd_fetch(f"/session/{sid}/execute/sync", "POST", {
            "script": f"return {STEALTH_SCRIPT}",
            "args": [],
        }, timeout=5)
    except Exception:
        pass

    try:
        await wd_fetch(f"/session/{sid}/goog/cdp/execute", "POST", {
            "cmd": "Emulation.setTimezoneOverride",
            "params": {"timezoneId": "Asia/Shanghai"},
        }, timeout=5)
        logger.info("Stealth: timezone -> Asia/Shanghai")
    except Exception:
        pass


async def _ensure_session_impl() -> str:
    global _session_id

    if _session_id:
        try:
            await wd_fetch(f"/session/{_session_id}/url", timeout=5)
            return _session_id
        except Exception:
            _session_id = None

    existing = await _find_existing_session()
    if existing:
        logger.info("Found existing session: %s, reusing it", existing)
        _session_id = existing
        try:
            await wd_fetch(f"/session/{_session_id}/url", timeout=5)
            return _session_id
        except Exception:
            logger.info("Existing session dead, cleaning up")
            await _cleanup_stale_session(existing)
            _session_id = None

    logger.info("Creating new WebDriver session...")
    client = _get_client()
    resp = await client.post(
        f"{SELENIUM_BASE}/session",
        json={
            "capabilities": {
                "alwaysMatch": {
                    "browserName": "chrome",
                    "goog:chromeOptions": {
                        "args": [
                            "--no-sandbox",
                            "--disable-dev-shm-usage",
                            "--disable-blink-features=AutomationControlled",
                            "--disable-infobars",
                            "--window-size=1280,800",
                            "--lang=zh-CN",
                        ],
                        "excludeSwitches": ["enable-automation"],
                        "useAutomationExtension": False,
                    },
                },
            },
        },
        timeout=15,
    )
    data = resp.json()
    _session_id = data["value"]["sessionId"]
    logger.info("WebDriver session created: %s", _session_id)

    try:
        await wd_fetch(f"/session/{_session_id}/window/rect", "POST", {
            "width": 1280, "height": 800,
        }, timeout=5)
    except Exception:
        pass

    await _inject_stealth(_session_id)
    return _session_id


async def ensure_session() -> str:
    async with _session_lock:
        return await _ensure_session_impl()


async def switch_to_latest_tab(sid: str) -> None:
    try:
        handles = await wd_fetch(f"/session/{sid}/window/handles", timeout=5)
        if handles and len(handles) > 1:
            current = await wd_fetch(f"/session/{sid}/window", timeout=5)
            latest = handles[-1]
            if current != latest:
                logger.info("Switching from tab %s to latest tab %s (%d tabs open)", current, latest, len(handles))
                await wd_fetch(f"/session/{sid}/window", "POST", {"handle": latest}, timeout=5)
    except Exception as exc:
        logger.warning("switchToLatestTab failed: %s", exc)


async def close_other_tabs(sid: str) -> None:
    try:
        handles = await wd_fetch(f"/session/{sid}/window/handles", timeout=5)
        if handles and len(handles) > 1:
            current = await wd_fetch(f"/session/{sid}/window", timeout=5)
            for h in handles:
                if h != current:
                    await wd_fetch(f"/session/{sid}/window", "POST", {"handle": h}, timeout=5)
                    await wd_fetch(f"/session/{sid}/window", "DELETE", timeout=5)
            await wd_fetch(f"/session/{sid}/window", "POST", {"handle": current}, timeout=5)
            logger.info("Closed %d extra tab(s)", len(handles) - 1)
    except Exception as exc:
        logger.warning("closeOtherTabs failed: %s", exc)


async def quick_observe(sid: str) -> dict:
    try:
        result = await wd_fetch(f"/session/{sid}/execute/sync", "POST", {
            "script": OBSERVE_SCRIPT,
            "args": [],
        }, timeout=10)
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


KEY_MAP: dict[str, str] = {
    "Enter": "\uE007", "Tab": "\uE004", "Escape": "\uE00C", "Backspace": "\uE003",
    "Delete": "\uE017", "Space": "\uE00D",
    "ArrowUp": "\uE013", "ArrowDown": "\uE014", "ArrowLeft": "\uE012", "ArrowRight": "\uE011",
    "Home": "\uE011", "End": "\uE010", "PageUp": "\uE00E", "PageDown": "\uE00F",
}
