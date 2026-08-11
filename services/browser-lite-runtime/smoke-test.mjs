#!/usr/bin/env node

import { writeFile } from "node:fs/promises";
import { resolve } from "node:path";

function option(name, fallback) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : fallback;
}

async function webdriver(baseUrl, path, method = "GET", body = undefined) {
  const response = await fetch(`${baseUrl}${path}`, {
    method,
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok || payload.value?.error) {
    throw new Error(`${method} ${path}: ${payload.value?.message ?? `HTTP ${response.status}`}`);
  }
  return payload.value;
}

const baseUrl = option("--base-url", "http://127.0.0.1:4444").replace(/\/$/, "");
const targetUrl = option("--url", "https://example.com/");
const screenshotPath = resolve(option("--screenshot", "/tmp/browser-lite-smoke.png"));
const marker = option("--marker", `browser-lite-${Date.now()}`);
const expectedMarker = option("--expect-marker", null);

try {
  const runtime = await webdriver(baseUrl, "/browser-lite/status");
  const session = await webdriver(baseUrl, "/session", "POST", {
    capabilities: { alwaysMatch: { browserName: "chrome" } },
  });
  const sessionPath = `/session/${encodeURIComponent(session.sessionId)}`;
  await webdriver(baseUrl, `${sessionPath}/url`, "POST", { url: targetUrl });
  const page = await webdriver(baseUrl, `${sessionPath}/execute/sync`, "POST", {
    script: `
      if (arg0) localStorage.setItem("browser_lite_smoke_marker", arg0);
      return {
        url: location.href,
        title: document.title,
        marker: localStorage.getItem("browser_lite_smoke_marker"),
        readyState: document.readyState,
        bodyText: document.body?.innerText?.slice(0, 120) || ""
      };
    `,
    args: expectedMarker ? [null] : [marker],
  });
  if (expectedMarker && page.marker !== expectedMarker) {
    throw new Error(`persistent marker mismatch: expected ${expectedMarker}, got ${page.marker}`);
  }
  if (!page.url || !page.title || !["interactive", "complete"].includes(page.readyState)) {
    throw new Error(`unexpected page state: ${JSON.stringify(page)}`);
  }

  await webdriver(baseUrl, `${sessionPath}/execute/sync`, "POST", {
    script: `
      const input = document.createElement("input");
      input.id = "browser-lite-smoke-input";
      input.setAttribute("aria-label", "Browser Lite smoke input");
      document.body.prepend(input);
      const spacer = document.createElement("div");
      spacer.style.height = "2000px";
      document.body.append(spacer);
      input.focus();
      return true;
    `,
    args: [],
  });
  const typedText = "Browser Lite actions OK";
  await webdriver(baseUrl, `${sessionPath}/actions`, "POST", {
    actions: [{
      type: "key",
      id: "keyboard",
      actions: [...typedText].flatMap((value) => [
        { type: "keyDown", value },
        { type: "keyUp", value },
      ]),
    }],
  });
  const typedValue = await webdriver(baseUrl, `${sessionPath}/execute/sync`, "POST", {
    script: "return document.querySelector('#browser-lite-smoke-input')?.value || '';",
    args: [],
  });
  if (typedValue !== typedText) throw new Error(`keyboard actions mismatch: ${typedValue}`);

  await webdriver(baseUrl, `${sessionPath}/actions`, "POST", {
    actions: [{
      type: "wheel",
      id: "wheel",
      actions: [{
        type: "scroll",
        x: 100,
        y: 100,
        deltaX: 0,
        deltaY: 500,
        duration: 100,
        origin: "viewport",
      }],
    }],
  });
  await new Promise((resolveDelay) => setTimeout(resolveDelay, 200));
  const scrollY = await webdriver(baseUrl, `${sessionPath}/execute/sync`, "POST", {
    script: "return window.scrollY;",
    args: [],
  });
  if (!(scrollY > 0)) throw new Error(`wheel action did not scroll: ${scrollY}`);

  const cookieName = "browser_lite_smoke";
  await webdriver(baseUrl, `${sessionPath}/cookie`, "POST", {
    cookie: { name: cookieName, value: marker, path: "/", secure: true },
  });
  const cookiesBeforeDelete = await webdriver(baseUrl, `${sessionPath}/cookie`);
  if (!cookiesBeforeDelete.some((cookie) => cookie.name === cookieName && cookie.value === marker)) {
    throw new Error("cookie was not stored");
  }
  await webdriver(baseUrl, `${sessionPath}/cookie/${cookieName}`, "DELETE");
  const cookiesAfterDelete = await webdriver(baseUrl, `${sessionPath}/cookie`);
  if (cookiesAfterDelete.some((cookie) => cookie.name === cookieName)) {
    throw new Error("cookie was not deleted");
  }

  await webdriver(baseUrl, `${sessionPath}/execute/sync`, "POST", {
    script: "window.scrollTo(0, 0); return window.scrollY;",
    args: [],
  });

  const screenshot = await webdriver(baseUrl, `${sessionPath}/screenshot`);
  const screenshotBytes = Buffer.from(screenshot, "base64");
  if (screenshotBytes.length < 1_000) throw new Error(`screenshot is too small: ${screenshotBytes.length} bytes`);
  await writeFile(screenshotPath, screenshotBytes);
  const rawCdp = await webdriver(baseUrl, `${sessionPath}/goog/cdp/execute`, "POST", {
    cmd: "Runtime.evaluate",
    params: { expression: "navigator.userAgent", returnByValue: true },
  });
  const accessibility = await webdriver(baseUrl, `${sessionPath}/goog/cdp/execute`, "POST", {
    cmd: "Accessibility.getFullAXTree",
    params: {},
  });
  if (!(accessibility.nodes?.length > 0)) throw new Error("Accessibility tree is empty");
  const handlesBefore = await webdriver(baseUrl, `${sessionPath}/window/handles`);
  const createdTarget = await webdriver(baseUrl, `${sessionPath}/goog/cdp/execute`, "POST", {
    cmd: "Target.createTarget",
    params: { url: "about:blank" },
  });
  await new Promise((resolveDelay) => setTimeout(resolveDelay, 100));
  const handlesWithNewTab = await webdriver(baseUrl, `${sessionPath}/window/handles`);
  if (!createdTarget.targetId || !handlesWithNewTab.includes(createdTarget.targetId)) {
    throw new Error("new tab did not appear in window handles");
  }
  await webdriver(baseUrl, `${sessionPath}/window`, "POST", { handle: createdTarget.targetId });
  const activeHandle = await webdriver(baseUrl, `${sessionPath}/window`);
  if (activeHandle !== createdTarget.targetId) throw new Error("window switch did not select the new tab");
  const handlesAfterClose = await webdriver(baseUrl, `${sessionPath}/window`, "DELETE");
  if (handlesAfterClose.includes(createdTarget.targetId) || handlesAfterClose.length !== handlesBefore.length) {
    throw new Error("window close did not restore the original tab set");
  }
  const windowRect = await webdriver(baseUrl, `${sessionPath}/window/rect`);
  if (!(windowRect.width >= 320 && windowRect.height >= 240)) {
    throw new Error(`invalid window rectangle: ${JSON.stringify(windowRect)}`);
  }
  process.stdout.write(`${JSON.stringify({
    ok: true,
    runtime,
    page,
    marker: page.marker,
    screenshotPath,
    screenshotBytes: screenshotBytes.length,
    rawCdpUserAgent: rawCdp.result?.value,
    accessibilityNodeCount: accessibility.nodes.length,
    typedText,
    scrollY,
    cookieRoundTrip: true,
    tabRoundTrip: true,
    windowRect,
    tabCount: handlesAfterClose.length,
  }, null, 2)}\n`);
} catch (error) {
  process.stderr.write(`browser-lite smoke failed: ${error.message}\n`);
  process.exitCode = 1;
}
