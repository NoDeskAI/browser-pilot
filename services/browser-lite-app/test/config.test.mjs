import assert from "node:assert/strict";
import { test } from "node:test";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

test("app manifest is arm64 DMG buildable with embedded Electron Chromium", async () => {
  const manifest = JSON.parse(await readFile(join(ROOT, "package.json"), "utf8"));
  assert.equal(manifest.main, "src/main.mjs");
  assert.match(manifest.devDependencies.electron, /^43\./);
  assert.equal(manifest.scripts["build:dmg"], "node scripts/build-dmg.mjs");
  assert.equal(manifest.version, "0.5.3");
});

test("renderer has a restrictive content security policy", async () => {
  const html = await readFile(join(ROOT, "src", "renderer", "index.html"), "utf8");
  assert.match(html, /default-src 'self'/);
  assert.match(html, /connect-src 'none'/);
  assert.doesNotMatch(html, /unsafe-eval|unsafe-inline/);
});

test("sandboxed preload uses the Electron-supported CommonJS format", async () => {
  const main = await readFile(join(ROOT, "src", "main.mjs"), "utf8");
  const preload = await readFile(join(ROOT, "src", "preload.cjs"), "utf8");
  assert.match(main, /preload\.cjs/);
  assert.match(preload, /contextBridge\.exposeInMainWorld/);
});

test("Spaces stay inside the single Browser Lite window", async () => {
  const main = await readFile(join(ROOT, "src", "main.mjs"), "utf8");
  const runtime = await readFile(join(ROOT, "src", "electron-runtime.mjs"), "utf8");
  const html = await readFile(join(ROOT, "src", "renderer", "index.html"), "utf8");
  assert.equal((main.match(/new BrowserWindow\(/g) || []).length, 1);
  assert.doesNotMatch(runtime, /new BrowserWindow\(/);
  assert.match(runtime, /const state = new ElectronBrowserLiteState\(config/);
  assert.match(runtime, /new WebContentsView\(/);
  assert.match(runtime, /this\.config\.hostWindow\.contentView\.addChildView\(view\)/);
  assert.match(runtime, /startUrl: "about:blank"/);
  assert.doesNotMatch(runtime, /this\.hostWindow\.hide\(\)/);
  assert.doesNotMatch(runtime, /nativeChromiumBinary|onNativeBrowserExit|--load-extension/);
  assert.match(main, /browser-lite:show-settings/);
  assert.match(main, /browser-lite:show-spaces/);
  assert.doesNotMatch(html, /id="show-spaces"/);
  assert.match(html, /id="settings-page"/);
});

test("Dock and status item restore the main workspace while right click exposes settings", async () => {
  const main = await readFile(join(ROOT, "src", "main.mjs"), "utf8");
  assert.match(main, /async function showMainWindow\(\)[\s\S]*workspace\?\.browserAvailable[\s\S]*showInstance/);
  assert.match(main, /async function showMainWindow\(\)[\s\S]*manager\.showSpaces\(\)/);
  assert.match(main, /app\.on\("activate", requestMainWindow\)/);
  assert.match(main, /tray\.on\("click", requestMainWindow\)/);
  assert.match(main, /label: "打开设置", click: showSettingsWindow/);
  assert.match(main, /tray\.on\("right-click", \(\) => tray\.popUpContextMenu\(contextMenu\)\)/);
  assert.doesNotMatch(main, /tray\.setContextMenu/);
});

test("renderer follows the Ego Lite overview and embedded views expose previews", async () => {
  const html = await readFile(join(ROOT, "src", "renderer", "index.html"), "utf8");
  const css = await readFile(join(ROOT, "src", "renderer", "styles.css"), "utf8");
  const runtime = await readFile(join(ROOT, "src", "electron-runtime.mjs"), "utf8");
  assert.match(html, /id="spaces-title"/);
  assert.match(html, /class="space-gallery"/);
  assert.match(html, /chrome:\/\/settings\//);
  assert.match(html, /class="settings-sidebar"/);
  assert.match(css, /color-scheme:\s*light/);
  assert.match(css, /grid-template-columns:\s*repeat\(4/);
  assert.match(css, /\.spaces-topbar\s*>\s*\.round-count\s*\{[^}]*top:\s*9px;\s*right:\s*14px/);
  assert.match(css, /\.tab-strip>\.round-count\s*\{[^}]*top:9px;\s*right:14px/);
  assert.match(css, /--toolbar-height:\s*112px/);
  assert.match(runtime, /ElectronBrowserLiteState/);
  assert.match(runtime, /window\.webContents\.debugger\.sendCommand/);
  assert.match(runtime, /view\.setVisible\(this\.visible && id === this\.activeTargetId\)/);
});

test("Task Space workspace delegates tabs and navigation to embedded views", async () => {
  const main = await readFile(join(ROOT, "src", "main.mjs"), "utf8");
  const preload = await readFile(join(ROOT, "src", "preload.cjs"), "utf8");
  const renderer = await readFile(join(ROOT, "src", "renderer", "renderer.mjs"), "utf8");
  const runtime = await readFile(join(ROOT, "src", "electron-runtime.mjs"), "utf8");
  const html = await readFile(join(ROOT, "src", "renderer", "index.html"), "utf8");
  const css = await readFile(join(ROOT, "src", "renderer", "styles.css"), "utf8");
  assert.match(main, /browser-lite:create-task-space/);
  assert.match(main, /browser-lite:task-space-browser-action/);
  assert.match(preload, /returnTaskSpace/);
  assert.match(renderer, /taskSpaceBrowserAction/);
  assert.match(html, /id="return-control"/);
  assert.match(html, /id="task-control-bar"/);
  assert.match(html, /id="overview-space-count"/);
  assert.doesNotMatch(html, /id="overview-space-switcher"/);
  assert.match(html, /id="browser-space-count"/);
  assert.match(html, /id="settings-space-count"/);
  assert.match(renderer, /countButton\.addEventListener\("click"[\s\S]*showSpaces\(\)/);
  assert.match(renderer, /overviewSpaceCount\.disabled = inSpacesOverview/);
  assert.match(renderer, /if \(currentState\?\.workspace\?\.mode === "spaces"\) return/);
  assert.match(runtime, /TASK_CONTROL_RESERVE/);
  assert.match(css, /prefers-reduced-motion/);
  assert.match(css, /focus-visible/);
  assert.match(runtime, /new ElectronBrowserLiteState\(config/);
  assert.match(await readFile(join(ROOT, "src", "task-space-manager.mjs"), "utf8"), /await state\.stopLoading\(\)/);
});

test("first launch requires an installation decision before browser startup", async () => {
  const main = await readFile(join(ROOT, "src", "main.mjs"), "utf8");
  const installation = await readFile(join(ROOT, "src", "installation.mjs"), "utf8");
  const html = await readFile(join(ROOT, "src", "renderer", "index.html"), "utf8");
  assert.match(main, /if \(installation\.isComplete\(\)\) \{[\s\S]*await startNodeAgent\(\)/);
  assert.match(main, /async function startNodeAgent/);
  assert.match(main, /browser-lite:install-import/);
  assert.match(main, /browser-lite:install-fresh/);
  assert.doesNotMatch(main, /await manager\.ensure\("browser_lite"/);
  assert.match(installation, /Chrome Safe Storage/);
  assert.match(installation, /Chromium Safe Storage/);
  assert.match(installation, /com\.citrolabs\.ego\.lite/);
  assert.match(installation, /CHROME_STORAGE_ITEMS/);
  assert.match(installation, /Imported Profile Seed/);
  assert.match(installation, /SELECT url, title FROM top_sites ORDER BY url_rank ASC LIMIT 7/);
  assert.match(installation, /captureChromiumSeed/);
  assert.match(installation, /Chromium User Data/);
  assert.match(installation, /return "about:blank"/);
  assert.doesNotMatch(installation, /data:text\/html/);
  assert.doesNotMatch(installation, /radial-gradient\(circle at 80% -10%/);
  assert.match(html, /id="installer"/);
  assert.match(html, /导入并开始使用/);
  assert.match(html, /扩展与 Tab 组/);
  assert.match(html, /<strong>密码<\/strong><small>暂不支持<\/small>/);
});

test("the Browser Lite shell owns the stable Space-count button", async () => {
  const main = await readFile(join(ROOT, "src", "main.mjs"), "utf8");
  const html = await readFile(join(ROOT, "src", "renderer", "index.html"), "utf8");
  const renderer = await readFile(join(ROOT, "src", "renderer", "renderer.mjs"), "utf8");
  const runtime = await readFile(join(ROOT, "src", "electron-runtime.mjs"), "utf8");
  const build = await readFile(join(ROOT, "scripts", "build-dmg.mjs"), "utf8");
  assert.match(main, /globalShortcut\.register\("Alt\+S"/);
  assert.match(html, /id="browser-space-count"/);
  assert.match(renderer, /countButton\.addEventListener\("click"[\s\S]*showSpaces\(\)/);
  assert.match(runtime, /await app\.dock\?\.show\(\);[\s\S]*this\.hostWindow\.show\(\)/);
  assert.doesNotMatch(runtime, /prepareRuntimeExtension|extensionPath|controlToken/);
  assert.doesNotMatch(build, /BROWSER_LITE_CHROMIUM_APP|bundledChromiumRoot|brandBundledChromium/);
});

test("embedded Browser Lite keeps bookmarks and browser chrome in its shell", async () => {
  const html = await readFile(join(ROOT, "src", "renderer", "index.html"), "utf8");
  const renderer = await readFile(join(ROOT, "src", "renderer", "renderer.mjs"), "utf8");
  assert.match(html, /id="browser-chrome"/);
  assert.match(html, /id="browser-bookmarks"/);
  assert.match(renderer, /elements\.browserBookmarks\.innerHTML = markup/);
  assert.match(html, /id="extensions-menu-button"/);
  assert.doesNotMatch(html, /id="new-tab-group"/);
  assert.match(renderer, /class="saved-tab-groups-menu" data-action="new-tab-group"/);
  assert.match(renderer, /class="bookmark-folder-icon" viewBox="0 0 16 16"/);
  assert.match(renderer, /class="extension-row-menu"/);
  assert.match(renderer, /class="extension-pin-state/);
  assert.match(renderer, /openBookmarkFolder/);
  assert.match(renderer, /openExtensionsMenu/);
  assert.match(renderer, /createTabGroup/);
  assert.match(await readFile(join(ROOT, "src", "installation.mjs"), "utf8"), /runtimeExtensions/);
  assert.match(await readFile(join(ROOT, "src", "electron-runtime.mjs"), "utf8"), /extensions\.loadExtension/);
});

test("settings expose recoverable reset and uninstall flows", async () => {
  const main = await readFile(join(ROOT, "src", "main.mjs"), "utf8");
  const installation = await readFile(join(ROOT, "src", "installation.mjs"), "utf8");
  const html = await readFile(join(ROOT, "src", "renderer", "index.html"), "utf8");
  assert.match(main, /browser-lite:reset-installation/);
  assert.match(main, /browser-lite:uninstall/);
  assert.match(main, /setLoginItemSettings\(\{ openAtLogin: false/);
  assert.doesNotMatch(main, /installation\.purgeData/);
  assert.match(installation, /\.Trash/);
  assert.match(installation, /preservePairing/);
  assert.match(html, /id="reset-installation"/);
  assert.match(html, /id="uninstall-app"/);
});

test("remote node token is encrypted without interactive Keychain access", async () => {
  const source = await readFile(join(ROOT, "src", "node-agent.mjs"), "utf8");
  assert.match(source, /createCipheriv\("aes-256-gcm"/);
  assert.match(source, /createDecipheriv\("aes-256-gcm"/);
  assert.match(source, /node-key\.bin/);
  assert.match(source, /mode: 0o600, flag: "wx"/);
  assert.doesNotMatch(source, /safeStorage|Keychain/);
  assert.match(source, /type: "auth", token: config\.token/);
  assert.doesNotMatch(source, /searchParams\.set\("token"/);
});

test("task-space capability is refreshed on reconnect and uses an allowlisted controller", async () => {
  const agent = await readFile(join(ROOT, "src", "node-agent.mjs"), "utf8");
  const controller = await readFile(join(ROOT, "src", "task-space-manager.mjs"), "utf8");
  assert.match(agent, /NODE_CAPABILITIES[\s\S]*"task_spaces"/);
  assert.match(agent, /type: "hello"[\s\S]*capabilities: NODE_CAPABILITIES/);
  assert.match(agent, /case "task_space"/);
  assert.match(agent, /errorCode: error\.code \|\| error\.error_code/);
  assert.match(controller, /const allowed = new Set\(/);
  assert.doesNotMatch(controller, /eval\(|new Function\(/);
});

test("DMG staging preserves Electron framework relative symlinks", async () => {
  const source = await readFile(join(ROOT, "scripts", "build-dmg.mjs"), "utf8");
  assert.match(source, /verbatimSymlinks:\s*true/);
  assert.match(source, /BROWSER_LITE_TEST_BUILD: "1"/);
  assert.match(source, /Browser-Lite-\$\{packageManifest\.version\}-arm64\.dmg/);
  assert.match(source, /"--force", "--deep", "--sign", identity \|\| "-"/);
});

test("Browser Lite owns its Dock identity and never packages an external Chromium app", async () => {
  const build = await readFile(join(ROOT, "scripts", "build-dmg.mjs"), "utf8");
  const main = await readFile(join(ROOT, "src", "main.mjs"), "utf8");
  assert.match(build, /LSUIElement:\s*false/);
  assert.match(build, /chromiumRuntime: "embedded Electron WebContentsView"/);
  assert.doesNotMatch(build, /brandBundledChromium|com\.nodeskai\.browserlite\.chromium|Google Chrome for Testing/);
  assert.match(main, /app\.dock\?\.hide\(\)/);
  assert.match(main, /wasOpenedAtLogin[\s\S]*dashboardWindow\.hide\(\);[\s\S]*app\.dock\?\.hide\(\)/);
  const runtime = await readFile(join(ROOT, "src", "electron-runtime.mjs"), "utf8");
  assert.match(runtime, /new WebContentsView\(/);
  assert.match(runtime, /await app\.dock\?\.show\(\)/);
  assert.doesNotMatch(runtime, /nativeChromiumBinary|updateControllerDock|hostWindow\.hide\(\)/);
});

test("packaged app registers as a persistent login item", async () => {
  const source = await readFile(join(ROOT, "src", "main.mjs"), "utf8");
  assert.match(source, /app\.isPackaged && !isTestBuild/);
  assert.match(source, /setLoginItemSettings\(\{ openAtLogin: true, openAsHidden: true \}\)/);
  assert.match(source, /installation\.isComplete\(\) && app\.isPackaged && !isTestBuild/);
});

test("application exit waits for embedded views and local servers to stop", async () => {
  const source = await readFile(join(ROOT, "src", "main.mjs"), "utf8");
  const manager = await readFile(join(ROOT, "src", "electron-runtime.mjs"), "utf8");
  assert.match(source, /app\.on\("before-quit", \(event\) =>/);
  assert.match(source, /event\.preventDefault\(\)/);
  assert.match(source, /Promise\.resolve\(manager\?\.shutdown\(\)\)/);
  assert.match(source, /shutdownComplete = true;[\s\S]*app\.quit\(\)/);
  assert.doesNotMatch(source, /app\.on\("will-quit"[\s\S]*manager\?\.shutdown/);
  assert.match(manager, /async stop\(\)[\s\S]*this\.destroyView\(targetId\)/);
  assert.match(manager, /async shutdown\(\)[\s\S]*entry\.state\.stop\(\)[\s\S]*closeServer\(entry\.server\)/);
  assert.match(manager, /server\.closeAllConnections\?\.\(\)/);
  assert.doesNotMatch(manager, /onNativeBrowserExit|process\.kill|Browser\.close/);
});

test("packaged app supports one-time command-line pairing without accepting a node token", async () => {
  const source = await readFile(join(ROOT, "src", "main.mjs"), "utf8");
  assert.match(source, /--pair-server/);
  assert.match(source, /--pairing-code/);
  assert.match(source, /requestSingleInstanceLock\(initialPairingOptions\)/);
  assert.match(source, /second-instance[\s\S]*additionalData/);
  assert.match(source, /writeFileSync\(PAIRING_REQUEST_PATH[\s\S]*mode: 0o600/);
  assert.match(source, /consumeStagedPairingRequest/);
  assert.match(source, /unlinkSync\(PAIRING_REQUEST_PATH\)/);
  assert.match(source, /if \(hasSingleInstanceLock\) \{[\s\S]*app\.whenReady\(\)\.then\(bootstrap\)/);
  assert.match(source, /BROWSER_LITE_TEST_BUILD[\s\S]*use-mock-keychain/);
  assert.match(source, /BROWSER_LITE_TEST_USER_DATA_DIR/);
  assert.doesNotMatch(source, /--node-token/);
});
