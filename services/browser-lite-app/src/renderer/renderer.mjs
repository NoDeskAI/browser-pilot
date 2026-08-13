const elements = {
  installer: document.querySelector("#installer"),
  installerError: document.querySelector("#installer-error"),
  chromeRunning: document.querySelector("#chrome-running"),
  profileList: document.querySelector("#profile-list"),
  importLoginState: document.querySelector("#import-login-state"),
  importBookmarks: document.querySelector("#import-bookmarks"),
  importHistory: document.querySelector("#import-history"),
  installFresh: document.querySelector("#install-fresh"),
  installImport: document.querySelector("#install-import"),
  connectionPill: document.querySelector("#connection-pill"),
  pairForm: document.querySelector("#pair-form"),
  serverUrl: document.querySelector("#server-url"),
  pairingCode: document.querySelector("#pairing-code"),
  pairButton: document.querySelector("#pair-button"),
  pairedState: document.querySelector("#paired-state"),
  nodeId: document.querySelector("#node-id"),
  nodeName: document.querySelector("#node-name"),
  nodeServer: document.querySelector("#node-server"),
  nodeError: document.querySelector("#node-error"),
  unpairButton: document.querySelector("#unpair-button"),
  openDefault: document.querySelector("#open-default"),
  instances: document.querySelector("#instances"),
  appVersion: document.querySelector("#app-version"),
  chromiumVersion: document.querySelector("#chromium-version"),
  activeInstance: document.querySelector("#active-instance"),
  showSettings: document.querySelector("#show-settings"),
  resetInstallation: document.querySelector("#reset-installation"),
  uninstallApp: document.querySelector("#uninstall-app"),
  reimportBrowserData: document.querySelector("#reimport-browser-data"),
  importSource: document.querySelector("#import-source"),
  importedCookies: document.querySelector("#imported-cookies"),
  importedBookmarks: document.querySelector("#imported-bookmarks"),
  importedHistory: document.querySelector("#imported-history"),
};

let currentState = null;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
}

function render(state) {
  currentState = state;
  const installation = state.installation || {};
  const installMode = installation.required === true;
  document.body.classList.toggle("install-mode", installMode);
  elements.installer.classList.toggle("hidden", !installMode);
  elements.chromeRunning.classList.toggle("hidden", !installation.chromeRunning);
  elements.installerError.textContent = installation.lastError || "";
  if (installMode) {
    const profiles = installation.profiles || [];
    elements.profileList.innerHTML = profiles.length ? profiles.map((profile, index) => `
      <label class="profile-option">
        <input type="radio" name="profile" value="${escapeHtml(profile.id)}" ${index === 0 ? "checked" : ""} />
        <span class="profile-name"><strong>${escapeHtml(profile.name)}</strong><small>${escapeHtml(profile.email || `${profile.browser} · ${profile.directory}`)}</small></span>
        <span class="profile-counts">${escapeHtml(profile.counts.cookies)} Cookie · ${escapeHtml(profile.counts.bookmarks)} 书签<br>${escapeHtml(profile.counts.history)} 历史 · ${escapeHtml(profile.counts.extensions)} 扩展</span>
      </label>
    `).join("") : '<div class="empty">没有发现可导入的 Chrome Profile，你可以选择“全新开始”。</div>';
    elements.installImport.disabled = installation.busy || profiles.length === 0;
    elements.installFresh.disabled = installation.busy;
  }
  const importResult = installation.result || {};
  elements.importSource.textContent = installation.mode === "imported"
    ? `已从 ${installation.source?.browser || "浏览器"} · ${installation.source?.profileDirectory || ""} 导入，本机独立保存。`
    : "当前使用全新的 Browser Lite Profile。";
  elements.importedCookies.textContent = importResult.cookies || 0;
  elements.importedBookmarks.textContent = importResult.bookmarks || 0;
  elements.importedHistory.textContent = importResult.history || 0;
  document.body.classList.remove("booting");
  const workspace = state.workspace || {};
  document.body.classList.toggle("browser-mode", workspace.mode === "browser");
  elements.activeInstance.textContent = workspace.activeInstanceId || "";
  const node = state.node || {};
  elements.appVersion.textContent = state.app?.version ? `v${state.app.version}` : "—";
  elements.chromiumVersion.textContent = state.app?.chromiumVersion || "—";
  elements.pairForm.classList.toggle("hidden", Boolean(node.paired));
  elements.pairedState.classList.toggle("hidden", !node.paired);
  elements.nodeId.textContent = node.nodeId || "";
  elements.nodeName.textContent = node.displayName || "Browser Lite node";
  elements.nodeServer.textContent = node.serverUrl || "";
  elements.nodeError.textContent = node.lastError || "";

  elements.connectionPill.className = "pill";
  if (node.connected) {
    elements.connectionPill.classList.add("online");
    elements.connectionPill.textContent = "节点在线";
  } else if (node.connecting) {
    elements.connectionPill.classList.add("connecting");
    elements.connectionPill.textContent = "正在连接";
  } else {
    elements.connectionPill.classList.add("offline");
    elements.connectionPill.textContent = node.paired ? "节点离线" : "未配对";
  }

  const instances = state.instances || [];
  if (instances.length === 0) {
    elements.instances.innerHTML = '<div class="empty">当前没有运行实例</div>';
  } else {
    elements.instances.innerHTML = instances.map((instance) => `
      <article class="instance" data-instance="${escapeHtml(instance.id)}">
        <div>
          <div class="instance-title"><span class="dot"></span>${escapeHtml(instance.id)}</div>
          <div class="instance-meta">${escapeHtml(instance.tabCount)} 个页面 · Chromium ${escapeHtml(instance.chromiumVersion)} · ${instance.paused ? "已暂停" : "运行中"}</div>
        </div>
        <div class="instance-actions">
          <button class="secondary" data-action="open">打开</button>
          <button class="secondary" data-action="pause">隐藏</button>
          <button class="secondary" data-action="stop">停止</button>
        </div>
      </article>
    `).join("");
  }
}

async function refresh() {
  render(await window.browserLite.getState());
}

elements.pairForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  elements.pairButton.disabled = true;
  elements.pairButton.textContent = "正在配对…";
  try {
    await window.browserLite.pair({
      serverUrl: elements.serverUrl.value,
      pairingCode: elements.pairingCode.value,
    });
    elements.pairingCode.value = "";
    await refresh();
  } catch (error) {
    window.alert(error.message || String(error));
  } finally {
    elements.pairButton.disabled = false;
    elements.pairButton.textContent = "配对节点";
  }
});

elements.unpairButton.addEventListener("click", async () => {
  if (!window.confirm("解除此 Mac 与 Browser Pilot 的配对？")) return;
  await window.browserLite.unpair();
  await refresh();
});

elements.openDefault.addEventListener("click", async () => {
  await window.browserLite.openInstance("browser_lite");
  await refresh();
});

elements.instances.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  const card = event.target.closest("[data-instance]");
  if (!button || !card) return;
  const instanceId = card.dataset.instance;
  if (button.dataset.action === "open") await window.browserLite.openInstance(instanceId);
  else if (button.dataset.action === "pause") await window.browserLite.pauseInstance(instanceId);
  else if (button.dataset.action === "stop") await window.browserLite.stopInstance(instanceId);
  await refresh();
});

elements.showSettings.addEventListener("click", async () => {
  await window.browserLite.showSettings();
  await refresh();
});

elements.installImport.addEventListener("click", async () => {
  const profile = document.querySelector('input[name="profile"]:checked');
  if (!profile) return;
  elements.installerError.textContent = "";
  elements.installImport.disabled = true;
  elements.installFresh.disabled = true;
  elements.installImport.textContent = "正在导入…";
  try {
    await window.browserLite.installImport({
      profileId: profile.value,
      loginState: elements.importLoginState.checked,
      bookmarks: elements.importBookmarks.checked,
      history: elements.importHistory.checked,
    });
    await refresh();
  } catch (error) {
    elements.installerError.textContent = error.message || String(error);
  } finally {
    elements.installImport.textContent = "导入并开始使用";
    elements.installImport.disabled = false;
    elements.installFresh.disabled = false;
  }
});

elements.installFresh.addEventListener("click", async () => {
  if (!window.confirm("确定不导入 Chrome，使用全新的 Browser Lite？")) return;
  await window.browserLite.installFresh();
  await refresh();
});

elements.resetInstallation.addEventListener("click", async () => {
  if (!window.confirm("重置会清空 Browser Lite 中的浏览与导入数据，并重新打开安装向导。Browser Pilot 配对会保留。继续吗？")) return;
  elements.resetInstallation.disabled = true;
  await window.browserLite.resetInstallation();
});

elements.reimportBrowserData.addEventListener("click", async () => {
  if (!window.confirm("重新导入会清空 Browser Lite 当前浏览数据，保留 Browser Pilot 配对，然后重启安装向导。继续吗？")) return;
  elements.reimportBrowserData.disabled = true;
  await window.browserLite.resetInstallation();
});

elements.uninstallApp.addEventListener("click", async () => {
  if (!window.confirm("彻底卸载 Browser Lite？应用与本地数据会移动到废纸篓，Browser Pilot 节点会解除配对。")) return;
  elements.uninstallApp.disabled = true;
  try {
    await window.browserLite.uninstall();
  } catch (error) {
    window.alert(error.message || String(error));
    elements.uninstallApp.disabled = false;
  }
});

window.browserLite.onState(render);
await refresh();
