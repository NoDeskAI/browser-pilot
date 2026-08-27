#!/usr/bin/env node

import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";

const endpoint = process.env.BROWSER_LITE_DEVTOOLS_URL || "http://127.0.0.1:9229";

function delay(milliseconds) {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds));
}

async function targets() {
  const response = await fetch(`${endpoint}/json/list`);
  if (!response.ok) throw new Error(`DevTools target list failed: ${response.status}`);
  return response.json();
}

async function controllerTarget() {
  const target = (await targets()).find((candidate) => (
    candidate.type === "page"
    && /\/renderer\/index\.html(?:$|[?#])/.test(candidate.url || "")
  ));
  if (!target?.webSocketDebuggerUrl) throw new Error("Browser Lite controller target was not found");
  return target;
}

async function connect(target) {
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  const pending = new Map();
  let sequence = 0;
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(String(event.data));
    if (!message.id || !pending.has(message.id)) return;
    const { resolve, reject } = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) reject(new Error(message.error.message || "CDP command failed"));
    else resolve(message.result);
  });
  await new Promise((resolveOpen, rejectOpen) => {
    socket.addEventListener("open", resolveOpen, { once: true });
    socket.addEventListener("error", rejectOpen, { once: true });
  });
  return {
    async command(method, params = {}) {
      const id = ++sequence;
      const response = new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
      socket.send(JSON.stringify({ id, method, params }));
      return response;
    },
    async evaluate(expression) {
      const id = ++sequence;
      const response = new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
      socket.send(JSON.stringify({
        id,
        method: "Runtime.evaluate",
        params: { expression, awaitPromise: true, returnByValue: true, userGesture: true },
      }));
      const result = await response;
      if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || "Evaluation failed");
      return result.result?.value;
    },
    close() { socket.close(); },
  };
}

async function waitForState(client, predicateExpression, timeout = 8_000, interval = 100) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await client.evaluate(predicateExpression)) return;
    await delay(interval);
  }
  throw new Error(`Timed out waiting for: ${predicateExpression}`);
}

async function captureController(client, path) {
  const result = await client.command("Page.captureScreenshot", { format: "png", fromSurface: true });
  await writeFile(path, Buffer.from(result.data, "base64"));
}

const client = await connect(await controllerTarget());
try {
  await waitForState(client, `!document.body.classList.contains('booting')`, 20_000);
  await delay(500);
  const before = await client.evaluate(`({
    bodyClass: document.body.className,
    visibility: document.visibilityState,
    spaceCards: document.querySelectorAll('[data-action="open-space"]').length,
  })`);
  assert.equal(before.visibility, "visible");
  if (!/spaces-mode/.test(before.bodyClass)) {
    await client.evaluate(`window.browserLite.showSpaces()`);
    await waitForState(client, `document.body.classList.contains('spaces-mode')`);
    before.bodyClass = await client.evaluate(`document.body.className`);
  }
  assert.match(before.bodyClass, /spaces-mode/);
  assert.ok(before.spaceCards > 0, "Mac mini must have at least one existing Space");

  const prewarmed = await client.evaluate(`window.browserLite.getState().then(state => ({
    activeSpaces: state.taskSpaces.taskSpaces.filter(space => space.status === "active").length,
    loadedActiveSpaces: state.taskSpaces.taskSpaces.filter(space => space.status === "active" && space.loaded).length,
    runningInstances: state.instances.filter(instance => instance.ready && !instance.stopped && !instance.paused).length,
    extensionCount: state.taskSpaces.taskSpaces.filter(space => space.status === "active").flatMap(space => space.extensions || []).length,
    loadedExtensionCount: state.taskSpaces.taskSpaces.filter(space => space.status === "active").flatMap(space => space.extensions || []).filter(extension => extension.status === "loaded").length,
    disabledExtensionCount: state.taskSpaces.taskSpaces.filter(space => space.status === "active").flatMap(space => space.extensions || []).filter(extension => extension.status === "disabled").length,
  }))`);
  assert.ok(prewarmed.activeSpaces > 0, "Mac mini must have at least one active Space");
  assert.equal(prewarmed.loadedActiveSpaces, prewarmed.activeSpaces, "Every active Space must be prewarmed before selection");
  assert.equal(prewarmed.runningInstances, prewarmed.activeSpaces, "Every active Space must keep a running embedded runtime");
  assert.ok(prewarmed.extensionCount > 0, "Imported extensions must remain listed");
  assert.equal(prewarmed.loadedExtensionCount, 0, "No extension may load at Browser Lite startup");
  assert.equal(prewarmed.disabledExtensionCount, prewarmed.extensionCount, "Every imported extension must default to disabled");

  const acceptanceDir = process.env.BROWSER_LITE_ACCEPTANCE_DIR || "";
  const openStartedAt = Date.now();
  await client.evaluate(`(() => { const spaces = document.querySelectorAll('[data-action="open-space"]'); (spaces[${acceptanceDir ? 1 : 0}] || spaces[0]).click(); })()`);
  await waitForState(client, `document.body.classList.contains('browser-mode')`);
  const openDurationMs = Date.now() - openStartedAt;
  assert.ok(openDurationMs < 1_000, `Prewarmed Space took ${openDurationMs}ms to become visible`);
  const opened = await client.evaluate(`({
    bodyClass: document.body.className,
    visibility: document.visibilityState,
    count: document.querySelector('#browser-space-count')?.textContent,
    countDisplay: getComputedStyle(document.querySelector('#browser-space-count')).display,
    browserChromeDisplay: getComputedStyle(document.querySelector('#browser-chrome')).display,
  })`);
  assert.equal(opened.visibility, "visible");
  assert.match(opened.bodyClass, /browser-mode/);
  assert.notEqual(opened.countDisplay, "none");
  assert.notEqual(opened.browserChromeDisplay, "none");
  assert.ok(Number(opened.count) > 0);

  const chromeSurface = await client.evaluate(`({
    bookmarks: document.querySelectorAll('#browser-bookmarks [data-action="open-bookmark"]').length,
    bookmarkFolders: document.querySelectorAll('#browser-bookmarks [data-action="open-bookmark-folder"]').length,
    extensionPins: document.querySelectorAll('#pinned-extensions [data-extension-id]').length,
    importedExtensions: Number(document.querySelector('#extensions-menu-button')?.dataset.extensionCount || 0),
  })`);
  assert.ok(chromeSurface.bookmarks > 0, "Imported bookmark buttons were not rendered");
  assert.ok(chromeSurface.bookmarkFolders > 0, "Imported bookmark folders were not rendered");
  assert.ok(chromeSurface.importedExtensions > 0, "Imported extensions were not exposed");

  await client.evaluate(`document.querySelector('#extensions-menu-button').click()`);
  await waitForState(client, `document.querySelectorAll('#chrome-popover .extension-row').length > 0`);
  const extensionMenuRows = await client.evaluate(`document.querySelectorAll('#chrome-popover .extension-row').length`);
  assert.ok(extensionMenuRows > 0);
  await client.evaluate(`document.querySelector('#chrome-popover [data-action="close-popover"]').click()`);

  await client.evaluate(`document.querySelector('#browser-bookmarks [data-action="open-bookmark-folder"]').click()`);
  await waitForState(client, `document.querySelectorAll('#chrome-popover .popover-row').length > 0`);
  const bookmarkMenuRows = await client.evaluate(`document.querySelectorAll('#chrome-popover .popover-row').length`);
  assert.ok(bookmarkMenuRows > 0);
  await client.evaluate(`document.querySelector('#chrome-popover [data-action="close-popover"]').click()`);

  await waitForState(client, `Boolean(document.querySelector('.browser-tab.active, [data-tab-group-id]'))`, 12_000);
  const staleTestGroupIds = await client.evaluate(`window.browserLite.getState().then(state => state.taskSpaces.activeTaskSpace.tabGroups.filter(group => group.name === "Mac mini 验收").map(group => group.id))`);
  for (const staleGroupId of staleTestGroupIds) {
    await client.evaluate(`window.browserLite.getState().then(state => window.browserLite.taskSpaceBrowserAction(state.taskSpaces.activeTaskSpace.id, "removeTabGroup", ${JSON.stringify(staleGroupId)}))`);
  }
  await waitForState(client, `window.browserLite.getState().then(state => !state.taskSpaces.activeTaskSpace.tabGroups.some(group => group.name === "Mac mini 验收"))`);
  const existingGroups = await client.evaluate(`window.browserLite.getState().then(state => state.taskSpaces.activeTaskSpace.tabGroups)`);
  let createdGroupId = "";
  let testedGroupId = existingGroups[0]?.id || "";
  if (!testedGroupId) {
    const groupMenuOpened = await client.evaluate(`(() => {
      document.querySelector('#browser-bookmarks [data-action="new-tab-group"]').click();
      return Boolean(document.querySelector('#tab-group-form'));
    })()`);
    assert.equal(groupMenuOpened, true, "Tab group editor did not open from the browser chrome");
    await client.evaluate(`(() => {
      const form = document.querySelector('#tab-group-form');
      form.elements.name.value = 'Mac mini 验收';
      form.querySelector('input[value="purple"]').checked = true;
      form.requestSubmit();
    })()`);
    await waitForState(client, `window.browserLite.getState().then(state => state.taskSpaces.activeTaskSpace.tabGroups.length > 0)`);
    createdGroupId = await client.evaluate(`window.browserLite.getState().then(state => state.taskSpaces.activeTaskSpace.tabGroups.at(-1).id)`);
    testedGroupId = createdGroupId;
    await waitForState(client, `Boolean(document.querySelector('[data-tab-group-id="${createdGroupId}"]'))`);
  }
  const groupSelector = `[data-tab-group-id="${testedGroupId}"] [data-action="toggle-tab-group"]`;
  const groupBeforeToggle = await client.evaluate(`document.querySelector(${JSON.stringify(groupSelector)}).getAttribute('aria-expanded')`);
  await client.evaluate(`document.querySelector(${JSON.stringify(groupSelector)}).click()`);
  await waitForState(client, `document.querySelector(${JSON.stringify(groupSelector)}).getAttribute('aria-expanded') !== ${JSON.stringify(groupBeforeToggle)}`);
  const tabGroups = await client.evaluate(`window.browserLite.getState().then(state => state.taskSpaces.activeTaskSpace.tabGroups)`);
  assert.ok(tabGroups.length > 0, "Tab group state was not persisted to the Space");

  const appState = await client.evaluate(`window.browserLite.getState()`);
  const activeInstance = appState.instances.find((instance) => (
    instance.id === appState.workspace.activeInstanceId
  ));
  assert.ok(activeInstance, "Active embedded instance was not exposed by Browser Lite");
  assert.equal(activeInstance.embedded, true);
  const health = await fetch(`${activeInstance.baseUrl}/healthz`).then((response) => response.json());
  assert.equal(health.value.embedded, true);
  const session = await fetch(`${activeInstance.baseUrl}/session`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ capabilities: {} }),
  }).then((response) => response.json());
  assert.equal(session.value.capabilities["browser-lite:embedded"], true);
  assert.equal(session.value.sessionId, activeInstance.sessionId);

  if (createdGroupId) {
    await client.evaluate(`window.browserLite.getState().then(state => window.browserLite.taskSpaceBrowserAction(state.taskSpaces.activeTaskSpace.id, "removeTabGroup", ${JSON.stringify(createdGroupId)}))`);
    await waitForState(client, `!document.querySelector('[data-tab-group-id="${createdGroupId}"]')`);
  } else {
    await client.evaluate(`document.querySelector(${JSON.stringify(groupSelector)}).click()`);
    await waitForState(client, `document.querySelector(${JSON.stringify(groupSelector)}).getAttribute('aria-expanded') === ${JSON.stringify(groupBeforeToggle)}`);
  }

  if (acceptanceDir) await mkdir(acceptanceDir, { recursive: true });
  await client.evaluate(`document.querySelector('#browser-space-count').click()`);
  let returnFrames = null;
  if (acceptanceDir) {
    await waitForState(client, `Boolean(document.querySelector('.space-return-flight'))`, 2_000, 2);
    const start = await client.evaluate(`(() => { const rect = document.querySelector('.space-return-flight').getBoundingClientRect(); return { left: rect.left, top: rect.top, width: rect.width, height: rect.height }; })()`);
    await captureController(client, `${acceptanceDir}/Browser-Lite-0.5.7-return-start.png`);
    await waitForState(client, `document.querySelector('.space-return-flight')?.dataset.phase === 'animating'`, 2_000, 2);
    await delay(120);
    const mid = await client.evaluate(`(() => { const rect = document.querySelector('.space-return-flight').getBoundingClientRect(); return { left: rect.left, top: rect.top, width: rect.width, height: rect.height }; })()`);
    await captureController(client, `${acceptanceDir}/Browser-Lite-0.5.7-return-mid.png`);
    returnFrames = { start, mid };
  }
  await waitForState(client, `document.body.classList.contains('spaces-mode')`);
  await waitForState(client, `window.__browserLiteLastSpaceReturn?.phase === 'complete'`);
  const returnAnimation = await client.evaluate(`window.__browserLiteLastSpaceReturn`);
  assert.equal(returnAnimation.transformOrigin, "0px 0px");
  assert.ok(returnAnimation.to.width > 0 && returnAnimation.to.height > 0, "Return animation must resolve a visible Space target");
  assert.ok(returnAnimation.to.width < returnAnimation.from.width, "Return surface must shrink to the Space preview width");
  assert.ok(returnAnimation.to.height < returnAnimation.from.height, "Return surface must shrink to the Space preview height");
  for (const key of ["left", "top", "width", "height"]) {
    assert.ok(Math.abs(returnAnimation.actualEnd[key] - returnAnimation.to[key]) <= 1, `Return animation ${key} missed its Space target`);
  }
  if (returnFrames) {
    for (const key of ["left", "top"]) {
      const direction = returnAnimation.to[key] - returnFrames.start[key];
      const progress = returnFrames.mid[key] - returnFrames.start[key];
      assert.ok(direction === 0 || Math.sign(progress) === Math.sign(direction), `Return animation moved in the wrong ${key} direction`);
    }
  }
  if (acceptanceDir) await captureController(client, `${acceptanceDir}/Browser-Lite-0.5.7-return-end.png`);
  const returned = await client.evaluate(`({
    bodyClass: document.body.className,
    visibility: document.visibilityState,
    overviewDisplay: getComputedStyle(document.querySelector('#main-content')).display,
  })`);
  assert.equal(returned.visibility, "visible");
  assert.match(returned.bodyClass, /spaces-mode/);
  assert.notEqual(returned.overviewDisplay, "none");

  const currentTargets = await targets();
  assert.ok(currentTargets.some((target) => (
    target.type === "page" && !/\/renderer\/index\.html(?:$|[?#])/.test(target.url || "")
  )), "Embedded Space target was not found");
  process.stdout.write(`${JSON.stringify({
    before,
    prewarmed,
    openDurationMs,
    opened,
    chromeSurface,
    extensionMenuRows,
    bookmarkMenuRows,
    tabGroups,
    returnAnimation,
    returnFrames,
    returned,
    targetCount: currentTargets.length,
    webdriverEmbedded: session.value.capabilities["browser-lite:embedded"],
  }, null, 2)}\n`);
} finally {
  client.close();
}
