#!/usr/bin/env node

import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import {
  mkdir,
  readFile,
  rm,
  stat,
} from "node:fs/promises";
import { createServer } from "node:http";
import { homedir, platform } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export const VERSION = "0.1.0";

const DEFAULT_TIMEOUTS = Object.freeze({
  script: 30_000,
  pageLoad: 60_000,
  implicit: 0,
});
const PROFILE_LOCKS = ["SingletonLock", "SingletonSocket", "SingletonCookie"];
const WEBDRIVER_KEYS = Object.freeze({
  "\ue003": { key: "Backspace", code: "Backspace", keyCode: 8 },
  "\ue004": { key: "Tab", code: "Tab", keyCode: 9 },
  "\ue006": { key: "Enter", code: "Enter", keyCode: 13 },
  "\ue007": { key: "Enter", code: "Enter", keyCode: 13 },
  "\ue008": { key: "Shift", code: "ShiftLeft", keyCode: 16 },
  "\ue009": { key: "Control", code: "ControlLeft", keyCode: 17 },
  "\ue00a": { key: "Alt", code: "AltLeft", keyCode: 18 },
  "\ue00c": { key: "Escape", code: "Escape", keyCode: 27 },
  "\ue012": { key: "ArrowLeft", code: "ArrowLeft", keyCode: 37 },
  "\ue013": { key: "ArrowUp", code: "ArrowUp", keyCode: 38 },
  "\ue014": { key: "ArrowRight", code: "ArrowRight", keyCode: 39 },
  "\ue015": { key: "ArrowDown", code: "ArrowDown", keyCode: 40 },
  "\ue017": { key: "ArrowDown", code: "ArrowDown", keyCode: 40 },
  "\ue018": { key: "Insert", code: "Insert", keyCode: 45 },
  "\ue019": { key: "Delete", code: "Delete", keyCode: 46 },
});

class WebDriverError extends Error {
  constructor(errorName, message, status = 500) {
    super(message);
    this.name = "WebDriverError";
    this.errorName = errorName;
    this.status = status;
  }
}

function log(message, details = undefined) {
  const suffix = details === undefined ? "" : ` ${JSON.stringify(details)}`;
  process.stdout.write(`${new Date().toISOString()} ${message}${suffix}\n`);
}

function delay(milliseconds) {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds));
}

function asInteger(value, fallback, name, minimum = 0, maximum = 65_535) {
  if (value === undefined) return fallback;
  const parsed = Number.parseInt(String(value), 10);
  if (!Number.isInteger(parsed) || parsed < minimum || parsed > maximum) {
    throw new Error(`${name} must be an integer between ${minimum} and ${maximum}`);
  }
  return parsed;
}

function defaultDataRoot() {
  if (platform() === "darwin") {
    return join(homedir(), "Library", "Application Support", "Browser Pilot", "Browser Lite");
  }
  return join(homedir(), ".browser-pilot", "browser-lite");
}

function defaultChromeCandidates() {
  if (platform() === "darwin") {
    return [
      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
      "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ];
  }
  return ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"];
}

function resolveExecutable(candidate) {
  if (candidate.includes("/")) return existsSync(candidate) ? resolve(candidate) : null;
  const result = spawnSync("sh", ["-lc", `command -v ${candidate}`], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "ignore"],
  });
  return result.status === 0 ? result.stdout.trim() : null;
}

export function resolveChromeBinary(explicitPath = undefined) {
  const candidates = explicitPath ? [explicitPath] : defaultChromeCandidates();
  for (const candidate of candidates) {
    const executable = resolveExecutable(candidate);
    if (executable) return executable;
  }
  throw new Error(
    explicitPath
      ? `Chrome executable not found: ${explicitPath}`
      : "Google Chrome or Chromium was not found. Install Chrome or pass --chrome-bin.",
  );
}

export function parseArgs(argv) {
  const raw = {};
  for (let index = 0; index < argv.length; index += 1) {
    const item = argv[index];
    if (item === "--help" || item === "-h") raw.help = true;
    else if (item === "--version") raw.version = true;
    else if (item.startsWith("--")) {
      const [key, inlineValue] = item.slice(2).split("=", 2);
      const value = inlineValue ?? argv[++index];
      if (value === undefined || value.startsWith("--")) {
        throw new Error(`Missing value for --${key}`);
      }
      raw[key] = value;
    } else {
      throw new Error(`Unknown argument: ${item}`);
    }
  }

  const instanceId = String(raw["instance-id"] ?? "browser_lite");
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(instanceId)) {
    throw new Error("--instance-id must contain only letters, numbers, dot, underscore, or hyphen");
  }
  const dataRoot = resolve(String(raw["data-root"] ?? defaultDataRoot()));
  const profileDir = resolve(String(raw["profile-dir"] ?? join(dataRoot, "instances", instanceId, "profile")));

  return {
    help: Boolean(raw.help),
    version: Boolean(raw.version),
    host: String(raw.host ?? "127.0.0.1"),
    port: asInteger(raw.port, 4444, "--port", 1),
    instanceId,
    dataRoot,
    profileDir,
    chromeBin: raw["chrome-bin"] ? resolve(String(raw["chrome-bin"])) : undefined,
    width: asInteger(raw.width, 1280, "--width", 320, 16_384),
    height: asInteger(raw.height, 800, "--height", 240, 16_384),
    profileDirectory: String(raw["profile-directory"] ?? "Default"),
    startUrl: String(raw["start-url"] ?? "chrome://newtab/"),
  };
}

export function helpText() {
  return `Browser Lite runtime ${VERSION}

Usage:
  node browser-lite.mjs [options]

Options:
  --host <address>       HTTP bind address (default: 127.0.0.1)
  --port <port>          WebDriver-compatible port (default: 4444)
  --instance-id <id>     Persistent instance name (default: browser_lite)
  --profile-dir <path>   Chrome profile directory
  --profile-directory   Profile directory inside the user data root (default: Default)
  --data-root <path>     Browser Lite data root
  --chrome-bin <path>    Chrome/Chromium executable
  --width <pixels>       Initial window width (default: 1280)
  --height <pixels>      Initial window height (default: 800)
  --version              Print version
  --help                 Print this help
`;
}

class CdpConnection {
  constructor(webSocketUrl, onEvent, onClose) {
    this.webSocketUrl = webSocketUrl;
    this.onEvent = onEvent;
    this.onClose = onClose;
    this.socket = null;
    this.sequence = 0;
    this.pending = new Map();
  }

  async connect() {
    if (typeof WebSocket === "undefined") {
      throw new Error("Browser Lite requires Node.js 22 or newer (global WebSocket is unavailable)");
    }
    const socket = new WebSocket(this.webSocketUrl);
    this.socket = socket;
    await new Promise((resolveOpen, rejectOpen) => {
      const timer = setTimeout(() => rejectOpen(new Error("Timed out connecting to Chrome CDP")), 10_000);
      socket.addEventListener("open", () => {
        clearTimeout(timer);
        resolveOpen();
      }, { once: true });
      socket.addEventListener("error", () => {
        clearTimeout(timer);
        rejectOpen(new Error("Failed to connect to Chrome CDP"));
      }, { once: true });
    });
    socket.addEventListener("message", (event) => this.handleMessage(event.data));
    socket.addEventListener("close", () => this.handleClose());
  }

  handleMessage(data) {
    let message;
    try {
      const text = typeof data === "string" ? data : Buffer.from(data).toString("utf8");
      message = JSON.parse(text);
    } catch (error) {
      log("cdp.invalid_message", { error: String(error) });
      return;
    }
    if (message.id !== undefined) {
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      clearTimeout(pending.timer);
      if (message.error) {
        pending.reject(new Error(`CDP ${pending.method}: ${message.error.message}`));
      } else {
        pending.resolve(message.result ?? {});
      }
      return;
    }
    this.onEvent?.(message);
  }

  handleClose() {
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(new Error("Chrome CDP connection closed"));
    }
    this.pending.clear();
    this.onClose?.();
  }

  async send(method, params = {}, sessionId = undefined, timeoutMs = 30_000) {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      throw new Error("Chrome CDP is not connected");
    }
    const id = ++this.sequence;
    const payload = { id, method, params };
    if (sessionId) payload.sessionId = sessionId;
    const response = new Promise((resolveResponse, rejectResponse) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        rejectResponse(new Error(`CDP ${method} timed out after ${timeoutMs}ms`));
      }, timeoutMs);
      this.pending.set(id, { resolve: resolveResponse, reject: rejectResponse, timer, method });
    });
    this.socket.send(JSON.stringify(payload));
    return response;
  }

  close() {
    this.socket?.close();
    this.socket = null;
  }
}

async function fetchJson(url, timeoutMs = 2_000) {
  const response = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) });
  if (!response.ok) throw new Error(`${url} returned HTTP ${response.status}`);
  return response.json();
}

async function pathExists(path) {
  try {
    await stat(path);
    return true;
  } catch {
    return false;
  }
}

function pageValue(result) {
  const remote = result?.result;
  if (!remote) return null;
  if (remote.subtype === "error") throw new Error(remote.description || "Page evaluation failed");
  if (Object.prototype.hasOwnProperty.call(remote, "value")) return remote.value;
  if (remote.type === "undefined") return null;
  if (remote.unserializableValue === "NaN") return null;
  if (remote.unserializableValue === "Infinity") return null;
  if (remote.unserializableValue === "-Infinity") return null;
  return null;
}

function evaluationError(result) {
  const details = result?.exceptionDetails;
  if (!details) return null;
  return details.exception?.description || details.text || "JavaScript execution failed";
}

export class BrowserLiteState {
  constructor(config) {
    this.config = config;
    this.sessionId = `browser-lite-${config.instanceId}`;
    this.startedAt = null;
    this.chromePid = null;
    this.chromeChild = null;
    this.debugPort = null;
    this.browserVersion = null;
    this.cdp = null;
    this.startPromise = null;
    this.activeTargetId = null;
    this.targetSessions = new Map();
    this.targetOrder = [];
    this.pointer = { x: 0, y: 0 };
    this.timeouts = { ...DEFAULT_TIMEOUTS };
    this.paused = false;
    this.stopped = false;
    this.shutdownRequested = false;
    this.nativeExitNotified = false;
    this.visible = false;
    this.preview = { capturedAt: 0, dataUrl: "" };
  }

  async start() {
    if (this.startPromise) return this.startPromise;
    this.startPromise = this.startInner().catch((error) => {
      this.startPromise = null;
      throw error;
    });
    return this.startPromise;
  }

  async startInner() {
    this.shutdownRequested = false;
    this.nativeExitNotified = false;
    await mkdir(this.config.profileDir, { recursive: true, mode: 0o700 });
    const existing = await this.findExistingChrome();
    const devTools = existing ?? await this.launchChrome();
    this.debugPort = devTools.port;
    this.browserVersion = devTools.version.Browser ?? null;
    this.cdp = new CdpConnection(
      devTools.version.webSocketDebuggerUrl,
      (event) => this.handleCdpEvent(event),
      () => this.handleCdpClose(),
    );
    await this.cdp.connect();
    await this.cdp.send("Target.setDiscoverTargets", { discover: true });
    const targets = await this.cdp.send("Target.getTargets");
    this.refreshTargetOrder(targets.targetInfos ?? []);
    await this.ensurePage();
    this.startedAt = new Date().toISOString();
    this.stopped = false;
    log(existing ? "chrome.reused" : "chrome.launched", {
      instanceId: this.config.instanceId,
      profileDir: this.config.profileDir,
      debugPort: this.debugPort,
      browserVersion: this.browserVersion,
    });
  }

  handleCdpClose() {
    if (this.shutdownRequested || this.stopped) return;
    this.stopped = true;
    this.paused = false;
    this.visible = false;
    this.cdp = null;
    this.startPromise = null;
    this.targetSessions.clear();
    this.config.onChanged?.();
  }

  handleChromeExit(code, signal, pid) {
    if (pid !== this.chromePid || this.nativeExitNotified) return;
    const requested = this.shutdownRequested;
    this.nativeExitNotified = true;
    this.stopped = true;
    this.paused = false;
    this.visible = false;
    this.chromePid = null;
    this.chromeChild = null;
    this.cdp?.close();
    this.cdp = null;
    this.startPromise = null;
    this.activeTargetId = null;
    this.targetSessions.clear();
    this.targetOrder = [];
    if (!requested) {
      this.config.onNativeBrowserExit?.({
        instanceId: this.config.instanceId,
        pid,
        code,
        signal,
      });
    }
    this.config.onChanged?.();
  }

  handleCdpEvent(event) {
    const targetInfo = event.params?.targetInfo;
    if ((event.method === "Target.targetCreated" || event.method === "Target.targetInfoChanged") && targetInfo?.type === "page") {
      if (!this.targetOrder.includes(targetInfo.targetId)) this.targetOrder.push(targetInfo.targetId);
    }
    if (event.method === "Target.targetDestroyed") {
      const targetId = event.params?.targetId;
      this.targetOrder = this.targetOrder.filter((candidate) => candidate !== targetId);
      this.targetSessions.delete(targetId);
      if (this.activeTargetId === targetId) this.activeTargetId = null;
    }
    if (event.method === "Target.detachedFromTarget") {
      const sessionId = event.params?.sessionId;
      for (const [targetId, knownSessionId] of this.targetSessions) {
        if (knownSessionId === sessionId) this.targetSessions.delete(targetId);
      }
    }
    this.config.onChanged?.();
  }

  refreshTargetOrder(targetInfos) {
    const pageIds = targetInfos.filter((target) => target.type === "page").map((target) => target.targetId);
    this.targetOrder = this.targetOrder.filter((targetId) => pageIds.includes(targetId));
    for (const targetId of pageIds) {
      if (!this.targetOrder.includes(targetId)) this.targetOrder.push(targetId);
    }
  }

  async readDevToolsPort() {
    const activePortFile = join(this.config.profileDir, "DevToolsActivePort");
    if (!(await pathExists(activePortFile))) return null;
    const content = await readFile(activePortFile, "utf8");
    const port = Number.parseInt(content.split(/\r?\n/, 1)[0], 10);
    return Number.isInteger(port) && port > 0 ? port : null;
  }

  async findExistingChrome() {
    try {
      const port = await this.readDevToolsPort();
      if (!port) return null;
      const version = await fetchJson(`http://127.0.0.1:${port}/json/version`);
      if (!version.webSocketDebuggerUrl) return null;
      return { port, version };
    } catch {
      return null;
    }
  }

  async removeStaleProfileState() {
    await rm(join(this.config.profileDir, "DevToolsActivePort"), { force: true });
    for (const name of PROFILE_LOCKS) {
      await rm(join(this.config.profileDir, name), { force: true, recursive: true }).catch(() => {});
    }
  }

  async launchChrome() {
    await this.removeStaleProfileState();
    const chromeBin = resolveChromeBinary(this.config.chromeBin);
    const args = [
      `--user-data-dir=${this.config.profileDir}`,
      "--remote-debugging-port=0",
      "--remote-allow-origins=*",
      "--no-first-run",
      "--no-default-browser-check",
      "--disable-background-mode",
      "--disable-component-update",
      "--disable-infobars",
      "--disable-session-crashed-bubble",
      `--profile-directory=${this.config.profileDirectory || "Default"}`,
      ...(this.config.extensionPath ? [`--load-extension=${this.config.extensionPath}`] : []),
      `--window-size=${this.config.width},${this.config.height}`,
      this.config.startUrl || "chrome://newtab/",
    ];
    const child = spawn(chromeBin, args, { detached: true, stdio: "ignore" });
    this.chromeChild = child;
    child.unref();
    this.chromePid = child.pid ?? null;
    const chromePid = this.chromePid;
    child.once("exit", (code, signal) => this.handleChromeExit(code, signal, chromePid));

    let lastError = null;
    for (let attempt = 0; attempt < 150; attempt += 1) {
      await delay(100);
      try {
        const port = await this.readDevToolsPort();
        if (!port) continue;
        const version = await fetchJson(`http://127.0.0.1:${port}/json/version`, 1_000);
        if (version.webSocketDebuggerUrl) return { port, version };
      } catch (error) {
        lastError = error;
      }
    }
    throw new Error(`Chrome did not expose DevToolsActivePort within 15 seconds${lastError ? `: ${lastError.message}` : ""}`);
  }

  async ensureConnected() {
    if (this.stopped) throw new Error("Browser Lite is stopped");
    if (this.cdp?.socket?.readyState === WebSocket.OPEN) return;
    this.handleCdpClose();
    throw new Error("Browser Lite Chromium exited");
  }

  async targets() {
    await this.ensureConnected();
    const result = await this.cdp.send("Target.getTargets");
    const pageTargets = (result.targetInfos ?? []).filter((target) => target.type === "page");
    this.refreshTargetOrder(pageTargets);
    const byId = new Map(pageTargets.map((target) => [target.targetId, target]));
    return this.targetOrder.map((targetId) => byId.get(targetId)).filter(Boolean).map((target) => ({
      ...target,
      active: target.targetId === this.activeTargetId,
    }));
  }

  async resetImportedExtensionStartupTabs(knownOnboardingUrls = []) {
    const startUrl = this.config.startUrl || "chrome://newtab/";
    const urlKey = (value) => {
      try {
        const url = new URL(String(value || ""));
        if (["http:", "https:"].includes(url.protocol) && url.pathname !== "/") {
          url.pathname = url.pathname.replace(/\/+$/, "");
        }
        return url.toString();
      } catch {
        return String(value || "");
      }
    };
    const blankUrls = new Set([startUrl, "chrome://new-tab-page/", "chrome://newtab/"]);
    const knownUrls = new Set(
      (Array.isArray(knownOnboardingUrls) ? knownOnboardingUrls : []).map(urlKey),
    );
    const fullReset = knownUrls.size === 0;
    let keepTargetId = null;
    let closed = 0;
    const closedUrls = new Set();
    let quietChecks = 0;
    for (let attempt = 0; attempt < 20 && quietChecks < 4; attempt += 1) {
      const pages = await this.targets();
      if (fullReset && !pages.some((page) => page.targetId === keepTargetId)) {
        keepTargetId = pages.find((page) => (
          page.url === startUrl
          || page.url === "chrome://new-tab-page/"
          || page.url === "chrome://newtab/"
        ))?.targetId ?? null;
      }
      if (fullReset && !keepTargetId) keepTargetId = (await this.createWindow(startUrl)).targetId;
      const blankPages = pages.filter((page) => blankUrls.has(page.url));
      const keepBlankTargetId = blankPages.find((page) => page.active)?.targetId ?? blankPages[0]?.targetId;
      const extras = fullReset
        ? pages.filter((page) => page.targetId !== keepTargetId)
        : pages.filter((page) => (
          knownUrls.has(urlKey(page.url))
          || (blankUrls.has(page.url) && page.targetId !== keepBlankTargetId)
        ));
      for (const page of extras) {
        await this.cdp.send("Target.closeTarget", { targetId: page.targetId }).catch(() => {});
        this.targetSessions.delete(page.targetId);
        this.targetOrder = this.targetOrder.filter((targetId) => targetId !== page.targetId);
        if (page.url && !blankUrls.has(page.url)) {
          closedUrls.add(urlKey(page.url));
        }
        closed += 1;
      }
      quietChecks = extras.length === 0 ? quietChecks + 1 : 0;
      if (quietChecks < 4) await delay(250);
    }
    const remaining = await this.targets();
    const selected = remaining.find((page) => page.targetId === this.activeTargetId)?.targetId
      ?? remaining.find((page) => page.targetId === keepTargetId)?.targetId
      ?? remaining.find((page) => blankUrls.has(page.url))?.targetId
      ?? remaining.at(-1)?.targetId
      ?? (await this.createWindow(startUrl)).targetId;
    await this.activate(selected);
    return {
      closedTabs: closed,
      onboardingUrls: [...new Set([...knownUrls, ...closedUrls])],
    };
  }

  async ensurePage() {
    const targets = await this.targets();
    if (this.activeTargetId && targets.some((target) => target.targetId === this.activeTargetId)) {
      return this.activeTargetId;
    }
    let targetId = targets.at(-1)?.targetId;
    if (!targetId) {
      const created = await this.cdp.send("Target.createTarget", { url: "about:blank" });
      targetId = created.targetId;
      if (!this.targetOrder.includes(targetId)) this.targetOrder.push(targetId);
    }
    this.activeTargetId = targetId;
    await this.attach(targetId);
    return targetId;
  }

  async attach(targetId) {
    const cached = this.targetSessions.get(targetId);
    if (cached) return cached;
    const result = await this.cdp.send("Target.attachToTarget", { targetId, flatten: true });
    this.targetSessions.set(targetId, result.sessionId);
    return result.sessionId;
  }

  async sendPage(method, params = {}, targetId = undefined, timeoutMs = 30_000) {
    await this.ensureConnected();
    const selectedTarget = targetId ?? await this.ensurePage();
    const sessionId = await this.attach(selectedTarget);
    return this.cdp.send(method, params, sessionId, timeoutMs);
  }

  async activate(targetId = undefined) {
    const selectedTarget = targetId ?? await this.ensurePage();
    await this.cdp.send("Target.activateTarget", { targetId: selectedTarget });
    this.activeTargetId = selectedTarget;
    await this.attach(selectedTarget);
    this.config.onChanged?.();
    return selectedTarget;
  }

  async sendRawCDP(method, params = {}, sessionId = undefined) {
    await this.ensureConnected();
    return this.cdp.send(method, params, sessionId);
  }

  async createWindow(url = this.config.startUrl || "chrome://newtab/") {
    await this.ensureConnected();
    const created = await this.cdp.send("Target.createTarget", { url });
    if (!this.targetOrder.includes(created.targetId)) this.targetOrder.push(created.targetId);
    await this.activate(created.targetId);
    return { targetId: created.targetId };
  }

  async destroyView(targetId) {
    await this.ensureConnected();
    const result = await this.cdp.send("Target.closeTarget", { targetId });
    this.targetSessions.delete(targetId);
    this.targetOrder = this.targetOrder.filter((candidate) => candidate !== targetId);
    if (this.activeTargetId === targetId) this.activeTargetId = null;
    await delay(100);
    if ((await this.targets()).length === 0) await this.createWindow();
    else await this.ensurePage();
    this.config.onChanged?.();
    return result.success !== false;
  }

  async navigationState() {
    await this.ensureConnected();
    const targetId = await this.ensurePage();
    const targets = await this.targets();
    const target = targets.find((candidate) => candidate.targetId === targetId);
    let history = { currentIndex: 0, entries: [] };
    let loading = false;
    try {
      history = await this.sendPage("Page.getNavigationHistory", {}, targetId);
      loading = !["interactive", "complete"].includes(
        await this.evaluateExpression("document.readyState", { timeoutMs: 1_000 }),
      );
    } catch {}
    const url = target?.url === "about:blank" ? "" : target?.url || "";
    return {
      url,
      title: target?.title || "",
      loading,
      canGoBack: history.currentIndex > 0,
      canGoForward: history.currentIndex >= 0 && history.currentIndex < history.entries.length - 1,
    };
  }

  async goBack() {
    const targetId = await this.ensurePage();
    const history = await this.sendPage("Page.getNavigationHistory", {}, targetId);
    const entry = history.entries?.[history.currentIndex - 1];
    if (entry) await this.sendPage("Page.navigateToHistoryEntry", { entryId: entry.id }, targetId);
  }

  async goForward() {
    const targetId = await this.ensurePage();
    const history = await this.sendPage("Page.getNavigationHistory", {}, targetId);
    const entry = history.entries?.[history.currentIndex + 1];
    if (entry) await this.sendPage("Page.navigateToHistoryEntry", { entryId: entry.id }, targetId);
  }

  async reload() {
    await this.sendPage("Page.reload", { ignoreCache: false });
  }

  async stopLoading() {
    await this.sendPage("Page.stopLoading");
  }

  async browserWindow(targetId = undefined) {
    await this.ensureConnected();
    const selectedTarget = targetId ?? await this.ensurePage();
    return this.cdp.send("Browser.getWindowForTarget", { targetId: selectedTarget });
  }

  async setVisible(visible, targetId = this.activeTargetId) {
    if (this.stopped && !visible) return;
    if (visible) await this.resume();
    else await this.ensureConnected();
    if (targetId) this.activeTargetId = targetId;
    const selectedTarget = await this.ensurePage();
    const current = await this.browserWindow(selectedTarget);
    const windowState = visible && !this.paused ? "normal" : "minimized";
    await this.cdp.send("Browser.setWindowBounds", { windowId: current.windowId, bounds: { windowState } });
    this.visible = Boolean(visible && !this.paused);
    if (this.visible) await this.activate(selectedTarget);
    this.config.onChanged?.();
  }

  layout() {}

  async capturePreview() {
    if (this.stopped) return this.preview.dataUrl;
    try {
      const result = await this.sendPage("Page.captureScreenshot", { format: "png", fromSurface: true });
      const data = result.data;
      if (data) this.preview = { capturedAt: Date.now(), dataUrl: `data:image/png;base64,${data}` };
    } catch {}
    return this.preview.dataUrl;
  }

  async previewDataUrl() {
    return this.preview.dataUrl || this.capturePreview();
  }

  async pause() {
    if (this.stopped) return;
    this.paused = true;
    await this.setVisible(false);
  }

  async resume() {
    this.paused = false;
    this.stopped = false;
    this.shutdownRequested = false;
    await this.start();
  }

  async stop() {
    if (this.stopped && !this.chromePid) return;
    this.shutdownRequested = true;
    this.stopped = true;
    this.visible = false;
    this.paused = false;
    const chromePid = this.chromePid;
    try { await this.cdp?.send("Browser.close"); } catch {}
    if (chromePid) {
      const chromeRunning = () => {
        try {
          process.kill(chromePid, 0);
          return true;
        } catch {
          return false;
        }
      };
      for (let attempt = 0; attempt < 20; attempt += 1) {
        if (!chromeRunning()) break;
        await delay(100);
      }
      if (chromeRunning()) {
        try { process.kill(chromePid, "SIGTERM"); } catch {}
        for (let attempt = 0; attempt < 10; attempt += 1) {
          if (!chromeRunning()) break;
          await delay(100);
        }
      }
      if (chromeRunning()) {
        try { process.kill(chromePid, "SIGKILL"); } catch {}
      }
    }
    this.cdp?.close();
    this.cdp = null;
    this.chromeChild = null;
    this.chromePid = null;
    this.startPromise = null;
    this.activeTargetId = null;
    this.targetSessions.clear();
    this.targetOrder = [];
    await delay(150);
    this.config.onChanged?.();
  }

  async remove() {
    await this.stop();
    await rm(this.config.profileDir, { recursive: true, force: true });
  }

  async evaluateExpression(expression, { awaitPromise = true, timeoutMs = undefined } = {}) {
    const result = await this.sendPage("Runtime.evaluate", {
      expression,
      awaitPromise,
      returnByValue: true,
      userGesture: true,
    }, undefined, timeoutMs ?? this.timeouts.script);
    const error = evaluationError(result);
    if (error) throw new WebDriverError("javascript error", error, 500);
    return pageValue(result);
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
        const readyState = await this.evaluateExpression("document.readyState", { timeoutMs: 2_000 });
        if (readyState === "interactive" || readyState === "complete") return;
      } catch {
        // A navigation may briefly replace the execution context.
      }
      await delay(100);
    }
    throw new WebDriverError("timeout", `Page did not become ready within ${this.timeouts.pageLoad}ms`, 500);
  }

  async navigate(url) {
    if (typeof url !== "string" || !url.trim()) {
      throw new WebDriverError("invalid argument", "url must be a non-empty string", 400);
    }
    await this.activate();
    await this.sendPage("Page.enable");
    const result = await this.sendPage("Page.navigate", { url: url.trim() }, undefined, this.timeouts.pageLoad);
    if (result.errorText) throw new WebDriverError("unknown error", result.errorText, 500);
    await this.waitForDocumentReady();
  }

  async currentUrl() {
    return this.evaluateExpression("location.href", { timeoutMs: 5_000 });
  }

  async title() {
    return this.evaluateExpression("document.title", { timeoutMs: 5_000 });
  }

  async source() {
    return this.evaluateExpression("document.documentElement ? document.documentElement.outerHTML : ''");
  }

  async screenshot() {
    await this.activate();
    const result = await this.sendPage("Page.captureScreenshot", { format: "png", fromSurface: true });
    return result.data;
  }

  async setActiveTarget(targetId) {
    const targets = await this.targets();
    if (!targets.some((target) => target.targetId === targetId)) {
      throw new WebDriverError("no such window", `Unknown window handle: ${targetId}`, 400);
    }
    await this.activate(targetId);
  }

  async closeActiveTarget() {
    const targetId = await this.ensurePage();
    await this.cdp.send("Target.closeTarget", { targetId });
    this.activeTargetId = null;
    this.targetSessions.delete(targetId);
    this.targetOrder = this.targetOrder.filter((candidate) => candidate !== targetId);
    await delay(100);
    await this.ensurePage();
    return (await this.targets()).map((target) => target.targetId);
  }

  async windowRect() {
    const targetId = await this.ensurePage();
    const result = await this.browserWindow(targetId);
    return {
      x: Number(result.bounds?.left ?? 0),
      y: Number(result.bounds?.top ?? 0),
      width: Number(result.bounds?.width ?? this.config.width),
      height: Number(result.bounds?.height ?? this.config.height),
    };
  }

  async setWindowRect(body) {
    const targetId = await this.ensurePage();
    const current = await this.browserWindow(targetId);
    const bounds = {
      left: asInteger(body.x, Number(current.bounds?.left ?? 0), "x", -32_768, 32_768),
      top: asInteger(body.y, Number(current.bounds?.top ?? 0), "y", -32_768, 32_768),
      width: asInteger(body.width, Number(current.bounds?.width ?? this.config.width), "width", 320, 16_384),
      height: asInteger(body.height, Number(current.bounds?.height ?? this.config.height), "height", 240, 16_384),
      windowState: "normal",
    };
    await this.cdp.send("Browser.setWindowBounds", { windowId: current.windowId, bounds });
    return this.windowRect();
  }

  async performActions(sources) {
    for (const source of sources ?? []) {
      for (const action of source.actions ?? []) {
        if (action.type === "pause") {
          await delay(Number(action.duration ?? 0));
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
    const value = String(action.value ?? "");
    const special = WEBDRIVER_KEYS[value] ?? (value.length > 1
      ? Object.values(WEBDRIVER_KEYS).find((candidate) => candidate.key === value)
      : null);
    if (action.type === "keyDown" && !special) {
      if (value) await this.sendPage("Input.insertText", { text: value });
      return;
    }
    if (!special || !["keyDown", "keyUp"].includes(action.type)) return;
    await this.sendPage("Input.dispatchKeyEvent", {
      type: action.type === "keyDown" ? "rawKeyDown" : "keyUp",
      key: special.key,
      code: special.code,
      windowsVirtualKeyCode: special.keyCode,
      nativeVirtualKeyCode: special.keyCode,
    });
  }

  async performPointerAction(action) {
    if (action.type === "pointerMove") {
      this.pointer = { x: Number(action.x ?? 0), y: Number(action.y ?? 0) };
      await this.sendPage("Input.dispatchMouseEvent", {
        type: "mouseMoved",
        x: this.pointer.x,
        y: this.pointer.y,
        button: "none",
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
      throw new WebDriverError("invalid argument", "cookie must include string name and value", 400);
    }
    const params = {
      name: cookie.name,
      value: cookie.value,
      url: cookie.url || fallbackUrl,
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
    if (result.success === false) throw new WebDriverError("unable to set cookie", "Chrome rejected cookie", 500);
  }

  async addCookies(cookies) {
    if (!Array.isArray(cookies)) {
      throw new WebDriverError("invalid argument", "cookies must be an array", 400);
    }
    const needsFallbackUrl = cookies.some((cookie) => !cookie?.url);
    const fallbackUrl = needsFallbackUrl ? await this.currentUrl() : "";
    const normalized = [];
    for (const cookie of cookies) {
      try {
        normalized.push(this.cookieParams(cookie, fallbackUrl));
      } catch {}
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
        name: cookie.name,
        url: currentUrl,
        domain: cookie.domain,
        path: cookie.path,
      });
    }
  }

  async runtimeStatus() {
    await this.ensureConnected();
    const targets = await this.targets();
    return {
      ready: true,
      runtime: "browser_lite",
      version: VERSION,
      instanceId: this.config.instanceId,
      sessionId: this.sessionId,
      host: this.config.host,
      port: this.config.port,
      profileDir: this.config.profileDir,
      chromePid: this.chromePid,
      debugPort: this.debugPort,
      browserVersion: this.browserVersion,
      startedAt: this.startedAt,
      activeTargetId: this.activeTargetId,
      tabCount: targets.length,
      chromiumVersion: this.browserVersion,
      electronVersion: null,
      bundledChromium: Boolean(this.config.bundledChromium),
      paused: this.paused,
      stopped: this.stopped,
    };
  }

  closeControlConnection() {
    this.cdp?.close();
  }
}

async function readJsonBody(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > 5 * 1024 * 1024) {
      throw new WebDriverError("invalid argument", "Request body exceeds 5 MiB", 413);
    }
    chunks.push(chunk);
  }
  if (chunks.length === 0) return {};
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw new WebDriverError("invalid argument", "Request body must be valid JSON", 400);
  }
}

function sendJson(response, status, value) {
  const body = JSON.stringify({ value });
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": Buffer.byteLength(body),
    "cache-control": "no-store",
  });
  response.end(body);
}

function sendError(response, error) {
  const status = error instanceof WebDriverError ? error.status : 500;
  const errorName = error instanceof WebDriverError ? error.errorName : "unknown error";
  sendJson(response, status, {
    error: errorName,
    message: error?.message ?? String(error),
    stacktrace: "",
  });
}

export function createBrowserLiteServer(state) {
  return createServer(async (request, response) => {
    try {
      const method = request.method ?? "GET";
      const url = new URL(request.url ?? "/", "http://browser-lite.local");
      const path = decodeURIComponent(url.pathname).replace(/\/$/, "") || "/";

      if (path === "/browser-lite/space-count" || path === "/browser-lite/show-spaces") {
        if (!state.config.controlToken || url.searchParams.get("token") !== state.config.controlToken) {
          throw new WebDriverError("invalid argument", "Invalid Browser Lite control token", 403);
        }
        if (method === "GET" && path === "/browser-lite/space-count") {
          sendJson(response, 200, { count: Number(state.config.getSpaceCount?.() || 0) });
          return;
        }
        if (method === "POST" && path === "/browser-lite/show-spaces") {
          await state.config.onShowSpaces?.();
          sendJson(response, 200, { shown: true });
          return;
        }
        throw new WebDriverError("unknown command", `${method} ${path} is not implemented`, 404);
      }

      if (method === "GET" && (path === "/healthz" || path === "/browser-lite/status")) {
        sendJson(response, 200, await state.runtimeStatus());
        return;
      }
      if (method === "GET" && path === "/status") {
        await state.ensureConnected();
        sendJson(response, 200, {
          ready: true,
          message: "Browser Lite runtime ready",
          nodes: [{ slots: [{ session: { sessionId: state.sessionId } }] }],
        });
        return;
      }
      if (method === "POST" && path === "/session") {
        await readJsonBody(request);
        await state.ensureConnected();
        sendJson(response, 200, {
          sessionId: state.sessionId,
          capabilities: {
            browserName: "chrome",
            browserVersion: state.browserVersion ?? "browser_lite",
            platformName: platform(),
            acceptInsecureCerts: false,
            setWindowRect: true,
            "browser-lite:instanceId": state.config.instanceId,
          },
        });
        return;
      }

      const match = path.match(/^\/session\/([^/]+)(\/.*)?$/);
      if (!match) throw new WebDriverError("unknown command", `${method} ${path} is not implemented`, 404);
      const command = match[2] || "";
      if (match[1] !== state.sessionId) {
        throw new WebDriverError("invalid session id", `Unknown session: ${match[1]}`, 404);
      }
      await state.ensureConnected();

      if (method === "DELETE" && command === "") sendJson(response, 200, null);
      else if (method === "GET" && command === "/url") sendJson(response, 200, await state.currentUrl());
      else if (method === "POST" && command === "/url") {
        const body = await readJsonBody(request);
        await state.navigate(body.url);
        sendJson(response, 200, null);
      } else if (method === "POST" && command === "/refresh") {
        await state.sendPage("Page.reload", {});
        await state.waitForDocumentReady();
        sendJson(response, 200, null);
      } else if (method === "POST" && (command === "/back" || command === "/forward")) {
        const history = await state.sendPage("Page.getNavigationHistory");
        const offset = command === "/back" ? -1 : 1;
        const entry = history.entries?.[history.currentIndex + offset];
        if (entry) {
          await state.sendPage("Page.navigateToHistoryEntry", { entryId: entry.id });
          await state.waitForDocumentReady();
        }
        sendJson(response, 200, null);
      } else if (method === "GET" && command === "/title") sendJson(response, 200, await state.title());
      else if (method === "GET" && command === "/source") sendJson(response, 200, await state.source());
      else if (method === "GET" && command === "/timeouts") sendJson(response, 200, { ...state.timeouts });
      else if (method === "POST" && command === "/timeouts") {
        const body = await readJsonBody(request);
        for (const key of Object.keys(DEFAULT_TIMEOUTS)) {
          if (body[key] !== undefined && body[key] !== null) {
            state.timeouts[key] = asInteger(body[key], state.timeouts[key], key, 0, 86_400_000);
          }
        }
        sendJson(response, 200, null);
      } else if (method === "GET" && command === "/cookie") sendJson(response, 200, await state.cookies());
      else if (method === "POST" && command === "/cookie") {
        const body = await readJsonBody(request);
        await state.addCookie(body.cookie);
        sendJson(response, 200, null);
      } else if (method === "DELETE" && command === "/cookie") {
        await state.deleteCookie();
        sendJson(response, 200, null);
      } else if (method === "DELETE" && command.startsWith("/cookie/")) {
        await state.deleteCookie(command.slice("/cookie/".length));
        sendJson(response, 200, null);
      } else if (method === "POST" && command === "/execute/sync") {
        const body = await readJsonBody(request);
        sendJson(response, 200, await state.execute(body.script, body.args));
      } else if (method === "GET" && command === "/screenshot") sendJson(response, 200, await state.screenshot());
      else if (method === "POST" && command === "/actions") {
        const body = await readJsonBody(request);
        await state.performActions(body.actions);
        sendJson(response, 200, null);
      } else if (method === "DELETE" && command === "/actions") sendJson(response, 200, null);
      else if (method === "POST" && command === "/goog/cdp/execute") {
        const body = await readJsonBody(request);
        if (!body.cmd) throw new WebDriverError("invalid argument", "Missing CDP cmd", 400);
        sendJson(response, 200, await state.sendPage(body.cmd, body.params ?? {}));
      } else if (method === "GET" && command === "/window/handles") {
        sendJson(response, 200, (await state.targets()).map((target) => target.targetId));
      } else if (method === "GET" && command === "/window") sendJson(response, 200, await state.ensurePage());
      else if (method === "POST" && command === "/window") {
        const body = await readJsonBody(request);
        await state.setActiveTarget(body.handle);
        sendJson(response, 200, null);
      } else if (method === "DELETE" && command === "/window") sendJson(response, 200, await state.closeActiveTarget());
      else if (method === "GET" && command === "/window/rect") sendJson(response, 200, await state.windowRect());
      else if (method === "POST" && command === "/window/rect") {
        sendJson(response, 200, await state.setWindowRect(await readJsonBody(request)));
      } else {
        throw new WebDriverError("unknown command", `${method} ${path} is not implemented`, 404);
      }
    } catch (error) {
      log("request.failed", { method: request.method, url: request.url, error: error?.message ?? String(error) });
      sendError(response, error);
    }
  });
}

export async function run(config) {
  const state = new BrowserLiteState(config);
  await state.start();
  const server = createBrowserLiteServer(state);
  await new Promise((resolveListen, rejectListen) => {
    server.once("error", rejectListen);
    server.listen(config.port, config.host, resolveListen);
  });
  log("runtime.listening", {
    url: `http://${config.host}:${config.port}`,
    instanceId: config.instanceId,
    profileDir: config.profileDir,
  });

  const shutdown = (signal) => {
    log("runtime.stopping", { signal, browserKeptAlive: true });
    server.close(() => process.exit(0));
    state.closeControlConnection();
    setTimeout(() => process.exit(0), 2_000).unref();
  };
  process.once("SIGINT", () => shutdown("SIGINT"));
  process.once("SIGTERM", () => shutdown("SIGTERM"));
  return { state, server };
}

const isMain = process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url;
if (isMain) {
  try {
    const config = parseArgs(process.argv.slice(2));
    if (config.help) process.stdout.write(helpText());
    else if (config.version) process.stdout.write(`${VERSION}\n`);
    else await run(config);
  } catch (error) {
    process.stderr.write(`browser-lite: ${error.message}\n`);
    process.exitCode = 1;
  }
}
