---
name: bpilot-browser
description: Control headless browser sessions via the bpilot CLI. Each session runs in an isolated Selenium container with Chrome, accessible via noVNC for visual monitoring.
---

# bpilot — Browser Pilot CLI

A CLI tool that controls headless browser sessions. Each session gets its own Docker container running Chrome + Selenium, with anti-bot stealth measures pre-configured.

## Prerequisites

```bash
pip install /path/to/browser-pilot/cli
bpilot config set api-url http://localhost:8000
```

The backend (`browser-pilot-backend`) must be running on the configured API URL.

## Workflow

A typical automation sequence:

```bash
# 1. Create a session
bpilot session create --name "Task Name"
# Returns: session ID

# 2. Set it as active (so you don't need --session every time)
bpilot session use <session-id>

# 3. Navigate (container starts automatically if not running)
bpilot navigate https://example.com

# 4. Observe the page — see all interactive elements with coordinates
bpilot observe

# 5. Interact
bpilot click 640 380                  # click coordinates from observe
bpilot click-element "a.login-btn"   # or use CSS selector
bpilot type "hello world"             # type into focused input
bpilot key Enter                      # press a key

# 6. Verify result
bpilot observe

# 7. Screenshot for visual confirmation
bpilot screenshot --output result.png
```

## Commands Reference

All browser commands use the active session (set via `bpilot session use`) or can be overridden with `--session/-s`.

Add `--json` / `-j` before the command for machine-readable output.

### Session Management

| Command | Description |
|---------|-------------|
| `bpilot session list` | List all sessions with container status |
| `bpilot session create [--name NAME] [--device PRESET] [--proxy URL]` | Create new session, returns ID |
| `bpilot session use <id>` | Set active session |
| `bpilot session start [<id>]` | Start container (optional — browser commands auto-start) |
| `bpilot session stop [<id>]` | Stop container (saves resources) |
| `bpilot session delete <id>` | Delete session and container |
| `bpilot session set-device <preset>` | Change device preset (restarts container). Presets: `desktop-1920x1080` (default), `desktop-1600x900`, `desktop-1440x900`, `desktop-1366x768`, `desktop-1280x800`, `desktop-1280x720`, `iphone-16-pro-max`, `iphone-16`, `iphone-se`, `ipad-air`, `galaxy-s24`, `pixel-8` |
| `bpilot session set-proxy <url>` | Set proxy (restarts container). Formats: `http://host:port`, `socks5://host:port`. Empty string to clear |

### Browser Primitives

| Command | Description |
|---------|-------------|
| `bpilot navigate <url>` | Navigate to URL |
| `bpilot observe` | Get page URL, title, visible text, and all interactive elements with coordinates |
| `bpilot click <x> <y>` | Click at coordinates (use coords from observe) |
| `bpilot click-element <selector>` | Click element by CSS selector |
| `bpilot type <text>` | Type text into focused input |
| `bpilot key <key>` | Press key: Enter, Tab, Escape, Backspace, ArrowUp, ArrowDown, etc. |
| `bpilot scroll <delta-y> [--delta-x N]` | Scroll page (positive delta-y = down) |
| `bpilot tabs` | List all browser tabs |
| `bpilot switch-tab [--handle H \| --index I]` | Switch tab |
| `bpilot page-info` | Get current URL and title (lightweight) |
| `bpilot screenshot [--output FILE]` | Capture screenshot (base64 or save to file) |
| `bpilot logs [--tail N]` | View container diagnostic logs |

### Configuration

| Command | Description |
|---------|-------------|
| `bpilot config init` | Interactive setup |
| `bpilot config set <key> <value>` | Set config (api-url, active-session) |
| `bpilot config show` | Show current config |

## Example: Boss直聘 QR Code Login

```bash
bpilot session create --name "Boss直聘"
# Output: Created session: a1b2c3d4-...

bpilot session use a1b2c3d4-...

bpilot navigate https://zhipin.com
# Navigates, container auto-starts if needed

bpilot observe
# Shows page elements, find "我要招聘" link

bpilot click-element "a[href*='intent=1']"
# Clicks "我要招聘", navigates to login page

bpilot observe
# Shows login page elements, find "APP扫码登录" tab

bpilot click 540 66
# Click "APP扫码登录" tab (coordinates from observe)

bpilot observe
# Verify: should show QR code image element, ~12 elements

bpilot screenshot --output qr-code.png
# Save QR code screenshot for user to scan
```

## Important Notes

- **Container auto-start**: Browser commands automatically start the container if it's not running. You only need `bpilot session start` if you want the VNC port for visual monitoring.
- **Anti-bot stealth**: Each container runs Chrome with fingerprint spoofing, human-like click/type patterns, and timezone override (Asia/Shanghai).
- **Per-call sessions**: Each CLI command creates and destroys a WebDriver session to minimize detection. This adds ~200ms overhead per command but prevents anti-bot triggers.
- **Observe before click**: Always run `bpilot observe` to get current element coordinates before clicking. Coordinates change when the page updates.
- **Device presets**: Use `--device` on create or `set-device` to switch between desktop resolutions and mobile emulation (iPhone/iPad/Galaxy/Pixel). Mobile presets automatically set DPR, viewport, and user agent. Switching restarts the container (~5-8s) but preserves cookies/bookmarks.
- **Proxy support**: Use `--proxy` on create or `set-proxy` to route traffic through HTTP/SOCKS proxy. Supports `http://`, `https://`, `socks4://`, `socks5://`. Changing proxy restarts the container.
