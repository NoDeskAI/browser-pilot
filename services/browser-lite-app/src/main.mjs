import { app, BrowserWindow, ipcMain, Menu, nativeImage, Tray } from "electron";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { BrowserLiteManager } from "./electron-runtime.mjs";
import { BrowserLiteNodeAgent } from "./node-agent.mjs";

const SOURCE_DIR = fileURLToPath(new URL(".", import.meta.url));
let dashboardWindow = null;
let tray = null;
let manager;
let nodeAgent;

app.setName("Browser Lite");
app.setPath("userData", join(app.getPath("appData"), "Browser Lite"));

const hasSingleInstanceLock = app.requestSingleInstanceLock();
if (!hasSingleInstanceLock) app.quit();

function emitState() {
  if (dashboardWindow && !dashboardWindow.isDestroyed()) {
    void getPublicState().then((state) => dashboardWindow.webContents.send("browser-lite:state", state));
  }
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

app.on("second-instance", () => createDashboardWindow());
app.on("window-all-closed", () => {});
app.on("before-quit", () => { app.isQuitting = true; });
app.on("will-quit", () => {
  nodeAgent?.disconnect();
  void manager?.shutdown();
});

async function bootstrap() {
  manager = new BrowserLiteManager({ onChanged: emitState });
  nodeAgent = new BrowserLiteNodeAgent(manager, { onChanged: emitState });
  registerIpc();
  createTray();
  createDashboardWindow();
  await nodeAgent.load();
  await manager.ensure("browser_lite", { port: 4444 });
  emitState();
}

app.whenReady().then(bootstrap).catch((error) => {
  console.error("Browser Lite failed to start", error);
  app.exit(1);
});
