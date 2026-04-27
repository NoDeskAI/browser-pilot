#!/usr/bin/env python3
"""Browser-level CDP fingerprint injector.

The agent auto-attaches to every page target before it runs, applies the
session fingerprint profile, then resumes the target.  It is intentionally
inside the container so manual address-bar navigation and newly opened tabs
share the same preload path as WebDriver-driven navigation.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import websocket


PROFILE_FILE = Path("/tmp/fingerprint-profile.json")
HEALTH_FILE = Path("/tmp/fingerprint-health.json")
STEALTH_FILE = Path("/opt/stealth-ext/stealth.js")
EXT_PROFILE_FILE = Path("/opt/stealth-ext/fp-profile.js")
CDP_VERSION_URL = "http://localhost:9222/json/version"
RETRY_INTERVAL = 1.0
MAX_RETRIES = 120


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _write_health(payload: dict[str, Any]) -> None:
    payload.setdefault("updatedAt", _now())
    tmp = HEALTH_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(HEALTH_FILE)


def _read_health() -> dict[str, Any]:
    try:
        if HEALTH_FILE.exists():
            return json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _load_runtime_profile() -> dict[str, Any]:
    if PROFILE_FILE.exists():
        return json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
    return {}


def _load_extension_profile() -> dict[str, Any]:
    if EXT_PROFILE_FILE.exists():
        content = EXT_PROFILE_FILE.read_text(encoding="utf-8")
        match = re.search(r"var\s+__FP__\s*=\s*(.*?);\s*$", content, re.S)
        if match:
            return json.loads(match.group(1))
    return {}


def _load_profile_with_retry() -> dict[str, Any]:
    last_error: Exception | None = None
    for _ in range(30):
        try:
            profile = _load_runtime_profile()
            if profile:
                return profile
        except Exception as exc:
            last_error = exc
            _write_health({
                "agent": "cdp-fingerprint-agent",
                "ok": False,
                "status": "profile-error",
                "warnings": [f"Fingerprint profile could not be read: {exc}"],
            })
        time.sleep(0.2)

    try:
        profile = _load_extension_profile()
        if profile:
            _write_health({
                "agent": "cdp-fingerprint-agent",
                "ok": False,
                "status": "extension-profile-fallback",
                "warnings": ["Runtime fingerprint profile was unavailable; using extension fallback profile."],
            })
            return profile
    except Exception as exc:
        last_error = exc

    warnings = ["Fingerprint profile file is missing; CDP injection is disabled."]
    if last_error:
        warnings.append(f"Last profile read error: {last_error}")
    _write_health({
        "agent": "cdp-fingerprint-agent",
        "ok": False,
        "status": "profile-missing",
        "warnings": warnings,
    })
    return {}


def _chrome_brands(chrome_version: str) -> list[dict[str, str]]:
    major = chrome_version.split(".")[0]
    return [
        {"brand": "Chromium", "version": major},
        {"brand": "Google Chrome", "version": major},
        {"brand": "Not=A?Brand", "version": "99"},
    ]


def _full_version_list(chrome_version: str) -> list[dict[str, str]]:
    return [
        {"brand": "Chromium", "version": chrome_version},
        {"brand": "Google Chrome", "version": chrome_version},
        {"brand": "Not=A?Brand", "version": "99.0.0.0"},
    ]


def _platform_from_nav(nav_platform: str) -> str:
    if nav_platform.startswith("Win"):
        return "Windows"
    if nav_platform.startswith("Mac"):
        return "macOS"
    if nav_platform.startswith("Linux"):
        return "Linux"
    return nav_platform


def _client_hints(profile: dict[str, Any]) -> dict[str, Any]:
    chrome_version = str(profile.get("chromeVersion") or "124.0.0.0")
    nav = profile.get("navigator") if isinstance(profile.get("navigator"), dict) else {}
    hints = dict(profile.get("clientHints") or {})
    hints.setdefault("platform", _platform_from_nav(str(nav.get("platform") or "")))
    hints.setdefault("platformVersion", "")
    hints.setdefault("architecture", "")
    hints.setdefault("bitness", "")
    hints.setdefault("model", "")
    hints["mobile"] = bool(hints.get("mobile", False))
    hints["wow64"] = bool(hints.get("wow64", False))
    if not isinstance(hints.get("brands"), list) or not hints["brands"]:
        hints["brands"] = _chrome_brands(chrome_version)
    if not isinstance(hints.get("fullVersionList"), list) or not hints["fullVersionList"]:
        hints["fullVersionList"] = _full_version_list(chrome_version)
    hints.setdefault("fullVersion", chrome_version)
    hints.setdefault("uaFullVersion", hints["fullVersion"])
    return hints


def _ua_metadata(profile: dict[str, Any]) -> dict[str, Any]:
    hints = _client_hints(profile)
    return {
        "brands": hints["brands"],
        "fullVersionList": hints["fullVersionList"],
        "fullVersion": hints["fullVersion"],
        "platform": hints["platform"],
        "platformVersion": hints["platformVersion"],
        "architecture": hints["architecture"],
        "model": hints.get("model", ""),
        "mobile": hints["mobile"],
        "bitness": hints["bitness"],
        "wow64": hints["wow64"],
    }


def _accept_language(languages: list[str]) -> str:
    if not languages:
        return "en-US,en;q=0.9"
    parts: list[str] = []
    for index, lang in enumerate(languages):
        if index == 0:
            parts.append(lang)
        else:
            q = max(0.1, round(1.0 - index * 0.1, 1))
            parts.append(f"{lang};q={q}")
    return ",".join(parts)


def _discover_browser_ws() -> str | None:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(CDP_VERSION_URL, timeout=2) as resp:
                data = json.loads(resp.read())
            ws_url = data.get("webSocketDebuggerUrl")
            if ws_url:
                return ws_url
        except (urllib.error.URLError, OSError, ValueError):
            pass
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_INTERVAL)
    return None


class CDPAgent:
    def __init__(self, profile: dict[str, Any]):
        self.profile = profile
        self.hints = _client_hints(profile)
        self.nav = profile.get("navigator") if isinstance(profile.get("navigator"), dict) else {}
        self.stealth_source = self._build_stealth_source()
        self.ws: websocket.WebSocket | None = None
        self.next_id = 0
        self.responses: dict[int, dict[str, Any]] = {}
        self.initializing: set[str] = set()
        self.initialized: set[str] = set()
        self.page_sessions: dict[str, dict[str, Any]] = {}

    def _build_stealth_source(self) -> str:
        stealth = STEALTH_FILE.read_text(encoding="utf-8") if STEALTH_FILE.exists() else ""
        profile_json = json.dumps(self.profile, ensure_ascii=False, separators=(",", ":"))
        return f"var __FP__={profile_json};\n{stealth}"

    def _send(self, method: str, params: dict[str, Any] | None = None, session_id: str | None = None) -> int:
        if self.ws is None:
            raise RuntimeError("CDP websocket is not connected")
        self.next_id += 1
        msg: dict[str, Any] = {"id": self.next_id, "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        self.ws.send(json.dumps(msg, separators=(",", ":")))
        return self.next_id

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        session_id: str | None = None,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        req_id = self._send(method, params, session_id)
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                if req_id in self.responses:
                    msg = self.responses.pop(req_id)
                    if "error" in msg:
                        raise RuntimeError(f"{method}: {msg['error']}")
                    return msg.get("result", {})
                if self.ws is None:
                    raise RuntimeError("CDP websocket closed")
                self.ws.settimeout(max(0.1, deadline - time.monotonic()))
                try:
                    raw = self.ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                self._handle_raw(raw)
            raise TimeoutError(f"{method} timed out")
        finally:
            if self.ws is not None:
                self.ws.settimeout(None)

    def safe_call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        session_id: str | None = None,
        timeout: float = 5.0,
    ) -> tuple[bool, str | None]:
        try:
            self.call(method, params, session_id, timeout)
            return True, None
        except Exception as exc:
            return False, str(exc)

    def connect(self, ws_url: str) -> None:
        self.ws = websocket.create_connection(ws_url, timeout=5)

    def bootstrap(self) -> None:
        _write_health({
            "agent": "cdp-fingerprint-agent",
            "ok": False,
            "status": "starting",
            "warnings": [],
            "expected": self._expected(),
        })
        self.call("Target.setDiscoverTargets", {"discover": True}, timeout=5)
        self.call("Target.setAutoAttach", {
            "autoAttach": True,
            "waitForDebuggerOnStart": True,
            "flatten": True,
        }, timeout=5)
        targets = self.call("Target.getTargets", {}, timeout=5).get("targetInfos", [])
        for target in targets:
            if target.get("type") == "page":
                try:
                    result = self.call("Target.attachToTarget", {
                        "targetId": target["targetId"],
                        "flatten": True,
                    }, timeout=3)
                    session_id = result.get("sessionId")
                    if session_id:
                        self._init_page(session_id, target)
                except Exception:
                    pass

    def loop(self) -> None:
        if self.ws is None:
            raise RuntimeError("CDP websocket is not connected")
        self.ws.settimeout(None)
        while True:
            try:
                raw = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            self._handle_raw(raw)

    def _handle_raw(self, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
        except Exception:
            return
        if "id" in msg:
            self.responses[int(msg["id"])] = msg
            return

        method = msg.get("method")
        params = msg.get("params", {})
        if method == "Target.attachedToTarget":
            session_id = params.get("sessionId")
            target = params.get("targetInfo") or {}
            if not session_id:
                return
            if target.get("type") == "page":
                self._init_page(session_id, target)
            else:
                self.safe_call("Runtime.runIfWaitingForDebugger", {}, session_id=session_id, timeout=1)
            return

        if method == "Target.detachedFromTarget":
            session_id = params.get("sessionId")
            if session_id:
                self.initializing.discard(session_id)
                self.initialized.discard(session_id)
                self.page_sessions.pop(session_id, None)
            return

        session_id = msg.get("sessionId")
        if session_id and method == "Page.frameNavigated":
            frame = params.get("frame") if isinstance(params.get("frame"), dict) else {}
            if frame.get("url"):
                target = self.page_sessions.setdefault(session_id, {})
                target["url"] = frame["url"]
        if session_id and method in ("Page.frameNavigated", "Runtime.executionContextCreated"):
            if session_id in self.initialized:
                self._check_health(session_id, self.page_sessions.get(session_id, {}), reason=method)

    def _init_page(self, session_id: str, target: dict[str, Any]) -> None:
        if session_id in self.initialized or session_id in self.initializing:
            return
        self.initializing.add(session_id)
        self.page_sessions[session_id] = target
        warnings: list[str] = []

        for method, params, timeout in [
            ("Runtime.enable", {}, 3),
            ("Page.enable", {}, 3),
            ("Network.enable", {}, 3),
            ("Page.setBypassCSP", {"enabled": True}, 3),
            ("Network.setUserAgentOverride", self._ua_override_params(), 5),
            ("Emulation.setTimezoneOverride", {"timezoneId": self.profile.get("timezone", "UTC")}, 3),
            ("Page.addScriptToEvaluateOnNewDocument", {"source": self.stealth_source}, 5),
        ]:
            ok, err = self.safe_call(method, params, session_id=session_id, timeout=timeout)
            if not ok and err:
                warnings.append(f"{method} failed: {err}")

        ok, err = self.safe_call("Runtime.runIfWaitingForDebugger", {}, session_id=session_id, timeout=3)
        if not ok and err:
            warnings.append(f"Runtime.runIfWaitingForDebugger failed: {err}")

        ok, err = self.safe_call("Runtime.evaluate", {
            "expression": self.stealth_source,
            "silent": True,
            "includeCommandLineAPI": False,
        }, session_id=session_id, timeout=5)
        if not ok and err:
            warnings.append(f"current document preload failed: {err}")

        self.initializing.discard(session_id)
        self.initialized.add(session_id)
        self._check_health(session_id, target, reason="attached", pre_warnings=warnings)

    def _ua_override_params(self) -> dict[str, Any]:
        chrome_version = str(self.profile.get("chromeVersion") or "124.0.0.0")
        ua = self.nav.get(
            "userAgent",
            f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_version} Safari/537.36",
        )
        languages = self.nav.get("languages") if isinstance(self.nav.get("languages"), list) else ["en-US", "en"]
        return {
            "userAgent": ua,
            "acceptLanguage": _accept_language([str(lang) for lang in languages]),
            "platform": self.nav.get("platform", ""),
            "userAgentMetadata": _ua_metadata(self.profile),
        }

    def _expected(self) -> dict[str, Any]:
        network = self.profile.get("network") if isinstance(self.profile.get("network"), dict) else {}
        return {
            "navigatorPlatform": self.nav.get("platform", ""),
            "uaCHPlatform": self.hints.get("platform", ""),
            "webglRenderer": (self.profile.get("webgl") or {}).get("renderer", ""),
            "timezone": self.profile.get("timezone", "UTC"),
            "hiddenFontFamilies": (self.profile.get("fontPolicy") or {}).get("hiddenFamilies", []),
            "network": {
                "ip": network.get("ip", ""),
                "countryCode": network.get("countryCode", ""),
                "timezone": network.get("timezone", self.profile.get("timezone", "UTC")),
                "dnsServers": network.get("dnsServers", []),
            },
        }

    def _health_expression(self) -> str:
        hidden = json.dumps(self._expected()["hiddenFontFamilies"], ensure_ascii=False)
        return f"""
(async function(){{
  var hiddenFamilies={hidden};
  var fontChecks={{}};
  try{{
    if(document.fonts&&document.fonts.check){{
      hiddenFamilies.slice(0,8).forEach(function(f){{
        try{{fontChecks[f]=document.fonts.check('16px "'+f+'"');}}catch(e){{fontChecks[f]=null;}}
      }});
    }}
  }}catch(e){{}}
  var glVendor='',glRenderer='';
  try{{
    var canvas=document.createElement('canvas');
    var gl=canvas.getContext('webgl')||canvas.getContext('experimental-webgl')||canvas.getContext('webgl2');
    if(gl){{
      var dbg=gl.getExtension('WEBGL_debug_renderer_info');
      glVendor=dbg?gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL):gl.getParameter(gl.VENDOR);
      glRenderer=dbg?gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL):gl.getParameter(gl.RENDERER);
    }}
  }}catch(e){{}}
  var high={{}},uadPlatform=null;
  try{{
    if(navigator.userAgentData){{
      uadPlatform=navigator.userAgentData.platform;
      high=await navigator.userAgentData.getHighEntropyValues(['platform','platformVersion','architecture','bitness','wow64','fullVersionList','uaFullVersion','fullVersion']);
    }}
  }}catch(e){{}}
  var tz='';
  try{{tz=(new Intl.DateTimeFormat()).resolvedOptions().timeZone||'';}}catch(e){{}}
  var fpNetwork={{}};
  try{{fpNetwork=(window.__FP__&&window.__FP__.network)||{{}};}}catch(e){{}}
  return {{
    hasFP:!!window.__FP__,
    platform:navigator.platform,
    userAgent:navigator.userAgent,
    language:navigator.language,
    languages:Array.prototype.slice.call(navigator.languages||[]),
    uaDataPlatform:uadPlatform,
    highEntropy:high,
    webglVendor:glVendor,
    webglRenderer:glRenderer,
    timezone:tz,
    fpNetwork:fpNetwork,
    hiddenFontChecks:fontChecks
  }};
}})()
"""

    def _check_health(
        self,
        session_id: str,
        target: dict[str, Any],
        *,
        reason: str,
        pre_warnings: list[str] | None = None,
    ) -> None:
        warnings = list(pre_warnings or [])
        checks: dict[str, bool] = {}
        observed: dict[str, Any] = {}

        try:
            result = self.call("Runtime.evaluate", {
                "expression": self._health_expression(),
                "awaitPromise": True,
                "returnByValue": True,
                "silent": True,
            }, session_id=session_id, timeout=10)
            observed = (((result or {}).get("result") or {}).get("value") or {})
        except Exception as exc:
            warnings.append(f"runtime health evaluate failed: {exc}")

        if not observed:
            previous = _read_health()
            if previous.get("ok") is True:
                previous["updatedAt"] = _now()
                previous["transientWarnings"] = _unique(
                    list(previous.get("transientWarnings") or []) + warnings
                )
                previous["lastTransientHealthFailure"] = {
                    "reason": reason,
                    "targetId": target.get("targetId"),
                    "targetUrl": target.get("url"),
                    "sessionId": session_id,
                    "warnings": warnings,
                    "updatedAt": _now(),
                }
                _write_health(previous)
                return

        expected = self._expected()
        if observed:
            checks["window.__FP__"] = bool(observed.get("hasFP"))
            checks["navigator.platform"] = observed.get("platform") == expected["navigatorPlatform"]
            checks["navigator.userAgentData.platform"] = observed.get("uaDataPlatform") == expected["uaCHPlatform"]
            checks["webgl.renderer"] = observed.get("webglRenderer") == expected["webglRenderer"]
            checks["timezone"] = observed.get("timezone") == expected["timezone"]

            hidden_checks = observed.get("hiddenFontChecks") if isinstance(observed.get("hiddenFontChecks"), dict) else {}
            hidden_values = [value for value in hidden_checks.values() if value is not None]
            checks["linux.fonts.hidden"] = bool(hidden_values) and not any(hidden_values)

            expected_network = expected.get("network") if isinstance(expected.get("network"), dict) else {}
            observed_network = observed.get("fpNetwork") if isinstance(observed.get("fpNetwork"), dict) else {}
            checks["network.ip"] = observed_network.get("ip", "") == expected_network.get("ip", "")
            checks["network.countryCode"] = observed_network.get("countryCode", "") == expected_network.get("countryCode", "")
            checks["network.timezone"] = observed_network.get("timezone", "") == expected_network.get("timezone", "")
            checks["network.dnsServers"] = observed_network.get("dnsServers", []) == expected_network.get("dnsServers", [])

        for key, ok in checks.items():
            if not ok:
                warnings.append(f"{key} mismatch during {reason}")

        warnings = _unique(warnings)
        _write_health({
            "agent": "cdp-fingerprint-agent",
            "ok": not warnings and bool(checks) and all(checks.values()),
            "status": "ok" if not warnings and checks and all(checks.values()) else "warning",
            "reason": reason,
            "targetId": target.get("targetId"),
            "targetUrl": target.get("url"),
            "sessionId": session_id,
            "expected": expected,
            "observed": observed,
            "checks": checks,
            "warnings": warnings,
        })


def main() -> int:
    while True:
        profile = _load_profile_with_retry()
        if not profile:
            time.sleep(2)
            continue
        ws_url = _discover_browser_ws()
        if not ws_url:
            _write_health({
                "agent": "cdp-fingerprint-agent",
                "ok": False,
                "status": "chrome-unreachable",
                "warnings": ["Chrome DevTools endpoint was not reachable."],
            })
            return 1

        try:
            agent = CDPAgent(profile)
            agent.connect(ws_url)
            agent.bootstrap()
            print("[cdp-fingerprint-agent] connected and auto-attach enabled", flush=True)
            agent.loop()
        except Exception as exc:
            _write_health({
                "agent": "cdp-fingerprint-agent",
                "ok": False,
                "status": "agent-error",
                "warnings": [f"CDP agent error: {exc}"],
            })
            traceback.print_exc(file=sys.stderr)
            time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())
