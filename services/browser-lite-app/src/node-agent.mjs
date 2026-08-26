import { app } from "electron";
import { createCipheriv, createDecipheriv, randomBytes, randomUUID } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { hostname, platform, arch } from "node:os";

function delay(milliseconds) {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds));
}

const TOKEN_ENCRYPTION = "local-aes-256-gcm-v1";
const NODE_CAPABILITIES = Object.freeze([
  "webdriver", "cdp", "screenshot", "persistent_profile", "multi_instance", "task_spaces",
]);

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
  constructor(manager, { onChanged, taskSpaces } = {}) {
    this.manager = manager;
    this.taskSpaces = taskSpaces;
    this.onChanged = onChanged;
    this.configPath = join(app.getPath("userData"), "node-config.json");
    this.keyPath = join(app.getPath("userData"), "node-key.bin");
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
      const token = await this.decryptToken(parsed);
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
    const key = await this.installationKey();
    const iv = randomBytes(12);
    const cipher = createCipheriv("aes-256-gcm", key, iv);
    const ciphertext = Buffer.concat([cipher.update(config.token, "utf8"), cipher.final()]);
    serialized.tokenEncryption = TOKEN_ENCRYPTION;
    serialized.tokenCiphertext = ciphertext.toString("base64");
    serialized.tokenIv = iv.toString("base64");
    serialized.tokenAuthTag = cipher.getAuthTag().toString("base64");
    delete serialized.token;
    delete serialized.tokenEncrypted;
    await writeFile(this.configPath, `${JSON.stringify(serialized, null, 2)}\n`, { mode: 0o600 });
  }

  async installationKey() {
    try {
      const key = await readFile(this.keyPath);
      if (key.length !== 32) throw new Error("Browser Lite installation key is invalid");
      return key;
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
      const key = randomBytes(32);
      try {
        await writeFile(this.keyPath, key, { mode: 0o600, flag: "wx" });
        return key;
      } catch (writeError) {
        if (writeError.code !== "EEXIST") throw writeError;
        const existing = await readFile(this.keyPath);
        if (existing.length !== 32) throw new Error("Browser Lite installation key is invalid");
        return existing;
      }
    }
  }

  async decryptToken(config) {
    if (config.tokenEncryption !== TOKEN_ENCRYPTION) return "";
    const key = await this.installationKey();
    const decipher = createDecipheriv("aes-256-gcm", key, Buffer.from(config.tokenIv, "base64"));
    decipher.setAuthTag(Buffer.from(config.tokenAuthTag, "base64"));
    return Buffer.concat([
      decipher.update(Buffer.from(config.tokenCiphertext, "base64")),
      decipher.final(),
    ]).toString("utf8");
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
        capabilities: NODE_CAPABILITIES,
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
      capabilities: NODE_CAPABILITIES,
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
        errorCode: error.code || error.error_code || undefined,
        details: error.details,
      }));
    }
  }

  async handleRequest(message) {
    const instanceId = message.instanceId;
    if (!instanceId) throw new Error("Browser Lite request is missing instanceId");
    const mappedInstanceId = this.taskSpaces?.instanceIdForContext(instanceId) || instanceId;
    switch (message.action) {
      case "ensure": {
        const space = this.taskSpaces
          ? await this.taskSpaces.ensureContextTaskSpace(instanceId, message.options?.taskName)
          : null;
        const entry = await this.manager.ensure(space?.instanceId || mappedInstanceId, message.options || {});
        return { status: "running", runtime: await entry.state.runtimeStatus() };
      }
      case "status":
        return this.manager.status(mappedInstanceId);
      case "pause":
        await this.manager.pause(mappedInstanceId);
        return this.manager.status(mappedInstanceId);
      case "stop":
        await this.manager.stop(mappedInstanceId);
        return { status: "exited" };
      case "remove":
        if (this.taskSpaces) await this.taskSpaces.removeContextTaskSpace(instanceId);
        else await this.manager.remove(mappedInstanceId);
        return { status: "not_found" };
      case "webdriver":
        if (this.taskSpaces && !this.taskSpaces.instanceIdForContext(instanceId)) {
          await this.taskSpaces.ensureContextTaskSpace(instanceId);
        }
        if (this.taskSpaces) return this.taskSpaces.agentWebDriverRequest(instanceId, message.request || {});
        return this.manager.request(mappedInstanceId, message.request || {});
      case "task_space":
        if (!this.taskSpaces) throw new Error("Browser Lite task-space controller is unavailable");
        return this.taskSpaces.dispatch(message.method, message.args || [], instanceId);
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
