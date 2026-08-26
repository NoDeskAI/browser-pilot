import assert from "node:assert/strict";
import { test } from "node:test";

import { BrowserLiteState, parseArgs, resolveChromeBinary, VERSION } from "./browser-lite.mjs";

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

test("cookie seeding batches imported cookies instead of serializing every CDP call", async () => {
  const state = new BrowserLiteState({ instanceId: "cookie-test", profileDir: "/tmp/cookie-test" });
  const calls = [];
  state.sendPage = async (method, params) => {
    calls.push({ method, params });
    return {};
  };
  state.currentUrl = async () => "https://fallback.example/";

  const imported = await state.addCookies([
    { name: "one", value: "1", url: "https://one.example/", sameSite: "lax" },
    { name: "two", value: "2", domain: ".two.example", path: "/", expirationDate: 2_000_000_000 },
  ]);

  assert.equal(imported, 2);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, "Network.setCookies");
  assert.deepEqual(calls[0].params.cookies, [
    { name: "one", value: "1", url: "https://one.example/", sameSite: "Lax" },
    {
      name: "two",
      value: "2",
      url: "https://fallback.example/",
      domain: ".two.example",
      path: "/",
      expires: 2_000_000_000,
    },
  ]);
});

test("first imported-extension launch keeps one Chrome new tab and closes onboarding tabs", async () => {
  const state = new BrowserLiteState({
    instanceId: "extension-test",
    profileDir: "/tmp/extension-test",
    startUrl: "chrome://newtab/",
  });
  state.cdp = { send: async () => ({}) };
  state.activeTargetId = "welcome";
  let calls = 0;
  state.targets = async () => {
    calls += 1;
    return calls === 1
      ? [
        { targetId: "newtab", url: "chrome://new-tab-page/" },
        { targetId: "welcome", url: "https://extension.example/welcome" },
      ]
      : [{ targetId: "newtab", url: "chrome://new-tab-page/" }];
  };
  let activated;
  state.activate = async (targetId) => { activated = targetId; };

  assert.deepEqual(await state.resetImportedExtensionStartupTabs(), {
    closedTabs: 1,
    onboardingUrls: ["https://extension.example/welcome"],
  });
  assert.equal(activated, "newtab");
});

test("known extension onboarding cleanup preserves user tabs", async () => {
  const state = new BrowserLiteState({
    instanceId: "extension-restart-test",
    profileDir: "/tmp/extension-restart-test",
    startUrl: "chrome://newtab/",
  });
  const closedTargets = [];
  state.cdp = { send: async (_method, params) => { closedTargets.push(params.targetId); return {}; } };
  state.activeTargetId = "welcome";
  let calls = 0;
  state.targets = async () => {
    calls += 1;
    return calls === 1
      ? [
        { targetId: "newtab", url: "chrome://new-tab-page/" },
        { targetId: "user", url: "https://user.example/work" },
        { targetId: "welcome", url: "https://extension.example/welcome/" },
        { targetId: "newtab-duplicate", url: "chrome://newtab/" },
      ]
      : [
        { targetId: "newtab", url: "chrome://new-tab-page/" },
        { targetId: "user", url: "https://user.example/work" },
      ];
  };
  let activated;
  state.activate = async (targetId) => { activated = targetId; };

  assert.deepEqual(
    await state.resetImportedExtensionStartupTabs(["https://extension.example/welcome"]),
    { closedTabs: 2, onboardingUrls: ["https://extension.example/welcome"] },
  );
  assert.deepEqual(closedTargets, ["welcome", "newtab-duplicate"]);
  assert.equal(activated, "newtab");
});

test("a disconnected native Chromium is stopped instead of relaunched by a state read", async () => {
  const state = new BrowserLiteState({
    instanceId: "dock-quit-test",
    profileDir: "/tmp/dock-quit-test",
  });
  let starts = 0;
  state.start = async () => { starts += 1; };
  state.cdp = { socket: { readyState: 3 }, close() {} };
  state.stopped = false;

  await assert.rejects(() => state.ensureConnected(), /exited/);
  assert.equal(starts, 0);
  assert.equal(state.stopped, true);
});

test("an external native Chromium exit notifies the controller exactly once", () => {
  const exits = [];
  const state = new BrowserLiteState({
    instanceId: "dock-quit-notification-test",
    profileDir: "/tmp/dock-quit-notification-test",
    onNativeBrowserExit: (details) => exits.push(details),
  });
  state.stopped = false;
  state.chromePid = 1234;
  state.cdp = { close() {} };

  state.handleChromeExit(0, null, 1234);
  state.handleChromeExit(0, null, 1234);

  assert.equal(state.stopped, true);
  assert.deepEqual(exits, [{
    instanceId: "dock-quit-notification-test",
    pid: 1234,
    code: 0,
    signal: null,
  }]);
});
