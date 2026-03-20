<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { solutions, type Solution } from './solutions'
import { useDocker } from './composables/useDocker'
import SolutionCard from './components/SolutionCard.vue'
import AiChat from './components/AiChat.vue'
import DOMDiffViewer from './components/DOMDiffViewer.vue'
import CDPViewer from './components/CDPViewer.vue'
import MJPEGViewer from './components/MJPEGViewer.vue'
import RrwebViewer from './components/RrwebViewer.vue'
import NoVNCViewer from './components/NoVNCViewer.vue'

const selected = ref<Solution | null>(null)
const urlInput = ref('https://www.baidu.com')
const navigating = ref(false)
const viewerRef = ref<any>(null)
const { state: docker, startAll, stopAll, startPolling, stopPolling } = useDocker()

const sidebarWidth = ref(288)
const isResizing = ref(false)
const aiPanelWidth = ref(340)
const isResizingAi = ref(false)
const aiPanelOpen = ref(true)

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

function select(s: Solution) {
  selected.value = s
}

function onBrowserActive() {
  const selenium = solutions.find(s => s.id === 'selenium')
  if (selenium && selected.value?.id !== 'selenium') {
    selected.value = selenium
  }
}

const navError = ref('')

async function navigate() {
  if (!selected.value) return
  let url = urlInput.value.trim()
  if (!url) return
  if (!url.startsWith('http')) url = 'https://' + url
  urlInput.value = url
  navigating.value = true
  navError.value = ''

  if (selected.value.viewerType !== 'iframe') {
    viewerRef.value?.navigate(url)
    navigating.value = false
    return
  }

  try {
    const resp = await fetch('/api/docker/navigate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ solutionId: selected.value.id, url }),
    })
    const data = await resp.json()
    if (!data.ok && data.error) {
      navError.value = data.error
      setTimeout(() => { navError.value = '' }, 4000)
    }
  } catch {}
  navigating.value = false
}

onMounted(startPolling)
onUnmounted(stopPolling)
</script>

<template>
  <div class="h-screen flex flex-col bg-[var(--color-bg)] overflow-hidden">
    <!-- Header -->
    <header class="shrink-0 flex items-center gap-3 px-5 py-2.5 border-b border-[var(--color-border)] bg-[var(--color-surface)]">
      <h1 class="text-lg font-bold tracking-tight whitespace-nowrap">
        Remote Browser
        <span class="text-[var(--color-accent)]">Playground</span>
      </h1>

      <div class="ml-auto flex items-center gap-2">
        <button
          @click="startAll"
          :disabled="docker.globalLoading"
          class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-green-600/20 text-green-400 border border-green-600/30 hover:bg-green-600/30 transition-colors disabled:opacity-50 disabled:cursor-wait"
        >
          <svg v-if="!docker.globalLoading" class="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 20 20"><path d="M6.3 2.841A1.5 1.5 0 004 4.11V15.89a1.5 1.5 0 002.3 1.269l9.344-5.89a1.5 1.5 0 000-2.538L6.3 2.84z"/></svg>
          <svg v-else class="w-3.5 h-3.5 animate-spin" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.49-8.49l2.83-2.83M2 12h4m12 0h4m-3.93 7.07l-2.83-2.83M7.76 7.76L4.93 4.93" /></svg>
          全部启动
        </button>
        <button
          @click="stopAll"
          :disabled="docker.globalLoading"
          class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-red-600/20 text-red-400 border border-red-600/30 hover:bg-red-600/30 transition-colors disabled:opacity-50 disabled:cursor-wait"
        >
          <svg class="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 20 20"><rect x="4" y="4" width="12" height="12" rx="1.5"/></svg>
          全部停止
        </button>
        <div class="w-px h-5 bg-[var(--color-border)]" />
        <button
          @click="aiPanelOpen = !aiPanelOpen"
          class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors"
          :class="aiPanelOpen
            ? 'bg-[var(--color-accent)]/20 text-[var(--color-accent)] border-[var(--color-accent)]/30 hover:bg-[var(--color-accent)]/30'
            : 'bg-[var(--color-surface-hover)] text-[var(--color-text-dim)] border-[var(--color-border)] hover:text-[var(--color-text)]'"
        >
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z" />
          </svg>
          AI 助手
        </button>
      </div>
    </header>

    <!-- Main content -->
    <div class="flex-1 flex overflow-hidden">
      <!-- Left sidebar (resizable) -->
      <aside
        class="shrink-0 border-r border-[var(--color-border)] bg-[var(--color-surface)] overflow-y-auto"
        :style="{ width: sidebarWidth + 'px' }"
      >
        <div class="p-3 space-y-2">
          <SolutionCard
            v-for="s in solutions"
            :key="s.id"
            :solution="s"
            :active="selected?.id === s.id"
            @select="select(s)"
          />
        </div>
      </aside>

      <!-- Resize handle -->
      <div
        class="shrink-0 w-1 cursor-col-resize hover:bg-[var(--color-accent)]/30 active:bg-[var(--color-accent)]/50 transition-colors"
        :class="isResizing ? 'bg-[var(--color-accent)]/50' : ''"
        @mousedown="startResize"
      />

      <!-- Right viewer container -->
      <main class="flex-1 flex flex-col overflow-hidden">
        <!-- URL bar (shown when a solution is selected) -->
        <div
          v-if="selected"
          class="shrink-0 flex items-center gap-2 px-3 py-2 bg-[var(--color-surface)] border-b border-[var(--color-border)]"
        >
          <div
            class="w-2.5 h-2.5 rounded-full shrink-0"
            :style="{ backgroundColor: selected.color }"
          />
          <span class="text-xs text-[var(--color-text-dim)] shrink-0">{{ selected.protocol }}</span>
          <input
            v-model="urlInput"
            @keydown.enter="navigate"
            class="flex-1 px-3 py-1.5 rounded-lg bg-[var(--color-bg)] border border-[var(--color-border)] text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-accent)] transition-colors"
            placeholder="输入网址后回车导航..."
          />
          <button
            @click="navigate"
            :disabled="navigating"
            class="px-3 py-1.5 rounded-lg text-xs font-medium bg-[var(--color-accent)] text-white hover:bg-[var(--color-accent-dim)] transition-colors disabled:opacity-50 shrink-0"
          >
            {{ navigating ? '前往中...' : '前往' }}
          </button>
          <span v-if="navError" class="text-[10px] text-yellow-400 shrink-0 whitespace-nowrap">{{ navError }}</span>
        </div>

        <!-- Viewer area -->
        <div class="flex-1 relative overflow-hidden">
          <!-- Empty state -->
          <div
            v-if="!selected"
            class="absolute inset-0 flex flex-col items-center justify-center text-[var(--color-text-dim)]"
          >
            <svg class="w-16 h-16 mb-4 opacity-20" fill="none" stroke="currentColor" stroke-width="1" viewBox="0 0 24 24">
              <rect x="2" y="3" width="20" height="14" rx="2" />
              <path d="M8 21h8M12 17v4" />
            </svg>
            <p class="text-lg mb-1">选择一个方案开始预览</p>
            <p class="text-sm">点击左侧任意方案卡片，右侧将展示远程浏览器的实时画面</p>
          </div>

          <CDPViewer
            v-if="selected?.viewerType === 'cdp-screenshot'"
            ref="viewerRef"
            :key="selected.id"
            :ws-url="selected.url"
            :initial-url="urlInput"
            @url-change="(url: string) => urlInput = url"
          />
          <MJPEGViewer
            v-else-if="selected?.viewerType === 'mjpeg'"
            ref="viewerRef"
            :key="selected.id"
            :service-url="selected.url"
            :initial-url="urlInput"
            @url-change="(url: string) => urlInput = url"
          />
          <DOMDiffViewer
            v-else-if="selected?.viewerType === 'dom-diff'"
            ref="viewerRef"
            :key="selected.id"
            :ws-url="selected.url"
            :initial-url="urlInput"
            @url-change="(url: string) => urlInput = url"
          />
          <RrwebViewer
            v-else-if="selected?.viewerType === 'rrweb'"
            ref="viewerRef"
            :key="selected.id"
            :ws-url="selected.url"
            :initial-url="urlInput"
            @url-change="(url: string) => urlInput = url"
          />
          <NoVNCViewer
            v-else-if="selected?.viewerType === 'novnc'"
            ref="viewerRef"
            :key="selected.id"
            :ws-url="selected.url"
            :initial-url="urlInput"
            :solution-id="selected.id"
            @url-change="(url: string) => urlInput = url"
          />
          <iframe
            v-else-if="selected"
            :key="selected.id"
            :src="selected.url"
            class="absolute inset-0 w-full h-full border-0"
            allow="fullscreen; clipboard-read; clipboard-write; autoplay"
            sandbox="allow-same-origin allow-scripts allow-forms allow-popups allow-modals"
          />
        </div>
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
        <AiChat @browser-active="onBrowserActive" />
      </aside>
    </div>

    <!-- Error toast -->
    <Transition name="slide">
      <div
        v-if="docker.lastError"
        class="fixed bottom-4 left-1/2 -translate-x-1/2 px-4 py-2 rounded-lg bg-red-900/90 border border-red-700 text-red-200 text-sm max-w-lg truncate shadow-lg"
      >
        {{ docker.lastError }}
      </div>
    </Transition>
  </div>
</template>

<style>
.slide-enter-active, .slide-leave-active {
  transition: all 0.3s ease;
}
.slide-enter-from, .slide-leave-to {
  opacity: 0;
  transform: translate(-50%, 1rem);
}
</style>

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
