const elements = {
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
};

let currentState = null;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
}

function render(state) {
  currentState = state;
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

window.browserLite.onState(render);
await refresh();
