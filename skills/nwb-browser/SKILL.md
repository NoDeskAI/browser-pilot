---
name: nwb-browser
description: Control headless browser sessions via the nwb CLI. Each session runs in an isolated Selenium container with Chrome, accessible via noVNC for visual monitoring.
---

# nwb — No-Window Browser CLI

A CLI tool that controls headless browser sessions. Each session gets its own Docker container running Chrome + Selenium, with anti-bot stealth measures pre-configured.

## Prerequisites

```bash
pip install /path/to/no-window-browser/cli
nwb config set api-url http://localhost:8000
```

The backend (`no-window-browser-backend`) must be running on the configured API URL.

## Workflow

A typical automation sequence:

```bash
# 1. Create a session
nwb session create --name "Task Name"
# Returns: session ID

# 2. Set it as active (so you don't need --session every time)
nwb session use <session-id>

# 3. Navigate (container starts automatically if not running)
nwb navigate https://example.com

# 4. Observe the page — see all interactive elements with coordinates
nwb observe

# 5. Interact
nwb click 640 380                  # click coordinates from observe
nwb click-element "a.login-btn"   # or use CSS selector
nwb type "hello world"             # type into focused input
nwb key Enter                      # press a key

# 6. Verify result
nwb observe

# 7. Screenshot for visual confirmation
nwb screenshot --output result.png
```

## Commands Reference

All browser commands use the active session (set via `nwb session use`) or can be overridden with `--session/-s`.

Add `--json` / `-j` before the command for machine-readable output.

### Session Management

| Command | Description |
|---------|-------------|
| `nwb session list` | List all sessions with container status |
| `nwb session create [--name NAME]` | Create new session, returns ID |
| `nwb session use <id>` | Set active session |
| `nwb session start [<id>]` | Start container (optional — browser commands auto-start) |
| `nwb session stop [<id>]` | Stop container (saves resources) |
| `nwb session delete <id>` | Delete session and container |

### Browser Primitives

| Command | Description |
|---------|-------------|
| `nwb navigate <url>` | Navigate to URL |
| `nwb observe` | Get page URL, title, visible text, and all interactive elements with coordinates |
| `nwb click <x> <y>` | Click at coordinates (use coords from observe) |
| `nwb click-element <selector>` | Click element by CSS selector |
| `nwb type <text>` | Type text into focused input |
| `nwb key <key>` | Press key: Enter, Tab, Escape, Backspace, ArrowUp, ArrowDown, etc. |
| `nwb scroll <delta-y> [--delta-x N]` | Scroll page (positive delta-y = down) |
| `nwb tabs` | List all browser tabs |
| `nwb switch-tab [--handle H \| --index I]` | Switch tab |
| `nwb page-info` | Get current URL and title (lightweight) |
| `nwb screenshot [--output FILE]` | Capture screenshot (base64 or save to file) |
| `nwb logs [--tail N]` | View container diagnostic logs |

### Configuration

| Command | Description |
|---------|-------------|
| `nwb config init` | Interactive setup |
| `nwb config set <key> <value>` | Set config (api-url, active-session) |
| `nwb config show` | Show current config |

## Example: Boss直聘 QR Code Login

```bash
nwb session create --name "Boss直聘"
# Output: Created session: a1b2c3d4-...

nwb session use a1b2c3d4-...

nwb navigate https://zhipin.com
# Navigates, container auto-starts if needed

nwb observe
# Shows page elements, find "我要招聘" link

nwb click-element "a[href*='intent=1']"
# Clicks "我要招聘", navigates to login page

nwb observe
# Shows login page elements, find "APP扫码登录" tab

nwb click 540 66
# Click "APP扫码登录" tab (coordinates from observe)

nwb observe
# Verify: should show QR code image element, ~12 elements

nwb screenshot --output qr-code.png
# Save QR code screenshot for user to scan
```

## Important Notes

- **Container auto-start**: Browser commands automatically start the container if it's not running. You only need `nwb session start` if you want the VNC port for visual monitoring.
- **Anti-bot stealth**: Each container runs Chrome with fingerprint spoofing, human-like click/type patterns, and timezone override (Asia/Shanghai).
- **Per-call sessions**: Each CLI command creates and destroys a WebDriver session to minimize detection. This adds ~200ms overhead per command but prevents anti-bot triggers.
- **Observe before click**: Always run `nwb observe` to get current element coordinates before clicking. Coordinates change when the page updates.
