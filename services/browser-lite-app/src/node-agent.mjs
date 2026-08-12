import { app, safeStorage } from "electron";
import { randomUUID } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { hostname, platform, arch } from "node:os";

function delay(milliseconds) {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds));
}

function normalizeServerUrl(value) {
  const url = new URL(String(value || ""));
  if (!["https:", "http:"].includes(url.protocol)) throw new Error("Browser Pilot URL must use HTTPS or HTTP");
  if (url.protocol === "http:" && !["localhost", "127.0.0.1", "::1"].includes(url.hostname)) {
    throw new Error("Remote Browser Pilot nodes require HTTPS");
  }
  return url.origin;
}

function websocketUrl(serverUrl, nodeId) {
  const url = new URL("/api/browser-lite/nodes/connect", serverUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("nodeId", nodeId);
  return url.toString();
}

export class BrowserLiteNodeAgent {
  constructor(manager, { onChanged } = {}) {
    this.manager = manager;
    this.onChanged = onChanged;
    this.configPath = join(app.getPath("userData"), "node-config.json");
    this.config = null;
    this.socket = null;
    this.connected = false;
    this.connecting = false;
    this.stopped = false;
    this.lastError = "";
    this.reconnectAttempt = 0;
  }

  async load() {
    try {
      const parsed = JSON.parse(await readFile(this.configPath, "utf8"));
      let token = "";
      if (parsed.tokenEncrypted) {
        if (!safeStorage.isEncryptionAvailable()) throw new Error("macOS Keychain encryption is unavailable");
        token = safeStorage.decryptString(Buffer.from(parsed.tokenEncrypted, "base64"));
      } else if (parsed.token && safeStorage.isEncryptionAvailable()) {
        token = parsed.token;
        await this.saveConfig({ ...parsed, token });
      }
      if (parsed.serverUrl && parsed.nodeId && token) this.config = { ...parsed, token };
    } catch {
      this.config = null;
    }
    if (this.config) void this.connectLoop();
    this.notifyChanged();
  }

  notifyChanged() {
    this.onChanged?.();
  }

  async saveConfig(config) {
    const serialized = { ...config };
    if (!safeStorage.isEncryptionAvailable()) throw new Error("macOS Keychain encryption is unavailable");
    serialized.tokenEncrypted = safeStorage.encryptString(config.token).toString("base64");
    delete serialized.token;
    await writeFile(this.configPath, `${JSON.stringify(serialized, null, 2)}\n`, { mode: 0o600 });
  }

  async pair(serverUrl, pairingCode, displayName = undefined) {
    const origin = normalizeServerUrl(serverUrl);
    const response = await fetch(`${origin}/api/browser-lite/pair`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        pairingCode: String(pairingCode || "").trim(),
        displayName: String(displayName || hostname()),
        platform: platform(),
        architecture: arch(),
        appVersion: app.getVersion(),
        chromiumVersion: process.versions.chrome,
        capabilities: ["webdriver", "cdp", "screenshot", "persistent_profile", "multi_instance"],
      }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || body.error || `Pairing failed: HTTP ${response.status}`);
    this.disconnect();
    this.config = {
      serverUrl: origin,
      nodeId: body.nodeId,
      token: body.token,
      displayName: body.displayName || displayName || hostname(),
    };
    await this.saveConfig(this.config);
    this.stopped = false;
    void this.connectLoop();
    this.notifyChanged();
    return this.publicState();
  }

  async unpair() {
    if (this.config) {
      await fetch(`${this.config.serverUrl}/api/browser-lite/nodes/${encodeURIComponent(this.config.nodeId)}/disconnect`, {
        method: "POST",
        headers: { authorization: `Bearer ${this.config.token}` },
      }).catch(() => {});
    }
    this.disconnect();
    this.config = null;
    await writeFile(this.configPath, "{}\n", { mode: 0o600 });
    this.notifyChanged();
  }

  disconnect() {
    this.stopped = true;
    this.socket?.close();
    this.socket = null;
    this.connected = false;
    this.connecting = false;
  }

  async connectLoop() {
    if (this.connecting || !this.config) return;
    this.connecting = true;
    this.stopped = false;
    while (!this.stopped && this.config) {
      try {
        await this.connectOnce();
      } catch (error) {
        this.lastError = error.message || String(error);
      }
      this.connected = false;
      this.notifyChanged();
      if (this.stopped) break;
      this.reconnectAttempt += 1;
      await delay(Math.min(30_000, 1_000 * 2 ** Math.min(5, this.reconnectAttempt)));
    }
    this.connecting = false;
  }

  async connectOnce() {
    const config = this.config;
    const socket = new WebSocket(websocketUrl(config.serverUrl, config.nodeId));
    this.socket = socket;
    await new Promise((resolveOpen, rejectOpen) => {
      const timer = setTimeout(() => rejectOpen(new Error("Browser Pilot node connection timed out")), 15_000);
      socket.addEventListener("open", () => {
        clearTimeout(timer);
        resolveOpen();
      }, { once: true });
      socket.addEventListener("error", () => {
        clearTimeout(timer);
        rejectOpen(new Error("Browser Pilot node connection failed"));
      }, { once: true });
    });
    socket.send(JSON.stringify({ type: "auth", token: config.token }));
    await new Promise((resolveAuth, rejectAuth) => {
      const timer = setTimeout(() => rejectAuth(new Error("Browser Pilot node authentication timed out")), 10_000);
      const listener = (event) => {
        let message;
        try {
          message = JSON.parse(typeof event.data === "string" ? event.data : Buffer.from(event.data).toString("utf8"));
        } catch {
          return;
        }
        if (message.type !== "auth_ok") return;
        clearTimeout(timer);
        socket.removeEventListener("message", listener);
        resolveAuth();
      };
      socket.addEventListener("message", listener);
      socket.addEventListener("close", () => {
        clearTimeout(timer);
        rejectAuth(new Error("Browser Pilot rejected node authentication"));
      }, { once: true });
    });
    this.connected = true;
    this.reconnectAttempt = 0;
    this.lastError = "";
    this.notifyChanged();
    socket.send(JSON.stringify({
      type: "hello",
      requestId: randomUUID(),
      nodeId: config.nodeId,
      appVersion: app.getVersion(),
      chromiumVersion: process.versions.chrome,
      instances: await this.manager.list(),
    }));
    const heartbeat = setInterval(() => {
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "heartbeat", at: new Date().toISOString() }));
    }, 15_000);
    await new Promise((resolveClose) => {
      socket.addEventListener("message", (event) => void this.handleMessage(socket, event.data));
      socket.addEventListener("close", resolveClose, { once: true });
    });
    clearInterval(heartbeat);
  }

  async handleMessage(socket, raw) {
    let message;
    try {
      message = JSON.parse(typeof raw === "string" ? raw : Buffer.from(raw).toString("utf8"));
    } catch {
      return;
    }
    if (message.type !== "request" || !message.requestId) return;
    try {
      const result = await this.handleRequest(message);
      socket.send(JSON.stringify({ type: "response", requestId: message.requestId, ok: true, result }));
    } catch (error) {
      socket.send(JSON.stringify({
        type: "response",
        requestId: message.requestId,
        ok: false,
        error: error.message || String(error),
      }));
    }
  }

  async handleRequest(message) {
    const instanceId = message.instanceId;
    if (!instanceId) throw new Error("Browser Lite request is missing instanceId");
    switch (message.action) {
      case "ensure": {
        const entry = await this.manager.ensure(instanceId, message.options || {});
        return { status: "running", runtime: await entry.state.runtimeStatus() };
      }
      case "status":
        return this.manager.status(instanceId);
      case "pause":
        await this.manager.pause(instanceId);
        return this.manager.status(instanceId);
      case "stop":
        await this.manager.stop(instanceId);
        return { status: "exited" };
      case "remove":
        await this.manager.remove(instanceId);
        return { status: "not_found" };
      case "webdriver":
        return this.manager.request(instanceId, message.request || {});
      default:
        throw new Error(`Unknown Browser Lite node action: ${message.action}`);
    }
  }

  publicState() {
    return {
      paired: Boolean(this.config),
      connected: this.connected,
      connecting: this.connecting,
      serverUrl: this.config?.serverUrl || "",
      nodeId: this.config?.nodeId || "",
      displayName: this.config?.displayName || hostname(),
      lastError: this.lastError,
    };
  }
}
