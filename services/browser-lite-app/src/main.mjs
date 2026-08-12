import { app, BrowserWindow, ipcMain, Menu, nativeImage, Tray } from "electron";
import { mkdirSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { BrowserLiteManager } from "./electron-runtime.mjs";
import { BrowserLiteNodeAgent } from "./node-agent.mjs";

const SOURCE_DIR = fileURLToPath(new URL(".", import.meta.url));
let dashboardWindow = null;
let tray = null;
let manager;
let nodeAgent;

// Ad-hoc signed test builds must stay unattended across upgrades. Chromium's
// own cookie encryption otherwise asks macOS Keychain to trust each new ad-hoc
// code identity. Developer ID release builds do not set this environment flag.
if (process.env.BROWSER_LITE_TEST_BUILD === "1") app.commandLine.appendSwitch("use-mock-keychain");
app.setName("Browser Lite");
app.setPath("userData", join(app.getPath("appData"), "Browser Lite"));

const PAIRING_REQUEST_PATH = join(app.getPath("userData"), "pairing-request.json");
const initialPairingOptions = pairingOptions(process.argv);
stagePairingRequest(initialPairingOptions);
const hasSingleInstanceLock = app.requestSingleInstanceLock(initialPairingOptions);
if (!hasSingleInstanceLock) app.quit();

function emitState() {
  if (dashboardWindow && !dashboardWindow.isDestroyed()) {
    void getPublicState().then((state) => dashboardWindow.webContents.send("browser-lite:state", state));
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

async function getPublicState() {
  return {
    app: {
      name: app.getName(),
      version: app.getVersion(),
      electronVersion: process.versions.electron,
      chromiumVersion: process.versions.chrome,
      packaged: app.isPackaged,
    },
    node: nodeAgent?.publicState() ?? null,
    instances: manager ? await manager.list() : [],
  };
}

function createDashboardWindow() {
  if (dashboardWindow && !dashboardWindow.isDestroyed()) {
    dashboardWindow.show();
    dashboardWindow.focus();
    return dashboardWindow;
  }
  dashboardWindow = new BrowserWindow({
    title: "Browser Lite",
    width: 960,
    height: 680,
    minWidth: 760,
    minHeight: 560,
    backgroundColor: "#0d1117",
    titleBarStyle: "hiddenInset",
    webPreferences: {
      preload: join(SOURCE_DIR, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  void dashboardWindow.loadFile(join(SOURCE_DIR, "renderer", "index.html"));
  dashboardWindow.on("close", (event) => {
    if (!app.isQuitting) {
      event.preventDefault();
      dashboardWindow.hide();
    }
  });
  return dashboardWindow;
}

function createTray() {
  const imagePath = join(app.isPackaged ? process.resourcesPath : join(app.getAppPath(), "assets"), "trayTemplate.png");
  let image = nativeImage.createFromPath(imagePath);
  if (image.isEmpty()) image = nativeImage.createEmpty();
  image.setTemplateImage(true);
  tray = new Tray(image);
  tray.setToolTip("Browser Lite");
  const updateMenu = () => {
    tray.setContextMenu(Menu.buildFromTemplate([
      { label: "打开 Browser Lite", click: () => createDashboardWindow() },
      { type: "separator" },
      { label: "退出", click: () => { app.isQuitting = true; app.quit(); } },
    ]));
  };
  updateMenu();
  tray.on("click", () => createDashboardWindow());
}

function registerIpc() {
  ipcMain.handle("browser-lite:get-state", () => getPublicState());
  ipcMain.handle("browser-lite:pair", async (_event, payload) => {
    const state = await nodeAgent.pair(payload.serverUrl, payload.pairingCode, payload.displayName);
    emitState();
    return state;
  });
  ipcMain.handle("browser-lite:unpair", async () => {
    await nodeAgent.unpair();
    emitState();
    return nodeAgent.publicState();
  });
  ipcMain.handle("browser-lite:open-instance", async (_event, instanceId = "browser_lite") => {
    await manager.ensure(instanceId, { port: instanceId === "browser_lite" ? 4444 : 0 });
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
}

app.on("second-instance", (_event, argv, _workingDirectory, additionalData) => {
  void pairFromArguments(argv, additionalData).catch((error) => console.error("Browser Lite pairing failed", error));
  createDashboardWindow();
});
app.on("window-all-closed", () => {});
app.on("before-quit", () => { app.isQuitting = true; });
app.on("will-quit", () => {
  nodeAgent?.disconnect();
  void manager?.shutdown();
});

async function bootstrap() {
  if (app.isPackaged) app.setLoginItemSettings({ openAtLogin: true, openAsHidden: true });
  manager = new BrowserLiteManager({ onChanged: emitState });
  nodeAgent = new BrowserLiteNodeAgent(manager, { onChanged: emitState });
  registerIpc();
  createTray();
  await nodeAgent.load();
  await pairFromArguments(process.argv);
  await manager.ensure("browser_lite", { port: 4444 });
  if (!app.isPackaged || !app.getLoginItemSettings().wasOpenedAtLogin) createDashboardWindow();
  emitState();
}

if (hasSingleInstanceLock) {
  app.whenReady().then(bootstrap).catch((error) => {
    console.error("Browser Lite failed to start", error);
    app.exit(1);
  });
}
