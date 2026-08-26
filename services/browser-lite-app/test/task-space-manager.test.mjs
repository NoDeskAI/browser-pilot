import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { BrowserLiteTaskSpaceManager, EGO_ERROR, EgoBridgeError } from "../src/task-space-manager.mjs";

class FakeBrowserState {
  constructor() {
    this.activeTargetId = "tab-1";
    this.commands = [];
  }

  async targets() {
    return [{ targetId: "tab-1", title: "Browser Lite", url: "about:blank", type: "page" }];
  }

  async sendPage(method, params) {
    this.commands.push({ method, params });
    if (method === "Target.createTarget") return { targetId: "tab-2" };
    if (method === "Accessibility.getFullAXTree") {
      return { nodes: [{ nodeId: "1", role: { value: "button" }, name: { value: "Continue" } }] };
    }
    return { ok: true };
  }

  async sendRawCDP(method, params, sessionId) {
    this.commands.push({ method, params, sessionId });
    return { ok: true };
  }

  async ensurePage() { return this.activeTargetId; }
  async currentUrl() { return "https://example.com/"; }
  async title() { return "Example"; }
}

class FakeBrowserManager {
  constructor() {
    this.instances = new Map();
    this.paused = [];
    this.stopped = [];
    this.removed = [];
    this.shown = [];
  }

  async ensure(instanceId) {
    if (!this.instances.has(instanceId)) this.instances.set(instanceId, { state: new FakeBrowserState() });
    return this.instances.get(instanceId);
  }

  async pause(instanceId) { this.paused.push(instanceId); }
  async stop(instanceId) { this.stopped.push(instanceId); }
  async remove(instanceId) { this.removed.push(instanceId); this.instances.delete(instanceId); }
  showInstance(instanceId) { this.shown.push(instanceId); }
  async request(instanceId, request) { return { instanceId, request }; }
}

function createTaskSpaces() {
  return new BrowserLiteTaskSpaceManager(new FakeBrowserManager(), {
    listProfiles: async () => [{ id: "Default", isDefault: true, name: "Local profile" }],
    getBrowserVersion: async () => ({ currentVersion: "0.1.0", updateAvailable: false }),
  });
}

test("task-space creation exposes the recovered Ego-compatible shape", async () => {
  const taskSpaces = createTaskSpaces();
  const created = await taskSpaces.createTaskSpace("research", "Default");
  assert.deepEqual(created, {
    createdBy: "agent",
    id: 1,
    name: "research",
    ownership: "agent",
    profileId: "Default",
    profileName: "Local profile",
    recentTabTitles: ["Browser Lite"],
    taskId: "research",
  });
  assert.deepEqual(await taskSpaces.listTaskSpaces(), { taskSpaces: [created] });
});

test("handoff blocks agent CDP until explicit takeover", async () => {
  const taskSpaces = createTaskSpaces();
  await taskSpaces.createTaskSpace("handoff");
  const handedOff = await taskSpaces.handOffTaskSpace();
  assert.equal(handedOff.ownership, "agentDelegatedToUser");
  await assert.rejects(
    () => taskSpaces.sendCDPMessage('{"id":1,"method":"Runtime.enable"}'),
    (error) => error instanceof EgoBridgeError && error.code === EGO_ERROR.TASK_SPACE_USER_IN_CONTROL,
  );
  const takenOver = await taskSpaces.takeOverTaskSpace();
  assert.equal(takenOver.ownership, "agent");
  assert.equal(await taskSpaces.sendCDPMessage('{"id":1,"method":"Runtime.enable"}'), '{"id":1,"result":{"ok":true}}');
});

test("completion preserves profile state and makes the space deletable", async () => {
  const taskSpaces = createTaskSpaces();
  await taskSpaces.createTaskSpace("complete-me");
  const result = await taskSpaces.completeTaskSpace();
  assert.equal(result.completed, true);
  assert.equal(result.taskSpace.ownership, "agentDelegatedToUser");
  const deletion = await taskSpaces.deleteSpaces({ ids: [1] });
  assert.deepEqual(deletion, { deletedSpaceIds: [1], skippedRunningSpaceIds: [] });
});

test("active spaces are not destructively deleted", async () => {
  const taskSpaces = createTaskSpaces();
  await taskSpaces.createTaskSpace("still-running");
  assert.deepEqual(await taskSpaces.deleteSpaces({ scope: "all" }), {
    deletedSpaceIds: [],
    skippedRunningSpaceIds: [1],
  });
});

test("snapshot and raw CDP use the selected Browser Lite view", async () => {
  const taskSpaces = createTaskSpaces();
  await taskSpaces.createTaskSpace("inspect");
  const snapshot = await taskSpaces.snapshot({ interactiveOnly: true, includeStableLocator: true });
  assert.equal(snapshot.url, "https://example.com/");
  assert.deepEqual(snapshot.nodes, [{ nodeId: "1", role: "button", name: "Continue" }]);
  assert.match(snapshot.content, /button "Continue"/);
  assert.equal(await taskSpaces.sendCDPMessage('{"id":7,"method":"Page.enable"}'), '{"id":7,"result":{"ok":true}}');
});

test("pointer highlight validates coordinates and targets the selected view", async () => {
  const taskSpaces = createTaskSpaces();
  await taskSpaces.createTaskSpace("point");
  assert.deepEqual(await taskSpaces.animationHighlightMouseToPosition(120, 240), { x: 120, y: 240, shown: true });
  await assert.rejects(
    () => taskSpaces.animationHighlightMouseToPosition("left", 240),
    (error) => error instanceof EgoBridgeError && error.code === EGO_ERROR.INVALID_ARGUMENT,
  );
});

test("useTaskSpace defers missing-space validation like the observed Ego binding", async () => {
  const taskSpaces = createTaskSpaces();
  assert.equal(await taskSpaces.useTaskSpace(404), 404);
  await assert.rejects(
    () => taskSpaces.listTabs(),
    (error) => error instanceof EgoBridgeError && error.code === EGO_ERROR.TASK_SPACE_NOT_FOUND,
  );
});

test("task spaces persist without browser data or CDP payloads", async () => {
  const directory = await mkdtemp(join(tmpdir(), "browser-lite-task-spaces-"));
  const statePath = join(directory, "task-spaces.json");
  try {
    const first = new BrowserLiteTaskSpaceManager(new FakeBrowserManager(), { statePath });
    await first.createTaskSpace("persistent");
    await first.setAgentTaskState("Waiting for result");

    const serialized = JSON.parse(await readFile(statePath, "utf8"));
    assert.equal(serialized.spaces[0].name, "persistent");
    assert.equal(serialized.spaces[0].agentTaskState, "Waiting for result");

    const restored = new BrowserLiteTaskSpaceManager(new FakeBrowserManager(), { statePath });
    await restored.load();
    assert.equal((await restored.listTaskSpaces()).taskSpaces[0].name, "persistent");
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("remote dispatch serializes lifecycle mutations", async () => {
  const taskSpaces = createTaskSpaces();
  const first = taskSpaces.dispatch("createTaskSpace", ["first"]);
  const second = taskSpaces.dispatch("createTaskSpace", ["second"]);
  const [firstSpace, secondSpace] = await Promise.all([first, second]);
  assert.equal(firstSpace.id, 1);
  assert.equal(secondSpace.id, 2);
  assert.deepEqual((await taskSpaces.listTaskSpaces()).taskSpaces.map((space) => space.name), ["first", "second"]);
});

test("remote sessions keep independent selected task spaces", async () => {
  const taskSpaces = createTaskSpaces();
  await taskSpaces.dispatch("createTaskSpace", ["first"], "session-a");
  await taskSpaces.dispatch("createTaskSpace", ["second"], "session-b");
  await taskSpaces.dispatch("handOffTaskSpace", [], "session-a");
  await assert.rejects(
    () => taskSpaces.dispatch("listTabs", [], "session-a"),
    (error) => error instanceof EgoBridgeError && error.code === EGO_ERROR.TASK_SPACE_USER_IN_CONTROL,
  );
  assert.equal((await taskSpaces.dispatch("listTabs", [], "session-b")).tabs.length, 1);
});

test("WebDriver requests hard stop while the user controls an agent space", async () => {
  const taskSpaces = createTaskSpaces();
  const created = await taskSpaces.ensureContextTaskSpace("session-a", "agent-work");
  assert.deepEqual(await taskSpaces.agentWebDriverRequest("session-a", { path: "/status" }), {
    instanceId: "task-space-1",
    request: { path: "/status" },
  });
  await taskSpaces.openForUser(created.id);
  await assert.rejects(
    () => taskSpaces.agentWebDriverRequest("session-a", { path: "/status" }),
    (error) => error instanceof EgoBridgeError && error.code === EGO_ERROR.TASK_SPACE_USER_IN_CONTROL,
  );
});

test("closing a Space stops native Chromium instead of only minimizing it", async () => {
  const taskSpaces = createTaskSpaces();
  const created = await taskSpaces.createUserTaskSpace("close-me");
  await taskSpaces.closeForUser(created.id);

  assert.deepEqual(taskSpaces.browserManager.stopped, ["task-space-1"]);
  assert.deepEqual(taskSpaces.browserManager.paused, []);
  assert.equal(taskSpaces.spaces.get(created.id).status, "closed");
});
