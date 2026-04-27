---
name: bpilot-browser
description: Control headless browser sessions via the bpilot CLI. Each session runs in an isolated Selenium container with Chrome, accessible via noVNC for visual monitoring.
---

# bpilot — Browser Pilot CLI

A CLI tool that controls browser sessions. Each session gets its own Docker container running Chrome + Selenium, with anti-bot stealth measures pre-configured.

## Prerequisites

```bash
curl -fsSL http://localhost:8000/api/cli/install | bash
bpilot config set api-url http://localhost:8000
```

The backend (`browser-pilot-backend`) must be running on the configured API URL. For Agent usage, copy the real session id into every `--session "<session-id>"` argument.

## Workflow

A typical stateless automation sequence:

```bash
# 1. Create a session.
bpilot session create --name "Task Name" --json
# Read the returned "id".

# 2. Start the target session when you need the VNC port.
bpilot --session "<session-id>" session start

# 3. Navigate. The container auto-starts if it is not running.
bpilot --session "<session-id>" navigate https://example.com

# 4. Observe the page.
bpilot --session "<session-id>" observe --json

# 5. Interact.
bpilot --session "<session-id>" click 640 380
bpilot --session "<session-id>" click-element "a.login-btn"
bpilot --session "<session-id>" type "hello world"
bpilot --session "<session-id>" key Enter

# 6. Verify result.
bpilot --session "<session-id>" observe --json

# 7. Screenshot for visual confirmation.
bpilot --session "<session-id>" screenshot --output result.png
```

## Commands Reference

Agent workflows should be stateless: do not use `bpilot session use`, shell variables, or saved active sessions. Pass the real id with `--session "<session-id>"` on every command that targets a browser session.

Add `--json` / `-j` to state-reading commands for machine-readable output.

### Session Management

| Command | Description |
|---------|-------------|
| `bpilot session list --json` | List all sessions with container status |
| `bpilot session create --name NAME --json` | Create new session, returns ID |
| `bpilot --session "<session-id>" session start` | Start container for the target session |
| `bpilot --session "<session-id>" session stop` | Stop container for the target session |
| `bpilot --session "<session-id>" session delete` | Delete session and container |

### Browser Primitives

| Command | Description |
|---------|-------------|
| `bpilot --session "<session-id>" navigate <url>` | Navigate to URL |
| `bpilot --session "<session-id>" observe --json` | Get page URL, title, visible text, and all interactive elements with coordinates |
| `bpilot --session "<session-id>" click <x> <y>` | Click at coordinates from observe |
| `bpilot --session "<session-id>" click-element <selector>` | Click element by CSS selector |
| `bpilot --session "<session-id>" type <text>` | Type into focused input |
| `bpilot --session "<session-id>" key <key>` | Press key: Enter, Tab, Escape, Backspace, ArrowUp, ArrowDown, etc. |
| `bpilot --session "<session-id>" scroll <delta-y> [--delta-x N]` | Scroll page; positive delta-y means down |
| `bpilot --session "<session-id>" tabs --json` | List browser tabs |
| `bpilot --session "<session-id>" switch-tab [--handle H \| --index I]` | Switch tab |
| `bpilot --session "<session-id>" page-info --json` | Get current URL and title |
| `bpilot --session "<session-id>" screenshot [--output FILE]` | Capture screenshot |
| `bpilot --session "<session-id>" logs [--tail N]` | View container diagnostic logs |

### Configuration

| Command | Description |
|---------|-------------|
| `bpilot config init` | Interactive setup |
| `bpilot config set <key> <value>` | Set config (api-url, api-token) |
| `bpilot config show` | Show current config |

## Example: Boss直聘 QR Code Login

```bash
bpilot session create --name "Boss直聘" --json
# Read the returned id, for example: a1b2c3d4-...

bpilot --session "a1b2c3d4-..." navigate https://zhipin.com
# Navigates, container auto-starts if needed.

bpilot --session "a1b2c3d4-..." observe --json
# Find the "我要招聘" link.

bpilot --session "a1b2c3d4-..." click-element "a[href*='intent=1']"
# Clicks "我要招聘", navigates to login page.

bpilot --session "a1b2c3d4-..." observe --json
# Find the "APP扫码登录" tab.

bpilot --session "a1b2c3d4-..." click 540 66
# Click "APP扫码登录" tab using coordinates from observe.

bpilot --session "a1b2c3d4-..." observe --json
# Verify: should show QR code image element.

bpilot --session "a1b2c3d4-..." screenshot --output qr-code.png
# Save QR code screenshot for user to scan.
```

## Important Notes

- **Stateless session targeting**: For Agent usage, copy the real session id into every `--session "<session-id>"` argument. Do not rely on `session use`, shell variables, or saved `active_session`.
- **Container auto-start**: Browser commands automatically start the container if it is not running. You only need `bpilot --session "<session-id>" session start` if you want the VNC port for visual monitoring.
- **Anti-bot stealth**: Each container runs Chrome with fingerprint spoofing, human-like click/type patterns, and timezone override (Asia/Shanghai).
- **Per-call sessions**: Each CLI command creates and destroys a WebDriver session to minimize detection. This adds small overhead per command but prevents anti-bot triggers.
- **Observe before click**: Always run `bpilot --session "<session-id>" observe --json` to get current element coordinates before clicking. Coordinates change when the page updates.
