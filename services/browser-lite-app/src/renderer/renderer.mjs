const elements = Object.fromEntries([
  "installer", "installer-error", "chrome-running", "profile-list", "import-login-state", "import-bookmarks",
  "import-history", "import-extensions", "install-fresh", "install-import", "spaces-title", "overview-space-count", "task-spaces",
  "connection-pill", "pair-form", "server-url", "pairing-code", "pair-button", "paired-state", "node-id",
  "node-name", "node-server", "node-error", "unpair-button", "app-version", "chromium-version",
  "reset-installation", "uninstall-app", "reimport-browser-data", "import-source", "imported-cookies",
  "imported-bookmarks", "imported-history", "workspace-title", "browser-tabs", "new-tab", "browser-space-count",
  "nav-back", "nav-forward", "nav-reload", "address-form", "address-input", "agent-state",
  "return-control", "take-control", "terminate-task", "task-control-bar", "task-control-name",
  "settings-search-input", "settings-profile-page", "settings-about-page", "profile-display-name",
  "browser-bookmarks", "settings-bookmarks", "settings-space-count",
].map((id) => [id.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase()), document.querySelector(`#${id}`)]));

let currentState = null;
let addressEditing = false;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
}

function ownershipLabel(space) {
  if (space.ownership === "agent") return "Agent 正在控制";
  if (space.ownership === "agentDelegatedToUser") return "你正在控制";
  return "用户空间";
}

function ownershipClass(space) {
  if (space.ownership === "agent") return "agent";
  if (space.ownership === "agentDelegatedToUser") return "delegated";
  return "user";
}

function renderInstaller(installation) {
  const installMode = installation.required === true;
  document.body.classList.toggle("install-mode", installMode);
  elements.installer.classList.toggle("hidden", !installMode);
  elements.chromeRunning.classList.toggle("hidden", !installation.chromeRunning);
  elements.installerError.textContent = installation.lastError || "";
  if (!installMode) return;
  const profiles = installation.profiles || [];
  elements.profileList.innerHTML = profiles.length ? profiles.map((profile, index) => `
    <label class="profile-option">
      <input type="radio" name="profile" value="${escapeHtml(profile.id)}" ${index === 0 ? "checked" : ""} />
      <span class="profile-name"><strong>${escapeHtml(profile.name)}</strong><small>${escapeHtml(profile.email || `${profile.browser} · ${profile.directory}`)}</small></span>
      <span class="profile-counts">${escapeHtml(profile.counts.cookies)} Cookie · ${escapeHtml(profile.counts.bookmarks)} 书签<br>${escapeHtml(profile.counts.history)} 历史 · ${escapeHtml(profile.counts.extensions)} 扩展</span>
    </label>
  `).join("") : '<div class="empty-space">没有发现可导入的 ego lite 或 Chrome Profile，你可以全新开始。</div>';
  elements.installImport.disabled = installation.busy || profiles.length === 0;
  elements.installFresh.disabled = installation.busy;
}

function safePreview(space) {
  const value = String(space.previewDataUrl || "");
  return /^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(value) ? value : "";
}

function renderBookmarks(installation) {
  const bookmarks = installation.bookmarks || [];
  const markup = [
    '<span class="bookmark-chip"><span aria-hidden="true">▦</span>应用</span>',
    ...bookmarks.map((bookmark) => `<span class="bookmark-chip" title="${escapeHtml(bookmark.url)}"><span class="bookmark-dot" aria-hidden="true"></span>${escapeHtml(bookmark.name)}</span>`),
  ].join("");
  elements.browserBookmarks.innerHTML = markup;
  elements.settingsBookmarks.innerHTML = markup;
}

function renderTaskSpaces(taskSpaceState) {
  const spaces = taskSpaceState?.taskSpaces || [];
  elements.spacesTitle.innerHTML = `${spaces.length} ${spaces.length === 1 ? "Space" : "Spaces"} <span aria-hidden="true">⌄</span>`;
  for (const countButton of [elements.overviewSpaceCount, elements.browserSpaceCount, elements.settingsSpaceCount]) {
    countButton.textContent = String(spaces.length);
    countButton.setAttribute("aria-label", `返回 Space 总览，共 ${spaces.length} 个 Space`);
  }
  const cards = spaces.map((space) => {
    const preview = safePreview(space);
    const status = space.ownership === "agent" && space.status === "active"
      ? "运行中"
      : space.ownership === "agentDelegatedToUser" ? "需要你处理" : "";
    const previewMarkup = preview
      ? `<img src="${preview}" alt="${escapeHtml(space.name)} 当前页面预览" />`
      : `<span class="preview-placeholder" aria-hidden="true"></span>`;
    return `<article class="space-card ${space.selected ? "selected" : ""} ${ownershipClass(space)}" data-space-id="${space.id}">
      <button class="space-preview" data-action="open-space" type="button" aria-label="打开 ${escapeHtml(space.name)}">${previewMarkup}</button>
      <button class="card-close" data-action="close-space" type="button" aria-label="关闭 ${escapeHtml(space.name)}">×</button>
      <div class="space-meta">
        <div class="space-name-row">${status ? `<span class="space-status ${ownershipClass(space)}"><i></i>${escapeHtml(status)}</span>` : ""}<strong>${escapeHtml(space.name)}</strong></div>
        <span class="space-profile">${escapeHtml(space.profileName || "Browser Lite")}</span>
      </div>
    </article>`;
  });
  cards.push(`<button class="create-space-tile" data-action="create-space" type="button" aria-label="新建 Space"><span aria-hidden="true">＋</span></button>`);
  elements.taskSpaces.innerHTML = cards.join("");
}

function renderBrowser(taskSpaceState) {
  const space = taskSpaceState?.activeTaskSpace || null;
  if (!space) {
    elements.taskControlBar.classList.add("hidden");
    return;
  }
  elements.workspaceTitle.textContent = space.name;
  const taskControlled = space.ownership !== "user";
  elements.taskControlBar.classList.toggle("hidden", !taskControlled);
  elements.taskControlName.textContent = space.name;
  elements.agentState.textContent = taskControlled ? ownershipLabel(space) : "";
  elements.returnControl.classList.toggle("hidden", space.ownership !== "agentDelegatedToUser");
  elements.takeControl.classList.toggle("hidden", space.ownership !== "agent");
  const navigation = space.navigation || {};
  if (!addressEditing) elements.addressInput.value = navigation.url || "";
  elements.navBack.disabled = !navigation.canGoBack;
  elements.navForward.disabled = !navigation.canGoForward;
  elements.navReload.textContent = navigation.loading ? "×" : "↻";
  elements.browserTabs.innerHTML = (space.tabs || []).map((tab) => `
    <div class="browser-tab ${tab.active ? "active" : ""}" role="tab" aria-selected="${tab.active}" data-target-id="${escapeHtml(tab.targetId)}">
      <button data-action="activate-tab" type="button" title="${escapeHtml(tab.url)}"><span class="tab-favicon">e</span><span>${escapeHtml(tab.title || "新标签页")}</span></button>
      <button data-action="close-tab" type="button" aria-label="关闭标签页">×</button>
    </div>
  `).join("");
}

function renderSettings(state) {
  const installation = state.installation || {};
  renderBookmarks(installation);
  const importResult = installation.result || {};
  elements.importSource.textContent = installation.mode === "imported"
    ? `来自 ${installation.source?.browser || "浏览器"} · ${installation.source?.profileDirectory || ""}`
    : "本机独立 Profile";
  elements.profileDisplayName.textContent = installation.mode === "imported"
    ? (installation.source?.profileName || state.taskSpaces?.taskSpaces?.[0]?.profileName || installation.source?.profileDirectory || "Browser Lite")
    : "Browser Lite";
  elements.importedCookies.textContent = importResult.cookies || 0;
  elements.importedBookmarks.textContent = importResult.bookmarks || 0;
  elements.importedHistory.textContent = importResult.history || 0;
  elements.appVersion.textContent = state.app?.version ? `v${state.app.version}` : "—";
  elements.chromiumVersion.textContent = state.app?.chromiumVersion || "—";

  const node = state.node || {};
  elements.pairForm.classList.toggle("hidden", Boolean(node.paired));
  elements.pairedState.classList.toggle("hidden", !node.paired);
  elements.nodeId.textContent = node.nodeId || "";
  elements.nodeName.textContent = node.displayName || "Browser Lite node";
  elements.nodeServer.textContent = node.serverUrl || "";
  elements.nodeError.textContent = node.lastError || "";
  elements.connectionPill.className = "";
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
}

function render(state) {
  currentState = state;
  const installation = state.installation || {};
  const inSpacesOverview = state.workspace?.mode === "spaces";
  renderInstaller(installation);
  document.body.classList.remove("booting", "spaces-mode", "browser-mode", "settings-mode");
  if (!installation.required) document.body.classList.add(`${state.workspace?.mode || "spaces"}-mode`);
  renderTaskSpaces(state.taskSpaces);
  elements.overviewSpaceCount.disabled = inSpacesOverview;
  elements.overviewSpaceCount.title = inSpacesOverview ? "当前位于 Space 总览" : "返回 Space 总览";
  elements.overviewSpaceCount.setAttribute("aria-current", inSpacesOverview ? "page" : "false");
  renderBrowser(state.taskSpaces);
  renderSettings(state);
}

async function refresh() {
  render(await window.browserLite.getState());
}

async function run(button, label, operation) {
  const previous = button?.textContent;
  if (button) {
    button.disabled = true;
    if (label) button.textContent = label;
  }
  try {
    render(await operation());
  } catch (error) {
    window.alert(error.message || String(error));
  } finally {
    if (button) {
      button.disabled = false;
      if (label) button.textContent = previous;
    }
  }
}

elements.taskSpaces.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  if (button.dataset.action === "create-space") {
    const number = (currentState?.taskSpaces?.taskSpaces?.length || 0) + 1;
    await run(button, null, () => window.browserLite.createTaskSpace(`Space ${number}`));
    return;
  }
  const card = event.target.closest("[data-space-id]");
  if (!card) return;
  const id = Number(card.dataset.spaceId);
  if (button.dataset.action === "open-space") await run(button, null, () => window.browserLite.openTaskSpace(id));
  if (button.dataset.action === "close-space" && window.confirm("关闭这个 Space？浏览数据会保留。")) {
    await run(button, null, () => window.browserLite.closeTaskSpace(id));
  }
});

elements.browserTabs.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  const tab = event.target.closest("[data-target-id]");
  const space = currentState?.taskSpaces?.activeTaskSpace;
  if (!button || !tab || !space) return;
  await run(button, null, () => window.browserLite.taskSpaceBrowserAction(space.id, button.dataset.action === "close-tab" ? "closeTab" : "activateTab", tab.dataset.targetId));
});

elements.addressInput.addEventListener("focus", () => { addressEditing = true; elements.addressInput.select(); });
elements.addressInput.addEventListener("blur", () => { addressEditing = false; });
elements.addressForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  addressEditing = false;
  const space = currentState?.taskSpaces?.activeTaskSpace;
  if (space) await run(null, null, () => window.browserLite.taskSpaceBrowserAction(space.id, "navigate", elements.addressInput.value));
});

for (const [button, action] of [[elements.navBack, "back"], [elements.navForward, "forward"], [elements.navReload, "reload"], [elements.newTab, "newTab"]]) {
  button.addEventListener("click", async () => {
    const space = currentState?.taskSpaces?.activeTaskSpace;
    if (space) await run(button, null, () => window.browserLite.taskSpaceBrowserAction(space.id, action));
  });
}

elements.returnControl.addEventListener("click", async () => {
  const space = currentState?.taskSpaces?.activeTaskSpace;
  if (space) await run(elements.returnControl, "交还中…", () => window.browserLite.returnTaskSpace(space.id));
});
elements.takeControl.addEventListener("click", async () => {
  const space = currentState?.taskSpaces?.activeTaskSpace;
  if (space) await run(elements.takeControl, "接管中…", () => window.browserLite.openTaskSpace(space.id));
});
elements.terminateTask.addEventListener("click", async () => {
  const space = currentState?.taskSpaces?.activeTaskSpace;
  if (space && window.confirm("终止这个任务？浏览数据会保留。")) {
    await run(elements.terminateTask, "终止中…", () => window.browserLite.closeTaskSpace(space.id));
  }
});
for (const countButton of [elements.overviewSpaceCount, elements.browserSpaceCount, elements.settingsSpaceCount]) {
  countButton.addEventListener("click", async () => {
    if (currentState?.workspace?.mode === "spaces") return;
    await run(countButton, null, () => window.browserLite.showSpaces());
  });
}

function showSettingsPage(page) {
  const about = page === "about";
  elements.settingsProfilePage.classList.toggle("hidden", about);
  elements.settingsAboutPage.classList.toggle("hidden", !about);
  for (const button of document.querySelectorAll(".settings-nav-item[data-settings-page]")) {
    button.classList.toggle("active", button.dataset.settingsPage === (about ? "about" : "profile"));
  }
}

for (const button of document.querySelectorAll("[data-settings-page]")) {
  button.addEventListener("click", () => showSettingsPage(button.dataset.settingsPage));
}

elements.pairForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await run(elements.pairButton, "配对中…", async () => {
    await window.browserLite.pair({ serverUrl: elements.serverUrl.value, pairingCode: elements.pairingCode.value });
    elements.pairingCode.value = "";
    return window.browserLite.getState();
  });
});
elements.unpairButton.addEventListener("click", async () => {
  if (window.confirm("解除这台 Mac 与 Browser Pilot 的配对？")) await run(elements.unpairButton, null, async () => { await window.browserLite.unpair(); return window.browserLite.getState(); });
});

elements.installImport.addEventListener("click", async () => {
  const profile = document.querySelector('input[name="profile"]:checked');
  if (!profile) return;
  elements.installerError.textContent = "";
  await run(elements.installImport, "正在导入…", async () => {
    await window.browserLite.installImport({
      profileId: profile.value,
      loginState: elements.importLoginState.checked,
      bookmarks: elements.importBookmarks.checked,
      history: elements.importHistory.checked,
      extensions: elements.importExtensions.checked,
    });
    return window.browserLite.getState();
  });
});
elements.installFresh.addEventListener("click", async () => {
  if (window.confirm("确定使用全新的 Browser Lite Profile？")) await run(elements.installFresh, "正在准备…", async () => { await window.browserLite.installFresh(); return window.browserLite.getState(); });
});
elements.resetInstallation.addEventListener("click", async () => {
  if (window.confirm("重置会清空 Browser Lite 浏览数据并重新进入安装流程，配对会保留。继续吗？")) await window.browserLite.resetInstallation();
});
elements.reimportBrowserData.addEventListener("click", async () => {
  if (window.confirm("重新导入会清空当前 Browser Lite 浏览数据并重启安装流程。继续吗？")) await window.browserLite.resetInstallation();
});
elements.uninstallApp.addEventListener("click", async () => {
  if (window.confirm("彻底卸载 Browser Lite，并把应用和数据移动到废纸篓？")) await window.browserLite.uninstall();
});

elements.settingsSearchInput.addEventListener("input", () => {
  const query = elements.settingsSearchInput.value.trim().toLocaleLowerCase();
  for (const section of document.querySelectorAll(".settings-subpage > h2, .settings-subpage > .settings-card")) {
    section.classList.toggle("search-hidden", Boolean(query) && !section.textContent.toLocaleLowerCase().includes(query));
  }
});

window.addEventListener("keydown", async (event) => {
  if (!event.altKey || event.key.toLocaleLowerCase() !== "s") return;
  event.preventDefault();
  const spaces = currentState?.taskSpaces?.taskSpaces || [];
  if (document.body.classList.contains("spaces-mode") && spaces.length) {
    const selectedIndex = spaces.findIndex((space) => space.selected);
    const next = spaces[(selectedIndex + 1) % spaces.length];
    await run(null, null, () => window.browserLite.openTaskSpace(next.id));
  } else {
    await run(null, null, () => window.browserLite.showSpaces());
  }
});

window.browserLite.onState(render);
await refresh();
