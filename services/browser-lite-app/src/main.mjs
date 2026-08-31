import { app, BrowserWindow, globalShortcut, ipcMain, Menu, nativeImage, shell, Tray } from "electron";
import { mkdirSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { BrowserLiteManager } from "./electron-runtime.mjs";
import { BrowserLiteInstallation } from "./installation.mjs";
import { BrowserLiteNodeAgent } from "./node-agent.mjs";
import { BrowserLiteTaskSpaceManager } from "./task-space-manager.mjs";

const SOURCE_DIR = fileURLToPath(new URL(".", import.meta.url));
let dashboardWindow = null;
let tray = null;
let manager;
let nodeAgent;
let installation;
let taskSpaces;
let nodeAgentLoaded = false;
let shutdownStarted = false;
let shutdownComplete = false;
let bootstrapReady = false;
let resolveBootstrapReady;
const bootstrapReadyPromise = new Promise((resolveReady) => { resolveBootstrapReady = resolveReady; });
const isTestBuild = process.env.BROWSER_LITE_TEST_BUILD === "1";
const startupTraceEnabled = process.env.BROWSER_LITE_STARTUP_TRACE === "1";
const startupTraceStartedAt = Date.now();
let publicStateSequence = 0;
let pendingAuthCallback = authCallbackOptions(process.argv).authCallback;

function traceStartup(phase, details = "") {
  if (!startupTraceEnabled) return;
  console.log(`[startup +${Date.now() - startupTraceStartedAt}ms] ${phase}${details ? ` ${details}` : ""}`);
}

// Ad-hoc signed test builds must stay unattended across upgrades. Chromium's
// own cookie encryption otherwise asks macOS Keychain to trust each new ad-hoc
// code identity. Developer ID release builds do not set this environment flag.
if (isTestBuild) app.commandLine.appendSwitch("use-mock-keychain");
app.setName("Browser Lite");
if (app.isPackaged) app.setAsDefaultProtocolClient("browserlite");
const testUserDataPath = isTestBuild
  ? process.env.BROWSER_LITE_TEST_USER_DATA_DIR
  : "";
app.setPath("userData", testUserDataPath || join(app.getPath("appData"), "Browser Lite"));

const PAIRING_REQUEST_PATH = join(app.getPath("userData"), "pairing-request.json");
const initialPairingOptions = pairingOptions(process.argv);
stagePairingRequest(initialPairingOptions);
const hasSingleInstanceLock = app.requestSingleInstanceLock({
  ...initialPairingOptions,
  ...authCallbackOptions(process.argv),
});
if (!hasSingleInstanceLock) app.quit();

function emitState() {
  if (!bootstrapReady) return;
  if (dashboardWindow && !dashboardWindow.isDestroyed()) {
    void getPublicState()
      .then((state) => dashboardWindow.webContents.send("browser-lite:state", state))
      .catch((error) => console.error("Browser Lite failed to publish state", error));
  }
}

function pairingOptions(argv = [], additionalData = {}) {
  const option = (name) => {
    const index = argv.indexOf(name);
    return index >= 0 ? argv[index + 1] || "" : "";
  };
  const data = additionalData && typeof additionalData === "object" ? additionalData : {};
  return {
    serverUrl: String(data.serverUrl || option("--pair-server") || ""),
    pairingCode: String(data.pairingCode || option("--pairing-code") || ""),
  };
}

function authCallbackOptions(argv = [], additionalData = {}) {
  const data = additionalData && typeof additionalData === "object" ? additionalData : {};
  const authCallback = String(data.authCallback || argv.find((value) => String(value).startsWith("browserlite://auth/callback")) || "");
  return authCallback.startsWith("browserlite://auth/callback") ? { authCallback } : {};
}

function validPairingOptions(options = {}) {
  return Boolean(options.serverUrl && /^\d{10}$/.test(options.pairingCode));
}

function stagePairingRequest(options) {
  if (!validPairingOptions(options)) return;
  mkdirSync(app.getPath("userData"), { recursive: true, mode: 0o700 });
  writeFileSync(PAIRING_REQUEST_PATH, `${JSON.stringify(options)}\n`, { mode: 0o600 });
}

function consumeStagedPairingRequest() {
  try {
    const options = JSON.parse(readFileSync(PAIRING_REQUEST_PATH, "utf8"));
    return validPairingOptions(options) ? options : {};
  } catch {
    return {};
  } finally {
    try { unlinkSync(PAIRING_REQUEST_PATH); } catch {}
  }
}

async function pairFromArguments(argv = [], additionalData = {}) {
  const supplied = pairingOptions(argv, additionalData);
  const options = validPairingOptions(supplied) ? supplied : consumeStagedPairingRequest();
  if (!validPairingOptions(options)) return false;
  try { unlinkSync(PAIRING_REQUEST_PATH); } catch {}
  await nodeAgent.pair(options.serverUrl, options.pairingCode);
  emitState();
  return true;
}

async function startNodeAgent() {
  if (nodeAgentLoaded) return;
  await nodeAgent.load();
  nodeAgentLoaded = true;
  if (!await pairFromArguments(process.argv)) await pairFromArguments([]);
}

async function completeBrowserPilotLogin(callbackUrl) {
  pendingAuthCallback = "";
  await nodeAgent.completeLogin(callbackUrl);
  createDashboardWindow();
  await manager.showSettings();
  app.focus({ steal: true });
  dashboardWindow.show();
  dashboardWindow.focus();
  emitState();
}

function receiveAuthCallback(callbackUrl) {
  if (!callbackUrl?.startsWith("browserlite://auth/callback")) return;
  pendingAuthCallback = callbackUrl;
  if (!bootstrapReady || !nodeAgentLoaded) return;
  void completeBrowserPilotLogin(callbackUrl).catch((error) => {
    console.error("Browser Lite login callback failed", error);
    requestMainWindow();
    emitState();
  });
}

async function getPublicState() {
  const sequence = ++publicStateSequence;
  traceStartup("public-state:start", `#${sequence}`);
  const taskSpaceState = taskSpaces ? await taskSpaces.workspaceState() : { taskSpaces: [], activeTaskSpace: null };
  traceStartup("public-state:spaces", `#${sequence}`);
  const instances = manager ? await manager.list() : [];
  traceStartup("public-state:instances", `#${sequence}`);
  const installationState = installation ? await installation.publicState() : { complete: false, required: true, profiles: [] };
  traceStartup("public-state:installation", `#${sequence}`);
  return {
    app: {
      name: app.getName(),
      version: app.getVersion(),
      electronVersion: process.versions.electron,
      chromiumVersion: process.versions.chrome,
      packaged: app.isPackaged,
    },
    node: nodeAgent?.publicState() ?? null,
    instances,
    taskSpaces: taskSpaceState,
    workspace: manager?.workspaceState() ?? { mode: "spaces", activeInstanceId: null, browserAvailable: false },
    installation: installationState,
  };
}

function createDashboardWindow() {
  if (dashboardWindow && !dashboardWindow.isDestroyed()) {
    app.focus({ steal: true });
    dashboardWindow.show();
    dashboardWindow.focus();
    return dashboardWindow;
  }
  dashboardWindow = new BrowserWindow({
    title: "Browser Lite",
    width: 1280,
    height: 800,
    minWidth: 760,
    minHeight: 560,
    backgroundColor: "#ffffff",
    titleBarStyle: "hiddenInset",
    webPreferences: {
      preload: join(SOURCE_DIR, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  void dashboardWindow.loadFile(join(SOURCE_DIR, "renderer", "index.html"));
  dashboardWindow.on("resize", () => manager?.layout());
  dashboardWindow.on("close", (event) => {
    if (!app.isQuitting) {
      event.preventDefault();
      dashboardWindow.hide();
    }
  });
  return dashboardWindow;
}

async function showMainWindow() {
  createDashboardWindow();
  const workspace = manager?.workspaceState();
  if (workspace?.browserAvailable) {
    await manager.showInstance(workspace.activeInstanceId);
    return;
  }
  if (!manager || !taskSpaces || !installation?.isComplete()) {
    await manager?.showSettings();
    return;
  }
  await manager.showSpaces();
}

function requestMainWindow() {
  void showMainWindow().catch((error) => {
    console.error("Browser Lite failed to open its main workspace", error);
    showSettingsWindow();
  });
}

function showSettingsWindow() {
  createDashboardWindow();
  void manager?.showSettings();
}

function createTray() {
  const imagePath = join(app.isPackaged ? process.resourcesPath : join(app.getAppPath(), "assets"), "trayTemplate.png");
  let image = nativeImage.createFromPath(imagePath);
  if (image.isEmpty()) image = nativeImage.createEmpty();
  image.setTemplateImage(true);
  tray = new Tray(image);
  tray.setToolTip("Browser Lite");
  const contextMenu = Menu.buildFromTemplate([
    { label: "打开 Browser Lite", click: requestMainWindow },
    { label: "打开设置", click: showSettingsWindow },
    { type: "separator" },
    { label: "退出", click: () => { app.isQuitting = true; app.quit(); } },
  ]);
  tray.on("click", requestMainWindow);
  tray.on("right-click", () => tray.popUpContextMenu(contextMenu));
}

function registerIpc() {
  ipcMain.handle("browser-lite:get-state", async () => {
    await bootstrapReadyPromise;
    return getPublicState();
  });
  ipcMain.handle("browser-lite:install-import", async (_event, payload) => {
    const result = await installation.importProfile(payload);
    await startNodeAgent();
    await manager.showSpaces();
    emitState();
    return { result, state: await getPublicState() };
  });
  ipcMain.handle("browser-lite:install-fresh", async () => {
    await installation.freshStart();
    await startNodeAgent();
    await manager.showSpaces();
    emitState();
    return getPublicState();
  });
  ipcMain.handle("browser-lite:reset-installation", async () => {
    nodeAgent.disconnect();
    await manager.resetForReinstall();
    await installation.resetData({ preservePairing: true });
    app.relaunch();
    app.isQuitting = true;
    app.quit();
    return { restarting: true };
  });
  ipcMain.handle("browser-lite:uninstall", async () => {
    await nodeAgent.unpair();
    await manager.resetForReinstall();
    if (!isTestBuild) app.setLoginItemSettings({ openAtLogin: false, openAsHidden: false });
    const destinations = installation.scheduleRecoverableUninstall();
    app.isQuitting = true;
    app.quit();
    return destinations;
  });
  ipcMain.handle("browser-lite:pair", async (_event, payload) => {
    const state = await nodeAgent.pair(payload.serverUrl, payload.pairingCode, payload.displayName);
    emitState();
    return state;
  });
  ipcMain.handle("browser-lite:login", async (_event, payload = {}) => {
    const result = await nodeAgent.beginLogin(payload.serverUrl, payload.displayName);
    await shell.openExternal(result.authorizeUrl);
    emitState();
    return result.state;
  });
  ipcMain.handle("browser-lite:logout", async () => {
    await nodeAgent.unpair();
    emitState();
    return nodeAgent.publicState();
  });
  ipcMain.handle("browser-lite:unpair", async () => {
    await nodeAgent.unpair();
    emitState();
    return nodeAgent.publicState();
  });
  ipcMain.handle("browser-lite:open-instance", async (_event, instanceId = "browser_lite") => {
    if (!installation.isComplete()) throw new Error("请先完成 Browser Lite 安装");
    await manager.ensure(instanceId, { port: instanceId === "browser_lite" ? 4444 : 0 });
    manager.setTaskControlVisible(false);
    await manager.showInstance(instanceId);
    emitState();
    return getPublicState();
  });
  ipcMain.handle("browser-lite:show-settings", async () => {
    await manager.showSettings();
    emitState();
    return getPublicState();
  });
  ipcMain.handle("browser-lite:prepare-space-return", async (_event, id) => {
    await bootstrapReadyPromise;
    return taskSpaces.prepareSpaceReturn(id);
  });
  ipcMain.handle("browser-lite:show-spaces", async (_event, options = {}) => {
    await manager.showSpaces({
      capturePreview: options?.capturePreview !== false,
      activateWindow: options?.activateWindow !== false,
      preserveWindowGeometry: options?.preserveWindowGeometry === true,
    });
    emitState();
    return getPublicState();
  });
  ipcMain.handle("browser-lite:pause-instance", async (_event, instanceId) => {
    await manager.pause(instanceId);
    emitState();
    return getPublicState();
  });
  ipcMain.handle("browser-lite:stop-instance", async (_event, instanceId) => {
    await manager.stop(instanceId);
    emitState();
    return getPublicState();
  });
  ipcMain.handle("browser-lite:remove-instance", async (_event, instanceId) => {
    await manager.remove(instanceId);
    emitState();
    return getPublicState();
  });
  ipcMain.handle("browser-lite:create-task-space", async (_event, name) => {
    const created = await taskSpaces.createUserTaskSpace(name || "新任务空间");
    await taskSpaces.openForUser(created.id);
    emitState();
    return getPublicState();
  });
  ipcMain.handle("browser-lite:open-task-space", async (_event, id) => {
    await taskSpaces.openForUser(id);
    emitState();
    return getPublicState();
  });
  ipcMain.handle("browser-lite:prepare-task-space-open", async (_event, id) => {
    await taskSpaces.openForUser(id, { reveal: false });
    return { prepared: true };
  });
  ipcMain.handle("browser-lite:commit-task-space-open", async (_event, id) => {
    await taskSpaces.commitOpenForUser(id);
    return getPublicState();
  });
  ipcMain.handle("browser-lite:reveal-task-space", async (_event, id) => {
    await taskSpaces.revealForUser(id);
    emitState();
    return { revealed: true };
  });
  ipcMain.handle("browser-lite:return-task-space", async (_event, id) => {
    await taskSpaces.returnControlToAgent(id);
    emitState();
    return getPublicState();
  });
  ipcMain.handle("browser-lite:close-task-space", async (_event, id) => {
    await taskSpaces.closeForUser(id);
    emitState();
    return getPublicState();
  });
  ipcMain.handle("browser-lite:task-space-browser-action", async (_event, id, action, payload) => {
    await taskSpaces.userBrowserAction(id, action, payload);
    emitState();
    return getPublicState();
  });
  ipcMain.handle("browser-lite:set-chrome-menu-open", async (_event, open) => {
    manager.setChromeMenuOpen(Boolean(open));
    return { open: manager.chromeMenuOpen };
  });
}

app.on("second-instance", (_event, argv, _workingDirectory, additionalData) => {
  const callback = authCallbackOptions(argv, additionalData).authCallback;
  if (callback) receiveAuthCallback(callback);
  stagePairingRequest(pairingOptions(argv, additionalData));
  if (installation?.isComplete()) {
    void pairFromArguments(argv, additionalData).catch((error) => console.error("Browser Lite pairing failed", error));
  }
  requestMainWindow();
});
app.on("open-url", (event, url) => {
  event.preventDefault();
  receiveAuthCallback(url);
});
app.on("activate", requestMainWindow);
app.on("window-all-closed", () => {});
app.on("before-quit", (event) => {
  app.isQuitting = true;
  if (shutdownComplete) return;
  event.preventDefault();
  if (shutdownStarted) return;
  shutdownStarted = true;
  globalShortcut.unregisterAll();
  nodeAgent?.disconnect();
  Promise.resolve(manager?.shutdown()).catch((error) => {
    console.error("Browser Lite failed to stop embedded browser views cleanly", error);
  }).finally(() => {
    shutdownComplete = true;
    app.quit();
  });
});

async function bootstrap() {
  let handledAuthCallback = false;
  if (app.isPackaged && !isTestBuild) {
    app.setLoginItemSettings({ openAtLogin: true, openAsHidden: true });
  }
  registerIpc();
  createDashboardWindow();
  traceStartup("dashboard-created");
  installation = new BrowserLiteInstallation();
  await installation.load();
  traceStartup("installation-loaded");
  dashboardWindow.setTitle(installation.isComplete() ? "Browser Lite — 设置" : "Browser Lite — 安装");
  manager = new BrowserLiteManager({ hostWindow: dashboardWindow, installation, onChanged: emitState });
  taskSpaces = new BrowserLiteTaskSpaceManager(manager, {
    statePath: join(app.getPath("userData"), "task-spaces.json"),
    listProfiles: async () => {
      const installationState = await installation.publicState();
      const importedProfiles = await installation.profiles().catch(() => []);
      const imported = importedProfiles.find((profile) => profile.directory === installationState.source?.profileDirectory);
      return [{ id: "Default", isDefault: true, name: imported?.name || installationState.source?.profileName || "Browser Lite" }];
    },
    getBrowserVersion: async () => ({ currentVersion: app.getVersion(), updateAvailable: false }),
  });
  manager.getSpaceCount = () => taskSpaces?.spaces?.size || 0;
  await taskSpaces.load();
  traceStartup("task-spaces-loaded");
  nodeAgent = new BrowserLiteNodeAgent(manager, { onChanged: emitState, taskSpaces });
  createTray();
  globalShortcut.register("Alt+S", () => {
    void manager.showSpaces().then(emitState).catch((error) => console.error("Browser Lite failed to show Spaces", error));
  });
  if (installation.isComplete()) {
    await taskSpaces.startActiveSpaces();
    traceStartup("active-spaces-started");
    await startNodeAgent();
    traceStartup("node-agent-started");
    if (pendingAuthCallback) {
      await completeBrowserPilotLogin(pendingAuthCallback);
      handledAuthCallback = true;
    }
  }
  if (installation.isComplete() && !handledAuthCallback) await manager.showSpaces();
  else await manager.showSettings();
  traceStartup("workspace-shown");
  if (installation.isComplete() && app.isPackaged && !isTestBuild && app.getLoginItemSettings().wasOpenedAtLogin) {
    dashboardWindow.hide();
    app.dock?.hide();
  }
  bootstrapReady = true;
  resolveBootstrapReady();
  traceStartup("bootstrap-ready");
  emitState();
}

if (hasSingleInstanceLock) {
  app.whenReady().then(bootstrap).catch((error) => {
    console.error("Browser Lite failed to start", error);
    app.exit(1);
  });
}
