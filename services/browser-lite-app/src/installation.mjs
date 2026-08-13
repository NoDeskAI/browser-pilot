import { app, session } from "electron";
import { execFile, spawn } from "node:child_process";
import {
  access,
  cp,
  mkdir,
  readFile,
  readdir,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import { homedir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
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
const CHROME_STORAGE_ITEMS = ["Local Storage", "IndexedDB", "Session Storage", "WebStorage"];
const COOKIE_SEED_ENCRYPTION = "browser-lite-cookie-seed-aes-256-gcm-v1";
const CHROME_EPOCH_OFFSET_SECONDS = 11_644_473_600;

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

  async chromeRunning() {
    try {
      await execFileResult("/usr/bin/pgrep", ["-x", "Google Chrome"]);
      return true;
    } catch {
      return false;
    }
  }

  async profiles() {
    if (!await pathExists(CHROME_ROOT)) return [];
    const localState = await readJson(join(CHROME_ROOT, "Local State"), {});
    const infoCache = localState?.profile?.info_cache ?? {};
    const lastUsed = localState?.profile?.last_used ?? "";
    const candidates = Object.entries(infoCache)
      .filter(([directory]) => directory === "Default" || /^Profile \d+$/.test(directory));
    const results = [];
    for (const [directory, info] of candidates) {
      const profilePath = join(CHROME_ROOT, directory);
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
        id: `chrome:${directory}`,
        browser: "Google Chrome",
        directory,
        name: String(info?.name || directory),
        email: String(info?.user_name || ""),
        lastUsed: directory === lastUsed,
        counts: { bookmarks: bookmarkCount, history: historyCount, cookies: cookieCount, extensions: extensionIds },
        storageBytes,
      });
    }
    return results.sort((left, right) => Number(right.lastUsed) - Number(left.lastUsed));
  }

  async publicState() {
    const profiles = this.isComplete() ? [] : await this.profiles();
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
      chromeRunning: !this.isComplete() && await this.chromeRunning(),
      profiles,
      supported: {
        loginState: true,
        bookmarks: true,
        history: true,
        passwords: false,
        extensions: false,
      },
    };
  }

  profilePath(profileId) {
    const [browser, directory] = String(profileId || "").split(":", 2);
    if (browser !== "chrome" || !(directory === "Default" || /^Profile \d+$/.test(directory))) {
      throw new Error("请选择有效的 Chrome Profile");
    }
    const path = resolve(CHROME_ROOT, directory);
    if (dirname(path) !== resolve(CHROME_ROOT)) throw new Error("Chrome Profile 路径无效");
    return { directory, path };
  }

  async quitChrome() {
    if (!await this.chromeRunning()) return false;
    await execFileResult("/usr/bin/osascript", ["-e", `tell application id "${CHROME_APP_ID}" to quit`]);
    for (let attempt = 0; attempt < 60; attempt += 1) {
      if (!await this.chromeRunning()) return true;
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 250));
    }
    throw new Error("Chrome 仍在运行。请保存工作并退出 Chrome 后重试。");
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

  async chromeSafeStoragePassword() {
    const cached = await this.loadEncryptedJson(
      this.chromeSafeStoragePath,
      "browser-lite-chrome-safe-storage-aes-256-gcm-v1",
      "",
    ).catch(() => "");
    if (cached) return cached;
    let password;
    try {
      const result = await execFileResult("/usr/bin/security", [
        "find-generic-password", "-w", "-s", "Chrome Safe Storage",
      ], { encoding: "utf8" });
      password = result.stdout.trim();
    } catch {
      throw new Error("无法读取 Chrome Safe Storage。请在 macOS 提示中选择“始终允许”，然后重试。");
    }
    if (!password) throw new Error("Chrome Safe Storage 密钥为空");
    await this.saveEncryptedJson(
      this.chromeSafeStoragePath,
      password,
      "browser-lite-chrome-safe-storage-aes-256-gcm-v1",
    );
    return password;
  }

  async importCookies(profilePath) {
    const cookiePath = await cookieDatabasePath(profilePath);
    if (!await pathExists(cookiePath)) return [];
    const password = await this.chromeSafeStoragePassword();
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

  async importProfile(payload = {}) {
    if (this.busy) throw new Error("安装正在进行");
    if (this.isComplete()) throw new Error("Browser Lite 已完成安装");
    this.busy = true;
    this.lastError = "";
    let reopenChrome = false;
    try {
      const { directory, path: profilePath } = this.profilePath(payload.profileId);
      reopenChrome = await this.quitChrome();
      await rm(this.seedRoot, { recursive: true, force: true });
      await mkdir(this.seedRoot, { recursive: true, mode: 0o700 });

      const options = {
        loginState: payload.loginState !== false,
        bookmarks: payload.bookmarks !== false,
        history: payload.history !== false,
      };
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
      const cookies = options.loginState ? await this.importCookies(profilePath) : [];
      const storage = options.loginState ? await this.copyStorage(profilePath) : [];
      await this.saveCookieSeed(cookies);
      const metadata = {
        browser: "Google Chrome",
        profileDirectory: directory,
        importedAt: new Date().toISOString(),
        bookmarks,
        history,
        storage,
      };
      await writeFile(this.seedMetadataPath, `${JSON.stringify(metadata, null, 2)}\n`, { mode: 0o600 });
      const result = {
        cookies: cookies.length,
        bookmarks: bookmarks.length,
        history: history.length,
        storage,
      };
      this.state = {
        version: INSTALLATION_VERSION,
        complete: true,
        mode: "imported",
        completedAt: new Date().toISOString(),
        source: { browser: "Google Chrome", profileDirectory: directory },
        result,
      };
      await writeFile(this.statePath, `${JSON.stringify(this.state, null, 2)}\n`, { mode: 0o600 });
      return result;
    } catch (error) {
      await rm(this.seedRoot, { recursive: true, force: true }).catch(() => {});
      this.lastError = error.message || String(error);
      throw error;
    } finally {
      if (reopenChrome) {
        await execFileResult("/usr/bin/open", ["-a", "Google Chrome"]).catch(() => {});
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
      result: { cookies: 0, bookmarks: 0, history: 0, storage: [] },
    };
    await writeFile(this.statePath, `${JSON.stringify(this.state, null, 2)}\n`, { mode: 0o600 });
    return this.state;
  }

  partitionPath(instanceId) {
    return join(this.dataRoot, "Partitions", `browser-lite-${safeInstanceId(instanceId)}`);
  }

  async prepareInstance(instanceId) {
    if (!this.isComplete()) throw new Error("请先完成 Browser Lite 安装");
    const destination = this.partitionPath(instanceId);
    const marker = join(destination, ".browser-lite-seeded");
    if (await pathExists(marker)) return;
    await rm(destination, { recursive: true, force: true });
    await mkdir(destination, { recursive: true, mode: 0o700 });
    const storageRoot = join(this.seedRoot, "Storage");
    for (const item of CHROME_STORAGE_ITEMS) {
      const source = join(storageRoot, item);
      if (!await pathExists(source)) continue;
      await cp(source, join(destination, item), { recursive: true, preserveTimestamps: true });
    }
    const cookies = await this.loadCookieSeed().catch(() => []);
    const partition = `persist:browser-lite-${safeInstanceId(instanceId)}`;
    const targetSession = session.fromPartition(partition);
    let importedCookies = 0;
    for (const cookie of cookies) {
      try {
        await targetSession.cookies.set(cookie);
        importedCookies += 1;
      } catch {}
    }
    await targetSession.cookies.flushStore();
    targetSession.flushStorageData();
    await writeFile(marker, `${JSON.stringify({ importedAt: new Date().toISOString(), cookies: importedCookies })}\n`, { mode: 0o600 });
  }

  async metadata() {
    return await readJson(this.seedMetadataPath, {});
  }

  async startUrl() {
    const metadata = await this.metadata();
    const bookmarks = (metadata.bookmarks ?? []).slice(0, 24);
    const history = (metadata.history ?? []).slice(0, 12);
    const sourceLine = this.state?.mode === "imported"
      ? `已从 ${escapeHtml(this.state.source?.browser)} · ${escapeHtml(this.state.source?.profileDirectory)} 导入浏览状态`
      : "Browser Lite 已就绪";
    const bookmarkHtml = bookmarks.length
      ? bookmarks.map((item) => `<a class="item" href="${escapeHtml(item.url)}"><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.url)}</span></a>`).join("")
      : '<p class="empty">没有导入书签。Browser Pilot 导航后，这个实例会持续保留登录状态。</p>';
    const historyHtml = history.map((item) => `<a class="history" href="${escapeHtml(item.url)}">${escapeHtml(item.title)}</a>`).join("");
    const html = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Browser Lite</title><style>
      :root{color-scheme:light dark;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display",sans-serif;background:#0b1018;color:#eef4ff}*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(circle at 80% -10%,#5f49ca55,transparent 42%),#0b1018}.wrap{width:min(980px,calc(100% - 48px));margin:auto;padding:64px 0}h1{font-size:42px;margin:0 0 8px}.sub{color:#91a0b7;margin:0 0 28px}.search{display:flex;margin-bottom:34px}.search input{flex:1;border:1px solid #ffffff24;border-radius:14px 0 0 14px;padding:14px 16px;background:#111927;color:#fff;font-size:16px}.search button{border:0;border-radius:0 14px 14px 0;padding:0 20px;background:#65d8ff;color:#071018;font-weight:700}.section{margin-top:30px}h2{font-size:15px;color:#9fb0c8}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.item,.history{display:block;text-decoration:none;color:#edf4ff;border:1px solid #ffffff1c;background:#131b28cc;border-radius:14px;padding:14px}.item strong,.item span{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.item span{font-size:11px;color:#8190a8;margin-top:6px}.history{padding:10px 13px;margin-bottom:7px}.empty{color:#8190a8}@media(max-width:760px){.grid{grid-template-columns:1fr 1fr}}</style></head><body><main class="wrap"><h1>Browser Lite</h1><p class="sub">${sourceLine}</p><form class="search" action="https://www.google.com/search"><input name="q" placeholder="搜索 Google"><button>搜索</button></form><section class="section"><h2>书签</h2><div class="grid">${bookmarkHtml}</div></section>${historyHtml ? `<section class="section"><h2>最近历史</h2>${historyHtml}</section>` : ""}</main></body></html>`;
    return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
  }

  async resetData({ preservePairing = true } = {}) {
    const preserved = new Set([
      "installation-key.bin",
      "chrome-safe-storage.enc.json",
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
