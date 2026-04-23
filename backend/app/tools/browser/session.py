from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import DOCKER_HOST_ADDR
from app.container import ensure_container_running
from app.db import get_pool
from app.device_presets import get_preset, DEFAULT_PRESET
from app.tools.browser.scripts import OBSERVE_SCRIPT, get_stealth_script

logger = logging.getLogger("browser.session")

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
    stealth_injected: bool = False


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


async def _cdp(sid: str, cmd: str, params: dict | None = None, *, base_url: str) -> Any:
    return await wd_fetch(f"/session/{sid}/goog/cdp/execute", "POST", {
        "cmd": cmd, "params": params or {},
    }, timeout=5, base_url=base_url)


def _build_accept_language(languages: list[str]) -> str:
    if not languages:
        return "en-US,en;q=0.9"
    parts = []
    for i, lang in enumerate(languages):
        if i == 0:
            parts.append(lang)
        else:
            q = max(0.1, round(1.0 - i * 0.1, 1))
            parts.append(f"{lang};q={q}")
    return ",".join(parts)


async def _inject_stealth(sid: str, *, base_url: str, fingerprint_profile: dict | None = None) -> None:
    if not fingerprint_profile:
        raise ValueError("fingerprint_profile is required")
    profile = fingerprint_profile
    nav = profile.get("navigator", {})
    hints = profile.get("clientHints", {})
    tz = profile.get("timezone", "UTC")

    chrome_ver = profile.get("chromeVersion", "124.0.6367.78")
    chrome_major = chrome_ver.split(".")[0]

    stealth_js = get_stealth_script()
    if stealth_js:
        fp_decl = f"var __FP__={json.dumps(profile, separators=(',', ':'))};"
        script = fp_decl + stealth_js
        try:
            await _cdp(sid, "Page.addScriptToEvaluateOnNewDocument", {"source": script}, base_url=base_url)
            logger.info("Stealth: addScriptToEvaluateOnNewDocument OK")
        except Exception as exc:
            logger.warning("Stealth: CDP inject failed (%s), will rely on execute_script", exc)

        try:
            await wd_fetch(f"/session/{sid}/execute/sync", "POST", {
                "script": f"{fp_decl}{stealth_js}", "args": [],
            }, timeout=5, base_url=base_url)
        except Exception:
            pass

    ua = nav.get("userAgent", f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_ver} Safari/537.36")
    accept_lang = _build_accept_language(nav.get("languages", ["en-US", "en"]))
    platform = nav.get("platform", "Linux x86_64")

    try:
        await _cdp(sid, "Network.setUserAgentOverride", {
            "userAgent": ua,
            "acceptLanguage": accept_lang,
            "platform": platform,
            "userAgentMetadata": {
                "brands": [
                    {"brand": "Chromium", "version": chrome_major},
                    {"brand": "Google Chrome", "version": chrome_major},
                    {"brand": "Not=A?Brand", "version": "99"},
                ],
                "fullVersionList": [
                    {"brand": "Chromium", "version": chrome_ver},
                    {"brand": "Google Chrome", "version": chrome_ver},
                    {"brand": "Not=A?Brand", "version": "99.0.0.0"},
                ],
                "fullVersion": chrome_ver,
                "platform": hints.get("platform", "Linux"),
                "platformVersion": hints.get("platformVersion", "6.5.0"),
                "architecture": hints.get("architecture", "x86"),
                "model": "",
                "mobile": hints.get("mobile", False),
                "bitness": hints.get("bitness", "64"),
                "wow64": hints.get("wow64", False),
            },
        }, base_url=base_url)
        logger.info("Stealth: UA + client hints set from profile (Chrome %s)", chrome_ver)
    except Exception:
        pass

    try:
        await _cdp(sid, "Page.setBypassCSP", {"enabled": True}, base_url=base_url)
    except Exception:
        pass

    try:
        await _cdp(sid, "Emulation.setTimezoneOverride", {"timezoneId": tz}, base_url=base_url)
        logger.info("Stealth: timezone -> %s", tz)
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


async def _inject_device_emulation(sid: str, preset_data: dict, *, base_url: str) -> None:
    """For mobile presets, inject CDP Emulation.setDeviceMetricsOverride."""
    if preset_data.get("category") != "mobile":
        return
    try:
        await _cdp(sid, "Emulation.setDeviceMetricsOverride", {
            "width": preset_data["width"],
            "height": preset_data["height"],
            "deviceScaleFactor": preset_data.get("dpr", 1),
            "mobile": True,
        }, base_url=base_url)
        logger.info("Device emulation injected: %s (%dx%d @%.1fx)",
                     preset_data.get("label", "?"), preset_data["width"],
                     preset_data["height"], preset_data.get("dpr", 1))
    except Exception as exc:
        logger.warning("Device emulation inject failed: %s", exc)


def invalidate_session_cache(chat_session_id: str) -> None:
    """Clear cached WD session after container recreation."""
    bs = _sessions.pop(chat_session_id, None)
    if bs:
        logger.info("Invalidated session cache for %s", chat_session_id)


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
    bs.selenium_base = f"http://{DOCKER_HOST_ADDR}:{ports['selenium_port']}"

    async with bs.lock:
        sid = await _ensure_session_impl(bs)

    try:
        pool = get_pool()
        row = await pool.fetchrow(
            "SELECT device_preset, fingerprint_profile FROM sessions WHERE id = $1", chat_session_id,
        )
        preset_id = (row["device_preset"] if row else None) or DEFAULT_PRESET
        preset_data = get_preset(preset_id)
        await _inject_device_emulation(sid, preset_data, base_url=bs.selenium_base)

        if not bs.stealth_injected:
            fp = row["fingerprint_profile"] if row else None
            if fp:
                await _inject_stealth(sid, base_url=bs.selenium_base, fingerprint_profile=fp)
                bs.stealth_injected = True
                logger.info("Stealth injected for session %s", chat_session_id)
    except Exception as exc:
        logger.debug("Device emulation / stealth inject skipped: %s", exc)

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


async def get_viewport_offset(sid: str, *, base_url: str) -> dict:
    """Pixel offset from VNC screen origin to browser viewport origin via CDP."""
    try:
        bounds_result = await _cdp(sid, "Browser.getWindowForTarget", {}, base_url=base_url)
        bounds = (bounds_result or {}).get("bounds", {})
        inner = await wd_fetch(f"/session/{sid}/execute/sync", "POST", {
            "script": "return window.innerHeight", "args": [],
        }, timeout=5, base_url=base_url)
        win_top = bounds.get("top", 0)
        win_height = bounds.get("height", 0)
        inner_h = inner if isinstance(inner, (int, float)) else 0
        offset_y = win_top + (win_height - inner_h) if win_height > inner_h else 0
        return {"x": bounds.get("left", 0), "y": offset_y}
    except Exception:
        return {"x": 0, "y": 0}


async def quick_observe(sid: str, *, base_url: str) -> dict:
    try:
        result = await wd_fetch(f"/session/{sid}/execute/sync", "POST", {
            "script": OBSERVE_SCRIPT,
            "args": [],
        }, timeout=10, base_url=base_url)
        vp_offset = await get_viewport_offset(sid, base_url=base_url)
        return {
            "url": (result or {}).get("url", ""),
            "title": (result or {}).get("title", ""),
            "elementCount": len((result or {}).get("elements", [])),
            "viewportOffset": vp_offset,
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


async def cdp_human_click(sid: str, x: int, y: int, *, base_url: str) -> None:
    steps = 3 + random.randint(0, 3)
    start_x = x + random.randint(-60, 60)
    start_y = y + random.randint(-60, 60)
    cp_x = (start_x + x) / 2 + random.randint(-20, 20)
    cp_y = (start_y + y) / 2 + random.randint(-20, 20)

    for i in range(steps + 1):
        t = i / steps
        px = (1 - t) ** 2 * start_x + 2 * (1 - t) * t * cp_x + t ** 2 * x
        py = (1 - t) ** 2 * start_y + 2 * (1 - t) * t * cp_y + t ** 2 * y
        await _cdp(sid, "Input.dispatchMouseEvent", {
            "type": "mouseMoved",
            "x": max(0, round(px)),
            "y": max(0, round(py)),
        }, base_url=base_url)
        await asyncio.sleep((15 + random.randint(0, 35)) / 1000)

    await _cdp(sid, "Input.dispatchMouseEvent", {
        "type": "mouseMoved",
        "x": x + random.randint(-1, 1),
        "y": y + random.randint(-1, 1),
    }, base_url=base_url)
    await asyncio.sleep((15 + random.randint(0, 50)) / 1000)

    await _cdp(sid, "Input.dispatchMouseEvent", {
        "type": "mousePressed", "x": x, "y": y,
        "button": "left", "clickCount": 1, "buttons": 1,
    }, base_url=base_url)
    await asyncio.sleep((30 + random.randint(0, 60)) / 1000)
    await _cdp(sid, "Input.dispatchMouseEvent", {
        "type": "mouseReleased", "x": x, "y": y,
        "button": "left", "clickCount": 1, "buttons": 0,
    }, base_url=base_url)


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
