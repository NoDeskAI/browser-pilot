import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

const OWNERSHIP_AGENT = "agent";
const OWNERSHIP_AGENT_DELEGATED_TO_USER = "agentDelegatedToUser";
const OWNERSHIP_USER = "user";

export const EGO_ERROR = Object.freeze({
  BROWSER_UNAVAILABLE: "EGO_BROWSER_UNAVAILABLE",
  CDP_CHANNEL_UNAVAILABLE: "EGO_CDP_CHANNEL_UNAVAILABLE",
  CDP_SEND_FAILED: "EGO_CDP_SEND_FAILED",
  INVALID_ARGUMENT: "EGO_INVALID_ARGUMENT",
  INVALID_RESULT_PAYLOAD: "EGO_INVALID_RESULT_PAYLOAD",
  OPERATION_FAILED: "EGO_OPERATION_FAILED",
  PROFILE_NOT_FOUND: "EGO_PROFILE_NOT_FOUND",
  RESULT_CONVERSION_FAILED: "EGO_RESULT_CONVERSION_FAILED",
  SNAPSHOT_FAILED: "EGO_SNAPSHOT_FAILED",
  TASK_HOST_DISCONNECTED: "EGO_TASK_HOST_DISCONNECTED",
  TASK_SPACE_INACTIVE: "EGO_TASK_SPACE_INACTIVE",
  TASK_SPACE_NOT_FOUND: "EGO_TASK_SPACE_NOT_FOUND",
  TASK_SPACE_NOT_SELECTED: "EGO_TASK_SPACE_NOT_SELECTED",
  TASK_SPACE_UNAVAILABLE: "EGO_TASK_SPACE_UNAVAILABLE",
  TASK_SPACE_USER_IN_CONTROL: "EGO_TASK_SPACE_USER_IN_CONTROL",
  WEB_CONTENTS_UNAVAILABLE: "EGO_WEB_CONTENTS_UNAVAILABLE",
});

export class EgoBridgeError extends Error {
  constructor(code, message, details = undefined) {
    super(message);
    this.name = "EgoBridgeError";
    this.code = code;
    this.error_code = code;
    if (details !== undefined) this.details = details;
  }

  toJSON() {
    return {
      error_code: this.error_code,
      message: this.message,
      ...(this.details === undefined ? {} : { details: this.details }),
    };
  }
}

function bridgeError(code, message, details = undefined) {
  return new EgoBridgeError(code, message, details);
}

function requireNoArguments(name, args) {
  if (args.length !== 0) throw bridgeError(EGO_ERROR.INVALID_ARGUMENT, `ego.${name}() does not accept arguments.`);
}

function requireNonEmptyString(value, message) {
  if (typeof value !== "string" || !value.trim()) throw bridgeError(EGO_ERROR.INVALID_ARGUMENT, message);
  return value.trim();
}

function requireNumericId(value, message) {
  const id = Number(value);
  if (!Number.isSafeInteger(id) || id < 1) throw bridgeError(EGO_ERROR.INVALID_ARGUMENT, message);
  return id;
}

function normalizeProfile(profile, index) {
  return {
    id: String(profile?.id || (index === 0 ? "Default" : `Profile ${index + 1}`)),
    isDefault: Boolean(profile?.isDefault ?? index === 0),
    name: String(profile?.name || profile?.displayName || "Browser Lite"),
  };
}

function taskInstanceId(id) {
  return `task-space-${id}`;
}

function publicTaskSpace(space) {
  return {
    createdBy: space.createdBy,
    id: space.id,
    name: space.name,
    ownership: space.ownership,
    profileId: space.profileId,
    profileName: space.profileName,
    recentTabTitles: [...space.recentTabTitles],
    taskId: space.taskId,
  };
}

function persistedPreview(value) {
  const preview = String(value || "");
  return preview.length <= 8_000_000 && /^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(preview) ? preview : "";
}

function persistedBrowserSession(value) {
  if (!value || typeof value !== "object") return { version: 1, tabs: [], groups: [] };
  const groups = (Array.isArray(value.groups) ? value.groups : []).slice(0, 50).map((group) => ({
    id: String(group?.id || ""),
    name: String(group?.name || "新建标签组").slice(0, 40),
    color: String(group?.color || "grey"),
    collapsed: Boolean(group?.collapsed),
  })).filter((group) => group.id);
  const groupIds = new Set(groups.map((group) => group.id));
  const tabs = (Array.isArray(value.tabs) ? value.tabs : []).slice(0, 100).map((tab) => ({
    url: String(tab?.url || "about:blank").slice(0, 8192),
    active: Boolean(tab?.active),
    groupId: groupIds.has(String(tab?.groupId || "")) ? String(tab.groupId) : "",
  }));
  return { version: 1, tabs, groups };
}

function interactiveNode(node) {
  const role = node?.role?.value;
  return [
    "button", "checkbox", "combobox", "link", "menuitem", "radio", "searchbox", "slider", "spinbutton",
    "switch", "tab", "textbox", "treeitem",
  ].includes(role) || Boolean(node?.properties?.some((property) => property.name === "focusable" && property.value?.value));
}

function compactAxNode(node, includeStableLocator) {
  const compact = {
    nodeId: node.nodeId,
    role: node.role?.value || "",
    name: node.name?.value || "",
    value: node.value?.value ?? undefined,
    disabled: node.properties?.some((property) => property.name === "disabled" && property.value?.value === true) || undefined,
  };
  if (includeStableLocator && node.backendDOMNodeId) compact.backendDOMNodeId = node.backendDOMNodeId;
  return Object.fromEntries(Object.entries(compact).filter(([, value]) => value !== undefined && value !== ""));
}

function navigationUrl(value) {
  const input = String(value || "").trim();
  if (!input) return "about:blank";
  try {
    const parsed = new URL(input);
    if (["http:", "https:", "about:", "data:"].includes(parsed.protocol)) return parsed.toString();
  } catch {}
  if (/^(localhost|127\.0\.0\.1|\[::1\])(?::\d+)?(?:\/|$)/i.test(input)) return `http://${input}`;
  if (/^[\w.-]+\.[a-z]{2,}(?::\d+)?(?:\/|$)/i.test(input)) return `https://${input}`;
  return `https://www.google.com/search?q=${encodeURIComponent(input)}`;
}

/**
 * Clean-room implementation of the task-space control surface recovered from
 * Ego Lite's public Node binding. It intentionally depends only on the
 * BrowserLiteManager contract, not on Ego binaries or private source.
 */
export class BrowserLiteTaskSpaceManager {
  constructor(browserManager, {
    listProfiles = async () => [{ id: "Default", isDefault: true, name: "Browser Lite" }],
    getBrowserVersion = async () => ({ currentVersion: "Browser Lite", updateAvailable: false }),
    statePath = undefined,
  } = {}) {
    if (!browserManager) throw new Error("Browser Lite task spaces require a browser manager");
    this.browserManager = browserManager;
    this.profileProvider = listProfiles;
    this.versionProvider = getBrowserVersion;
    this.statePath = statePath;
    this.spaces = new Map();
    this.selectedSpaceIds = new Map();
    this.dispatchContext = "default";
    this.nextSpaceId = 1;
    this.dispatchQueue = Promise.resolve();
  }

  get selectedSpaceId() {
    return this.selectedSpaceIds.get(this.dispatchContext) ?? null;
  }

  set selectedSpaceId(value) {
    if (value === null || value === undefined) this.selectedSpaceIds.delete(this.dispatchContext);
    else this.selectedSpaceIds.set(this.dispatchContext, value);
  }

  async load() {
    if (!this.statePath) return;
    try {
      const serialized = JSON.parse(await readFile(this.statePath, "utf8"));
      for (const value of serialized.spaces || []) {
        const id = Number(value?.id);
        if (!Number.isSafeInteger(id) || id < 1 || typeof value?.name !== "string") continue;
        this.spaces.set(id, {
          createdBy: value.createdBy === OWNERSHIP_USER ? OWNERSHIP_USER : OWNERSHIP_AGENT,
          id,
          name: value.name,
          ownership: [OWNERSHIP_AGENT, OWNERSHIP_AGENT_DELEGATED_TO_USER, OWNERSHIP_USER].includes(value.ownership)
            ? value.ownership
            : OWNERSHIP_AGENT,
          profileId: String(value.profileId || "Default"),
          profileName: String(value.profileName || "Browser Lite"),
          recentTabTitles: Array.isArray(value.recentTabTitles) ? value.recentTabTitles.map(String).slice(-5) : [],
          recentTabs: Array.isArray(value.recentTabs)
            ? value.recentTabs.slice(-5).map((tab) => ({ title: String(tab?.title || ""), url: String(tab?.url || "") }))
            : [],
          browserSession: persistedBrowserSession(value.browserSession),
          previewDataUrl: persistedPreview(value.previewDataUrl),
          taskId: String(value.taskId || value.name),
          instanceId: String(value.instanceId || taskInstanceId(id)),
          delegatedToUser: value.ownership === OWNERSHIP_AGENT_DELEGATED_TO_USER || Boolean(value.delegatedToUser),
          status: ["active", "closed", "completed", "error"].includes(value.status) ? value.status : "closed",
          agentTaskState: String(value.agentTaskState || ""),
          error: String(value.error || ""),
        });
      }
      this.nextSpaceId = Math.max(Number(serialized.nextSpaceId) || 1, ...[...this.spaces.keys()].map((id) => id + 1));
      const savedSelections = serialized.selectedSpaceIds && typeof serialized.selectedSpaceIds === "object"
        ? Object.entries(serialized.selectedSpaceIds)
        : [["default", serialized.selectedSpaceId]];
      for (const [contextId, value] of savedSelections) {
        const selectedId = Number(value);
        if (this.spaces.has(selectedId) && this.spaces.get(selectedId).status === "active") {
          this.selectedSpaceIds.set(String(contextId).slice(0, 128), selectedId);
        }
      }
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
    const profiles = await this.profiles().catch(() => []);
    let profileChanged = false;
    for (const space of this.spaces.values()) {
      const profile = profiles.find((candidate) => candidate.id === space.profileId)
        || profiles.find((candidate) => candidate.isDefault);
      if (profile?.name && profile.name !== space.profileName) {
        space.profileName = profile.name;
        profileChanged = true;
      }
    }
    if (profileChanged) await this.persist();
  }

  async persist() {
    if (!this.statePath) return;
    await mkdir(dirname(this.statePath), { recursive: true, mode: 0o700 });
    const temporaryPath = `${this.statePath}.tmp`;
    const serialized = {
      version: 1,
      nextSpaceId: this.nextSpaceId,
      selectedSpaceId: this.selectedSpaceIds.get("default") ?? null,
      selectedSpaceIds: Object.fromEntries(this.selectedSpaceIds),
      spaces: [...this.spaces.values()],
    };
    await writeFile(temporaryPath, `${JSON.stringify(serialized, null, 2)}\n`, { mode: 0o600 });
    await rename(temporaryPath, this.statePath);
  }

  async profiles() {
    const profiles = await this.profileProvider();
    return (Array.isArray(profiles) ? profiles : []).map(normalizeProfile);
  }

  async listProfiles(...args) {
    requireNoArguments("listProfiles", args);
    return { profiles: await this.profiles() };
  }

  async getBrowserVersion(...args) {
    requireNoArguments("getBrowserVersion", args);
    return this.versionProvider();
  }

  async createTaskSpace(name, profileId = undefined) {
    return this.createSpace(name, profileId, OWNERSHIP_AGENT);
  }

  async createUserTaskSpace(name, profileId = undefined) {
    return this.createSpace(name, profileId, OWNERSHIP_USER);
  }

  async createSpace(name, profileId, ownership) {
    const taskName = requireNonEmptyString(name, "ego.createTaskSpace(name) requires a non-empty task name.");
    if (profileId !== undefined && typeof profileId !== "string") {
      throw bridgeError(
        EGO_ERROR.INVALID_ARGUMENT,
        "ego.createTaskSpace(name, profileId?) expects an optional string profile id (use the id from ego.listProfiles()).",
      );
    }
    const profiles = await this.profiles();
    const selectedProfile = profileId
      ? profiles.find((profile) => profile.id === profileId)
      : profiles.find((profile) => profile.isDefault) || profiles[0];
    if (!selectedProfile) throw bridgeError(EGO_ERROR.PROFILE_NOT_FOUND, `Profile not found: ${profileId || "Default"}`);

    const id = this.nextSpaceId++;
    const instanceId = taskInstanceId(id);
    try {
      const entry = await this.browserManager.ensure(instanceId);
      await entry.state.restoreSessionState?.(null);
    } catch (error) {
      throw bridgeError(EGO_ERROR.BROWSER_UNAVAILABLE, error.message || "No active browser", { cause: String(error) });
    }
    const space = {
      createdBy: ownership,
      id,
      name: taskName,
      ownership,
      profileId: selectedProfile.id,
      profileName: selectedProfile.name,
      recentTabTitles: [],
      recentTabs: [],
      browserSession: { version: 1, tabs: [], groups: [] },
      previewDataUrl: "",
      taskId: taskName,
      instanceId,
      delegatedToUser: false,
      status: "active",
      agentTaskState: "",
      error: "",
    };
    this.spaces.set(id, space);
    this.selectedSpaceId = id;
    await this.refreshRecentTabs(space);
    await this.persist();
    return publicTaskSpace(space);
  }

  async ensureContextTaskSpace(contextId, name = undefined) {
    const context = String(contextId || "default").slice(0, 128);
    return this.runSerialized(context, async () => {
      const selectedId = this.selectedSpaceId;
      const selected = selectedId ? this.spaces.get(selectedId) : null;
      if (selected && selected.status === "active") {
        await this.ensureSpaceRuntime(selected);
        return selected;
      }
      const created = await this.createSpace(name || `Browser Pilot ${context}`, undefined, OWNERSHIP_AGENT);
      return this.requireSpace(created.id);
    });
  }

  instanceIdForContext(contextId) {
    const selectedId = this.selectedSpaceIds.get(String(contextId || "default").slice(0, 128));
    return selectedId ? this.spaces.get(selectedId)?.instanceId ?? null : null;
  }

  async agentWebDriverRequest(contextId, request) {
    return this.runSerialized(contextId, async () => {
      const space = this.requireSelectedAgentSpace();
      return this.browserManager.request(space.instanceId, request);
    });
  }

  async workspaceState() {
    const activeInstanceId = this.browserManager.activeInstanceId;
    const taskSpaces = [];
    let persistedStateChanged = false;
    for (const space of this.spaces.values()) {
      const state = this.browserManager.instances?.get(space.instanceId)?.state;
      let tabs = [];
      let tabGroups = [];
      let extensions = [];
      let navigation = null;
      if (state && space.status === "active") {
        try {
          tabs = (await state.targets()).map((tab) => ({
            targetId: tab.targetId,
            title: tab.title || "新标签页",
            url: tab.url || "about:blank",
            active: tab.targetId === state.activeTargetId,
            groupId: tab.groupId || "",
          }));
          tabGroups = state.tabGroupsState?.() || [];
          extensions = state.extensionsState?.() || [];
          navigation = await state.navigationState?.() || null;
          if (state.sessionStateRestored !== false && typeof state.exportSessionState === "function") {
            const browserSession = persistedBrowserSession(await state.exportSessionState());
            if (JSON.stringify(browserSession) !== JSON.stringify(space.browserSession)) {
              space.browserSession = browserSession;
              persistedStateChanged = true;
            }
          }
        } catch {}
      }
      let previewDataUrl = space.previewDataUrl || "";
      if (state && this.browserManager.viewMode === "spaces") {
        const capturedPreview = persistedPreview(await state.previewDataUrl?.() || "");
        if (capturedPreview && capturedPreview !== space.previewDataUrl) {
          space.previewDataUrl = capturedPreview;
          previewDataUrl = capturedPreview;
          persistedStateChanged = true;
        }
      }
      taskSpaces.push({
        ...publicTaskSpace(space),
        status: space.status,
        delegatedToUser: space.delegatedToUser,
        agentTaskState: space.agentTaskState,
        error: space.error,
        active: this.browserManager.viewMode === "browser" && activeInstanceId === space.instanceId,
        selected: activeInstanceId === space.instanceId,
        loaded: Boolean(state),
        previewDataUrl,
        tabs,
        tabGroups,
        extensions,
        navigation,
      });
    }
    if (persistedStateChanged) await this.persist();
    const activeTaskSpace = taskSpaces.find((space) => space.active) || null;
    return { taskSpaces, activeTaskSpace };
  }

  async openForUser(value) {
    const id = requireNumericId(value, "Task space ID must be numeric.");
    return this.runSerialized("ui", async () => {
      const space = this.requireSpace(id);
      this.selectedSpaceId = id;
      if (space.status === "closed" || space.status === "error") space.status = "active";
      if (space.ownership === OWNERSHIP_AGENT) {
        space.ownership = OWNERSHIP_AGENT_DELEGATED_TO_USER;
        space.delegatedToUser = true;
        space.agentTaskState = "User in control";
      }
      await this.ensureSpaceRuntime(space);
      this.browserManager.setTaskControlVisible?.(space.ownership !== OWNERSHIP_USER);
      await this.browserManager.showInstance(space.instanceId);
      await this.refreshRecentTabs(space);
      await this.persist();
      return publicTaskSpace(space);
    });
  }

  async returnControlToAgent(value) {
    const id = requireNumericId(value, "Task space ID must be numeric.");
    return this.runSerialized("ui", async () => {
      const space = this.requireSpace(id);
      if (space.ownership !== OWNERSHIP_AGENT_DELEGATED_TO_USER) {
        throw bridgeError(EGO_ERROR.TASK_SPACE_UNAVAILABLE, `Task space ${id} is not delegated to the user.`);
      }
      space.ownership = OWNERSHIP_AGENT;
      space.delegatedToUser = false;
      space.agentTaskState = "Waiting for agent";
      await this.browserManager.showSpaces();
      await this.persist();
      return publicTaskSpace(space);
    });
  }

  async closeForUser(value) {
    const id = requireNumericId(value, "Task space ID must be numeric.");
    return this.runSerialized("ui", async () => {
      const space = this.requireSpace(id);
      await this.refreshRecentTabs(space);
      await this.browserManager.stop(space.instanceId);
      space.status = "closed";
      for (const [contextId, selectedId] of this.selectedSpaceIds) {
        if (selectedId === id) this.selectedSpaceIds.delete(contextId);
      }
      await this.persist();
      return publicTaskSpace(space);
    });
  }

  async removeContextTaskSpace(contextId) {
    const context = String(contextId || "default").slice(0, 128);
    return this.runSerialized(context, async () => {
      const id = this.selectedSpaceId;
      if (!id) return false;
      const space = this.spaces.get(id);
      if (!space) {
        this.selectedSpaceId = null;
        await this.persist();
        return false;
      }
      await this.browserManager.remove(space.instanceId);
      this.spaces.delete(id);
      for (const [key, selectedId] of this.selectedSpaceIds) {
        if (selectedId === id) this.selectedSpaceIds.delete(key);
      }
      await this.persist();
      return true;
    });
  }

  async userBrowserAction(value, action, payload = undefined) {
    const id = requireNumericId(value, "Task space ID must be numeric.");
    return this.runSerialized("ui", async () => {
      const space = this.requireSpace(id);
      if (space.status !== "active") throw bridgeError(EGO_ERROR.TASK_SPACE_INACTIVE, `Task space ${id} is inactive.`);
      if (![OWNERSHIP_USER, OWNERSHIP_AGENT_DELEGATED_TO_USER].includes(space.ownership)) {
        throw bridgeError(EGO_ERROR.TASK_SPACE_UNAVAILABLE, "Open the task space for user control first.");
      }
      const entry = await this.ensureSpaceRuntime(space);
      const state = entry.state;
      if (action === "navigate") await state.navigate(navigationUrl(payload));
      else if (action === "back") await state.goBack();
      else if (action === "forward") await state.goForward();
      else if (action === "reload") {
        if ((await state.navigationState()).loading) await state.stopLoading();
        else await state.reload();
      }
      else if (action === "newTab") await state.createWindow(payload ? navigationUrl(payload) : state.config.startUrl);
      else if (action === "activateTab") await state.activate(requireNonEmptyString(payload, "Target ID is required."));
      else if (action === "closeTab") {
        const targetId = requireNonEmptyString(payload, "Target ID is required.");
        await state.destroyView(targetId);
      } else if (action === "createTabGroup") {
        if (!payload || typeof payload !== "object") throw bridgeError(EGO_ERROR.INVALID_ARGUMENT, "标签组参数无效");
        state.createTabGroup(payload);
      } else if (action === "toggleTabGroup") {
        state.toggleTabGroup(requireNonEmptyString(payload, "标签组 ID 不能为空"));
      } else if (action === "updateTabGroup") {
        if (!payload || typeof payload !== "object") throw bridgeError(EGO_ERROR.INVALID_ARGUMENT, "标签组参数无效");
        state.updateTabGroup(payload);
      } else if (action === "moveTabToGroup") {
        if (!payload || typeof payload !== "object") throw bridgeError(EGO_ERROR.INVALID_ARGUMENT, "标签页分组参数无效");
        state.moveTabToGroup(payload);
      } else if (action === "removeTabGroup") {
        state.removeTabGroup(requireNonEmptyString(payload, "标签组 ID 不能为空"));
      } else if (action === "openExtension") {
        await state.openExtension(requireNonEmptyString(payload, "扩展程序 ID 不能为空"));
      } else {
        throw bridgeError(EGO_ERROR.INVALID_ARGUMENT, `Unknown user browser action: ${action}`);
      }
      this.browserManager.setTaskControlVisible?.(space.ownership !== OWNERSHIP_USER);
      await this.browserManager.showInstance(space.instanceId);
      await this.refreshRecentTabs(space, state);
      await this.persist();
      return {
        taskSpace: publicTaskSpace(space),
        navigation: await state.navigationState(),
        tabs: await state.targets(),
        tabGroups: state.tabGroupsState?.() || [],
      };
    });
  }

  async claimTaskSpace(value, name = undefined) {
    const id = requireNumericId(value, "ego.claimTaskSpace(id, name?) expects a numeric task space ID.");
    if (name !== undefined && typeof name !== "string") {
      throw bridgeError(EGO_ERROR.INVALID_ARGUMENT, "ego.claimTaskSpace(id, name?) expects an optional string name.");
    }
    const space = this.requireSpace(id);
    if (space.delegatedToUser) {
      throw bridgeError(
        EGO_ERROR.TASK_SPACE_USER_IN_CONTROL,
        `Task space is delegated to user: ${id}. Use ego.takeOverTaskSpace() instead.`,
      );
    }
    if (space.ownership === OWNERSHIP_AGENT && space.status === "active") {
      throw bridgeError(EGO_ERROR.TASK_SPACE_UNAVAILABLE, `Task space ${id} is not available.`);
    }
    if (name !== undefined && name.trim()) {
      space.name = name.trim();
      space.taskId = name.trim();
    }
    space.ownership = OWNERSHIP_AGENT;
    space.status = "active";
    space.error = "";
    this.selectedSpaceId = id;
    await this.ensureSpaceRuntime(space);
    await this.refreshRecentTabs(space);
    await this.persist();
    return publicTaskSpace(space);
  }

  async useTaskSpace(value) {
    const id = requireNumericId(value, "ego.useTaskSpace(id) expects a numeric task space ID.");
    this.selectedSpaceId = id;
    await this.persist();
    return id;
  }

  async closeTaskSpace(...args) {
    requireNoArguments("closeTaskSpace", args);
    const space = this.requireSelectedSpace();
    if (space.ownership === OWNERSHIP_USER) {
      throw bridgeError(EGO_ERROR.TASK_SPACE_INACTIVE, "Claim this user-owned task space before closing it.");
    }
    await this.browserManager.stop(space.instanceId);
    space.status = "closed";
    this.selectedSpaceId = null;
    await this.persist();
    return { taskSpace: publicTaskSpace(space), closed: true };
  }

  async listTaskSpaces(...args) {
    requireNoArguments("listTaskSpaces", args);
    for (const space of this.spaces.values()) await this.refreshRecentTabs(space);
    return { taskSpaces: [...this.spaces.values()].map(publicTaskSpace) };
  }

  async deleteSpaces(options) {
    if (!options || typeof options !== "object" || Array.isArray(options)) {
      throw bridgeError(
        EGO_ERROR.INVALID_ARGUMENT,
        "ego.deleteSpaces(options) expects either {scope: 'all'|'task'} or {ids: number[]} options.",
      );
    }
    let ids;
    if (Array.isArray(options.ids)) {
      ids = options.ids.map((id) => requireNumericId(id, "ego.deleteSpaces({ids}) expects numeric task space IDs."));
    } else if (options.scope === "all") {
      ids = [...this.spaces.keys()];
    } else if (options.scope === "task") {
      ids = this.selectedSpaceId ? [this.selectedSpaceId] : [];
    } else {
      throw bridgeError(
        EGO_ERROR.INVALID_ARGUMENT,
        "ego.deleteSpaces(options) expects either {scope: 'all'|'task'} or {ids: number[]} options.",
      );
    }

    const deletedSpaceIds = [];
    const skippedRunningSpaceIds = [];
    for (const id of [...new Set(ids)]) {
      const space = this.spaces.get(id);
      if (!space) continue;
      if (space.status === "active") {
        skippedRunningSpaceIds.push(id);
        continue;
      }
      await this.browserManager.remove(space.instanceId);
      this.spaces.delete(id);
      for (const [contextId, selectedId] of this.selectedSpaceIds) {
        if (selectedId === id) this.selectedSpaceIds.delete(contextId);
      }
      deletedSpaceIds.push(id);
    }
    await this.persist();
    return { deletedSpaceIds, skippedRunningSpaceIds };
  }

  async handOffTaskSpace(...args) {
    requireNoArguments("handOffTaskSpace", args);
    const space = this.requireSelectedAgentSpace();
    space.ownership = OWNERSHIP_AGENT_DELEGATED_TO_USER;
    space.delegatedToUser = true;
    space.agentTaskState = "Waiting for user";
    this.browserManager.setTaskControlVisible?.(true);
    this.browserManager.showInstance?.(space.instanceId);
    await this.persist();
    return publicTaskSpace(space);
  }

  async takeOverTaskSpace(...args) {
    requireNoArguments("takeOverTaskSpace", args);
    const space = this.requireSelectedSpace();
    if (!space.delegatedToUser || space.ownership !== OWNERSHIP_AGENT_DELEGATED_TO_USER) {
      throw bridgeError(EGO_ERROR.TASK_SPACE_UNAVAILABLE, `Task space ${space.id} is not under user control.`);
    }
    await this.ensureSpaceRuntime(space);
    space.ownership = OWNERSHIP_AGENT;
    space.delegatedToUser = false;
    space.agentTaskState = "";
    await this.persist();
    return publicTaskSpace(space);
  }

  async completeTaskSpace(...args) {
    requireNoArguments("completeTaskSpace", args);
    const space = this.requireSelectedAgentSpace();
    space.status = "completed";
    space.ownership = OWNERSHIP_AGENT_DELEGATED_TO_USER;
    space.delegatedToUser = true;
    space.agentTaskState = "Completed";
    this.browserManager.setTaskControlVisible?.(true);
    this.browserManager.showInstance?.(space.instanceId);
    await this.persist();
    return { taskSpace: publicTaskSpace(space), completed: true };
  }

  async markTaskSpaceError(message) {
    const errorMessage = requireNonEmptyString(
      message,
      "ego.markTaskSpaceError(message) expects a string error message.",
    );
    const space = this.requireSelectedAgentSpace();
    space.status = "error";
    space.error = errorMessage;
    space.agentTaskState = "Error";
    await this.browserManager.pause(space.instanceId);
    this.selectedSpaceId = null;
    await this.persist();
    return { taskSpace: publicTaskSpace(space), error: errorMessage };
  }

  async setAgentTaskState(state) {
    const value = requireNonEmptyString(state, "ego.setAgentTaskState(state) expects a string state.");
    const space = this.requireSelectedAgentSpace();
    space.agentTaskState = value;
    await this.persist();
    return { taskSpaceId: space.id, state: value };
  }

  async createTab(url) {
    const value = requireNonEmptyString(url, "ego.createTab(url) expects a string URL.");
    let parsed;
    try {
      parsed = new URL(value);
    } catch {
      throw bridgeError(EGO_ERROR.INVALID_ARGUMENT, `Invalid URL: ${value}`);
    }
    if (!(["http:", "https:", "about:", "data:"].includes(parsed.protocol))) {
      throw bridgeError(EGO_ERROR.INVALID_ARGUMENT, `Unsupported URL scheme: ${parsed.protocol}`);
    }
    const { space, state } = await this.selectedAgentRuntime();
    const result = await state.sendRawCDP("Target.createTarget", { url: parsed.toString() });
    await this.refreshRecentTabs(space, state);
    return result;
  }

  async animationHighlightMouseToPosition(x, y) {
    const targetX = Number(x);
    const targetY = Number(y);
    if (!Number.isFinite(targetX) || !Number.isFinite(targetY)) {
      throw bridgeError(
        EGO_ERROR.INVALID_ARGUMENT,
        "ego.animationHighlightMouseToPosition(x, y) expects two numbers.",
      );
    }
    const { state } = await this.selectedAgentRuntime();
    await state.sendPage("Input.dispatchMouseEvent", { type: "mouseMoved", x: targetX, y: targetY, button: "none" });
    await state.sendPage("Runtime.evaluate", {
      expression: `(() => {
        const id = "__browser_lite_agent_pointer";
        document.getElementById(id)?.remove();
        const mark = document.createElement("div");
        mark.id = id;
        Object.assign(mark.style, {
          position: "fixed", left: "${targetX - 12}px", top: "${targetY - 12}px", width: "24px", height: "24px",
          border: "3px solid #ff4d6d", borderRadius: "999px", boxSizing: "border-box", pointerEvents: "none",
          zIndex: "2147483647", boxShadow: "0 0 0 6px rgba(255,77,109,.2)", transition: "opacity .25s ease",
        });
        document.documentElement.append(mark);
        setTimeout(() => { mark.style.opacity = "0"; setTimeout(() => mark.remove(), 300); }, 700);
      })()`,
      userGesture: false,
    });
    return { x: targetX, y: targetY, shown: true };
  }

  async listTabs(...args) {
    requireNoArguments("listTabs", args);
    const { space, state } = await this.selectedAgentRuntime();
    const tabs = (await state.targets()).map((tab) => ({
      ...tab,
      active: tab.targetId === state.activeTargetId,
    }));
    await this.refreshRecentTabs(space, state);
    return { tabs };
  }

  async snapshot(options = {}) {
    if (!options || typeof options !== "object" || Array.isArray(options)) {
      throw bridgeError(EGO_ERROR.INVALID_ARGUMENT, "ego.snapshot(options?) expects an options object.");
    }
    const { state } = await this.selectedAgentRuntime();
    try {
      const targetId = state.activeTargetId || await state.ensurePage();
      const [tree, url, title] = await Promise.all([
        state.sendPage("Accessibility.getFullAXTree", {}, targetId),
        state.currentUrl(),
        state.title(),
      ]);
      let nodes = tree.nodes || [];
      if (options.interactiveOnly) nodes = nodes.filter(interactiveNode);
      const snapshotNodes = nodes.map((node) => compactAxNode(node, Boolean(options.includeStableLocator)));
      const refs = snapshotNodes
        .filter((node) => node.backendDOMNodeId)
        .map((node) => ({
          backendNodeId: node.backendDOMNodeId,
          role: node.role || "",
          name: node.name || "",
        }));
      const actionMarks = options.includeActionMarks === false ? "" : "@";
      let content = snapshotNodes.map((node, index) => {
        const ref = node.backendDOMNodeId ? `${actionMarks}ref-${node.backendDOMNodeId}` : `${index + 1}`;
        const value = node.value === undefined ? "" : ` value=${JSON.stringify(node.value)}`;
        return `[${ref}] ${node.role || "node"}${node.name ? ` ${JSON.stringify(node.name)}` : ""}${value}`;
      }).join("\n");
      const result = {
        targetId,
        url,
        title,
        content,
        refs,
        nodes: snapshotNodes,
      };
      const maxResultLength = Number(options.maxResultLength);
      if (Number.isSafeInteger(maxResultLength) && maxResultLength > 0) {
        if (content.length > maxResultLength) {
          content = content.slice(0, maxResultLength);
          return { ...result, content, truncated: true };
        }
      }
      return result;
    } catch (error) {
      if (error instanceof EgoBridgeError) throw error;
      throw bridgeError(EGO_ERROR.SNAPSHOT_FAILED, error.message || "Failed to snapshot task space.");
    }
  }

  async sendCDPMessage(message) {
    if (typeof message !== "string") {
      throw bridgeError(EGO_ERROR.INVALID_ARGUMENT, "ego.sendCDPMessage(message) expects a string message.");
    }
    let command;
    try {
      command = JSON.parse(message);
    } catch {
      throw bridgeError(EGO_ERROR.INVALID_ARGUMENT, "ego.sendCDPMessage(message) expects valid JSON.");
    }
    if (!command || typeof command.method !== "string" || !command.method) {
      throw bridgeError(EGO_ERROR.INVALID_ARGUMENT, "CDP message requires a method.");
    }
    const { state } = await this.selectedAgentRuntime();
    try {
      const result = typeof state.sendRawCDP === "function"
        ? await state.sendRawCDP(command.method, command.params || {}, command.sessionId)
        : await state.sendPage(command.method, command.params || {});
      return JSON.stringify({ ...(command.id === undefined ? {} : { id: command.id }), result });
    } catch (error) {
      throw bridgeError(EGO_ERROR.CDP_SEND_FAILED, error.message || "Failed to send CDP message.");
    }
  }

  async dispatch(method, args = [], contextId = "default") {
    const allowed = new Set([
      "createTab", "listTabs", "listTaskSpaces", "deleteSpaces", "listProfiles", "snapshot", "createTaskSpace",
      "claimTaskSpace", "closeTaskSpace", "useTaskSpace", "handOffTaskSpace", "takeOverTaskSpace",
      "completeTaskSpace", "markTaskSpaceError", "setAgentTaskState", "getBrowserVersion", "sendCDPMessage",
      "animationHighlightMouseToPosition",
    ]);
    if (!allowed.has(method) || typeof this[method] !== "function") {
      throw bridgeError(EGO_ERROR.INVALID_ARGUMENT, `Unknown Browser Lite task-space method: ${method}`);
    }
    if (!Array.isArray(args)) throw bridgeError(EGO_ERROR.INVALID_ARGUMENT, "Task-space method args must be an array.");
    return this.runSerialized(contextId, () => this[method](...args));
  }

  async runSerialized(contextId, operation) {
    const context = String(contextId || "default").slice(0, 128);
    const invoke = async () => {
      const previousContext = this.dispatchContext;
      this.dispatchContext = context;
      try {
        return await operation();
      } finally {
        this.dispatchContext = previousContext;
      }
    };
    const result = this.dispatchQueue.then(invoke, invoke);
    this.dispatchQueue = result.then(() => undefined, () => undefined);
    return result;
  }

  requireSpace(id) {
    const space = this.spaces.get(id);
    if (!space) throw bridgeError(EGO_ERROR.TASK_SPACE_NOT_FOUND, `Task space not found: ${id}`);
    return space;
  }

  requireSelectedSpace() {
    if (!this.selectedSpaceId) throw bridgeError(EGO_ERROR.TASK_SPACE_NOT_SELECTED, "Task space not selected");
    return this.requireSpace(this.selectedSpaceId);
  }

  requireSelectedAgentSpace() {
    const space = this.requireSelectedSpace();
    if (space.status !== "active") {
      throw bridgeError(EGO_ERROR.TASK_SPACE_INACTIVE, `Task space ${space.id} is not active.`);
    }
    if (space.delegatedToUser || space.ownership === OWNERSHIP_AGENT_DELEGATED_TO_USER) {
      throw bridgeError(EGO_ERROR.TASK_SPACE_USER_IN_CONTROL, "The task is under user control.");
    }
    if (space.ownership === OWNERSHIP_USER) {
      throw bridgeError(
        EGO_ERROR.TASK_SPACE_INACTIVE,
        "Task space is not assigned to an agent. Claim this task space before sending commands.",
      );
    }
    if (space.ownership !== OWNERSHIP_AGENT) {
      throw bridgeError(
        EGO_ERROR.TASK_SPACE_UNAVAILABLE,
        "Task space is not assigned to an agent. Claim this task space before sending commands.",
      );
    }
    return space;
  }

  async selectedAgentRuntime() {
    const space = this.requireSelectedAgentSpace();
    const entry = await this.ensureSpaceRuntime(space);
    if (!entry?.state) throw bridgeError(EGO_ERROR.CDP_CHANNEL_UNAVAILABLE, "CDP agent host is not available.");
    return { space, state: entry.state };
  }

  async refreshRecentTabs(space, suppliedState = undefined) {
    const state = suppliedState || this.browserManager.instances?.get(space.instanceId)?.state;
    if (!state || space.status === "completed" || space.status === "error") return;
    try {
      const targets = await state.targets();
      space.recentTabs = targets
        .map((target) => ({ title: String(target.title || ""), url: String(target.url || "") }))
        .filter((target) => target.url && !target.url.startsWith("data:text/html"))
        .slice(-5);
      space.recentTabTitles = targets
        .map((target) => target.title)
        .filter(Boolean)
        .slice(-5);
      if (typeof state.exportSessionState === "function") {
        space.browserSession = persistedBrowserSession(await state.exportSessionState());
      }
    } catch {}
  }

  async ensureSpaceRuntime(space) {
    const entry = await this.browserManager.ensure(space.instanceId);
    if (typeof entry?.state?.restoreSessionState === "function") {
      await entry.state.restoreSessionState(space.browserSession);
    }
    return entry;
  }
}
