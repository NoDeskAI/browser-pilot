<script setup lang="ts">
import { ref, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useSessions } from '../composables/useSessions'
import { Loader, PanelLeftClose, PanelLeft, Plus, Monitor, Play, SquareTerminal } from 'lucide-vue-next'
import SessionItem from '../components/SessionItem.vue'
import NoVNCViewer from '../components/NoVNCViewer.vue'
import BrowserLogPanel from '../components/BrowserLogPanel.vue'
import CliDocModal from '../components/CliDocModal.vue'

const { t } = useI18n()

const {
  state: sessions,
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
const cliDocOpen = ref(false)

const vncUrl = computed(() => {
  if (!sessions.activePorts?.vncPort) return null
  return `ws://${location.hostname}:${sessions.activePorts.vncPort}/websockify`
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

async function onNewSession() {
  await createSession()
}
</script>

<template>
  <div class="flex-1 flex flex-col overflow-hidden">
    <!-- Toolbar -->
    <div class="shrink-0 flex items-center gap-2 px-3 py-1.5 border-b border-[var(--color-border)] bg-[var(--color-surface)]">
      <button
        @click="sidebarOpen = !sidebarOpen"
        class="shrink-0 w-7 h-7 flex items-center justify-center rounded-md text-[var(--color-text-dim)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface-hover)] transition-colors"
        :title="sidebarOpen ? t('app.collapseSidebar') : t('app.expandSidebar')"
      >
        <PanelLeftClose v-if="sidebarOpen" class="w-4 h-4" />
        <PanelLeft v-else class="w-4 h-4" />
      </button>
      <div class="flex-1" />
    </div>

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
            {{ t('app.newSession') }}
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
            <p class="text-xs text-center leading-relaxed">{{ t('app.emptySessionHint') }}</p>
          </div>
        </div>
        <div class="shrink-0 px-3 py-2 border-t border-[var(--color-border)]">
          <button
            @click="cliDocOpen = true"
            class="w-full flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs text-[var(--color-text-dim)] hover:text-[var(--color-accent)] hover:bg-[var(--color-surface-hover)] transition-colors"
          >
            <SquareTerminal class="w-3.5 h-3.5" />
            {{ t('app.cliAccess') }}
          </button>
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
        <div class="flex-1 relative overflow-hidden min-h-0">
          <div
            v-if="!sessions.activeId"
            class="absolute inset-0 flex flex-col items-center justify-center text-[var(--color-text-dim)]"
          >
            <Monitor class="w-16 h-16 mb-4 opacity-20" :stroke-width="1" />
            <p class="text-sm mb-1">{{ t('app.noSessionTitle') }}</p>
            <p class="text-xs opacity-60">{{ t('app.noSessionSubtitle') }}</p>
          </div>
          <div
            v-else-if="sessions.containerLoading"
            class="absolute inset-0 flex flex-col items-center justify-center text-[var(--color-text-dim)]"
          >
            <Loader class="w-10 h-10 mb-4 animate-spin opacity-40" />
            <p class="text-sm mb-1">{{ t('app.startingBrowser') }}</p>
            <p class="text-xs opacity-60">{{ t('app.startingBrowserHint') }}</p>
          </div>
          <NoVNCViewer
            v-else-if="vncUrl && sessions.activeId"
            :key="sessions.activeId"
            :ws-url="vncUrl"
            :session-id="sessions.activeId"
          />
          <div
            v-else-if="sessions.activeId && activeSession?.containerStatus === 'paused'"
            class="absolute inset-0 flex flex-col items-center justify-center text-[var(--color-text-dim)]"
          >
            <Monitor class="w-16 h-16 mb-4 opacity-20" :stroke-width="1" />
            <p class="text-sm mb-1">{{ t('app.browserHibernated') }}</p>
            <p class="text-xs opacity-60 mb-4">{{ t('app.browserHibernatedHint') }}</p>
            <button
              @click="sessions.activeId && startContainer(sessions.activeId)"
              class="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-medium bg-amber-500/20 text-amber-400 border border-amber-500/30 hover:bg-amber-500/30 transition-colors"
            >
              <Play class="w-3.5 h-3.5" />
              {{ t('app.resumeBrowser') }}
            </button>
          </div>
          <div
            v-else-if="sessions.activeId && !vncUrl"
            class="absolute inset-0 flex flex-col items-center justify-center text-[var(--color-text-dim)]"
          >
            <Monitor class="w-16 h-16 mb-4 opacity-20" :stroke-width="1" />
            <p class="text-sm mb-1">{{ t('app.containerStopped') }}</p>
            <p class="text-xs opacity-60">{{ t('app.containerStoppedHint') }}</p>
          </div>
        </div>
        <BrowserLogPanel :session-id="sessions.activeId" />
      </main>
    </div>

    <CliDocModal :open="cliDocOpen" @close="cliDocOpen = false" />
  </div>
</template>
