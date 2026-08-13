# Browser Lite macOS app

Browser Lite is the native macOS runtime node for Browser Pilot. The app bundles
Electron's arm64 Chromium and exposes the Browser Pilot WebDriver/CDP
compatibility surface without depending on system Chrome, Node.js, Docker, or a
LaunchAgent.

## Development

```bash
npm install
npm test
npm start
```

The app uses one native window. It opens on the settings page and swaps an
isolated browser view into that same window when a Browser Pilot instance is
opened or controlled. The local compatibility endpoint is
`http://127.0.0.1:4444`; it is loopback-only.

## Build DMG

```bash
npm run build:dmg
```

Output:

```text
dist/Browser-Lite-0.1.0-arm64.dmg
```

The build uses an available Developer ID or Apple Development identity. If the
Mac has no signing identity, it creates an ad-hoc signed build for local testing.
Production distribution additionally requires a Developer ID Application
certificate and Apple notarization credentials. `dist/build-manifest.json`
records whether notarization was performed, so a development build cannot be
mistaken for a public release.

## Data and isolation

- App configuration: `~/Library/Application Support/Browser Lite`
- Each Browser Pilot session: a separate persistent Electron partition
- Node token: AES-256-GCM encrypted with a per-installation 32-byte key
- Installation key: local `node-key.bin`, created once with owner-only mode `0600`
- Remote connection: outbound WebSocket to Browser Pilot
- Local WebDriver/CDP: loopback only

Stopping an instance keeps its partition. Removing an instance clears its
cookies, storage, and cache.

## Browser Pilot pairing

Generate a 10-digit one-time code in Browser Pilot, then enter it in the app.
For managed Mac nodes, pairing can also be initiated without typing into the UI:

```bash
"/Applications/Browser Lite.app/Contents/MacOS/Browser Lite" \
  --pair-server https://bpilot.nodeskai.com \
  --pairing-code 0123456789
```

The code expires after 10 minutes and can be used once. The long-lived node
token is returned only to the app, encrypted with the local installation key,
and is never accepted as a command-line argument. The test build deliberately
does not use interactive Keychain access, so unattended upgrades and restarts
do not trigger a macOS password prompt.

Browser Lite deliberately does not expose a remote shell, container egress
profiles, synthetic fingerprint injection, or a Browser Pilot Web VNC viewer.
Browser automation, screenshots, CDP, persistent profiles, and lifecycle
controls run against the real Chromium window on the Mac node.
