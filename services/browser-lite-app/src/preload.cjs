const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("browserLite", Object.freeze({
  getState: () => ipcRenderer.invoke("browser-lite:get-state"),
  pair: (payload) => ipcRenderer.invoke("browser-lite:pair", payload),
  unpair: () => ipcRenderer.invoke("browser-lite:unpair"),
  openInstance: (instanceId) => ipcRenderer.invoke("browser-lite:open-instance", instanceId),
  showSettings: () => ipcRenderer.invoke("browser-lite:show-settings"),
  pauseInstance: (instanceId) => ipcRenderer.invoke("browser-lite:pause-instance", instanceId),
  stopInstance: (instanceId) => ipcRenderer.invoke("browser-lite:stop-instance", instanceId),
  removeInstance: (instanceId) => ipcRenderer.invoke("browser-lite:remove-instance", instanceId),
  onState: (callback) => {
    const listener = (_event, state) => callback(state);
    ipcRenderer.on("browser-lite:state", listener);
    return () => ipcRenderer.removeListener("browser-lite:state", listener);
  },
}));
