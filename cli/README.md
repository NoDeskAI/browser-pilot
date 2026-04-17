# bpilot-cli

Command-line tool for [browser-pilot](https://github.com/NoDeskAI/browser-pilot) — control headless browser sessions from your terminal or integrate with AI Agent frameworks.

## Install

```bash
pip install bpilot-cli
```

Or from source:

```bash
pip install ./cli
```

## Quick Start

```bash
bpilot config set api-url http://localhost:8000

bpilot session create --name "My Task"
bpilot session use <session-id>

bpilot navigate https://example.com
bpilot observe
bpilot click 640 380
bpilot type "hello world"
bpilot screenshot --output page.png
```

Add `--json` for machine-readable output (for AI Agents).

## Commands

| Command | Description |
|---------|-------------|
| `config init` | Interactive configuration setup |
| `config set <key> <value>` | Set config value (api-url, active-session) |
| `config show` | Show current configuration |
| `session list` | List all sessions |
| `session create [--name <n>] [--device <d>] [--proxy <p>]` | Create a new session |
| `session use <id>` | Set active session |
| `session start [id]` | Start browser container |
| `session stop [id]` | Stop browser container |
| `session delete <id>` | Delete session and container |
| `navigate <url>` | Navigate to URL |
| `observe` | Get page elements with coordinates |
| `click <x> <y>` | Click at coordinates |
| `click-element <selector>` | Click by CSS selector |
| `type <text>` | Type into focused input |
| `key <key>` | Press key (Enter, Tab, Escape, etc.) |
| `scroll <delta_y>` | Scroll the page |
| `tabs` | List browser tabs |
| `switch-tab` | Switch to a different tab |
| `screenshot [-o file]` | Take screenshot |
| `logs [--tail <n>]` | View CDP event logs |

## License

Apache License 2.0
