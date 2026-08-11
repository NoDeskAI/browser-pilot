import assert from "node:assert/strict";
import { test } from "node:test";

import { parseArgs, resolveChromeBinary, VERSION } from "./browser-lite.mjs";

test("parseArgs uses a persistent named profile and local-only defaults", () => {
  const config = parseArgs(["--instance-id", "mac-mini-01", "--port", "4555"]);
  assert.equal(config.host, "127.0.0.1");
  assert.equal(config.port, 4555);
  assert.equal(config.instanceId, "mac-mini-01");
  assert.match(config.profileDir, /Browser Lite.*instances.*mac-mini-01.*profile/);
});

test("parseArgs rejects unsafe instance identifiers", () => {
  assert.throws(() => parseArgs(["--instance-id", "../outside"]), /instance-id/);
});

test("parseArgs validates port and viewport bounds", () => {
  assert.throws(() => parseArgs(["--port", "0"]), /port/);
  assert.throws(() => parseArgs(["--width", "100"]), /width/);
  assert.throws(() => parseArgs(["--height", "99999"]), /height/);
});

test("explicit Chrome executable must exist", () => {
  assert.throws(() => resolveChromeBinary("/definitely/missing/chrome"), /not found/);
});

test("version is a stable semantic version", () => {
  assert.match(VERSION, /^\d+\.\d+\.\d+$/);
});
