from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from bpilot import client, config
from bpilot.config import _CMD_NAME

app = typer.Typer(help=f"{_CMD_NAME} — Remote Browser CLI", no_args_is_help=True)
config_app = typer.Typer(help="Manage CLI configuration", no_args_is_help=True)
session_app = typer.Typer(help="Manage browser sessions", no_args_is_help=True)
app.add_typer(config_app, name="config")
app.add_typer(session_app, name="session")

console = Console()

# ── Global options ──────────────────────────────────────────────────────

_json_output = False
_api_url_override = ""
_session_override = ""


@app.callback()
def main_callback(
    json_flag: bool = typer.Option(False, "--json", "-j", help="Machine-readable JSON output"),
    api_url: str = typer.Option("", "--api-url", help="Override API base URL"),
    session: str = typer.Option("", "--session", "-s", help="Override active session ID"),
):
    global _json_output, _api_url_override, _session_override
    _json_output = json_flag
    _api_url_override = api_url
    _session_override = session


def _sid() -> str:
    sid = _session_override or config.get("active_session")
    if not sid:
        console.print(f"[red]No active session. Run: {_CMD_NAME} session use <id>[/red]")
        raise typer.Exit(1)
    return sid


def _api() -> str:
    return _api_url_override


def _out(data: dict) -> None:
    if _json_output:
        typer.echo(json.dumps(data, ensure_ascii=False))
    else:
        _pretty(data)


def _pretty(data: dict) -> None:
    if not data.get("ok", True):
        console.print(f"[red]Error: {data.get('error', 'unknown')}[/red]")
        return

    if "url" in data and "elements" in data:
        console.print(f"[bold]URL:[/bold]    {data['url']}")
        console.print(f"[bold]Title:[/bold]  {data.get('title', '')}")
        vt = data.get("visibleText", "")
        if vt:
            preview = vt[:200] + ("..." if len(vt) > 200 else "")
            console.print(f"[dim]{preview}[/dim]")
        elements = data.get("elements", [])
        console.print(f"\n[bold]Elements ({len(elements)}):[/bold]")
        for i, el in enumerate(elements[:50], 1):
            tag = el.get("tag", "?").upper()
            text = el.get("text", "")[:40]
            x, y = el.get("x", 0), el.get("y", 0)
            console.print(f"  [{i:>3}]  {tag:<8} {text!r:<42} @ ({x}, {y})")
        if len(elements) > 50:
            console.print(f"  ... and {len(elements) - 50} more")
        return

    if "tabs" in data:
        table = Table(title="Browser Tabs")
        table.add_column("#", width=3)
        table.add_column("Handle", width=20)
        table.add_column("URL")
        table.add_column("Title")
        table.add_column("Active", width=6)
        for i, t in enumerate(data["tabs"]):
            table.add_row(
                str(i), t["handle"], t["url"], t["title"],
                "[green]yes[/green]" if t["active"] else "",
            )
        console.print(table)
        return

    if "sessions" in data:
        table = Table(title="Sessions")
        table.add_column("ID", width=36)
        table.add_column("Name")
        table.add_column("Status", width=12)
        table.add_column("URL")
        for s in data["sessions"]:
            status = s.get("containerStatus", "?")
            style = "green" if status == "running" else "dim"
            table.add_row(s["id"], s["name"], f"[{style}]{status}[/{style}]", s.get("currentUrl", ""))
        console.print(table)
        return

    if "screenshot" in data:
        console.print("[green]Screenshot captured (base64, use --output to save as file)[/green]")
        return

    for k, v in data.items():
        if k == "ok":
            continue
        console.print(f"[bold]{k}:[/bold] {v}")


# ── Config commands ─────────────────────────────────────────────────────

@config_app.command("init")
def config_init():
    """Interactive configuration setup."""
    url = typer.prompt("API URL", default="http://localhost:8000")
    config.save({"api_url": url, "active_session": ""})
    console.print(f"[green]Config saved to {config.CONFIG_FILE}[/green]")


@config_app.command("set")
def config_set(key: str, value: str):
    """Set a config value (api-url, active-session)."""
    normalized = key.replace("-", "_")
    config.set_key(normalized, value)
    console.print(f"[green]{normalized} = {value}[/green]")


@config_app.command("show")
def config_show():
    """Show current configuration."""
    cfg = config.load()
    if _json_output:
        typer.echo(json.dumps(cfg, ensure_ascii=False))
    else:
        for k, v in cfg.items():
            console.print(f"[bold]{k}:[/bold] {v or '(not set)'}")


# ── Session commands ────────────────────────────────────────────────────

@session_app.command("list")
def session_list():
    """List all sessions."""
    data = client.get_request("/api/sessions", api_url=_api())
    _out(data)


@session_app.command("create")
def session_create(
    name: str = typer.Option("新会话", "--name", "-n"),
    device: str = typer.Option("desktop-1920x1080", "--device", "-d", help="Device preset (e.g. desktop-1920x1080, iphone-16)"),
    proxy: str = typer.Option("", "--proxy", "-p", help="Proxy URL (e.g. socks5://host:port)"),
):
    """Create a new session."""
    body: dict = {"name": name, "devicePreset": device}
    if proxy:
        body["proxyUrl"] = proxy
    data = client.post("/api/sessions", body, api_url=_api())
    if _json_output:
        typer.echo(json.dumps(data, ensure_ascii=False))
    else:
        console.print(f"[green]Created session:[/green] {data['id']}  ({data['name']})")
        if device != "desktop-1920x1080":
            console.print(f"[dim]Device: {device}[/dim]")
        if proxy:
            console.print(f"[dim]Proxy: {proxy}[/dim]")
        console.print(f"[dim]Run: {_CMD_NAME} session use {data['id']}[/dim]")


@session_app.command("use")
def session_use(session_id: str):
    """Set the active session."""
    config.set_key("active_session", session_id)
    if _json_output:
        typer.echo(json.dumps({"ok": True, "active_session": session_id}))
    else:
        console.print(f"[green]Active session set to:[/green] {session_id}")


@session_app.command("start")
def session_start(session_id: Optional[str] = typer.Argument(None)):
    """Start container for a session."""
    sid = session_id or _sid()
    data = client.post(f"/api/sessions/{sid}/container/start", api_url=_api())
    if _json_output:
        typer.echo(json.dumps(data, ensure_ascii=False))
    else:
        if data.get("ok"):
            ports = data.get("ports", {})
            console.print(f"[green]Container started[/green]")
            console.print(f"  Selenium: localhost:{ports.get('selenium_port')}")
            console.print(f"  VNC:      localhost:{ports.get('vnc_port')}")
        else:
            console.print(f"[red]Failed: {data.get('error')}[/red]")


@session_app.command("stop")
def session_stop(session_id: Optional[str] = typer.Argument(None)):
    """Stop container for a session."""
    sid = session_id or _sid()
    data = client.post(f"/api/sessions/{sid}/container/stop", api_url=_api())
    _out(data)


@session_app.command("delete")
def session_delete(session_id: str):
    """Delete a session and its container."""
    data = client.delete(f"/api/sessions/{session_id}", api_url=_api())
    _out(data)


@session_app.command("set-device")
def session_set_device(
    preset: str = typer.Argument(..., help="Device preset ID (e.g. desktop-1920x1080, iphone-16)"),
    session_id: Optional[str] = typer.Option(None, "--session", "-s"),
):
    """Change device preset for a session (triggers container restart)."""
    sid = session_id or _sid()
    data = client.post(f"/api/sessions/{sid}/device-preset", {"preset": preset}, api_url=_api())
    if _json_output:
        typer.echo(json.dumps(data, ensure_ascii=False))
    else:
        if data.get("ok"):
            ports = data.get("ports", {})
            console.print(f"[green]Device changed to {preset}[/green]")
            console.print(f"  Selenium: localhost:{ports.get('selenium_port')}")
            console.print(f"  VNC:      localhost:{ports.get('vnc_port')}")
        else:
            console.print(f"[red]Failed: {data.get('error')}[/red]")


@session_app.command("set-proxy")
def session_set_proxy(
    proxy_url: str = typer.Argument("", help='Proxy URL (e.g. socks5://host:port) or empty to clear'),
    session_id: Optional[str] = typer.Option(None, "--session", "-s"),
):
    """Change proxy for a session (triggers container restart)."""
    sid = session_id or _sid()
    data = client.post(f"/api/sessions/{sid}/proxy", {"proxyUrl": proxy_url}, api_url=_api())
    if _json_output:
        typer.echo(json.dumps(data, ensure_ascii=False))
    else:
        if data.get("ok"):
            ports = data.get("ports", {})
            if proxy_url:
                console.print(f"[green]Proxy set to {proxy_url}[/green]")
            else:
                console.print("[green]Proxy cleared (direct connection)[/green]")
            console.print(f"  Selenium: localhost:{ports.get('selenium_port')}")
            console.print(f"  VNC:      localhost:{ports.get('vnc_port')}")
        else:
            console.print(f"[red]Failed: {data.get('error')}[/red]")


# ── Browser commands ────────────────────────────────────────────────────

@app.command()
def navigate(url: str):
    """Navigate to a URL."""
    data = client.post("/api/browser/navigate", {"sessionId": _sid(), "url": url}, api_url=_api())
    _out(data)


@app.command()
def observe():
    """Observe current page: URL, title, visible text, interactive elements."""
    data = client.post("/api/browser/observe", {"sessionId": _sid()}, api_url=_api())
    _out(data)


@app.command()
def click(x: int, y: int):
    """Click at coordinates."""
    data = client.post("/api/browser/click", {"sessionId": _sid(), "x": x, "y": y}, api_url=_api())
    _out(data)


@app.command("click-element")
def click_element(selector: str):
    """Click element by CSS selector."""
    data = client.post("/api/browser/click-element", {"sessionId": _sid(), "selector": selector}, api_url=_api())
    _out(data)


@app.command("type")
def type_text(text: str):
    """Type text into the focused input."""
    data = client.post("/api/browser/type", {"sessionId": _sid(), "text": text}, api_url=_api())
    _out(data)


@app.command()
def key(key_name: str):
    """Press a key (Enter, Tab, Escape, etc.)."""
    data = client.post("/api/browser/key", {"sessionId": _sid(), "key": key_name}, api_url=_api())
    _out(data)


@app.command()
def scroll(
    delta_y: int = typer.Argument(..., help="Vertical scroll amount (positive = down)"),
    delta_x: int = typer.Option(0, "--delta-x", help="Horizontal scroll amount"),
    x: int = typer.Option(640, help="Scroll origin X"),
    y: int = typer.Option(360, help="Scroll origin Y"),
):
    """Scroll the page."""
    data = client.post("/api/browser/scroll", {
        "sessionId": _sid(), "deltaY": delta_y, "deltaX": delta_x, "x": x, "y": y,
    }, api_url=_api())
    _out(data)


@app.command()
def tabs():
    """List all browser tabs."""
    data = client.get_request("/api/browser/tabs", {"sessionId": _sid()}, api_url=_api())
    _out(data)


@app.command("switch-tab")
def switch_tab(
    handle: Optional[str] = typer.Option(None, "--handle"),
    index: Optional[int] = typer.Option(None, "--index"),
    close_current: bool = typer.Option(False, "--close-current"),
):
    """Switch to a different browser tab."""
    body: dict = {"sessionId": _sid(), "closeCurrent": close_current}
    if handle:
        body["handle"] = handle
    if index is not None:
        body["index"] = index
    data = client.post("/api/browser/switch-tab", body, api_url=_api())
    _out(data)


@app.command("page-info")
def page_info():
    """Get current page URL and title."""
    data = client.get_request("/api/browser/current", {"sessionId": _sid()}, api_url=_api())
    _out(data)


@app.command()
def screenshot(output: Optional[str] = typer.Option(None, "--output", "-o", help="Save to file")):
    """Take a screenshot of the current page."""
    data = client.get_request("/api/browser/screenshot", {"sessionId": _sid()}, api_url=_api())
    if not data.get("ok"):
        _out(data)
        return
    if output:
        raw = base64.b64decode(data["screenshot"])
        Path(output).write_bytes(raw)
        if _json_output:
            typer.echo(json.dumps({"ok": True, "file": output, "size": len(raw)}))
        else:
            console.print(f"[green]Screenshot saved to {output} ({len(raw)} bytes)[/green]")
    else:
        if _json_output:
            typer.echo(json.dumps(data, ensure_ascii=False))
        else:
            console.print(data["screenshot"][:80] + "...")
            console.print(f"[dim]({len(data['screenshot'])} chars base64, use --output to save)[/dim]")


@app.command()
def logs(tail: int = typer.Option(200, "--tail", "-n")):
    """View container CDP logs."""
    data = client.get_request(f"/api/sessions/{_sid()}/logs", {"tail": tail}, api_url=_api())
    if _json_output:
        typer.echo(json.dumps(data, ensure_ascii=False))
    else:
        for entry in data.get("logs", []):
            ts = entry.get("ts", "")
            typ = entry.get("type", "?")
            msg = entry.get("message", entry.get("url", json.dumps(entry, ensure_ascii=False)))
            console.print(f"[dim]{ts}[/dim] [{typ}] {msg}")


if __name__ == "__main__":
    app()
