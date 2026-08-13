import { app, session, WebContentsView } from "electron";
import { createServer } from "node:http";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";

let runtimeModulePromise;

async function runtimeModule() {
  if (!runtimeModulePromise) {
    const modulePath = app.isPackaged
      ? join(process.resourcesPath, "browser-lite-runtime", "browser-lite.mjs")
      : join(app.getAppPath(), "..", "browser-lite-runtime", "browser-lite.mjs");
    runtimeModulePromise = import(modulePath);
  }
  return runtimeModulePromise;
}

function safeInstanceId(value) {
  const normalized = String(value ?? "browser_lite").replace(/[^A-Za-z0-9._-]/g, "-");
  if (!/^[A-Za-z0-9]/.test(normalized)) return `instance-${normalized}`;
  return normalized.slice(0, 64);
}

export const APP_BAR_HEIGHT = 56;

function targetIdFor(view) {
  return `electron-${view.webContents.id}`;
}

export class ElectronBrowserLiteState {
  constructor(config, { onChanged } = {}) {
    this.config = config;
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
    this.partition = `persist:browser-lite-${safeInstanceId(config.instanceId)}`;
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

  async createWindow(url = "about:blank") {
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
    this.activeTargetId = targetId;
    view.webContents.on("destroyed", () => {
      this.windows.delete(targetId);
      if (this.activeTargetId === targetId) this.activeTargetId = [...this.windows.keys()].at(-1) ?? null;
      this.notifyChanged();
    });
    view.webContents.on("did-finish-load", () => this.notifyChanged());
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

  liveWindows() {
    for (const [targetId, view] of this.windows) {
      if (view.webContents.isDestroyed()) this.windows.delete(targetId);
    }
    return [...this.windows.entries()];
  }

  windowForTarget(targetId = undefined) {
    const selected = targetId ?? this.activeTargetId;
    const view = selected ? this.windows.get(selected) : null;
    if (!view || view.webContents.isDestroyed()) return null;
    return view;
  }

  layout() {
    const bounds = this.config.browserBounds();
    for (const [, view] of this.liveWindows()) view.setBounds(bounds);
  }

  setVisible(visible, targetId = this.activeTargetId) {
    this.visible = Boolean(visible && !this.paused && !this.stopped);
    if (targetId && this.windows.has(targetId)) this.activeTargetId = targetId;
    for (const [id, view] of this.liveWindows()) {
      view.setVisible(this.visible && id === this.activeTargetId);
    }
    if (this.visible) this.windowForTarget()?.webContents.focus();
  }

  destroyView(targetId) {
    const view = this.windows.get(targetId);
    if (!view) return false;
    this.config.hostWindow.contentView.removeChildView(view);
    this.windows.delete(targetId);
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
    }));
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

  async activate(targetId = undefined) {
    const selectedTarget = targetId ?? await this.ensurePage();
    const window = this.windowForTarget(selectedTarget);
    if (!window) throw new Error(`Unknown Browser Lite target: ${selectedTarget}`);
    this.activeTargetId = selectedTarget;
    this.onActivate?.(this.config.instanceId, selectedTarget);
    window.webContents.focus();
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

  async addCookie(cookie) {
    const params = { name: cookie.name, value: cookie.value, url: await this.currentUrl() };
    for (const key of ["domain", "path", "secure", "httpOnly", "sameSite"]) {
      if (cookie[key] !== undefined) params[key] = cookie[key];
    }
    if (cookie.expiry !== undefined) params.expires = Number(cookie.expiry);
    await this.sendPage("Network.setCookie", params);
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
    this.startPromise = null;
    this.stopped = true;
    this.notifyChanged();
  }

  async remove() {
    await this.stop();
    const partitionSession = session.fromPartition(this.partition);
    await partitionSession.clearCache();
    await partitionSession.clearStorageData();
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
      bundledChromium: true,
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
    this.viewMode = "settings";
    this.activeInstanceId = null;
    this.settingsContentSize = hostWindow.getContentSize();
    hostWindow.on("resize", () => {
      if (this.viewMode === "settings") this.settingsContentSize = hostWindow.getContentSize();
      this.layout();
    });
  }

  browserBounds() {
    const [width, contentHeight] = this.hostWindow.getContentSize();
    return { x: 0, y: APP_BAR_HEIGHT, width, height: Math.max(240, contentHeight - APP_BAR_HEIGHT) };
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

  showSettings() {
    for (const entry of this.instances.values()) entry.state.setVisible(false);
    this.viewMode = "settings";
    this.hostWindow.setTitle(this.installation?.isComplete() ? "Browser Lite — 设置" : "Browser Lite — 安装");
    this.hostWindow.setContentSize(this.settingsContentSize[0], this.settingsContentSize[1]);
    this.hostWindow.show();
    this.hostWindow.focus();
    this.onChanged?.();
  }

  showInstance(instanceId) {
    const id = safeInstanceId(instanceId ?? this.activeInstanceId);
    const entry = this.instances.get(id);
    if (!entry) return false;
    for (const [otherId, other] of this.instances) other.state.setVisible(otherId === id);
    this.activeInstanceId = id;
    this.viewMode = "browser";
    this.hostWindow.setTitle(`Browser Lite — ${id}`);
    this.hostWindow.setContentSize(entry.state.config.width, entry.state.config.height + APP_BAR_HEIGHT);
    entry.state.layout();
    this.hostWindow.show();
    this.hostWindow.focus();
    this.onChanged?.();
    return true;
  }

  async ensure(instanceId, options = {}) {
    const id = safeInstanceId(instanceId);
    let entry = this.instances.get(id);
    if (!entry) {
      await this.installation?.prepareInstance(id);
      const { createBrowserLiteServer } = await runtimeModule();
      const profileDir = join(app.getPath("userData"), "Instances", id);
      const config = {
        host: "127.0.0.1",
        port: Number(options.port ?? 0),
        instanceId: id,
        profileDir,
        dataRoot: app.getPath("userData"),
        hostWindow: this.hostWindow,
        browserBounds: () => this.browserBounds(),
        onActivate: (activeId) => this.showInstance(activeId),
        startUrl: await this.installation?.startUrl(),
        width: Math.max(320, Number(options.width) || 1280),
        height: Math.max(240, Number(options.height) || 800),
      };
      const state = new ElectronBrowserLiteState(config, { onChanged: () => this.onChanged?.() });
      await state.start();
      const server = createBrowserLiteServer(state);
      await new Promise((resolveListen, rejectListen) => {
        server.once("error", rejectListen);
        server.listen(config.port, config.host, resolveListen);
      });
      const address = server.address();
      config.port = typeof address === "object" && address ? address.port : config.port;
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
    if (this.viewMode === "browser" && this.activeInstanceId === id) this.showSettings();
  }

  async stop(instanceId) {
    const id = safeInstanceId(instanceId);
    const entry = this.instances.get(id);
    if (entry) await entry.state.stop();
    if (this.viewMode === "browser" && this.activeInstanceId === id) this.showSettings();
  }

  async remove(instanceId) {
    const id = safeInstanceId(instanceId);
    const entry = this.instances.get(id);
    if (!entry) return;
    await entry.state.remove();
    await new Promise((resolveClose) => entry.server.close(resolveClose));
    this.instances.delete(id);
    if (this.activeInstanceId === id) {
      this.activeInstanceId = null;
      this.showSettings();
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
      result.push({ id, baseUrl: entry.baseUrl, ...(await entry.state.runtimeStatus()) });
    }
    return result;
  }

  async shutdown() {
    for (const entry of this.instances.values()) {
      await entry.state.stop();
      await new Promise((resolveClose) => entry.server.close(resolveClose));
    }
    this.instances.clear();
    this.activeInstanceId = null;
    this.viewMode = "settings";
  }

  async resetForReinstall() {
    await this.shutdown();
    this.onChanged?.();
  }
}
