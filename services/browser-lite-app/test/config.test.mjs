import assert from "node:assert/strict";
import { test } from "node:test";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

test("app manifest is arm64 DMG buildable and uses Electron Chromium", async () => {
  const manifest = JSON.parse(await readFile(join(ROOT, "package.json"), "utf8"));
  assert.equal(manifest.main, "src/main.mjs");
  assert.match(manifest.devDependencies.electron, /^43\./);
  assert.equal(manifest.scripts["build:dmg"], "node scripts/build-dmg.mjs");
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

test("settings and browser share one native BrowserWindow", async () => {
  const main = await readFile(join(ROOT, "src", "main.mjs"), "utf8");
  const runtime = await readFile(join(ROOT, "src", "electron-runtime.mjs"), "utf8");
  const html = await readFile(join(ROOT, "src", "renderer", "index.html"), "utf8");
  assert.equal((main.match(/new BrowserWindow\(/g) || []).length, 1);
  assert.doesNotMatch(runtime, /new BrowserWindow\(/);
  assert.match(runtime, /new WebContentsView\(/);
  assert.match(main, /browser-lite:show-settings/);
  assert.match(html, /id="show-settings"/);
});

test("first launch requires an installation decision before browser startup", async () => {
  const main = await readFile(join(ROOT, "src", "main.mjs"), "utf8");
  const installation = await readFile(join(ROOT, "src", "installation.mjs"), "utf8");
  const html = await readFile(join(ROOT, "src", "renderer", "index.html"), "utf8");
  assert.match(main, /if \(installation\.isComplete\(\)\) \{[\s\S]*await manager\.ensure/);
  assert.match(main, /async function startNodeAgent/);
  assert.match(main, /browser-lite:install-import/);
  assert.match(main, /browser-lite:install-fresh/);
  assert.match(installation, /Chrome Safe Storage/);
  assert.match(installation, /CHROME_STORAGE_ITEMS/);
  assert.match(installation, /Imported Profile Seed/);
  assert.match(html, /id="installer"/);
  assert.match(html, /导入并开始使用/);
  assert.match(html, /密码与扩展/);
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

test("DMG staging preserves Electron framework relative symlinks", async () => {
  const source = await readFile(join(ROOT, "scripts", "build-dmg.mjs"), "utf8");
  assert.match(source, /verbatimSymlinks:\s*true/);
  assert.match(source, /BROWSER_LITE_TEST_BUILD: "1"/);
});

test("packaged app registers as a persistent login item", async () => {
  const source = await readFile(join(ROOT, "src", "main.mjs"), "utf8");
  assert.match(source, /setLoginItemSettings\(\{ openAtLogin: true, openAsHidden: true \}\)/);
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
  assert.doesNotMatch(source, /--node-token/);
});
