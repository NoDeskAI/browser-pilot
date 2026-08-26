const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("browserLite", Object.freeze({
  getState: () => ipcRenderer.invoke("browser-lite:get-state"),
  installImport: (payload) => ipcRenderer.invoke("browser-lite:install-import", payload),
  installFresh: () => ipcRenderer.invoke("browser-lite:install-fresh"),
  resetInstallation: () => ipcRenderer.invoke("browser-lite:reset-installation"),
  uninstall: () => ipcRenderer.invoke("browser-lite:uninstall"),
  pair: (payload) => ipcRenderer.invoke("browser-lite:pair", payload),
  unpair: () => ipcRenderer.invoke("browser-lite:unpair"),
  openInstance: (instanceId) => ipcRenderer.invoke("browser-lite:open-instance", instanceId),
  showSettings: () => ipcRenderer.invoke("browser-lite:show-settings"),
  showSpaces: () => ipcRenderer.invoke("browser-lite:show-spaces"),
  pauseInstance: (instanceId) => ipcRenderer.invoke("browser-lite:pause-instance", instanceId),
  stopInstance: (instanceId) => ipcRenderer.invoke("browser-lite:stop-instance", instanceId),
  removeInstance: (instanceId) => ipcRenderer.invoke("browser-lite:remove-instance", instanceId),
  createTaskSpace: (name) => ipcRenderer.invoke("browser-lite:create-task-space", name),
  openTaskSpace: (id) => ipcRenderer.invoke("browser-lite:open-task-space", id),
  returnTaskSpace: (id) => ipcRenderer.invoke("browser-lite:return-task-space", id),
  closeTaskSpace: (id) => ipcRenderer.invoke("browser-lite:close-task-space", id),
  taskSpaceBrowserAction: (id, action, payload) => ipcRenderer.invoke("browser-lite:task-space-browser-action", id, action, payload),
  onState: (callback) => {
    const listener = (_event, state) => callback(state);
    ipcRenderer.on("browser-lite:state", listener);
    return () => ipcRenderer.removeListener("browser-lite:state", listener);
  },
}));
