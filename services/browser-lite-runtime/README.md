# Browser Lite Runtime

`browser_lite` is a persistent native Chrome runtime for macOS. It keeps one
named Chrome profile alive and exposes the WebDriver/CDP subset used by Browser
Pilot on a loopback-only HTTP endpoint.

This is a runtime instance, not an Ego Lite/Chromium fork. It intentionally does
not implement Ego's same-process Spaces, login-state cloning, native Snapshot,
or human/agent handoff UI. Separate instance IDs use separate Chrome profiles
and processes.

## Requirements

- macOS with Google Chrome installed.
- Node.js 22 or newer.
- An active macOS GUI login session. Chrome cannot be launched by a system
  daemon outside the user's Aqua session.

No npm dependencies or package installation are required.

## Run once

```bash
node browser-lite.mjs \
  --host 127.0.0.1 \
  --port 4444 \
  --instance-id browser_lite
```

The default profile is stored at:

```text
~/Library/Application Support/Browser Pilot/Browser Lite/instances/browser_lite/profile
```

Stopping the control service leaves Chrome running. Restarting the service
reconnects to the same Chrome process through the profile's
`DevToolsActivePort`, which preserves open tabs and login state.

## Install as a macOS LaunchAgent

Run this from the final directory where the runtime will remain installed:

```bash
node install-launch-agent.mjs --instance-id browser_lite --port 4444
```

Preview and validate the generated plist without installing it:

```bash
node install-launch-agent.mjs --instance-id browser_lite --port 4444 --dry-run \
  > /tmp/browser-lite.plist
plutil -lint /tmp/browser-lite.plist
```

The LaunchAgent is scoped to the signed-in user, starts inside the Aqua GUI
session, binds only to `127.0.0.1`, and restarts the control service if it exits.
Uninstall it with:

```bash
node install-launch-agent.mjs --instance-id browser_lite --uninstall
```

## Smoke test

With the service running:

```bash
node smoke-test.mjs \
  --base-url http://127.0.0.1:4444 \
  --url https://example.com/ \
  --screenshot /tmp/browser-lite-smoke.png
```

The test creates/reuses the fixed WebDriver session, navigates a real page,
executes JavaScript, stores a localStorage marker, calls raw CDP, and captures a
PNG screenshot. To verify process/service restart persistence, save the printed
marker and run again with `--expect-marker <marker>` after restarting the
service.

## Browser Pilot compatibility surface

- `GET /status`, `POST /session`, `DELETE /session/:id`
- URL, title, source, refresh, back, and forward
- synchronous JavaScript execution and screenshots
- raw `goog/cdp/execute`
- window handles, switching, closing, and window rectangle
- W3C keyboard, pointer, and wheel actions
- cookie read, write, and delete
- `GET /browser-lite/status` for runtime-specific diagnostics

The loopback binding is a security boundary. For remote use, connect through an
authenticated SSH tunnel or a future Browser Pilot node agent; do not change
the host to `0.0.0.0` on an untrusted network.
