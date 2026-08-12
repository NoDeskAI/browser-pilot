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

The app opens a dashboard and a persistent `browser_lite` browser instance. The
local compatibility endpoint is `http://127.0.0.1:4444`; it is loopback-only.

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
- Node token: encrypted using macOS Keychain-backed Electron `safeStorage`
- Remote connection: outbound WebSocket to Browser Pilot
- Local WebDriver/CDP: loopback only

Stopping an instance keeps its partition. Removing an instance clears its
cookies, storage, and cache.
