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
  "browser-bookmarks", "settings-bookmarks", "settings-space-count", "pinned-extensions",
  "extensions-menu-button", "chrome-popover",
].map((id) => [id.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase()), document.querySelector(`#${id}`)]));

let currentState = null;
let addressEditing = false;
let popoverKind = "";

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

function safeDataImage(value) {
  const image = String(value || "");
  return /^data:image\/(?:png|jpeg|gif|webp);base64,[A-Za-z0-9+/=]+$/.test(image) ? image : "";
}

function folderIcon() {
  return `<svg class="bookmark-folder-icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M1.5 4.25c0-.69.56-1.25 1.25-1.25h3.1l1.28 1.5h6.12c.69 0 1.25.56 1.25 1.25v6.5c0 .69-.56 1.25-1.25 1.25H2.75c-.69 0-1.25-.56-1.25-1.25v-8Z" /></svg>`;
}

function savedGroupsIcon() {
  return `<svg class="saved-groups-icon" viewBox="0 0 16 16" aria-hidden="true"><rect x="1.75" y="1.75" width="4.5" height="4.5" rx="1"/><rect x="9.75" y="1.75" width="4.5" height="4.5" rx="1"/><rect x="1.75" y="9.75" width="4.5" height="4.5" rx="1"/><rect x="9.75" y="9.75" width="4.5" height="4.5" rx="1"/></svg>`;
}

function genericBookmarkIcon() {
  return `<svg class="bookmark-generic-icon" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.25"/><path d="M1.9 8h12.2M8 1.75c1.65 1.7 2.5 3.78 2.5 6.25S9.65 12.55 8 14.25C6.35 12.55 5.5 10.47 5.5 8S6.35 3.45 8 1.75Z"/></svg>`;
}

function bookmarkIcon(bookmark) {
  const image = safeDataImage(bookmark?.iconDataUrl);
  if (image) return `<img class="bookmark-favicon" src="${image}" alt="" />`;
  if (bookmark?.type === "folder") return folderIcon();
  return genericBookmarkIcon();
}

function bookmarkButton(bookmark) {
  const action = bookmark.type === "folder" ? "open-bookmark-folder" : "open-bookmark";
  const data = bookmark.type === "folder"
    ? `data-bookmark-id="${escapeHtml(bookmark.id)}"`
    : `data-bookmark-url="${escapeHtml(bookmark.url)}"`;
  return `<button class="bookmark-chip ${bookmark.type === "folder" ? "folder" : ""}" data-action="${action}" ${data} type="button" title="${escapeHtml(bookmark.name)}">${bookmarkIcon(bookmark)}<span>${escapeHtml(bookmark.name)}</span></button>`;
}

function renderBookmarks(installation, taskSpaceState) {
  const bookmarks = installation.bookmarkBar || installation.bookmarks || [];
  const groups = taskSpaceState?.activeTaskSpace?.tabGroups || [];
  const visible = bookmarks.slice(0, 14);
  const markup = [
    ...groups.map((group) => `<button class="saved-tab-group group-color-${escapeHtml(group.color)}" data-action="saved-tab-group" data-group-id="${escapeHtml(group.id)}" type="button" title="标签页分组：${escapeHtml(group.name)}"><span>${escapeHtml(group.name)}</span></button>`),
    `<button class="saved-tab-groups-menu" data-action="new-tab-group" type="button" aria-label="标签页分组" title="标签页分组">${savedGroupsIcon()}</button>`,
    '<span class="bookmark-separator" aria-hidden="true"></span>',
    ...visible.map(bookmarkButton),
    bookmarks.length > visible.length ? `<button class="bookmark-overflow" data-action="bookmark-overflow" type="button" aria-label="显示 ${bookmarks.length - visible.length} 个隐藏书签">»</button>` : "",
    '<span class="bookmark-separator bookmark-tail-separator" aria-hidden="true"></span>',
    `<button class="bookmark-all" data-action="all-bookmarks" type="button">${folderIcon()}<span>所有书签</span></button>`,
  ].join("");
  elements.browserBookmarks.innerHTML = markup;
  elements.settingsBookmarks.innerHTML = markup;
}

function renderExtensions(space, installation) {
  const extensions = space?.extensions?.length ? space.extensions : installation.extensions || [];
  const pinned = extensions.filter((extension) => extension.pinned).slice(0, 7);
  elements.pinnedExtensions.innerHTML = pinned.map((extension) => {
    const image = safeDataImage(extension.iconDataUrl);
    const icon = image
      ? `<img src="${image}" alt="" />`
      : `<span aria-hidden="true">${escapeHtml(extension.name.slice(0, 1).toLocaleUpperCase())}</span>`;
    return `<button class="extension-pin ${extension.status === "error" ? "extension-error" : ""}" data-extension-id="${escapeHtml(extension.id)}" type="button" title="${escapeHtml(extension.name)}">${icon}</button>`;
  }).join("");
  elements.extensionsMenuButton.dataset.extensionCount = String(extensions.length);
  elements.extensionsMenuButton.title = `扩展程序（${extensions.length}）`;
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

function tabMarkup(tab) {
  let favicon = "e";
  try { favicon = new URL(tab.url).hostname.slice(0, 1).toLocaleUpperCase() || "e"; } catch {}
  return `<div class="browser-tab ${tab.active ? "active" : ""}" role="tab" aria-selected="${tab.active}" data-target-id="${escapeHtml(tab.targetId)}" data-group-id="${escapeHtml(tab.groupId || "")}">
    <button data-action="activate-tab" type="button" title="${escapeHtml(tab.url)}"><span class="tab-favicon">${escapeHtml(favicon)}</span><span>${escapeHtml(tab.title || "新标签页")}</span></button>
    <button data-action="close-tab" type="button" aria-label="关闭标签页">×</button>
  </div>`;
}

function renderBrowser(taskSpaceState, installation) {
  const space = taskSpaceState?.activeTaskSpace || null;
  if (!space) {
    elements.taskControlBar.classList.add("hidden");
    elements.browserTabs.innerHTML = "";
    renderExtensions(null, installation);
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
  const tabs = space.tabs || [];
  const groups = space.tabGroups || [];
  const groupedIds = new Set();
  const groupedMarkup = groups.map((group) => {
    const groupTabs = tabs.filter((tab) => tab.groupId === group.id);
    for (const tab of groupTabs) groupedIds.add(tab.targetId);
    return `<div class="tab-group group-color-${escapeHtml(group.color)} ${group.collapsed ? "collapsed" : "expanded"}" data-tab-group-id="${escapeHtml(group.id)}">
      <button class="tab-group-pill" data-action="toggle-tab-group" data-group-id="${escapeHtml(group.id)}" type="button" aria-expanded="${!group.collapsed}" title="${group.collapsed ? "展开" : "折叠"} ${escapeHtml(group.name)}"><span>${escapeHtml(group.name)}</span></button>
      ${group.collapsed ? "" : groupTabs.map(tabMarkup).join("")}
    </div>`;
  }).join("");
  const ungroupedMarkup = tabs.filter((tab) => !groupedIds.has(tab.targetId)).map(tabMarkup).join("");
  elements.browserTabs.innerHTML = groupedMarkup + ungroupedMarkup;
  renderExtensions(space, installation);
}

function renderSettings(state) {
  const installation = state.installation || {};
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
  renderBookmarks(installation, state.taskSpaces);
  renderBrowser(state.taskSpaces, installation);
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

function activeSpace() {
  return currentState?.taskSpaces?.activeTaskSpace || null;
}

function allBookmarkNodes() {
  const installation = currentState?.installation || {};
  return [...(installation.bookmarkBar || installation.bookmarks || []), ...(installation.otherBookmarks || [])];
}

function findBookmark(id, nodes = allBookmarkNodes()) {
  for (const bookmark of nodes) {
    if (String(bookmark.id) === String(id)) return bookmark;
    const found = bookmark.children?.length ? findBookmark(id, bookmark.children) : null;
    if (found) return found;
  }
  return null;
}

function bookmarkMenuItems(bookmarks) {
  const visible = bookmarks.slice(0, 80);
  const markup = visible.map((bookmark) => {
    const action = bookmark.type === "folder" ? "popover-bookmark-folder" : "popover-bookmark";
    const data = bookmark.type === "folder"
      ? `data-bookmark-id="${escapeHtml(bookmark.id)}"`
      : `data-bookmark-url="${escapeHtml(bookmark.url)}"`;
    return `<button class="popover-row" data-action="${action}" ${data} type="button">${bookmarkIcon(bookmark)}<span>${escapeHtml(bookmark.name)}</span>${bookmark.type === "folder" ? '<span class="row-arrow">›</span>' : ""}</button>`;
  }).join("");
  return markup + (bookmarks.length > visible.length ? `<p class="popover-note">还有 ${bookmarks.length - visible.length} 项，请从子文件夹继续查看。</p>` : "");
}

function openPopover(title, content, kind) {
  popoverKind = kind;
  elements.chromePopover.innerHTML = `<div class="popover-heading"><strong>${escapeHtml(title)}</strong><button data-action="close-popover" type="button" aria-label="关闭">×</button></div><div class="popover-content">${content}</div>`;
  elements.chromePopover.classList.remove("hidden");
  void window.browserLite.setChromeMenuOpen(true);
}

function closePopover() {
  if (elements.chromePopover.classList.contains("hidden")) return;
  popoverKind = "";
  elements.chromePopover.classList.add("hidden");
  elements.chromePopover.innerHTML = "";
  void window.browserLite.setChromeMenuOpen(false);
}

function openBookmarkFolder(bookmark) {
  if (!bookmark) return;
  openPopover(bookmark.name || "书签", bookmarkMenuItems(bookmark.children || []), "bookmarks");
}

function currentExtensions() {
  const space = activeSpace();
  return space?.extensions?.length ? space.extensions : currentState?.installation?.extensions || [];
}

function extensionIcon(extension) {
  const image = safeDataImage(extension.iconDataUrl);
  return image
    ? `<img class="extension-menu-icon" src="${image}" alt="" />`
    : `<span class="extension-menu-fallback" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M20.5 11H19V7c0-1.1-.9-2-2-2h-4V3.5A2.5 2.5 0 0 0 10.5 1 2.5 2.5 0 0 0 8 3.5V5H4a2 2 0 0 0-2 2v3.8h1.5a2.7 2.7 0 1 1 0 5.4H2V20c0 1.1.9 2 2 2h3.8v-1.5a2.7 2.7 0 1 1 5.4 0V22H17c1.1 0 2-.9 2-2v-4h1.5a2.5 2.5 0 0 0 0-5Z"/></svg></span>`;
}

function extensionPinIcon(pinned) {
  return `<span class="extension-pin-state ${pinned ? "pinned" : ""}" title="${pinned ? "已固定" : "未固定"}" aria-label="${pinned ? "已固定" : "未固定"}"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M13.75 8.25V4.5l1-1v-1h-9.5v1l1 1v3.75c0 1.1-.9 2-2 2v1.75h4.95v5.5h1.6V12h4.95v-1.75c-1.1 0-2-.9-2-2Z"/></svg></span>`;
}

function extensionMoreIcon() {
  return `<svg class="extension-more-icon" viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="4.5" r="1.25"/><circle cx="10" cy="10" r="1.25"/><circle cx="10" cy="15.5" r="1.25"/></svg>`;
}

function extensionStatus(extension) {
  if (extension.status === "loaded") return "已启用";
  if (extension.status === "disabled") return "已停用";
  if (extension.status === "error") return "不兼容";
  return "等待加载";
}

function openExtensionsMenu() {
  const extensions = currentExtensions();
  const content = extensions.length ? extensions.map((extension) => `<div class="extension-row" data-extension-id="${escapeHtml(extension.id)}"><button class="extension-main" data-action="extension-details" data-extension-id="${escapeHtml(extension.id)}" type="button">${extensionIcon(extension)}<span><strong>${escapeHtml(extension.name)}</strong><small>${escapeHtml(extensionStatus(extension))} · v${escapeHtml(extension.version)}</small></span></button>${extensionPinIcon(extension.pinned)}<button class="extension-row-menu" data-action="extension-details" data-extension-id="${escapeHtml(extension.id)}" type="button" aria-label="${escapeHtml(extension.name)}的更多操作">${extensionMoreIcon()}</button></div>`).join("") : '<p class="popover-empty">没有导入扩展程序</p>';
  openPopover("扩展程序", content, "extensions");
}

function openExtensionDetails(extensionId) {
  const extension = currentExtensions().find((candidate) => candidate.id === extensionId);
  if (!extension) return;
  const canOpen = extension.status === "loaded" && extension.defaultPopup;
  const warning = extension.error ? `<p class="popover-warning">${escapeHtml(extension.error)}</p>` : "";
  openPopover(extension.name, `<div class="extension-detail">${extensionIcon(extension)}<div><strong>v${escapeHtml(extension.version)}</strong><span>${escapeHtml(extensionStatus(extension))}</span></div></div>${warning}<button class="popover-primary" data-action="open-extension" data-extension-id="${escapeHtml(extension.id)}" type="button" ${canOpen ? "" : "disabled"}>${extension.defaultPopup ? "打开扩展" : "此扩展没有弹出页"}</button>`, "extensions");
}

const GROUP_COLORS = ["grey", "blue", "red", "yellow", "green", "pink", "purple", "cyan", "orange"];

function groupForm(group = null, targetId = "") {
  const color = group?.color || "grey";
  return `<form id="tab-group-form" data-group-id="${escapeHtml(group?.id || "")}" data-target-id="${escapeHtml(targetId)}">
    <label class="popover-label">名称<input name="name" maxlength="40" value="${escapeHtml(group?.name || "新建标签组")}" autocomplete="off" /></label>
    <fieldset class="group-colors"><legend>颜色</legend>${GROUP_COLORS.map((value) => `<label class="group-color-${value}" title="${value}"><input type="radio" name="color" value="${value}" ${value === color ? "checked" : ""} /><span></span></label>`).join("")}</fieldset>
    <div class="popover-actions">${group ? '<button class="popover-danger" data-action="remove-tab-group" type="button">取消分组</button>' : ""}<button class="popover-primary" type="submit">${group ? "保存" : "创建"}</button></div>
  </form>`;
}

function openNewGroupMenu() {
  const space = activeSpace();
  const activeTab = space?.tabs?.find((tab) => tab.active);
  const targetId = activeTab?.targetId || document.querySelector(".browser-tab.active")?.dataset.targetId || "";
  if (!targetId) return;
  openPopover("新建标签组", groupForm(null, targetId), "tab-group");
}

function openGroupEditor(groupId) {
  const group = activeSpace()?.tabGroups?.find((candidate) => candidate.id === groupId);
  if (group) openPopover("编辑标签组", groupForm(group), "tab-group");
}

function openTabGroupAssignment(targetId) {
  const groups = activeSpace()?.tabGroups || [];
  const rows = groups.map((group) => `<button class="popover-row group-color-${escapeHtml(group.color)}" data-action="assign-tab-group" data-target-id="${escapeHtml(targetId)}" data-group-id="${escapeHtml(group.id)}" type="button"><span class="tab-group-dot"></span><span>${escapeHtml(group.name)}</span></button>`).join("");
  openPopover("将标签页加入分组", `${rows}<button class="popover-row" data-action="assign-tab-group" data-target-id="${escapeHtml(targetId)}" data-group-id="" type="button"><span class="ungroup-icon">—</span><span>从分组中移除</span></button>`, "tab-group");
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
  const space = activeSpace();
  if (!button || !space) return;
  if (button.dataset.action === "toggle-tab-group") {
    await run(button, null, () => window.browserLite.taskSpaceBrowserAction(space.id, "toggleTabGroup", button.dataset.groupId));
    return;
  }
  if (button.dataset.action === "edit-tab-group") {
    openGroupEditor(button.dataset.groupId);
    return;
  }
  const tab = event.target.closest("[data-target-id]");
  if (!tab) return;
  await run(button, null, () => window.browserLite.taskSpaceBrowserAction(space.id, button.dataset.action === "close-tab" ? "closeTab" : "activateTab", tab.dataset.targetId));
});

elements.browserTabs.addEventListener("contextmenu", (event) => {
  const group = event.target.closest("[data-tab-group-id]");
  const tab = event.target.closest("[data-target-id]");
  if (group && !tab) {
    event.preventDefault();
    openGroupEditor(group.dataset.tabGroupId);
    return;
  }
  if (!tab) return;
  event.preventDefault();
  openTabGroupAssignment(tab.dataset.targetId);
});

elements.browserBookmarks.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  const space = activeSpace();
  if (!button) return;
  if (button.dataset.action === "open-bookmark" && space) {
    closePopover();
    await run(button, null, () => window.browserLite.taskSpaceBrowserAction(space.id, "navigate", button.dataset.bookmarkUrl));
  } else if (button.dataset.action === "open-bookmark-folder") {
    openBookmarkFolder(findBookmark(button.dataset.bookmarkId));
  } else if (button.dataset.action === "bookmark-overflow") {
    const bookmarks = currentState?.installation?.bookmarkBar || currentState?.installation?.bookmarks || [];
    openPopover("隐藏的书签", bookmarkMenuItems(bookmarks.slice(14)), "bookmarks");
  } else if (button.dataset.action === "all-bookmarks") {
    openPopover("所有书签", bookmarkMenuItems(allBookmarkNodes()), "bookmarks");
  } else if (button.dataset.action === "new-tab-group") {
    openNewGroupMenu();
  } else if (button.dataset.action === "saved-tab-group" && space) {
    await run(button, null, () => window.browserLite.taskSpaceBrowserAction(space.id, "toggleTabGroup", button.dataset.groupId));
  }
});

elements.extensionsMenuButton.addEventListener("click", openExtensionsMenu);
elements.pinnedExtensions.addEventListener("click", (event) => {
  const button = event.target.closest("[data-extension-id]");
  if (button) openExtensionDetails(button.dataset.extensionId);
});

elements.chromePopover.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const space = activeSpace();
  if (button.dataset.action === "close-popover") closePopover();
  else if (button.dataset.action === "popover-bookmark-folder") openBookmarkFolder(findBookmark(button.dataset.bookmarkId));
  else if (button.dataset.action === "popover-bookmark" && space) {
    closePopover();
    await run(button, null, () => window.browserLite.taskSpaceBrowserAction(space.id, "navigate", button.dataset.bookmarkUrl));
  } else if (button.dataset.action === "extension-details") openExtensionDetails(button.dataset.extensionId);
  else if (button.dataset.action === "open-extension" && space) {
    const extensionId = button.dataset.extensionId;
    closePopover();
    await run(button, null, () => window.browserLite.taskSpaceBrowserAction(space.id, "openExtension", extensionId));
  } else if (button.dataset.action === "assign-tab-group" && space) {
    const payload = { targetId: button.dataset.targetId, groupId: button.dataset.groupId || "" };
    closePopover();
    await run(button, null, () => window.browserLite.taskSpaceBrowserAction(space.id, "moveTabToGroup", payload));
  } else if (button.dataset.action === "remove-tab-group" && space) {
    const form = button.closest("form");
    const groupId = form?.dataset.groupId;
    closePopover();
    if (groupId) await run(button, null, () => window.browserLite.taskSpaceBrowserAction(space.id, "removeTabGroup", groupId));
  }
});

elements.chromePopover.addEventListener("submit", async (event) => {
  if (event.target.id !== "tab-group-form") return;
  event.preventDefault();
  const space = activeSpace();
  const activeTab = space?.tabs?.find((tab) => tab.active);
  const targetId = event.target.dataset.targetId || activeTab?.targetId || "";
  if (!space || !targetId) return;
  const form = new FormData(event.target);
  const groupId = event.target.dataset.groupId;
  const payload = { name: String(form.get("name") || "新建标签组"), color: String(form.get("color") || "grey") };
  closePopover();
  if (groupId) {
    await run(null, null, () => window.browserLite.taskSpaceBrowserAction(space.id, "updateTabGroup", { ...payload, groupId }));
  } else {
    await run(null, null, () => window.browserLite.taskSpaceBrowserAction(space.id, "createTabGroup", { ...payload, targetId }));
  }
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
  if (event.key === "Escape" && !elements.chromePopover.classList.contains("hidden")) {
    event.preventDefault();
    closePopover();
    return;
  }
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

document.addEventListener("click", (event) => {
  if (elements.chromePopover.classList.contains("hidden")) return;
  if (event.target.closest("#chrome-popover, #browser-bookmarks, #extensions-menu-button, #pinned-extensions")) return;
  closePopover();
});

window.browserLite.onState(render);
await refresh();
