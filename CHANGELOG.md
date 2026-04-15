# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [1.0.0] - 2026-04-17

Initial public release.

### Added

- **Session management**: Create, start, stop, hibernate (pause/unpause), and delete isolated browser sessions via REST API.
- **Browser containers**: Each session runs in its own Docker container with Chrome, Selenium WebDriver, and anti-bot stealth extensions.
- **noVNC viewer**: Live browser viewing through WebSocket-based noVNC, with adjustable quality and compression.
- **CDP event logging**: Per-container Chrome DevTools Protocol logger (`/tmp/cdp-events.jsonl`) for debugging network, navigation, console, and error events.
- **CLI (`bpilot`)**: Command-line tool for session and browser control — `navigate`, `observe`, `click`, `type`, `screenshot`, and more. Supports `--json` for machine-readable output.
- **REST API**: Full browser automation API — navigate, observe, click, type, scroll, switch tabs, take screenshots.
- **Hibernation**: Pause/unpause browser containers with zero CPU cost; state is preserved across hibernate cycles.
- **Anti-bot stealth**: Fingerprint spoofing, human-like input simulation via native keyboard/mouse events (VNC-based).
- **File storage**: Built-in local storage with optional S3 backend, configurable via UI.
- **i18n**: Chinese and English UI, with browser-level language switching for automated sessions.
- **Docker Compose deployment**: One-command setup with PostgreSQL and backend service.
- **ARM support**: Compatible with Apple Silicon via `seleniarm/standalone-chromium` base image.
