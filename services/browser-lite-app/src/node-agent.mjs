import { app, safeStorage } from "electron";
import { createDecipheriv, createHash, randomBytes, randomUUID } from "node:crypto";
import { readFile, unlink, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { hostname, platform, arch } from "node:os";

function delay(milliseconds) {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds));
}

const LEGACY_TOKEN_ENCRYPTION = "local-aes-256-gcm-v1";
const TOKEN_ENCRYPTION = "electron-safe-storage-v1";
const DEFAULT_SERVER_URL = "https://bpilot.nodeskai.com";
const AUTH_CALLBACK_URL = "browserlite://auth/callback";
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
    this.pendingAuthPath = join(app.getPath("userData"), "browser-pilot-login.json");
    this.config = null;
    this.pendingAuth = null;
    this.authStatus = "idle";
    this.socket = null;
    this.connected = false;
    this.connecting = false;
    this.stopped = false;
    this.lastError = "";
    this.reconnectAttempt = 0;
    this.connectionGeneration = 0;
  }

  async load() {
    try {
      const parsed = JSON.parse(await readFile(this.configPath, "utf8"));
      const token = await this.decryptToken(parsed);
      if (parsed.serverUrl && parsed.nodeId && token) {
        this.config = { ...parsed, token };
        if (parsed.tokenEncryption === LEGACY_TOKEN_ENCRYPTION) await this.saveConfig(this.config);
      }
    } catch {
      this.config = null;
    }
    if (!this.config) {
      this.pendingAuth = await this.loadPendingAuth();
      this.authStatus = this.pendingAuth ? "waiting_for_browser" : "idle";
    } else {
      this.authStatus = this.config.account ? "connecting" : "idle";
    }
    if (this.config) void this.connectLoop();
    this.notifyChanged();
  }

  notifyChanged() {
    this.onChanged?.();
  }

  async saveConfig(config) {
    const serialized = { ...config };
    if (!safeStorage.isEncryptionAvailable()) throw new Error("macOS Keychain is unavailable");
    serialized.tokenEncryption = TOKEN_ENCRYPTION;
    serialized.tokenCiphertext = safeStorage.encryptString(config.token).toString("base64");
    delete serialized.token;
    delete serialized.tokenEncrypted;
    delete serialized.tokenIv;
    delete serialized.tokenAuthTag;
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
    if (config.tokenEncryption === TOKEN_ENCRYPTION) {
      if (!safeStorage.isEncryptionAvailable()) throw new Error("macOS Keychain is unavailable");
      return safeStorage.decryptString(Buffer.from(config.tokenCiphertext, "base64"));
    }
    if (config.tokenEncryption !== LEGACY_TOKEN_ENCRYPTION) return "";
    const key = await this.installationKey();
    const decipher = createDecipheriv("aes-256-gcm", key, Buffer.from(config.tokenIv, "base64"));
    decipher.setAuthTag(Buffer.from(config.tokenAuthTag, "base64"));
    return Buffer.concat([
      decipher.update(Buffer.from(config.tokenCiphertext, "base64")),
      decipher.final(),
    ]).toString("utf8");
  }

  async savePendingAuth(pending) {
    if (!safeStorage.isEncryptionAvailable()) throw new Error("macOS Keychain is unavailable");
    const serialized = {
      ...pending,
      codeVerifierCiphertext: safeStorage.encryptString(pending.codeVerifier).toString("base64"),
    };
    delete serialized.codeVerifier;
    await writeFile(this.pendingAuthPath, `${JSON.stringify(serialized, null, 2)}\n`, { mode: 0o600 });
  }

  async loadPendingAuth() {
    try {
      const parsed = JSON.parse(await readFile(this.pendingAuthPath, "utf8"));
      if (!safeStorage.isEncryptionAvailable()) return null;
      const expiresAt = Date.parse(parsed.expiresAt || "");
      if (!Number.isFinite(expiresAt) || expiresAt <= Date.now()) {
        await this.clearPendingAuth();
        return null;
      }
      return {
        ...parsed,
        codeVerifier: safeStorage.decryptString(Buffer.from(parsed.codeVerifierCiphertext, "base64")),
      };
    } catch {
      return null;
    }
  }

  async clearPendingAuth() {
    this.pendingAuth = null;
    await unlink(this.pendingAuthPath).catch((error) => {
      if (error.code !== "ENOENT") throw error;
    });
  }

  nodeMetadata(displayName = undefined) {
    return {
      displayName: String(displayName || hostname()),
      platform: platform(),
      architecture: arch(),
      appVersion: app.getVersion(),
      chromiumVersion: process.versions.chrome,
      capabilities: NODE_CAPABILITIES,
    };
  }

  async beginLogin(serverUrl = DEFAULT_SERVER_URL, displayName = undefined) {
    const origin = normalizeServerUrl(serverUrl || DEFAULT_SERVER_URL);
    const codeVerifier = randomBytes(48).toString("base64url");
    const codeChallenge = createHash("sha256").update(codeVerifier).digest("base64url");
    const state = randomBytes(32).toString("base64url");
    this.authStatus = "opening_browser";
    this.lastError = "";
    this.notifyChanged();
    try {
      const response = await fetch(`${origin}/api/browser-lite/auth/requests`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          codeChallenge,
          state,
          callbackUrl: AUTH_CALLBACK_URL,
          ...this.nodeMetadata(displayName),
        }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || body.error || `Login failed: HTTP ${response.status}`);
      const authorizeUrl = new URL(body.authorizePath, origin).toString();
      this.pendingAuth = {
        serverUrl: origin,
        requestId: body.requestId,
        state,
        codeVerifier,
        expiresAt: body.expiresAt,
      };
      await this.savePendingAuth(this.pendingAuth);
      this.authStatus = "waiting_for_browser";
      this.notifyChanged();
      return { authorizeUrl, state: this.publicState() };
    } catch (error) {
      this.authStatus = "error";
      this.lastError = error.message || String(error);
      this.notifyChanged();
      throw error;
    }
  }

  async completeLogin(callbackUrl) {
    this.authStatus = "exchanging";
    this.lastError = "";
    this.notifyChanged();
    try {
      const callback = new URL(String(callbackUrl || ""));
      if (callback.protocol !== "browserlite:" || callback.hostname !== "auth" || callback.pathname !== "/callback") {
        throw new Error("Browser Lite login callback is invalid");
      }
      const pending = this.pendingAuth || await this.loadPendingAuth();
      if (!pending) throw new Error("Browser Lite login request is missing or expired");
      if (callback.searchParams.get("state") !== pending.state) throw new Error("Browser Lite login state does not match");
      if (callback.searchParams.get("request") !== pending.requestId) throw new Error("Browser Lite login request does not match");
      const authorizationCode = callback.searchParams.get("code") || "";
      if (!authorizationCode) throw new Error("Browser Lite login callback has no authorization code");
      const response = await fetch(`${pending.serverUrl}/api/browser-lite/auth/token`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ authorizationCode, codeVerifier: pending.codeVerifier }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || body.error || `Login failed: HTTP ${response.status}`);

      const previous = this.config;
      this.disconnect();
      this.config = {
        serverUrl: pending.serverUrl,
        nodeId: body.nodeId,
        token: body.token,
        displayName: body.displayName || hostname(),
        account: body.account || null,
      };
      await this.saveConfig(this.config);
      await this.clearPendingAuth();
      if (previous) {
        void fetch(`${previous.serverUrl}/api/browser-lite/nodes/${encodeURIComponent(previous.nodeId)}/disconnect`, {
          method: "POST",
          headers: { authorization: `Bearer ${previous.token}` },
        }).catch(() => {});
      }
      this.stopped = false;
      this.authStatus = "connecting";
      void this.connectLoop();
      this.notifyChanged();
      return this.publicState();
    } catch (error) {
      this.authStatus = "error";
      this.lastError = error.message || String(error);
      this.notifyChanged();
      throw error;
    }
  }

  async pair(serverUrl, pairingCode, displayName = undefined) {
    const origin = normalizeServerUrl(serverUrl);
    const response = await fetch(`${origin}/api/browser-lite/pair`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        pairingCode: String(pairingCode || "").trim(),
        ...this.nodeMetadata(displayName),
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
    await this.clearPendingAuth();
    this.stopped = false;
    this.authStatus = "connecting";
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
    await this.clearPendingAuth();
    await writeFile(this.configPath, "{}\n", { mode: 0o600 });
    this.authStatus = "idle";
    this.lastError = "";
    this.notifyChanged();
  }

  disconnect() {
    this.connectionGeneration += 1;
    this.stopped = true;
    this.socket?.close();
    this.socket = null;
    this.connected = false;
    this.connecting = false;
  }

  async connectLoop() {
    if (this.connecting || !this.config) return;
    const generation = this.connectionGeneration;
    this.connecting = true;
    this.stopped = false;
    while (!this.stopped && this.config && generation === this.connectionGeneration) {
      try {
        await this.connectOnce();
      } catch (error) {
        this.lastError = error.message || String(error);
        if (this.config?.account) this.authStatus = "error";
      }
      this.connected = false;
      this.notifyChanged();
      if (this.stopped || generation !== this.connectionGeneration) break;
      this.reconnectAttempt += 1;
      await delay(Math.min(30_000, 1_000 * 2 ** Math.min(5, this.reconnectAttempt)));
    }
    if (generation === this.connectionGeneration) this.connecting = false;
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
    this.authStatus = "connected";
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
      authenticated: Boolean(this.config?.account),
      legacyPaired: Boolean(this.config && !this.config.account),
      connected: this.connected,
      connecting: this.connecting,
      authStatus: this.authStatus,
      serverUrl: this.config?.serverUrl || "",
      nodeId: this.config?.nodeId || "",
      displayName: this.config?.displayName || hostname(),
      account: this.config?.account || null,
      lastError: this.lastError,
    };
  }
}
