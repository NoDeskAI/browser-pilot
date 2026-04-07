#!/usr/bin/env python3
"""CDP event logger — connects to Chrome's DevTools Protocol via WebSocket,
subscribes to Console/Network/Page/Log events, and writes structured JSONL
to /tmp/cdp-events.jsonl.  Managed by supervisor inside the container."""

import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import websocket

LOG_FILE = "/tmp/cdp-events.jsonl"
LOG_OLD = LOG_FILE + ".old"
MAX_LOG_SIZE = 5 * 1024 * 1024  # 5 MB
CDP_URL = "http://localhost:9222/json"
RETRY_INTERVAL = 2
MAX_RETRIES = 60

_msg_id = 0
_pending_requests: dict[str, dict] = {}


def _next_id() -> int:
    global _msg_id
    _msg_id += 1
    return _msg_id


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _rotate_if_needed():
    try:
        if os.path.getsize(LOG_FILE) > MAX_LOG_SIZE:
            shutil.move(LOG_FILE, LOG_OLD)
            print(f"[cdp-logger] rotated log ({MAX_LOG_SIZE} bytes exceeded)")
    except FileNotFoundError:
        pass


def _write(entry: dict):
    _rotate_if_needed()
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _discover_ws_url() -> str | None:
    """Poll localhost:9222/json until Chrome is ready. Returns page WS URL."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = urllib.request.urlopen(CDP_URL, timeout=3)
            pages = json.loads(resp.read())
            for page in pages:
                if page.get("type") == "page" and page.get("webSocketDebuggerUrl"):
                    url = page["webSocketDebuggerUrl"]
                    print(f"[cdp-logger] found page WS: {url}  (attempt {attempt})")
                    return url
        except (urllib.error.URLError, ConnectionError, OSError, ValueError):
            pass
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_INTERVAL)
    return None


def _format_console(params: dict) -> dict:
    args = params.get("args", [])
    text_parts = []
    for arg in args:
        val = arg.get("value")
        if val is not None:
            text_parts.append(str(val))
        elif arg.get("description"):
            text_parts.append(arg["description"])
        elif arg.get("type"):
            text_parts.append(f"[{arg['type']}]")
    level = params.get("type", "log")
    summary = " ".join(text_parts)[:200]
    log_type = "error" if level in ("error", "assert") else "console"
    return {"type": log_type, "summary": f"[{level}] {summary}"}


def _format_exception(params: dict) -> dict:
    exc = params.get("exceptionDetails", {})
    text = exc.get("text", "")
    exception = exc.get("exception", {})
    desc = exception.get("description", exception.get("value", ""))
    summary = f"{text}: {desc}"[:300] if desc else text[:300]
    return {"type": "error", "summary": summary}


def _format_request(params: dict) -> dict:
    req = params.get("request", {})
    url = req.get("url", "")
    method = req.get("method", "?")
    req_id = params.get("requestId", "")
    _pending_requests[req_id] = {
        "method": method,
        "url": url,
        "ts": time.monotonic(),
    }
    short_url = url[:120]
    return {"type": "network", "summary": f"-> {method} {short_url}"}


def _format_response(params: dict) -> dict:
    resp = params.get("response", {})
    status = resp.get("status", 0)
    url = resp.get("url", "")
    req_id = params.get("requestId", "")
    pending = _pending_requests.pop(req_id, None)
    elapsed = ""
    method = "?"
    if pending:
        ms = int((time.monotonic() - pending["ts"]) * 1000)
        elapsed = f" {ms}ms"
        method = pending.get("method", "?")
    short_url = url[:120]
    return {"type": "network", "summary": f"<- {method} {short_url} {status}{elapsed}"}


def _format_loading_failed(params: dict) -> dict:
    req_id = params.get("requestId", "")
    error = params.get("errorText", "unknown")
    pending = _pending_requests.pop(req_id, None)
    url = ""
    method = "?"
    if pending:
        url = pending.get("url", "")[:120]
        method = pending.get("method", "?")
    return {"type": "error", "summary": f"FAILED {method} {url} — {error}"}


def _format_navigation(params: dict) -> dict:
    frame = params.get("frame", {})
    url = frame.get("url", "")
    name = frame.get("name", "")
    suffix = f" ({name})" if name else ""
    return {"type": "navigation", "summary": f"navigated -> {url[:150]}{suffix}"}


def _format_log_entry(params: dict) -> dict:
    entry = params.get("entry", {})
    level = entry.get("level", "info")
    text = entry.get("text", "")[:200]
    source = entry.get("source", "")
    log_type = "error" if level in ("error", "warning") else "console"
    return {"type": log_type, "summary": f"[{source}/{level}] {text}"}


EVENT_HANDLERS = {
    "Runtime.consoleAPICalled": _format_console,
    "Runtime.exceptionThrown": _format_exception,
    "Network.requestWillBeSent": _format_request,
    "Network.responseReceived": _format_response,
    "Network.loadingFailed": _format_loading_failed,
    "Page.frameNavigated": _format_navigation,
    "Log.entryAdded": _format_log_entry,
}


def on_message(ws, raw):
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return
    method = msg.get("method")
    if not method or method not in EVENT_HANDLERS:
        return
    params = msg.get("params", {})
    try:
        entry = EVENT_HANDLERS[method](params)
    except Exception as e:
        entry = {"type": "error", "summary": f"[cdp-logger] format error: {e}"}
    entry["ts"] = _ts()
    entry["method"] = method
    _write(entry)


def on_open(ws):
    print("[cdp-logger] WebSocket connected, enabling domains...")
    domains = ["Runtime.enable", "Network.enable", "Page.enable", "Log.enable"]
    for d in domains:
        ws.send(json.dumps({"id": _next_id(), "method": d}))
    print("[cdp-logger] all domains enabled, listening for events")
    _write({
        "ts": _ts(),
        "type": "console",
        "method": "cdp-logger.started",
        "summary": "CDP logger connected and listening",
    })


def on_error(ws, error):
    print(f"[cdp-logger] WebSocket error: {error}", file=sys.stderr)


def on_close(ws, close_status_code, close_msg):
    print(f"[cdp-logger] WebSocket closed (code={close_status_code})")
    _pending_requests.clear()


def main():
    print("[cdp-logger] starting...")
    while True:
        ws_url = _discover_ws_url()
        if not ws_url:
            print("[cdp-logger] Chrome not reachable after retries, exiting",
                  file=sys.stderr)
            sys.exit(1)

        ws = websocket.WebSocketApp(
            ws_url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        ws.run_forever(reconnect=0)
        print("[cdp-logger] connection lost, reconnecting in 3s...")
        time.sleep(3)


if __name__ == "__main__":
    main()
