<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useSessions } from './composables/useSessions'
import { Loader, Sparkles, PanelLeftClose, PanelLeft, Plus, Monitor, Play } from 'lucide-vue-next'
import SessionItem from './components/SessionItem.vue'
import AiChat from './components/AiChat.vue'
import NoVNCViewer from './components/NoVNCViewer.vue'
import BrowserLogPanel from './components/BrowserLogPanel.vue'

const {
  state: sessions,
  init: initSessions,
  createSession,
  switchSession,
  deleteSession,
  renameSession,
  startContainer,
  pauseContainer,
  stopContainer,
} = useSessions()

const activeSession = computed(() =>
  sessions.sessions.find(s => s.id === sessions.activeId)
)

const sidebarWidth = ref(260)
const isResizing = ref(false)
const sidebarOpen = ref(true)
const aiPanelWidth = ref(340)
const isResizingAi = ref(false)
const aiPanelOpen = ref(true)

const vncUrl = computed(() => {
  if (!sessions.activePorts?.vncPort) return null
  return `ws://localhost:${sessions.activePorts.vncPort}/websockify`
})

function startResize(e: MouseEvent) {
  e.preventDefault()
  isResizing.value = true
  document.body.classList.add('resizing')
  const onMove = (ev: MouseEvent) => {
    const w = Math.max(200, Math.min(480, ev.clientX))
    sidebarWidth.value = w
  }
  const onUp = () => {
    isResizing.value = false
    document.body.classList.remove('resizing')
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
  }
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
}

function startResizeAi(e: MouseEvent) {
  e.preventDefault()
  isResizingAi.value = true
  document.body.classList.add('resizing')
  const onMove = (ev: MouseEvent) => {
    const w = Math.max(280, Math.min(600, window.innerWidth - ev.clientX))
    aiPanelWidth.value = w
  }
  const onUp = () => {
    isResizingAi.value = false
    document.body.classList.remove('resizing')
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
  }
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
}

async function onNewSession() {
  await createSession()
}

const noVncRef = ref<InstanceType<typeof NoVNCViewer> | null>(null)

function onSessionCreated(id: string) {
  switchSession(id)
}

function onHighlightClick(coords: { x: number; y: number }) {
  noVncRef.value?.highlightClick(coords.x, coords.y)
}

onMounted(async () => {
  await initSessions()
})
</script>

<template>
  <div class="h-screen flex flex-col bg-[var(--color-bg)] overflow-hidden">
    <!-- Header -->
    <header class="shrink-0 flex items-center gap-3 px-5 py-2.5 border-b border-[var(--color-border)] bg-[var(--color-surface)]">
      <button
        @click="sidebarOpen = !sidebarOpen"
        class="shrink-0 w-7 h-7 flex items-center justify-center rounded-md text-[var(--color-text-dim)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface-hover)] transition-colors"
        :title="sidebarOpen ? '收起侧边栏' : '展开侧边栏'"
      >
        <PanelLeftClose v-if="sidebarOpen" class="w-4 h-4" />
        <PanelLeft v-else class="w-4 h-4" />
      </button>
      <h1 class="text-lg font-bold tracking-tight whitespace-nowrap">
        Remote Browser
        <span class="text-[var(--color-accent)]">Playground</span>
      </h1>

      <div class="ml-auto flex items-center gap-2">
        <button
          @click="aiPanelOpen = !aiPanelOpen"
          class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors"
          :class="aiPanelOpen
            ? 'bg-[var(--color-accent)]/20 text-[var(--color-accent)] border-[var(--color-accent)]/30 hover:bg-[var(--color-accent)]/30'
            : 'bg-[var(--color-surface-hover)] text-[var(--color-text-dim)] border-[var(--color-border)] hover:text-[var(--color-text)]'"
        >
          <Sparkles class="w-3.5 h-3.5" />
          NoDeskPane Agent
        </button>
      </div>
    </header>

    <!-- Main content -->
    <div class="flex-1 flex overflow-hidden">
      <!-- Left sidebar (sessions) -->
      <aside
        v-if="sidebarOpen"
        class="shrink-0 border-r border-[var(--color-border)] bg-[var(--color-surface)] flex flex-col overflow-hidden"
        :style="{ width: sidebarWidth + 'px' }"
      >
        <div class="shrink-0 p-3 pb-2">
          <button
            @click="onNewSession"
            class="w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium border border-dashed border-[var(--color-border)] text-[var(--color-text-dim)] hover:border-[var(--color-accent)] hover:text-[var(--color-accent)] transition-colors"
          >
            <Plus class="w-3.5 h-3.5" />
            新建会话
          </button>
        </div>
        <div class="flex-1 overflow-y-auto px-2 pb-3 space-y-0.5">
          <SessionItem
            v-for="s in sessions.sessions"
            :key="s.id"
            :session="s"
            :active="sessions.activeId === s.id"
            @select="switchSession(s.id)"
            @delete="deleteSession(s.id)"
            @rename="(name) => renameSession(s.id, name)"
            @start="startContainer(s.id)"
            @stop="stopContainer(s.id)"
            @pause="pauseContainer(s.id)"
          />
          <div v-if="!sessions.loading && sessions.sessions.length === 0" class="flex flex-col items-center justify-center py-12 text-[var(--color-text-dim)]">
            <p class="text-xs text-center leading-relaxed">新建一个会话开始对话</p>
          </div>
        </div>
      </aside>

      <!-- Resize handle -->
      <div
        v-if="sidebarOpen"
        class="shrink-0 w-1 cursor-col-resize hover:bg-[var(--color-accent)]/30 active:bg-[var(--color-accent)]/50 transition-colors"
        :class="isResizing ? 'bg-[var(--color-accent)]/50' : ''"
        @mousedown="startResize"
      />

      <!-- Right viewer container -->
      <main class="flex-1 flex flex-col overflow-hidden">
        <!-- Viewer area -->
        <div class="flex-1 relative overflow-hidden min-h-0">
          <!-- No session selected -->
          <div
            v-if="!sessions.activeId"
            class="absolute inset-0 flex flex-col items-center justify-center text-[var(--color-text-dim)]"
          >
            <Monitor class="w-16 h-16 mb-4 opacity-20" :stroke-width="1" />
            <p class="text-sm mb-1">新建一个会话开始使用</p>
            <p class="text-xs opacity-60">浏览器会在创建会话后自动连接</p>
          </div>

          <!-- Container loading -->
          <div
            v-else-if="sessions.containerLoading"
            class="absolute inset-0 flex flex-col items-center justify-center text-[var(--color-text-dim)]"
          >
            <Loader class="w-10 h-10 mb-4 animate-spin opacity-40" />
            <p class="text-sm mb-1">正在启动浏览器...</p>
            <p class="text-xs opacity-60">首次启动可能需要几秒钟</p>
          </div>

          <!-- VNC viewer -->
          <NoVNCViewer
            ref="noVncRef"
            v-else-if="vncUrl && sessions.activeId"
            :key="sessions.activeId"
            :ws-url="vncUrl"
            :session-id="sessions.activeId"
          />

          <!-- Container paused (hibernated) -->
          <div
            v-else-if="sessions.activeId && activeSession?.containerStatus === 'paused'"
            class="absolute inset-0 flex flex-col items-center justify-center text-[var(--color-text-dim)]"
          >
            <Monitor class="w-16 h-16 mb-4 opacity-20" :stroke-width="1" />
            <p class="text-sm mb-1">浏览器已休眠</p>
            <p class="text-xs opacity-60 mb-4">页面状态已冻结，恢复后与休眠前完全一致</p>
            <button
              @click="sessions.activeId && startContainer(sessions.activeId)"
              class="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-medium bg-amber-500/20 text-amber-400 border border-amber-500/30 hover:bg-amber-500/30 transition-colors"
            >
              <Play class="w-3.5 h-3.5" />
              恢复浏览器
            </button>
          </div>

          <!-- Container stopped -->
          <div
            v-else-if="sessions.activeId && !vncUrl"
            class="absolute inset-0 flex flex-col items-center justify-center text-[var(--color-text-dim)]"
          >
            <Monitor class="w-16 h-16 mb-4 opacity-20" :stroke-width="1" />
            <p class="text-sm mb-1">浏览器容器未运行</p>
            <p class="text-xs opacity-60">点击侧边栏会话的播放按钮启动</p>
          </div>
        </div>

        <!-- Browser log panel -->
        <BrowserLogPanel :session-id="sessions.activeId" />
      </main>

      <!-- AI Panel resize handle -->
      <div
        v-if="aiPanelOpen"
        class="shrink-0 w-1 cursor-col-resize hover:bg-[var(--color-accent)]/30 active:bg-[var(--color-accent)]/50 transition-colors"
        :class="isResizingAi ? 'bg-[var(--color-accent)]/50' : ''"
        @mousedown="startResizeAi"
      />

      <!-- AI Panel -->
      <aside
        v-if="aiPanelOpen"
        class="shrink-0 border-l border-[var(--color-border)] overflow-hidden"
        :style="{ width: aiPanelWidth + 'px' }"
      >
        <AiChat
          :session-id="sessions.activeId"
          @session-created="onSessionCreated"
          @highlight-click="onHighlightClick"
        />
      </aside>
    </div>
  </div>
</template>

<style>
body.resizing,
body.resizing * {
  cursor: col-resize !important;
  user-select: none !important;
}
body.resizing iframe {
  pointer-events: none !important;
}
</style>
