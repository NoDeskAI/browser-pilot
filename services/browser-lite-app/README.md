# Browser Lite macOS app

Browser Lite is the native macOS runtime node for Browser Pilot. One Electron
window owns the Space overview, browser chrome, settings, and every embedded
browser view. Opening a Space swaps an isolated `WebContentsView` inside that
window; Browser Lite never launches or reuses a standalone Chrome/Chromium app.

## Development

```bash
npm install
npm test
npm start
```

Each Browser Pilot session receives a persistent, isolated Electron session
partition; users can also create local Spaces. Opening a Space keeps the host
window visible and displays the embedded view below Browser Lite's stable
toolbar. When a user takes over an agent-owned Space, agent commands hard-stop
until the user explicitly returns control. The local compatibility endpoint is
loopback-only.

Closing the window keeps Browser Lite running. Clicking its Dock icon or
left-clicking the menu bar item restores the last active Space when one is
loaded, or the Space gallery otherwise. Right-clicking the menu bar item exposes
the main-window and settings actions. In a Space, `Option+S` or the shell-owned
Space-count button returns to the gallery. The button does not depend on an
extension or profile pin state.

Browser Lite owns one application and one Dock identity. Switching between the
overview, settings, and a Space never hides the host window or transfers focus
to another browser application.

On first launch, Browser Lite requires an installation decision. The user can
import a local Ego Lite or Google Chrome profile, or choose a fresh profile. The
importer creates a filtered local seed without modifying the source profile.
Passwords and the source browser's open-window session are excluded.

## Build DMG

```bash
npm run build:dmg
```

The DMG contains Electron's embedded Chromium only. There is no build option or
fallback that packages or starts a standalone Chromium application.

Output:

```text
dist/Browser-Lite-0.5.1-arm64.dmg
```

The build uses an available Developer ID or Apple Development identity. If the
Mac has no signing identity, it creates an ad-hoc signed build for local testing.
Production distribution additionally requires a Developer ID Application
certificate and Apple notarization credentials. `dist/build-manifest.json`
records the signing and notarization state.

## Data and isolation

- App configuration: `~/Library/Application Support/Browser Lite`
- Each Browser Pilot session: a separate persistent Electron session partition
- Node token: AES-256-GCM encrypted with a per-installation 32-byte key
- Installation key: local `node-key.bin`, created once with owner-only mode `0600`
- Remote connection: outbound WebSocket to Browser Pilot
- Local WebDriver/CDP compatibility endpoint: loopback only

Stopping an instance keeps its browser data. Removing an instance clears only
that Space's isolated partition.

Settings provides two maintenance flows:

- Reset installation preserves Browser Pilot pairing, clears imported/browser
  data, restarts the app, and shows the installation assistant again.
- Complete uninstall disconnects the node, disables login startup, and moves the
  installed app and local data to the macOS Trash for recovery.

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
and is never accepted as a command-line argument. Test builds avoid interactive
Keychain access so unattended upgrades and restarts do not prompt for a macOS
password.

Browser Lite does not expose a remote shell, container egress profiles,
synthetic fingerprint injection, or a Browser Pilot Web VNC viewer. Browser
automation, screenshots, CDP, persistent sessions, and lifecycle controls run
against the embedded Chromium view on the Mac node.

## Task-space control plane

Browser Lite includes a clean-room task-space controller modeled from observed
Ego Lite behavior. No recovered vendor source is included. Each task space owns
a persistent embedded browser partition and supports agent/user handoff,
explicit takeover, completion/error states, tabs, accessibility snapshots, raw
CDP messages, and a visible pointer highlight.

The metadata is stored in `task-spaces.json`; browser cookies and storage remain
inside the per-Space Electron session partition. The authenticated node
WebSocket exposes the controller through the `task_space` action with a method
name and argument array. It does not expose a host Node.js or shell evaluator;
browser-scoped JavaScript remains available through the CDP channel.

The evidence, known limits, and inferred Mojo sketch are documented in
[`reverse-engineering/README.md`](reverse-engineering/README.md).
