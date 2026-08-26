#!/usr/bin/env node

import assert from "node:assert/strict";

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

async function waitForState(client, predicateExpression, timeout = 8_000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await client.evaluate(predicateExpression)) return;
    await delay(100);
  }
  throw new Error(`Timed out waiting for: ${predicateExpression}`);
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

  await client.evaluate(`document.querySelector('[data-action="open-space"]').click()`);
  await waitForState(client, `document.body.classList.contains('browser-mode')`);
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

  await waitForState(client, `Boolean(document.querySelector('.browser-tab.active'))`, 12_000);
  const hadGroups = await client.evaluate(`document.querySelectorAll('[data-tab-group-id]').length > 0`);
  if (!hadGroups) {
    const groupMenuOpened = await client.evaluate(`(() => {
      document.querySelector('#new-tab-group').click();
      return Boolean(document.querySelector('#tab-group-form'));
    })()`);
    assert.equal(groupMenuOpened, true, "Tab group editor did not open from the browser chrome");
    await client.evaluate(`(() => {
      const form = document.querySelector('#tab-group-form');
      form.elements.name.value = 'Mac mini 验收';
      form.querySelector('input[value="purple"]').checked = true;
      form.requestSubmit();
    })()`);
    await waitForState(client, `document.querySelectorAll('[data-tab-group-id]').length > 0`);
  }
  const groupBeforeToggle = await client.evaluate(`document.querySelector('[data-action="toggle-tab-group"]').getAttribute('aria-expanded')`);
  await client.evaluate(`document.querySelector('[data-action="toggle-tab-group"]').click()`);
  await waitForState(client, `document.querySelector('[data-action="toggle-tab-group"]').getAttribute('aria-expanded') !== ${JSON.stringify(groupBeforeToggle)}`);
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

  await client.evaluate(`document.querySelector('#browser-space-count').click()`);
  await waitForState(client, `document.body.classList.contains('spaces-mode')`);
  const returned = await client.evaluate(`({
    bodyClass: document.body.className,
    visibility: document.visibilityState,
    overviewDisplay: getComputedStyle(document.querySelector('#main-content')).display,
  })`);
  assert.equal(returned.visibility, "visible");
  assert.match(returned.bodyClass, /spaces-mode/);
  assert.notEqual(returned.overviewDisplay, "none");

  const currentTargets = await targets();
  assert.ok(currentTargets.some((target) => target.url === "about:blank"), "Embedded Space target was not found");
  process.stdout.write(`${JSON.stringify({
    before,
    opened,
    chromeSurface,
    extensionMenuRows,
    bookmarkMenuRows,
    tabGroups,
    returned,
    targetCount: currentTargets.length,
    webdriverEmbedded: session.value.capabilities["browser-lite:embedded"],
  }, null, 2)}\n`);
} finally {
  client.close();
}
