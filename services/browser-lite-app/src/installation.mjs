import { app } from "electron";
import { execFile, spawn } from "node:child_process";
import {
  access,
  copyFile,
  cp,
  mkdir,
  readFile,
  readdir,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import { homedir } from "node:os";
import { basename, dirname, join, relative, resolve } from "node:path";
import {
  createCipheriv,
  createDecipheriv,
  createHash,
  pbkdf2Sync,
  randomBytes,
  timingSafeEqual,
} from "node:crypto";
import { DatabaseSync } from "node:sqlite";

const INSTALLATION_VERSION = 1;
const CHROME_ROOT = join(homedir(), "Library", "Application Support", "Google", "Chrome");
const CHROME_APP_ID = "com.google.Chrome";
const EGO_ROOT = join(homedir(), "Library", "Application Support", "Citro Labs", "ego lite");
const EGO_APP_ID = "com.citrolabs.ego.lite";
const CHROME_STORAGE_ITEMS = ["Local Storage", "IndexedDB", "Session Storage", "WebStorage"];
const CHROME_PROFILE_CACHE_ITEMS = new Set([
  "Cache", "Code Cache", "DawnCache", "GPUCache", "GrShaderCache", "ShaderCache",
  "component_crx_cache", "optimization_guide_model_store", "Safe Browsing",
]);
const CHROME_EXTENSION_ITEMS = new Set([
  "Extensions", "Extension Rules", "Extension Scripts", "Extension State",
  "Local Extension Settings", "Sync Extension Settings", "Secure Preferences",
]);
const CHROMIUM_SESSION_ITEMS = new Set([
  "Sessions", "Current Session", "Current Tabs", "Last Session", "Last Tabs",
]);
const BROWSER_SOURCES = Object.freeze({
  ego: Object.freeze({
    id: "ego",
    name: "ego lite",
    root: EGO_ROOT,
    appId: EGO_APP_ID,
    processName: "ego lite",
    safeStorageService: "Chromium Safe Storage",
    priority: 0,
  }),
  chrome: Object.freeze({
    id: "chrome",
    name: "Google Chrome",
    root: CHROME_ROOT,
    appId: CHROME_APP_ID,
    processName: "Google Chrome",
    safeStorageService: "Chrome Safe Storage",
    priority: 1,
  }),
});
const COOKIE_SEED_ENCRYPTION = "browser-lite-cookie-seed-aes-256-gcm-v1";
const CHROME_EPOCH_OFFSET_SECONDS = 11_644_473_600;
const BROWSER_LITE_EXTENSION_ID = "inojjeiaefalehjeknfidmiobpjddmok";
const BROWSER_LITE_EXTENSION_KEY = "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAxMvsHcmkDUnq8+cV/h/cfpnsqaiCXCfDVvjgLRsI7Su7f5vlzG0ZNBgf5sAyTFCM+DGwfMhValByHuzCHK0WnqKMiiCPkwLsMJBGAhZkYGUvw0rDp2HqXMhTs108igYnh4zIg2A93T1jmp+cxy0fcGobOx7k+ErRRNd2ihAjFEBmikZHbnV7W/cXxov6iaEin5gDoA2QsASmFTgovNoDp/e88ZJpofouUCUleVCU8RajBtV9g3vEN99xeLPHTU/TnsIbwydNvNuB5XYrZ9wbAPetGPSvhvtuxUClOH88v+AzBPUgd6FvKNC4A2IIig68E5YEQ4pwJ3diT+xnSpPyTwIDAQAB";

function execFileResult(file, args, options = {}) {
  return new Promise((resolveResult, rejectResult) => {
    execFile(file, args, options, (error, stdout, stderr) => {
      if (error) {
        error.stdout = stdout;
        error.stderr = stderr;
        rejectResult(error);
        return;
      }
      resolveResult({ stdout, stderr });
    });
  });
}

function safeInstanceId(value) {
  const normalized = String(value ?? "browser_lite").replace(/[^A-Za-z0-9._-]/g, "-");
  if (!/^[A-Za-z0-9]/.test(normalized)) return `instance-${normalized}`;
  return normalized.slice(0, 64);
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
}

async function pathExists(path) {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

function readJson(path, fallback = null) {
  return readFile(path, "utf8").then(JSON.parse).catch(() => fallback);
}

function countBookmarks(node) {
  if (!node || typeof node !== "object") return 0;
  let count = node.type === "url" && node.url ? 1 : 0;
  for (const child of node.children ?? []) count += countBookmarks(child);
  return count;
}

function collectBookmarks(node, result = []) {
  if (!node || typeof node !== "object") return result;
  if (node.type === "url" && node.url) result.push({ name: node.name || node.url, url: node.url });
  for (const child of node.children ?? []) collectBookmarks(child, result);
  return result;
}

function readDatabase(path, callback, fallback) {
  let database;
  try {
    database = new DatabaseSync(path, { readOnly: true, readBigInts: true });
    return callback(database);
  } catch {
    return fallback;
  } finally {
    try { database?.close(); } catch {}
  }
}

async function readTopSites(profilePath) {
  const source = join(profilePath, "Top Sites");
  if (!await pathExists(source)) return [];
  const snapshot = join(
    app.getPath("temp"),
    `browser-lite-top-sites-${process.pid}-${randomBytes(8).toString("hex")}.sqlite`,
  );
  try {
    await cp(source, snapshot, { preserveTimestamps: true });
    return readDatabase(
      snapshot,
      (database) => database.prepare(`
        SELECT url, title FROM top_sites ORDER BY url_rank ASC LIMIT 7
      `).all().map((row) => ({
        url: webUrl(row.url),
        title: String(row.title || row.url || ""),
      })).filter((row) => row.url),
      [],
    );
  } finally {
    await rm(snapshot, { force: true }).catch(() => {});
  }
}

function sqliteNumber(value) {
  return Number(value ?? 0);
}

function chromeTimestampToUnix(value) {
  const seconds = Number(typeof value === "bigint" ? value / 1_000_000n : Number(value) / 1_000_000) - CHROME_EPOCH_OFFSET_SECONDS;
  return Number.isFinite(seconds) ? seconds : 0;
}

function webUrl(value) {
  try {
    const url = new URL(String(value || ""));
    return ["http:", "https:"].includes(url.protocol) ? url.toString() : "";
  } catch {
    return "";
  }
}

function cookieDatabasePath(profilePath) {
  const legacyPath = join(profilePath, "Cookies");
  const networkPath = join(profilePath, "Network", "Cookies");
  return pathExists(legacyPath).then((legacy) => legacy ? legacyPath : networkPath);
}

function sameSiteValue(value) {
  if (Number(value) === 0) return "no_restriction";
  if (Number(value) === 1) return "lax";
  if (Number(value) === 2) return "strict";
  return "unspecified";
}

function decryptChromeCookie(row, password, databaseVersion) {
  const encrypted = Buffer.from(row.encrypted_value ?? []);
  if (encrypted.length < 4 || encrypted.subarray(0, 3).toString("ascii") !== "v10") return null;
  const key = pbkdf2Sync(password, "saltysalt", 1003, 16, "sha1");
  const decipher = createDecipheriv("aes-128-cbc", key, Buffer.alloc(16, 0x20));
  let decrypted = Buffer.concat([decipher.update(encrypted.subarray(3)), decipher.final()]);
  if (Number(databaseVersion) >= 24) {
    if (decrypted.length < 32) return null;
    const expected = createHash("sha256").update(row.host_key).digest();
    if (!timingSafeEqual(decrypted.subarray(0, 32), expected)) return null;
    decrypted = decrypted.subarray(32);
  }
  return decrypted.toString("utf8");
}

async function directorySize(path) {
  let total = 0;
  const entries = await readdir(path, { withFileTypes: true }).catch(() => []);
  for (const entry of entries) {
    const child = join(path, entry.name);
    if (entry.isDirectory()) total += await directorySize(child);
    else if (entry.isFile()) total += (await stat(child).catch(() => ({ size: 0 }))).size;
  }
  return total;
}

export class BrowserLiteInstallation {
  constructor() {
    this.dataRoot = app.getPath("userData");
    this.statePath = join(this.dataRoot, "installation.json");
    this.seedRoot = join(this.dataRoot, "Imported Profile Seed");
    this.seedMetadataPath = join(this.seedRoot, "metadata.json");
    this.chromiumSeedRoot = join(this.seedRoot, "Chromium User Data");
    this.cookieSeedPath = join(this.seedRoot, "cookies.enc.json");
    this.keyPath = join(this.dataRoot, "installation-key.bin");
    this.chromeSafeStoragePath = join(this.dataRoot, "chrome-safe-storage.enc.json");
    this.state = null;
    this.busy = false;
    this.lastError = "";
  }

  async load() {
    this.state = await readJson(this.statePath, null);
    if (this.state?.version !== INSTALLATION_VERSION || this.state?.complete !== true) this.state = null;
    return this.state;
  }

  isComplete() {
    return this.state?.complete === true;
  }

  async sourceRunning(source) {
    try {
      await execFileResult("/usr/bin/pgrep", ["-x", source.processName]);
      return true;
    } catch {
      return false;
    }
  }

  async profiles() {
    const results = [];
    for (const source of Object.values(BROWSER_SOURCES)) {
      if (!await pathExists(source.root)) continue;
      const localState = await readJson(join(source.root, "Local State"), {});
      const infoCache = localState?.profile?.info_cache ?? {};
      const lastUsed = localState?.profile?.last_used ?? "";
      const candidates = Object.entries(infoCache)
        .filter(([directory]) => directory === "Default" || /^Profile \d+$/.test(directory));
      for (const [directory, info] of candidates) {
        const profilePath = join(source.root, directory);
        if (!await pathExists(profilePath)) continue;
        const bookmarks = await readJson(join(profilePath, "Bookmarks"), {});
        const bookmarkCount = Object.values(bookmarks?.roots ?? {}).reduce((sum, root) => sum + countBookmarks(root), 0);
        const historyCount = readDatabase(
          join(profilePath, "History"),
          (database) => sqliteNumber(database.prepare("SELECT COUNT(*) AS count FROM urls").get()?.count),
          0,
        );
        const cookiesPath = await cookieDatabasePath(profilePath);
        const cookieCount = readDatabase(
          cookiesPath,
          (database) => sqliteNumber(database.prepare("SELECT COUNT(*) AS count FROM cookies").get()?.count),
          0,
        );
        const extensionRoot = join(profilePath, "Extensions");
        const extensionIds = (await readdir(extensionRoot, { withFileTypes: true }).catch(() => []))
          .filter((entry) => entry.isDirectory()).length;
        const storageBytes = (await Promise.all(CHROME_STORAGE_ITEMS.map((item) => directorySize(join(profilePath, item)))))
          .reduce((sum, value) => sum + value, 0);
        results.push({
          id: `${source.id}:${directory}`,
          browserId: source.id,
          browser: source.name,
          directory,
          name: String(info?.name || directory),
          email: String(info?.user_name || ""),
          lastUsed: directory === lastUsed,
          sourcePriority: source.priority,
          counts: { bookmarks: bookmarkCount, history: historyCount, cookies: cookieCount, extensions: extensionIds },
          storageBytes,
        });
      }
    }
    return results.sort((left, right) => (
      left.sourcePriority - right.sourcePriority || Number(right.lastUsed) - Number(left.lastUsed)
    ));
  }

  async publicState() {
    const profiles = this.isComplete() ? [] : await this.profiles();
    const metadata = this.isComplete() ? await this.metadata() : {};
    return {
      version: INSTALLATION_VERSION,
      complete: this.isComplete(),
      required: !this.isComplete(),
      busy: this.busy,
      lastError: this.lastError,
      mode: this.state?.mode ?? null,
      completedAt: this.state?.completedAt ?? null,
      source: this.state?.source ?? null,
      result: this.state?.result ?? null,
      bookmarks: (metadata.bookmarks || []).slice(0, 18).map((bookmark) => ({
        name: String(bookmark?.name || "书签"),
        url: String(bookmark?.url || ""),
      })),
      chromeRunning: !this.isComplete() && (await Promise.all(
        Object.values(BROWSER_SOURCES).map((source) => this.sourceRunning(source)),
      )).some(Boolean),
      profiles,
      supported: {
        loginState: true,
        bookmarks: true,
        history: true,
        passwords: false,
        extensions: true,
      },
    };
  }

  profilePath(profileId) {
    const [browser, directory] = String(profileId || "").split(":", 2);
    const source = BROWSER_SOURCES[browser];
    if (!source || !(directory === "Default" || /^Profile \d+$/.test(directory))) {
      throw new Error("请选择有效的浏览器 Profile");
    }
    const path = resolve(source.root, directory);
    if (dirname(path) !== resolve(source.root)) throw new Error("浏览器 Profile 路径无效");
    return { ...source, directory, path };
  }

  async quitSource(source) {
    if (!await this.sourceRunning(source)) return false;
    await execFileResult("/usr/bin/osascript", ["-e", `tell application id "${source.appId}" to quit`]);
    for (let attempt = 0; attempt < 60; attempt += 1) {
      if (!await this.sourceRunning(source)) return true;
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 250));
    }
    throw new Error(`${source.name} 仍在运行。请保存工作并退出后重试。`);
  }

  async installationKey() {
    try {
      const key = await readFile(this.keyPath);
      if (key.length !== 32) throw new Error("安装密钥无效");
      return key;
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
      const key = randomBytes(32);
      await writeFile(this.keyPath, key, { mode: 0o600, flag: "wx" });
      return key;
    }
  }

  async saveCookieSeed(cookies) {
    await this.saveEncryptedJson(this.cookieSeedPath, cookies, COOKIE_SEED_ENCRYPTION);
  }

  async saveEncryptedJson(path, value, encryption) {
    const key = await this.installationKey();
    const iv = randomBytes(12);
    const cipher = createCipheriv("aes-256-gcm", key, iv);
    const ciphertext = Buffer.concat([cipher.update(JSON.stringify(value), "utf8"), cipher.final()]);
    await writeFile(path, `${JSON.stringify({
      encryption,
      iv: iv.toString("base64"),
      authTag: cipher.getAuthTag().toString("base64"),
      ciphertext: ciphertext.toString("base64"),
    })}\n`, { mode: 0o600 });
  }

  async loadEncryptedJson(path, encryption, fallback) {
    const payload = await readJson(path, null);
    if (!payload || payload.encryption !== encryption) return fallback;
    const key = await this.installationKey();
    const decipher = createDecipheriv("aes-256-gcm", key, Buffer.from(payload.iv, "base64"));
    decipher.setAuthTag(Buffer.from(payload.authTag, "base64"));
    return JSON.parse(Buffer.concat([
      decipher.update(Buffer.from(payload.ciphertext, "base64")),
      decipher.final(),
    ]).toString("utf8"));
  }

  async loadCookieSeed() {
    return await this.loadEncryptedJson(this.cookieSeedPath, COOKIE_SEED_ENCRYPTION, []);
  }

  safeStorageCachePath(source) {
    return source.id === "chrome"
      ? this.chromeSafeStoragePath
      : join(this.dataRoot, `${source.id}-safe-storage.enc.json`);
  }

  async safeStoragePassword(source) {
    const encryption = `browser-lite-${source.id}-safe-storage-aes-256-gcm-v1`;
    const cached = await this.loadEncryptedJson(
      this.safeStorageCachePath(source),
      encryption,
      "",
    ).catch(() => "");
    if (cached) return cached;
    let password;
    try {
      const result = await execFileResult("/usr/bin/security", [
        "find-generic-password", "-w", "-s", source.safeStorageService,
      ], { encoding: "utf8" });
      password = result.stdout.trim();
    } catch {
      throw new Error(`无法读取 ${source.safeStorageService}。请在 macOS 提示中选择“始终允许”，然后重试。`);
    }
    if (!password) throw new Error(`${source.safeStorageService} 密钥为空`);
    await this.saveEncryptedJson(
      this.safeStorageCachePath(source),
      password,
      encryption,
    );
    return password;
  }

  async importCookies(profilePath, source) {
    const cookiePath = await cookieDatabasePath(profilePath);
    if (!await pathExists(cookiePath)) return [];
    const password = await this.safeStoragePassword(source);
    const database = new DatabaseSync(cookiePath, { readOnly: true, readBigInts: true });
    try {
      const databaseVersion = sqliteNumber(database.prepare("SELECT value FROM meta WHERE key = 'version'").get()?.value);
      const rows = database.prepare(`
        SELECT host_key, name, value, encrypted_value, path, expires_utc,
               is_secure, is_httponly, samesite
        FROM cookies
      `).all();
      const cookies = [];
      for (const row of rows) {
        try {
          const value = row.value || decryptChromeCookie(row, password, databaseVersion);
          if (value === null) continue;
          const host = String(row.host_key || "").replace(/^\./, "");
          if (!host) continue;
          const expirationDate = chromeTimestampToUnix(row.expires_utc);
          if (expirationDate > 0 && expirationDate <= Date.now() / 1000) continue;
          cookies.push({
            url: `${row.is_secure ? "https" : "http"}://${host}${String(row.path || "/")}`,
            name: String(row.name || ""),
            value,
            ...(String(row.host_key || "").startsWith(".") ? { domain: String(row.host_key) } : {}),
            path: String(row.path || "/"),
            secure: sqliteNumber(row.is_secure) === 1,
            httpOnly: sqliteNumber(row.is_httponly) === 1,
            sameSite: sameSiteValue(row.samesite),
            ...(expirationDate > 0 ? { expirationDate } : {}),
          });
        } catch {}
      }
      return cookies;
    } finally {
      database.close();
    }
  }

  async copyStorage(profilePath) {
    const storageRoot = join(this.seedRoot, "Storage");
    await rm(storageRoot, { recursive: true, force: true });
    await mkdir(storageRoot, { recursive: true, mode: 0o700 });
    const copied = [];
    for (const item of CHROME_STORAGE_ITEMS) {
      const source = join(profilePath, item);
      if (!await pathExists(source)) continue;
      await cp(source, join(storageRoot, item), {
        recursive: true,
        preserveTimestamps: true,
        filter: (path) => basename(path) !== "LOCK",
      });
      copied.push(item);
    }
    return copied;
  }

  async captureChromiumSeed(profilePath, directory, options = {}, source = BROWSER_SOURCES.chrome) {
    await rm(this.chromiumSeedRoot, { recursive: true, force: true });
    await mkdir(this.chromiumSeedRoot, { recursive: true, mode: 0o700 });
    const localState = join(source.root, "Local State");
    if (await pathExists(localState)) await cp(localState, join(this.chromiumSeedRoot, "Local State"), { preserveTimestamps: true });
    const destination = join(this.chromiumSeedRoot, directory);
    const shouldCopy = (path) => {
      if (path === profilePath) return true;
      const itemPath = relative(profilePath, path);
      const segments = itemPath.split(/[\\/]/).filter(Boolean);
      const first = segments[0] || "";
      const name = basename(path);
      if (name === "LOCK" || name.startsWith("Singleton") || name === "DevToolsActivePort") return false;
      if (CHROME_PROFILE_CACHE_ITEMS.has(first) || CHROME_PROFILE_CACHE_ITEMS.has(name)) return false;
      if (CHROMIUM_SESSION_ITEMS.has(first)) return false;
      if (options.extensions === false && CHROME_EXTENSION_ITEMS.has(first)) return false;
      if (/^Login Data(?: For Account)?(?:-journal)?$/.test(first)) return false;
      if (options.loginState === false && ["Cookies", "Network", ...CHROME_STORAGE_ITEMS].includes(first)) return false;
      if (options.bookmarks === false && ["Bookmarks", "Bookmarks.bak"].includes(first)) return false;
      if (options.history === false && ["History", "History-journal", "Favicons", "Favicons-journal", "Top Sites", "Top Sites-journal", "Visited Links"].includes(first)) return false;
      return true;
    };
    await cp(profilePath, destination, { recursive: true, preserveTimestamps: true, filter: shouldCopy });
  }

  async importProfile(payload = {}) {
    if (this.busy) throw new Error("安装正在进行");
    if (this.isComplete()) throw new Error("Browser Lite 已完成安装");
    this.busy = true;
    this.lastError = "";
    let reopenSource = false;
    let sourceToReopen = null;
    try {
      const source = this.profilePath(payload.profileId);
      sourceToReopen = source;
      const { directory, path: profilePath } = source;
      const selectedProfile = (await this.profiles()).find((profile) => profile.id === payload.profileId);
      reopenSource = await this.quitSource(source);
      await rm(this.seedRoot, { recursive: true, force: true });
      await mkdir(this.seedRoot, { recursive: true, mode: 0o700 });

      const options = {
        loginState: payload.loginState !== false,
        bookmarks: payload.bookmarks !== false,
        history: payload.history !== false,
        extensions: payload.extensions !== false,
      };
      await this.captureChromiumSeed(profilePath, directory, options, source);
      const bookmarksFile = options.bookmarks ? await readJson(join(profilePath, "Bookmarks"), {}) : {};
      const bookmarks = options.bookmarks
        ? Object.values(bookmarksFile?.roots ?? {})
          .flatMap((root) => collectBookmarks(root))
          .map((item) => ({ ...item, url: webUrl(item.url) }))
          .filter((item) => item.url)
          .slice(0, 5000)
        : [];
      const history = options.history ? readDatabase(
        join(profilePath, "History"),
        (database) => database.prepare(`
          SELECT url, title, last_visit_time AS lastVisitTime
          FROM urls WHERE hidden = 0 ORDER BY last_visit_time DESC LIMIT 500
        `).all().map((row) => ({
          url: webUrl(row.url),
          title: String(row.title || row.url || ""),
          lastVisitAt: chromeTimestampToUnix(row.lastVisitTime),
        })).filter((row) => row.url).map((row) => ({
          ...row,
          lastVisitAt: row.lastVisitAt > 0 ? new Date(row.lastVisitAt * 1000).toISOString() : null,
        })),
        [],
      ) : [];
      const shortcuts = options.history ? await readTopSites(profilePath) : [];
      const cookies = options.loginState ? await this.importCookies(profilePath, source) : [];
      const storage = options.loginState ? await this.copyStorage(profilePath) : [];
      const extensions = options.extensions
        ? (await readdir(join(profilePath, "Extensions"), { withFileTypes: true }).catch(() => []))
          .filter((entry) => entry.isDirectory()).length
        : 0;
      await this.saveCookieSeed(cookies);
      const metadata = {
        browser: source.name,
        profileDirectory: directory,
        importedAt: new Date().toISOString(),
        bookmarks,
        history,
        shortcuts,
        storage,
      };
      await writeFile(this.seedMetadataPath, `${JSON.stringify(metadata, null, 2)}\n`, { mode: 0o600 });
      const result = {
        cookies: cookies.length,
        bookmarks: bookmarks.length,
        history: history.length,
        extensions,
        storage,
      };
      this.state = {
        version: INSTALLATION_VERSION,
        complete: true,
        mode: "imported",
        completedAt: new Date().toISOString(),
        source: {
          browser: source.name,
          browserId: source.id,
          profileDirectory: directory,
          profileName: selectedProfile?.name || directory,
        },
        result,
      };
      await writeFile(this.statePath, `${JSON.stringify(this.state, null, 2)}\n`, { mode: 0o600 });
      return result;
    } catch (error) {
      await rm(this.seedRoot, { recursive: true, force: true }).catch(() => {});
      this.lastError = error.message || String(error);
      throw error;
    } finally {
      if (reopenSource) {
        await execFileResult("/usr/bin/open", ["-b", sourceToReopen?.appId ?? CHROME_APP_ID]).catch(() => {});
      }
      this.busy = false;
    }
  }

  async freshStart() {
    if (this.busy) throw new Error("安装正在进行");
    await rm(this.seedRoot, { recursive: true, force: true });
    this.state = {
      version: INSTALLATION_VERSION,
      complete: true,
      mode: "fresh",
      completedAt: new Date().toISOString(),
      source: null,
      result: { cookies: 0, bookmarks: 0, history: 0, extensions: 0, storage: [] },
    };
    await writeFile(this.statePath, `${JSON.stringify(this.state, null, 2)}\n`, { mode: 0o600 });
    return this.state;
  }

  profileDirectory() {
    return this.state?.mode === "imported" && this.state.source?.profileDirectory
      ? this.state.source.profileDirectory
      : "Default";
  }

  instanceProfilePath(instanceId) {
    return join(this.dataRoot, "Instances", safeInstanceId(instanceId));
  }

  async prepareInstance(instanceId) {
    if (!this.isComplete()) throw new Error("请先完成 Browser Lite 安装");
    const destination = this.instanceProfilePath(instanceId);
    const marker = join(destination, ".browser-lite-chromium-seeded");
    if (await pathExists(marker)) return;
    if (!await pathExists(this.chromiumSeedRoot) && this.state?.mode === "imported" && this.state.source?.profileDirectory) {
      const source = this.profilePath(`${this.state.source.browserId || "chrome"}:${this.state.source.profileDirectory}`);
      await this.captureChromiumSeed(source.path, source.directory, {
        loginState: true,
        bookmarks: true,
        history: true,
        extensions: true,
      }, source);
    }
    await rm(destination, { recursive: true, force: true });
    await mkdir(destination, { recursive: true, mode: 0o700 });
    if (await pathExists(this.chromiumSeedRoot)) {
      const entries = await readdir(this.chromiumSeedRoot, { withFileTypes: true });
      for (const entry of entries) {
        await cp(join(this.chromiumSeedRoot, entry.name), join(destination, entry.name), {
          recursive: true,
          preserveTimestamps: true,
        });
      }
    }
    await writeFile(marker, `${JSON.stringify({ importedAt: new Date().toISOString(), profileDirectory: this.profileDirectory() })}\n`, { mode: 0o600 });
  }

  async prepareRuntimeExtension(instanceId, port, controlToken) {
    const profileRoot = this.instanceProfilePath(instanceId);
    const extensionRoot = join(profileRoot, ".browser-lite-extension");
    await rm(extensionRoot, { recursive: true, force: true });
    await mkdir(extensionRoot, { recursive: true, mode: 0o700 });
    const manifest = {
      manifest_version: 3,
      name: "Browser Lite Spaces",
      description: "Return to Browser Lite Spaces",
      version: "0.5.0",
      key: BROWSER_LITE_EXTENSION_KEY,
      action: {
        default_title: "返回 Spaces",
        default_icon: { 16: "icon.png", 32: "icon.png" },
      },
      background: { service_worker: "background.js" },
      permissions: ["alarms"],
      host_permissions: [`http://127.0.0.1:${Number(port)}/*`],
    };
    const endpoint = `http://127.0.0.1:${Number(port)}/browser-lite`;
    const background = `const ENDPOINT = ${JSON.stringify(endpoint)};
const TOKEN = ${JSON.stringify(controlToken)};
async function updateBadge() {
  try {
    const response = await fetch(\`${endpoint}/space-count?token=\${encodeURIComponent(TOKEN)}\`);
    const payload = await response.json();
    const count = Number(payload?.value?.count || 0);
    await chrome.action.setBadgeBackgroundColor({ color: "#7c5cff" });
    await chrome.action.setBadgeText({ text: count > 0 ? String(count) : "" });
  } catch {}
}
chrome.action.onClicked.addListener(async () => {
  try { await fetch(\`${endpoint}/show-spaces?token=\${encodeURIComponent(TOKEN)}\`, { method: "POST" }); } catch {}
});
chrome.runtime.onInstalled.addListener(updateBadge);
chrome.runtime.onStartup.addListener(updateBadge);
chrome.alarms.onAlarm.addListener(updateBadge);
chrome.alarms.create("browser-lite-space-count", { periodInMinutes: 0.25 });
void updateBadge();
`;
    await writeFile(join(extensionRoot, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`, { mode: 0o600 });
    await writeFile(join(extensionRoot, "background.js"), background, { mode: 0o600 });
    const iconSource = app.isPackaged
      ? join(process.resourcesPath, "trayTemplate.png")
      : join(app.getAppPath(), "assets", "trayTemplate.png");
    await copyFile(iconSource, join(extensionRoot, "icon.png"));

    const preferencesPath = join(profileRoot, this.profileDirectory(), "Preferences");
    const preferences = await readJson(preferencesPath, {});
    const seedPreferences = await readJson(
      join(this.chromiumSeedRoot, this.profileDirectory(), "Preferences"),
      {},
    );
    const importedPinnedExtensions = seedPreferences.extensions?.pinned_extensions
      ?? seedPreferences.account_values?.extensions?.pinned_extensions
      ?? [];
    preferences.bookmark_bar ||= {};
    preferences.bookmark_bar.show_on_all_tabs = true;
    preferences.profile ||= {};
    preferences.profile.exit_type = "Normal";
    preferences.profile.exited_cleanly = true;
    preferences.extensions ||= {};
    preferences.extensions.pinned_extensions = [
      ...importedPinnedExtensions.filter((id) => id !== BROWSER_LITE_EXTENSION_ID),
      BROWSER_LITE_EXTENSION_ID,
    ];
    preferences.account_values ||= {};
    preferences.account_values.extensions ||= {};
    preferences.account_values.extensions.pinned_extensions = [
      ...importedPinnedExtensions.filter((id) => id !== BROWSER_LITE_EXTENSION_ID),
      BROWSER_LITE_EXTENSION_ID,
    ];
    if (this.state?.source?.browserId === "ego" && preferences.browser?.theme) {
      delete preferences.browser.theme;
    }
    await mkdir(dirname(preferencesPath), { recursive: true, mode: 0o700 });
    await writeFile(preferencesPath, JSON.stringify(preferences), { mode: 0o600 });

    if (this.state?.source?.browserId === "ego") {
      const localStatePath = join(profileRoot, "Local State");
      const localState = await readJson(localStatePath, {});
      const profileInfo = localState?.profile?.info_cache?.[this.profileDirectory()];
      if (profileInfo) {
        profileInfo.name = "ego";
        profileInfo.gaia_given_name = "";
        profileInfo.gaia_id = "";
        profileInfo.gaia_name = "";
        profileInfo.gaia_picture_file_name = "";
        profileInfo.is_consented_primary_account = false;
        profileInfo.user_name = "";
        await writeFile(localStatePath, JSON.stringify(localState), { mode: 0o600 });
      }
    }
    const importedExtensionsRoot = join(
      this.chromiumSeedRoot,
      this.profileDirectory(),
      "Extensions",
    );
    const importedExtensionPaths = [];
    const extensionIds = await readdir(importedExtensionsRoot, { withFileTypes: true }).catch(() => []);
    for (const extensionId of extensionIds) {
      if (!extensionId.isDirectory()) continue;
      const versionsRoot = join(importedExtensionsRoot, extensionId.name);
      const versions = (await readdir(versionsRoot, { withFileTypes: true }).catch(() => []))
        .filter((entry) => entry.isDirectory())
        .sort((left, right) => right.name.localeCompare(left.name, undefined, { numeric: true }));
      let current;
      for (const version of versions) {
        if (await pathExists(join(versionsRoot, version.name, "manifest.json"))) {
          current = version;
          break;
        }
      }
      if (current) importedExtensionPaths.push(join(versionsRoot, current.name));
    }
    return [extensionRoot, ...importedExtensionPaths].join(",");
  }

  async seedRuntimeCookies(instanceId, state) {
    const marker = join(this.instanceProfilePath(instanceId), ".browser-lite-cookies-seeded");
    if (await pathExists(marker)) return 0;
    const cookies = await this.loadCookieSeed().catch(() => []);
    let imported;
    if (typeof state.addCookies === "function") {
      imported = await state.addCookies(cookies);
    } else {
      imported = 0;
      for (const cookie of cookies) {
        try {
          await state.addCookie(cookie);
          imported += 1;
        } catch {}
      }
    }
    await writeFile(marker, `${JSON.stringify({ importedAt: new Date().toISOString(), cookies: imported })}\n`, { mode: 0o600 });
    return imported;
  }

  extensionStartupMarker(instanceId) {
    return join(this.instanceProfilePath(instanceId), ".browser-lite-extensions-initialized");
  }

  async extensionStartupState(instanceId) {
    return await readJson(this.extensionStartupMarker(instanceId), null);
  }

  async markExtensionStartupInitialized(instanceId, result = {}) {
    await writeFile(this.extensionStartupMarker(instanceId), `${JSON.stringify({
      initializedAt: new Date().toISOString(),
      closedTabs: Number(result.closedTabs) || 0,
      onboardingUrls: Array.isArray(result.onboardingUrls) ? result.onboardingUrls : [],
    })}\n`, { mode: 0o600 });
  }

  async metadata() {
    return await readJson(this.seedMetadataPath, {});
  }

  async startUrl() {
    return "chrome://newtab/";
  }

  async resetData({ preservePairing = true } = {}) {
    const preserved = new Set([
      "installation-key.bin",
      "chrome-safe-storage.enc.json",
      "ego-safe-storage.enc.json",
      ...(preservePairing ? ["node-config.json", "node-key.bin"] : []),
    ]);
    const entries = await readdir(this.dataRoot, { withFileTypes: true }).catch(() => []);
    for (const entry of entries) {
      if (preserved.has(entry.name)) continue;
      await rm(join(this.dataRoot, entry.name), { recursive: true, force: true });
    }
    this.state = null;
  }

  async purgeData() {
    const entries = await readdir(this.dataRoot, { withFileTypes: true }).catch(() => []);
    for (const entry of entries) {
      await rm(join(this.dataRoot, entry.name), { recursive: true, force: true });
    }
    this.state = null;
  }

  scheduleRecoverableUninstall() {
    if (!app.isPackaged) throw new Error("彻底卸载仅支持已安装的 Browser Lite.app");
    const appPath = resolve(dirname(process.execPath), "..", "..");
    const trashRoot = join(homedir(), ".Trash");
    const suffix = new Date().toISOString().replace(/[:.]/g, "-");
    const appDestination = join(trashRoot, `Browser Lite ${suffix}.app`);
    const dataDestination = join(trashRoot, `Browser Lite Data ${suffix}`);
    const helper = `
while /bin/kill -0 "$1" 2>/dev/null; do /bin/sleep 0.2; done
if test -e "$2"; then /bin/mv "$2" "$4"; fi
if test -e "$3"; then /bin/mv "$3" "$5"; fi
`;
    const child = spawn("/bin/zsh", ["-c", helper, "browser-lite-uninstall", String(process.pid), appPath, this.dataRoot, appDestination, dataDestination], {
      detached: true,
      stdio: "ignore",
    });
    child.unref();
    return { appDestination, dataDestination };
  }
}
