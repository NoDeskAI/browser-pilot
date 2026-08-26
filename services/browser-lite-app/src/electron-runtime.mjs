import { app, session, WebContentsView } from "electron";
import { mkdir, rm } from "node:fs/promises";
import { join } from "node:path";

import { createBrowserLiteServer } from "./webdriver-server.mjs";

function safeInstanceId(value) {
  const normalized = String(value ?? "browser_lite").replace(/[^A-Za-z0-9._-]/g, "-");
  if (!/^[A-Za-z0-9]/.test(normalized)) return `instance-${normalized}`;
  return normalized.slice(0, 64);
}

export const APP_BAR_HEIGHT = 112;
export const TASK_CONTROL_RESERVE = 105;
export const CHROME_MENU_RESERVE = 286;

function targetIdFor(view) {
  return `electron-${view.webContents.id}`;
}

function userVisibleUrl(url, startUrl) {
  return url === "about:blank" || url === startUrl ? "" : url;
}

const TAB_GROUP_COLORS = new Set(["grey", "blue", "red", "yellow", "green", "pink", "purple", "cyan", "orange"]);

function restoredUrl(value) {
  try {
    const url = new URL(String(value || "about:blank"));
    return ["http:", "https:", "about:", "chrome-extension:"].includes(url.protocol) ? url.toString() : "about:blank";
  } catch {
    return "about:blank";
  }
}

function closeServer(server) {
  return new Promise((resolveClose) => {
    let finished = false;
    let timeoutId;
    const finish = () => {
      if (finished) return;
      finished = true;
      clearTimeout(timeoutId);
      resolveClose();
    };
    timeoutId = setTimeout(finish, 1_500);
    try {
      server.close(finish);
      server.closeAllConnections?.();
    } catch {
      finish();
    }
  });
}

export class ElectronBrowserLiteState {
  constructor(config, { onChanged } = {}) {
    this.config = config;
    this.runtimeKind = "embedded";
    this.sessionId = `browser-lite-${config.instanceId}`;
    this.browserVersion = `Chromium/${process.versions.chrome}`;
    this.startedAt = null;
    this.activeTargetId = null;
    this.windows = new Map();
    this.pointer = { x: 0, y: 0 };
    this.timeouts = { script: 30_000, pageLoad: 60_000, implicit: 0 };
    this.startPromise = null;
    this.onChanged = onChanged;
    this.onActivate = config.onActivate;
    this.paused = false;
    this.stopped = false;
    this.visible = false;
    this.preview = { capturedAt: 0, dataUrl: "" };
    this.partition = `persist:browser-lite-${safeInstanceId(config.instanceId)}`;
    this.extensionDescriptors = [];
    this.extensionRuntime = new Map();
    this.extensionsLoaded = false;
    this.tabGroups = new Map();
    this.tabGroupByTarget = new Map();
    this.nextTabGroupId = 1;
    this.sessionStateRestored = false;
  }

  async start() {
    if (!this.startPromise) {
      this.startPromise = this.startInner().catch((error) => {
        this.startPromise = null;
        throw error;
      });
    }
    return this.startPromise;
  }

  async startInner() {
    this.stopped = false;
    await mkdir(this.config.profileDir, { recursive: true, mode: 0o700 });
    if (this.windows.size === 0) await this.createWindow(this.config.startUrl || "about:blank");
    this.startedAt ||= new Date().toISOString();
    this.notifyChanged();
  }

  notifyChanged() {
    this.onChanged?.();
  }

  async createWindow(url = "about:blank", { groupId = "" } = {}) {
    const view = new WebContentsView({
      webPreferences: {
        partition: this.partition,
        nodeIntegration: false,
        contextIsolation: true,
        sandbox: true,
        webSecurity: true,
        spellcheck: true,
      },
    });
    const targetId = targetIdFor(view);
    this.config.hostWindow.contentView.addChildView(view);
    view.setBounds(this.config.browserBounds());
    view.setVisible(false);
    view.webContents.setUserAgent(
      `Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) `
      + `AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${process.versions.chrome} Safari/537.36`,
    );
    this.windows.set(targetId, view);
    if (groupId && this.tabGroups.has(groupId)) this.tabGroupByTarget.set(targetId, groupId);
    this.activeTargetId = targetId;
    view.webContents.on("destroyed", () => {
      this.windows.delete(targetId);
      this.removeTargetFromGroup(targetId);
      if (this.activeTargetId === targetId) this.activeTargetId = [...this.windows.keys()].at(-1) ?? null;
      this.notifyChanged();
    });
    view.webContents.on("did-finish-load", () => {
      if (this.visible) void this.capturePreview();
      this.notifyChanged();
    });
    view.webContents.on("did-start-loading", () => this.notifyChanged());
    view.webContents.on("did-stop-loading", () => this.notifyChanged());
    view.webContents.on("did-navigate", () => this.notifyChanged());
    view.webContents.on("did-navigate-in-page", () => this.notifyChanged());
    view.webContents.on("page-title-updated", () => this.notifyChanged());
    view.webContents.setWindowOpenHandler(({ url: nextUrl }) => {
      void this.createWindow(nextUrl);
      return { action: "deny" };
    });
    if (!view.webContents.debugger.isAttached()) view.webContents.debugger.attach("1.3");
    await view.webContents.loadURL(url);
    if (this.visible) this.setVisible(true, targetId);
    this.notifyChanged();
    return { targetId, window: view };
  }

  removeTargetFromGroup(targetId) {
    const groupId = this.tabGroupByTarget.get(targetId);
    this.tabGroupByTarget.delete(targetId);
    if (groupId && ![...this.tabGroupByTarget.values()].includes(groupId)) this.tabGroups.delete(groupId);
  }

  liveWindows() {
    for (const [targetId, view] of this.windows) {
      try {
        if (view.webContents.isDestroyed()) this.windows.delete(targetId);
      } catch {
        this.windows.delete(targetId);
      }
    }
    return [...this.windows.entries()];
  }

  windowForTarget(targetId = undefined) {
    const selected = targetId ?? this.activeTargetId;
    const view = selected ? this.windows.get(selected) : null;
    if (!view) return null;
    try {
      if (view.webContents.isDestroyed()) {
        this.windows.delete(selected);
        return null;
      }
      return view;
    } catch {
      this.windows.delete(selected);
      return null;
    }
  }

  layout() {
    const bounds = this.config.browserBounds();
    for (const [targetId, view] of this.liveWindows()) {
      try { view.setBounds(bounds); } catch { this.windows.delete(targetId); }
    }
  }

  setVisible(visible, targetId = this.activeTargetId) {
    this.visible = Boolean(visible && !this.paused && !this.stopped);
    if (targetId && this.windows.has(targetId)) this.activeTargetId = targetId;
    for (const [id, view] of this.liveWindows()) {
      try { view.setVisible(this.visible && id === this.activeTargetId); } catch { this.windows.delete(id); }
    }
    if (this.visible) {
      this.layout();
      this.windowForTarget()?.webContents.focus();
    }
  }

  destroyView(targetId) {
    const view = this.windows.get(targetId);
    if (!view) return false;
    this.config.hostWindow.contentView.removeChildView(view);
    this.windows.delete(targetId);
    this.removeTargetFromGroup(targetId);
    if (!view.webContents.isDestroyed()) view.webContents.close();
    if (this.activeTargetId === targetId) this.activeTargetId = [...this.windows.keys()].at(-1) ?? null;
    if (this.visible && this.activeTargetId) this.setVisible(true, this.activeTargetId);
    return true;
  }

  async ensureConnected() {
    await this.start();
  }

  async targets() {
    await this.start();
    return this.liveWindows().map(([targetId, window]) => ({
      targetId,
      type: "page",
      title: window.webContents.getTitle(),
      url: window.webContents.getURL(),
      attached: window.webContents.debugger.isAttached(),
      active: targetId === this.activeTargetId,
      groupId: this.tabGroupByTarget.get(targetId) || "",
    }));
  }

  tabGroupsState() {
    return [...this.tabGroups.values()].map((group) => ({
      ...group,
      targetIds: [...this.tabGroupByTarget]
        .filter(([, groupId]) => groupId === group.id)
        .map(([targetId]) => targetId),
    }));
  }

  createTabGroup({ targetId = this.activeTargetId, name = "新建标签组", color = "grey" } = {}) {
    if (!targetId || !this.windows.has(targetId)) throw new Error("请选择要分组的标签页");
    this.removeTargetFromGroup(targetId);
    const id = `group-${this.nextTabGroupId++}`;
    this.tabGroups.set(id, {
      id,
      name: String(name || "新建标签组").trim().slice(0, 40) || "新建标签组",
      color: TAB_GROUP_COLORS.has(color) ? color : "grey",
      collapsed: false,
    });
    this.tabGroupByTarget.set(targetId, id);
    this.notifyChanged();
    return id;
  }

  toggleTabGroup(groupId) {
    const group = this.tabGroups.get(String(groupId));
    if (!group) throw new Error("标签组不存在");
    group.collapsed = !group.collapsed;
    this.notifyChanged();
  }

  updateTabGroup({ groupId, name, color } = {}) {
    const group = this.tabGroups.get(String(groupId));
    if (!group) throw new Error("标签组不存在");
    if (name !== undefined) group.name = String(name).trim().slice(0, 40) || "新建标签组";
    if (color !== undefined && TAB_GROUP_COLORS.has(color)) group.color = color;
    this.notifyChanged();
  }

  moveTabToGroup({ targetId, groupId = "" } = {}) {
    const id = String(targetId || "");
    if (!this.windows.has(id)) throw new Error("标签页不存在");
    if ((this.tabGroupByTarget.get(id) || "") === String(groupId || "")) return;
    this.removeTargetFromGroup(id);
    if (groupId) {
      if (!this.tabGroups.has(String(groupId))) throw new Error("标签组不存在");
      this.tabGroupByTarget.set(id, String(groupId));
    }
    this.notifyChanged();
  }

  removeTabGroup(groupId) {
    const id = String(groupId || "");
    if (!this.tabGroups.has(id)) return;
    for (const [targetId, assignedGroupId] of this.tabGroupByTarget) {
      if (assignedGroupId === id) this.tabGroupByTarget.delete(targetId);
    }
    this.tabGroups.delete(id);
    this.notifyChanged();
  }

  async exportSessionState() {
    const tabs = (await this.targets()).map((tab) => ({
      url: restoredUrl(tab.url),
      active: tab.active,
      groupId: tab.groupId || "",
    }));
    return { version: 1, tabs, groups: this.tabGroupsState().map(({ targetIds, ...group }) => group) };
  }

  async restoreSessionState(snapshot) {
    if (this.sessionStateRestored) return;
    this.sessionStateRestored = true;
    if (!Array.isArray(snapshot?.tabs) || snapshot.tabs.length === 0) return;
    for (const [targetId] of this.liveWindows()) this.destroyView(targetId);
    this.tabGroups.clear();
    this.tabGroupByTarget.clear();
    for (const saved of snapshot.groups || []) {
      const id = String(saved?.id || "");
      if (!id) continue;
      this.tabGroups.set(id, {
        id,
        name: String(saved?.name || "新建标签组").slice(0, 40),
        color: TAB_GROUP_COLORS.has(saved?.color) ? saved.color : "grey",
        collapsed: Boolean(saved?.collapsed),
      });
      const numericId = Number(id.replace(/^group-/, ""));
      if (Number.isSafeInteger(numericId)) this.nextTabGroupId = Math.max(this.nextTabGroupId, numericId + 1);
    }
    let activeTargetId = "";
    for (const saved of snapshot.tabs.slice(0, 100)) {
      const created = await this.createWindow(restoredUrl(saved?.url), { groupId: String(saved?.groupId || "") });
      if (saved?.active) activeTargetId = created.targetId;
    }
    if (activeTargetId) await this.activate(activeTargetId);
    this.notifyChanged();
  }

  async loadExtensions(descriptors = []) {
    if (this.extensionsLoaded) return;
    this.extensionsLoaded = true;
    this.extensionDescriptors = descriptors.map((descriptor) => ({ ...descriptor }));
    const partitionSession = session.fromPartition(this.partition);
    for (const descriptor of this.extensionDescriptors) {
      if (!descriptor.enabled) {
        this.extensionRuntime.set(descriptor.id, { status: "disabled", error: "" });
        continue;
      }
      let loadOperation;
      try {
        loadOperation = partitionSession.extensions.loadExtension(descriptor.path, { allowFileAccess: false });
        let timeoutId;
        const loaded = await Promise.race([
          loadOperation,
          new Promise((_, reject) => {
            timeoutId = setTimeout(() => reject(new Error("扩展加载超时")), 2_500);
          }),
        ]).finally(() => clearTimeout(timeoutId));
        this.extensionRuntime.set(descriptor.id, { status: "loaded", error: "", extension: loaded });
      } catch (error) {
        const message = String(error?.message || error).slice(0, 240);
        this.extensionRuntime.set(descriptor.id, { status: "error", error: message });
        if (message === "扩展加载超时") {
          void loadOperation?.then((loaded) => {
            this.extensionRuntime.set(descriptor.id, { status: "loaded", error: "", extension: loaded });
            this.notifyChanged();
          }).catch(() => {});
        }
      }
    }
    this.notifyChanged();
  }

  extensionsState() {
    return this.extensionDescriptors.map(({ path, ...descriptor }) => ({
      ...descriptor,
      status: this.extensionRuntime.get(descriptor.id)?.status || "available",
      error: this.extensionRuntime.get(descriptor.id)?.error || "",
    }));
  }

  async openExtension(extensionId) {
    const id = String(extensionId || "");
    const descriptor = this.extensionDescriptors.find((candidate) => candidate.id === id);
    if (!descriptor) throw new Error("扩展程序不存在");
    if (this.extensionRuntime.get(id)?.status !== "loaded") throw new Error("这个扩展未能在当前 Space 中加载");
    if (!descriptor.defaultPopup) throw new Error("这个扩展没有可打开的弹出页");
    return this.createWindow(`chrome-extension://${id}/${descriptor.defaultPopup.replace(/^\/+/, "")}`);
  }

  navigationState() {
    const window = this.windowForTarget();
    if (!window) {
      return { url: "", title: "", loading: false, canGoBack: false, canGoForward: false };
    }
    const navigation = window.webContents.navigationHistory;
    const url = window.webContents.getURL();
    return {
      url: userVisibleUrl(url, this.config.startUrl || ""),
      title: window.webContents.getTitle(),
      loading: window.webContents.isLoading(),
      canGoBack: navigation.canGoBack(),
      canGoForward: navigation.canGoForward(),
    };
  }

  async ensurePage() {
    await this.start();
    const active = this.windowForTarget();
    if (active) return this.activeTargetId;
    const existing = this.liveWindows().at(-1);
    if (existing) {
      this.activeTargetId = existing[0];
      return existing[0];
    }
    return (await this.createWindow()).targetId;
  }

  async attach(targetId) {
    const window = this.windowForTarget(targetId);
    if (!window) throw new Error(`Unknown Browser Lite target: ${targetId}`);
    if (!window.webContents.debugger.isAttached()) window.webContents.debugger.attach("1.3");
    return targetId;
  }

  async sendPage(method, params = {}, targetId = undefined) {
    await this.ensureConnected();

    if (method === "Target.createTarget") {
      const created = await this.createWindow(params.url || "about:blank");
      return { targetId: created.targetId };
    }
    if (method === "Target.getTargets") return { targetInfos: await this.targets() };
    if (method === "Target.activateTarget") {
      await this.activate(params.targetId);
      return {};
    }
    if (method === "Target.closeTarget") {
      return { success: this.destroyView(params.targetId) };
    }

    const selectedTarget = targetId ?? await this.ensurePage();
    const window = this.windowForTarget(selectedTarget);
    if (!window) throw new Error(`Unknown Browser Lite target: ${selectedTarget}`);

    if (method === "Browser.getWindowForTarget") {
      const [x, y] = this.config.hostWindow.getPosition();
      const { width, height } = this.config.browserBounds();
      return {
        windowId: this.config.hostWindow.id,
        bounds: { left: x, top: y + APP_BAR_HEIGHT, width, height, windowState: "normal" },
      };
    }
    if (method === "Browser.setWindowBounds") {
      const bounds = params.bounds ?? {};
      if (Number.isFinite(bounds.left) && Number.isFinite(bounds.top)) this.config.hostWindow.setPosition(bounds.left, bounds.top);
      if (Number.isFinite(bounds.width) && Number.isFinite(bounds.height)) {
        this.config.width = bounds.width;
        this.config.height = bounds.height;
        this.config.hostWindow.setContentSize(bounds.width, bounds.height + APP_BAR_HEIGHT);
      }
      return {};
    }
    if (method === "Browser.setDownloadBehavior") {
      const downloadPath = params.downloadPath || join(app.getPath("downloads"), "Browser Lite", this.config.instanceId);
      await mkdir(downloadPath, { recursive: true, mode: 0o700 });
      session.fromPartition(this.partition).setDownloadPath(downloadPath);
      return {};
    }

    await this.attach(selectedTarget);
    return window.webContents.debugger.sendCommand(method, params);
  }

  async sendRawCDP(method, params = {}, sessionId = undefined) {
    if (sessionId === undefined && ["Target.createTarget", "Target.getTargets", "Target.activateTarget", "Target.closeTarget"].includes(method)) {
      return this.sendPage(method, params);
    }
    const selectedTarget = await this.ensurePage();
    const window = this.windowForTarget(selectedTarget);
    if (!window) throw new Error(`Unknown Browser Lite target: ${selectedTarget}`);
    await this.attach(selectedTarget);
    if (sessionId !== undefined) return window.webContents.debugger.sendCommand(method, params, sessionId);
    return this.sendPage(method, params, selectedTarget);
  }

  async activate(targetId = undefined) {
    const selectedTarget = targetId ?? await this.ensurePage();
    const window = this.windowForTarget(selectedTarget);
    if (!window) throw new Error(`Unknown Browser Lite target: ${selectedTarget}`);
    this.activeTargetId = selectedTarget;
    this.onActivate?.(this.config.instanceId, selectedTarget);
    if (this.visible) this.setVisible(true, selectedTarget);
    else window.webContents.focus();
    return selectedTarget;
  }

  async setActiveTarget(targetId) {
    await this.activate(targetId);
  }

  async evaluateExpression(expression, { awaitPromise = true } = {}) {
    const result = await this.sendPage("Runtime.evaluate", {
      expression,
      awaitPromise,
      returnByValue: true,
      userGesture: true,
    });
    if (result.exceptionDetails) {
      const message = result.exceptionDetails.exception?.description || result.exceptionDetails.text || "JavaScript execution failed";
      throw new Error(message);
    }
    return Object.prototype.hasOwnProperty.call(result.result ?? {}, "value") ? result.result.value : null;
  }

  async execute(script, args) {
    const expression = `(() => {
      const __args = ${JSON.stringify(args ?? [])};
      const __fn = new Function(...__args.map((_, index) => "arg" + index), ${JSON.stringify(script ?? "")});
      return __fn(...__args);
    })()`;
    return this.evaluateExpression(expression);
  }

  async waitForDocumentReady() {
    const deadline = Date.now() + this.timeouts.pageLoad;
    while (Date.now() < deadline) {
      try {
        const readyState = await this.evaluateExpression("document.readyState");
        if (readyState === "interactive" || readyState === "complete") return;
      } catch {
        // The execution context changes during navigation.
      }
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 100));
    }
    throw new Error(`Page did not become ready within ${this.timeouts.pageLoad}ms`);
  }

  async navigate(url) {
    if (typeof url !== "string" || !url.trim()) throw new Error("url must be a non-empty string");
    await this.activate();
    const result = await this.sendPage("Page.navigate", { url: url.trim() });
    if (result.errorText) throw new Error(result.errorText);
    await this.waitForDocumentReady();
  }

  async goBack() {
    const window = this.windowForTarget(await this.ensurePage());
    if (window?.webContents.navigationHistory.canGoBack()) await window.webContents.navigationHistory.goBack();
  }

  async goForward() {
    const window = this.windowForTarget(await this.ensurePage());
    if (window?.webContents.navigationHistory.canGoForward()) await window.webContents.navigationHistory.goForward();
  }

  async reload() {
    const window = this.windowForTarget(await this.ensurePage());
    window?.webContents.reload();
  }

  async currentUrl() {
    return this.evaluateExpression("location.href");
  }

  async title() {
    return this.evaluateExpression("document.title");
  }

  async source() {
    return this.evaluateExpression("document.documentElement ? document.documentElement.outerHTML : ''");
  }

  async screenshot() {
    const result = await this.sendPage("Page.captureScreenshot", { format: "png", fromSurface: true });
    return result.data;
  }

  async capturePreview() {
    const window = this.windowForTarget();
    if (!window) return this.preview.dataUrl;
    try {
      const data = await this.screenshot();
      if (!data) return this.preview.dataUrl;
      this.preview = { capturedAt: Date.now(), dataUrl: `data:image/png;base64,${data}` };
      return this.preview.dataUrl;
    } catch {
      return this.preview.dataUrl;
    }
  }

  async previewDataUrl() {
    if (this.preview.dataUrl) return this.preview.dataUrl;
    return this.capturePreview();
  }

  async closeActiveTarget() {
    const targetId = await this.ensurePage();
    this.destroyView(targetId);
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 100));
    if (this.liveWindows().length === 0) await this.createWindow();
    return (await this.targets()).map((target) => target.targetId);
  }

  async windowRect() {
    await this.ensurePage();
    const [x, y] = this.config.hostWindow.getPosition();
    const { width, height } = this.config.browserBounds();
    return { x, y, width, height };
  }

  async setWindowRect(body) {
    await this.ensurePage();
    const current = await this.windowRect();
    const x = Number.isFinite(Number(body.x)) ? Number(body.x) : current.x;
    const y = Number.isFinite(Number(body.y)) ? Number(body.y) : current.y;
    const width = Math.max(320, Number(body.width) || current.width);
    const height = Math.max(240, Number(body.height) || current.height);
    this.config.width = width;
    this.config.height = height;
    this.config.hostWindow.setBounds({ x, y, width, height: height + APP_BAR_HEIGHT });
    return this.windowRect();
  }

  async performActions(sources) {
    for (const source of sources ?? []) {
      for (const action of source.actions ?? []) {
        if (action.type === "pause") {
          await new Promise((resolveDelay) => setTimeout(resolveDelay, Number(action.duration ?? 0)));
        } else if (source.type === "key") {
          await this.performKeyAction(action);
        } else if (source.type === "wheel" && action.type === "scroll") {
          this.pointer = { x: Number(action.x ?? 0), y: Number(action.y ?? 0) };
          await this.sendPage("Input.dispatchMouseEvent", {
            type: "mouseWheel",
            x: this.pointer.x,
            y: this.pointer.y,
            deltaX: Number(action.deltaX ?? 0),
            deltaY: Number(action.deltaY ?? 0),
          });
        } else if (source.type === "pointer") {
          await this.performPointerAction(action);
        }
      }
    }
  }

  async performKeyAction(action) {
    const special = {
      "\ue003": ["Backspace", "Backspace", 8],
      "\ue004": ["Tab", "Tab", 9],
      "\ue006": ["Enter", "Enter", 13],
      "\ue007": ["Enter", "Enter", 13],
      "\ue00c": ["Escape", "Escape", 27],
      "\ue012": ["ArrowLeft", "ArrowLeft", 37],
      "\ue013": ["ArrowUp", "ArrowUp", 38],
      "\ue014": ["ArrowRight", "ArrowRight", 39],
      "\ue015": ["ArrowDown", "ArrowDown", 40],
      "\ue017": ["ArrowDown", "ArrowDown", 40],
    }[String(action.value ?? "")];
    if (action.type === "keyDown" && !special) {
      const text = String(action.value ?? "");
      if (text) await this.sendPage("Input.insertText", { text });
      return;
    }
    if (!special || !["keyDown", "keyUp"].includes(action.type)) return;
    await this.sendPage("Input.dispatchKeyEvent", {
      type: action.type === "keyDown" ? "rawKeyDown" : "keyUp",
      key: special[0],
      code: special[1],
      windowsVirtualKeyCode: special[2],
      nativeVirtualKeyCode: special[2],
    });
  }

  async performPointerAction(action) {
    if (action.type === "pointerMove") {
      this.pointer = { x: Number(action.x ?? 0), y: Number(action.y ?? 0) };
      await this.sendPage("Input.dispatchMouseEvent", {
        type: "mouseMoved", x: this.pointer.x, y: this.pointer.y, button: "none",
      });
    } else if (action.type === "pointerDown" || action.type === "pointerUp") {
      await this.sendPage("Input.dispatchMouseEvent", {
        type: action.type === "pointerDown" ? "mousePressed" : "mouseReleased",
        x: this.pointer.x,
        y: this.pointer.y,
        button: "left",
        buttons: action.type === "pointerDown" ? 1 : 0,
        clickCount: 1,
      });
    }
  }

  async cookies() {
    const result = await this.sendPage("Network.getAllCookies");
    return (result.cookies ?? []).map((cookie) => ({
      name: cookie.name,
      value: cookie.value,
      path: cookie.path,
      domain: cookie.domain,
      secure: cookie.secure,
      httpOnly: cookie.httpOnly,
      sameSite: cookie.sameSite,
      ...(cookie.expires > 0 ? { expiry: Math.trunc(cookie.expires) } : {}),
    }));
  }

  cookieParams(cookie, fallbackUrl = "") {
    if (!cookie || typeof cookie.name !== "string" || typeof cookie.value !== "string") {
      throw new Error("cookie must include string name and value");
    }
    const domainUrl = cookie.domain
      ? `${cookie.secure ? "https" : "http"}://${String(cookie.domain).replace(/^\./, "")}${cookie.path || "/"}`
      : "";
    const params = {
      name: cookie.name,
      value: cookie.value,
      url: cookie.url || fallbackUrl || domainUrl,
    };
    for (const key of ["domain", "path", "secure", "httpOnly"]) {
      if (cookie[key] !== undefined) params[key] = cookie[key];
    }
    const sameSite = {
      no_restriction: "None",
      lax: "Lax",
      strict: "Strict",
      None: "None",
      Lax: "Lax",
      Strict: "Strict",
    }[cookie.sameSite];
    if (sameSite) params.sameSite = sameSite;
    if (cookie.expiry !== undefined || cookie.expirationDate !== undefined) {
      params.expires = Number(cookie.expiry ?? cookie.expirationDate);
    }
    return params;
  }

  async addCookie(cookie) {
    const params = this.cookieParams(cookie, cookie?.url ? "" : await this.currentUrl());
    const result = await this.sendPage("Network.setCookie", params);
    if (result.success === false) throw new Error("Embedded Chromium rejected cookie");
  }

  async addCookies(cookies) {
    if (!Array.isArray(cookies)) throw new Error("cookies must be an array");
    const needsFallbackUrl = cookies.some((cookie) => !cookie?.url && !cookie?.domain);
    const fallbackUrl = needsFallbackUrl ? await this.currentUrl() : "";
    const normalized = [];
    for (const cookie of cookies) {
      try { normalized.push(this.cookieParams(cookie, fallbackUrl)); } catch {}
    }
    let imported = 0;
    for (let offset = 0; offset < normalized.length; offset += 100) {
      const chunk = normalized.slice(offset, offset + 100);
      try {
        await this.sendPage("Network.setCookies", { cookies: chunk });
        imported += chunk.length;
      } catch {
        for (const params of chunk) {
          try {
            const result = await this.sendPage("Network.setCookie", params);
            if (result.success !== false) imported += 1;
          } catch {}
        }
      }
    }
    return imported;
  }

  async deleteCookie(name = undefined) {
    const cookies = await this.cookies();
    const currentUrl = await this.currentUrl();
    for (const cookie of cookies) {
      if (name !== undefined && cookie.name !== name) continue;
      await this.sendPage("Network.deleteCookies", {
        name: cookie.name, url: currentUrl, domain: cookie.domain, path: cookie.path,
      });
    }
  }

  async pause() {
    this.paused = true;
    this.setVisible(false);
    this.notifyChanged();
  }

  async resume() {
    this.paused = false;
    this.stopped = false;
    await this.start();
    this.notifyChanged();
  }

  async stop() {
    this.visible = false;
    for (const [targetId] of this.liveWindows()) this.destroyView(targetId);
    this.activeTargetId = null;
    this.tabGroups.clear();
    this.tabGroupByTarget.clear();
    this.sessionStateRestored = false;
    this.startPromise = null;
    this.stopped = true;
    this.notifyChanged();
  }

  async remove() {
    await this.stop();
    const partitionSession = session.fromPartition(this.partition);
    await partitionSession.clearCache();
    await partitionSession.clearStorageData();
    await rm(this.config.profileDir, { recursive: true, force: true });
  }

  async runtimeStatus() {
    const targets = this.stopped ? [] : await this.targets();
    return {
      ready: !this.stopped,
      runtime: "browser_lite",
      version: app.getVersion(),
      instanceId: this.config.instanceId,
      sessionId: this.sessionId,
      host: this.config.host,
      port: this.config.port,
      profileDir: this.config.profileDir,
      browserVersion: this.browserVersion,
      chromiumVersion: process.versions.chrome,
      electronVersion: process.versions.electron,
      bundledChromium: false,
      embedded: true,
      startedAt: this.startedAt,
      activeTargetId: this.activeTargetId,
      tabCount: targets.length,
      paused: this.paused,
      stopped: this.stopped,
    };
  }

  closeControlConnection() {}
}

export class BrowserLiteManager {
  constructor({ hostWindow, installation, onChanged } = {}) {
    if (!hostWindow) throw new Error("Browser Lite requires a host window");
    this.instances = new Map();
    this.hostWindow = hostWindow;
    this.installation = installation;
    this.onChanged = onChanged;
    this.viewMode = "spaces";
    this.activeInstanceId = null;
    this.taskControlVisible = false;
    this.chromeMenuOpen = false;
    this.modeContentSizes = {
      spaces: [1280, 760],
      settings: [1280, 800],
    };
    hostWindow.on("resize", () => {
      if (this.viewMode === "settings" || this.viewMode === "spaces") {
        this.modeContentSizes[this.viewMode] = hostWindow.getContentSize();
      }
      this.layout();
    });
  }

  browserBounds() {
    const [width, contentHeight] = this.hostWindow.getContentSize();
    const footer = this.taskControlVisible ? TASK_CONTROL_RESERVE : 0;
    const menu = this.chromeMenuOpen ? CHROME_MENU_RESERVE : 0;
    return { x: 0, y: APP_BAR_HEIGHT + menu, width, height: Math.max(240, contentHeight - APP_BAR_HEIGHT - footer - menu) };
  }

  setTaskControlVisible(visible) {
    this.taskControlVisible = Boolean(visible);
    this.layout();
  }

  setChromeMenuOpen(open) {
    this.chromeMenuOpen = Boolean(open && this.viewMode === "browser");
    this.layout();
  }

  layout() {
    for (const entry of this.instances.values()) entry.state.layout();
  }

  workspaceState() {
    return {
      mode: this.viewMode,
      activeInstanceId: this.activeInstanceId,
      browserAvailable: Boolean(this.activeInstanceId && this.instances.has(this.activeInstanceId)),
    };
  }

  async showSpaces() {
    await Promise.all([...this.instances.values()].map(async (entry) => {
      try { await entry.state.capturePreview?.(); } catch {}
      try { entry.state.setVisible(false); } catch {}
    }));
    this.taskControlVisible = false;
    this.chromeMenuOpen = false;
    this.viewMode = "spaces";
    try { this.hostWindow.setTitle("Browser Lite"); } catch (error) { throw new Error(`Space 总览设置标题失败: ${error.message || error}`); }
    try { this.hostWindow.setContentSize(...this.modeContentSizes.spaces); } catch (error) { throw new Error(`Space 总览调整窗口失败: ${error.message || error}`); }
    try { await app.dock?.show(); } catch (error) { throw new Error(`Space 总览恢复 Dock 失败: ${error.message || error}`); }
    try { this.hostWindow.show(); } catch (error) { throw new Error(`Space 总览显示窗口失败: ${error.message || error}`); }
    try { app.focus({ steal: true }); } catch {}
    try { this.hostWindow.focus(); } catch (error) { throw new Error(`Space 总览聚焦窗口失败: ${error.message || error}`); }
    this.onChanged?.();
  }

  async showSettings() {
    await Promise.all([...this.instances.values()].map(async (entry) => {
      try { entry.state.setVisible(false); } catch {}
    }));
    this.taskControlVisible = false;
    this.chromeMenuOpen = false;
    this.viewMode = "settings";
    this.hostWindow.setTitle(this.installation?.isComplete() ? "Browser Lite — 设置" : "Browser Lite — 安装");
    this.hostWindow.setContentSize(...this.modeContentSizes.settings);
    await app.dock?.show();
    this.hostWindow.show();
    app.focus({ steal: true });
    this.hostWindow.focus();
    this.onChanged?.();
  }

  async showInstance(instanceId) {
    const id = safeInstanceId(instanceId ?? this.activeInstanceId);
    const entry = this.instances.get(id);
    if (!entry) return false;
    this.activeInstanceId = id;
    this.viewMode = "browser";
    this.hostWindow.setTitle(`Browser Lite — ${id}`);
    await app.dock?.show();
    this.hostWindow.show();
    app.focus({ steal: true });
    this.hostWindow.focus();
    this.layout();
    await Promise.all([...this.instances].map(async ([otherId, other]) => {
      try { other.state.setVisible(otherId === id); } catch {}
    }));
    this.onChanged?.();
    return true;
  }

  async ensure(instanceId, options = {}) {
    const id = safeInstanceId(instanceId);
    let entry = this.instances.get(id);
    if (!entry) {
      await this.installation?.prepareInstance(id);
      const profileDir = join(app.getPath("userData"), "Instances", id);
      const config = {
        host: "127.0.0.1",
        port: Number(options.port ?? 0),
        instanceId: id,
        profileDir,
        dataRoot: app.getPath("userData"),
        startUrl: "about:blank",
        profileDirectory: this.installation?.profileDirectory?.() || "Default",
        hostWindow: this.hostWindow,
        browserBounds: () => this.browserBounds(),
        onChanged: () => this.onChanged?.(),
        appVersion: app.getVersion(),
        width: Math.max(320, Number(options.width) || 1280),
        height: Math.max(240, Number(options.height) || 800),
      };
      const state = new ElectronBrowserLiteState(config, { onChanged: config.onChanged });
      const server = createBrowserLiteServer(state);
      await new Promise((resolveListen, rejectListen) => {
        server.once("error", rejectListen);
        server.listen(config.port, config.host, resolveListen);
      });
      const address = server.address();
      config.port = typeof address === "object" && address ? address.port : config.port;
      try {
        const extensionLoading = state.loadExtensions(await this.installation?.runtimeExtensions?.() || []);
        await state.start();
        await this.installation?.seedRuntimeCookies(id, state);
        await state.setVisible(false);
        void extensionLoading.catch(() => {});
      } catch (error) {
        await closeServer(server);
        throw error;
      }
      entry = { id, state, server, baseUrl: `http://${config.host}:${config.port}` };
      this.instances.set(id, entry);
      this.onChanged?.();
    } else {
      if (Number(options.width) >= 320) entry.state.config.width = Number(options.width);
      if (Number(options.height) >= 240) entry.state.config.height = Number(options.height);
      await entry.state.resume();
    }
    return entry;
  }

  async request(instanceId, request) {
    const entry = await this.ensure(instanceId);
    const url = `${entry.baseUrl}${request.path}`;
    const response = await fetch(url, {
      method: request.method || "GET",
      headers: { "content-type": "application/json" },
      body: request.body === undefined || request.body === null
        ? undefined
        : typeof request.body === "string" ? request.body : JSON.stringify(request.body),
    });
    return {
      status: response.status,
      headers: { "content-type": response.headers.get("content-type") || "application/json" },
      body: await response.text(),
    };
  }

  async pause(instanceId) {
    const id = safeInstanceId(instanceId);
    const entry = this.instances.get(id);
    if (entry) await entry.state.pause();
    if (this.viewMode === "browser" && this.activeInstanceId === id) await this.showSpaces();
  }

  async stop(instanceId) {
    const id = safeInstanceId(instanceId);
    const entry = this.instances.get(id);
    if (entry) await entry.state.stop();
    if (this.viewMode === "browser" && this.activeInstanceId === id) await this.showSpaces();
  }

  async remove(instanceId) {
    const id = safeInstanceId(instanceId);
    const entry = this.instances.get(id);
    if (!entry) return;
    await entry.state.remove();
    await closeServer(entry.server);
    this.instances.delete(id);
    if (this.activeInstanceId === id) {
      this.activeInstanceId = null;
      await this.showSpaces();
    }
    this.onChanged?.();
  }

  async status(instanceId) {
    const entry = this.instances.get(safeInstanceId(instanceId));
    if (!entry) return { status: "not_found" };
    const runtime = await entry.state.runtimeStatus();
    return { status: runtime.stopped ? "exited" : runtime.paused ? "paused" : "running", runtime };
  }

  async list() {
    const result = [];
    for (const [id, entry] of this.instances) {
      try {
        result.push({ id, baseUrl: entry.baseUrl, ...(await entry.state.runtimeStatus()) });
      } catch (error) {
        result.push({ id, baseUrl: entry.baseUrl, ready: false, error: error.message || String(error) });
      }
    }
    return result;
  }

  async shutdown() {
    for (const entry of this.instances.values()) {
      await entry.state.stop();
      await closeServer(entry.server);
    }
    this.instances.clear();
    this.activeInstanceId = null;
    this.viewMode = "spaces";
  }

  async resetForReinstall() {
    await this.shutdown();
    this.onChanged?.();
  }
}
