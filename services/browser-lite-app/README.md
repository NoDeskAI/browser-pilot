# Browser Lite macOS app

Browser Lite is the native macOS runtime node for Browser Pilot. Electron owns
only the Space overview, installation, and settings controller. Opening a Space
launches a real, isolated Chrome/Chromium profile, so tabs, the omnibox,
bookmarks, extensions UI, settings, and `chrome://newtab/` are native Chromium
surfaces rather than HTML replicas.

## Development

```bash
npm install
npm test
npm start
```

The controller uses one native window centered on a light Task Space gallery. Each
Browser Pilot session receives a persistent, isolated Task Space; users can also
create local spaces. The gallery shows live page thumbnails. Opening a Space
hides the controller and activates that Space's native Chromium window.
When a user takes over an agent-owned space, agent commands hard-stop until the
user explicitly returns control. The local compatibility endpoint is
`http://127.0.0.1:4444`; it is loopback-only.

Closing the window keeps Browser Lite running. Clicking its Dock icon or
left-clicking the menu bar item restores the last active Task Space when one is
loaded, or the Space gallery otherwise. It never opens Settings as the main
surface. Right-clicking the menu bar item exposes separate actions for the main
window and Browser Lite settings. In a native Chromium Space, `Option+S` or the
pinned Space-count extension button returns to the gallery.

On macOS the Electron controller runs as a background UI element. Before a
native Space is loaded it temporarily owns the Browser Lite Dock icon; after
Chromium starts, the branded native Chromium app becomes the sole Dock owner.
This keeps the Space gallery and native browser under one visible Browser Lite
identity without changing Chromium's resource or sandbox layout.

On the first installed launch, Browser Lite requires an installation decision
before it starts the browser runtime. The user can import a local Ego Lite or
Google Chrome profile, or choose a fresh profile. The importer creates a
filtered Chromium user-data seed containing the selected login state,
bookmarks, history, extensions, pinned-extension order, theme, and saved Tab
groups without modifying the source profile. Password databases and the source
browser's open-window session are excluded; every Space receives an independent
copy of the seed.

## Build DMG

```bash
npm run build:dmg
```

For Ego Lite 0.4.7.1-compatible native chrome, package the pinned official
arm64 Chrome for Testing 150.0.7871.224 app with the controller:

```bash
BROWSER_LITE_CHROMIUM_APP="/path/to/Google Chrome for Testing.app" npm run build:dmg
```

If no bundled runtime is supplied, development builds fall back to the locally
installed Google Chrome. Current stable Google Chrome ignores unpacked-extension
launch flags, so that fallback keeps native browsing but cannot expose the
pinned Space button.

Output:

```text
dist/Browser-Lite-0.5.0-arm64.dmg
```

The build uses an available Developer ID or Apple Development identity. If the
Mac has no signing identity, it creates an ad-hoc signed build for local testing.
Production distribution additionally requires a Developer ID Application
certificate and Apple notarization credentials. `dist/build-manifest.json`
records whether notarization was performed, so a development build cannot be
mistaken for a public release.

## Data and isolation

- App configuration: `~/Library/Application Support/Browser Lite`
- Each Browser Pilot session: a separate persistent Chromium user-data directory
- Node token: AES-256-GCM encrypted with a per-installation 32-byte key
- Installation key: local `node-key.bin`, created once with owner-only mode `0600`
- Remote connection: outbound WebSocket to Browser Pilot
- Local WebDriver/CDP: loopback only

Stopping an instance keeps its profile. Removing an instance deletes only that
Space's isolated user-data directory.

Settings provides two maintenance flows for local testing:

- Reset installation: preserves Browser Pilot pairing, clears imported/browser
  data, restarts the app, and shows the installation assistant again.
- Complete uninstall: disconnects the node, disables login startup, and moves
  both the installed app and its local data to the macOS Trash for recovery.

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

## Task-space control plane

Browser Lite includes a clean-room task-space controller modeled from observed
Ego Lite behavior. No recovered vendor source is included. Each task space owns
a persistent Chromium profile and supports agent/user handoff, explicit
takeover, completion/error states, tabs, accessibility snapshots, raw CDP
messages, and a visible pointer highlight.
The metadata is stored in `task-spaces.json`; browser cookies and storage remain
inside the per-instance Chromium user-data directory.

The authenticated node WebSocket exposes this controller through the
`task_space` action with a method name and argument array. It does not expose a
host Node.js or shell evaluator; browser-scoped JavaScript remains available
through the explicitly exposed CDP channel.

The evidence, known limits, and inferred Mojo sketch are documented in
[`reverse-engineering/README.md`](reverse-engineering/README.md).
