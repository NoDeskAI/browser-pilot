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
