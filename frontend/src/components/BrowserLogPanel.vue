<script setup lang="ts">
import { ref, watch, onUnmounted, nextTick, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ChevronUp, ChevronDown, Trash2 } from 'lucide-vue-next'
import { api } from '../lib/api'
import {
  Tooltip, TooltipContent, TooltipProvider, TooltipTrigger,
} from '@/components/ui/tooltip'

const { t, locale } = useI18n()

interface LogEntry {
  ts: string
  type: string
  method: string
  summary: string
}

const props = defineProps<{
  sessionId: string | null
}>()

const expanded = ref(false)
const logs = ref<LogEntry[]>([])
const activeFilter = ref<string | null>(null)
const panelHeight = ref(200)
const isResizingPanel = ref(false)
const logContainer = ref<HTMLElement | null>(null)
let pollTimer: ReturnType<typeof setInterval> | null = null

const FILTERS = computed(() => [
  { key: null, label: t('logs.all') },
  { key: 'console', label: t('logs.console') },
  { key: 'network', label: t('logs.network') },
  { key: 'navigation', label: t('logs.navigation') },
  { key: 'error', label: t('logs.error') },
])

const filteredLogs = computed(() => {
  if (!activeFilter.value) return logs.value
  return logs.value.filter(l => l.type === activeFilter.value)
})

function typeColor(type: string): string {
  switch (type) {
    case 'network': return 'text-blue-400'
    case 'navigation': return 'text-green-400'
    case 'error': return 'text-red-400'
    case 'console': return 'text-yellow-300'
    default: return 'text-muted-foreground'
  }
}

function typeLabel(type: string): string {
  switch (type) {
    case 'network': return 'NET'
    case 'navigation': return 'NAV'
    case 'error': return 'ERR'
    case 'console': return 'CON'
    default: return type.slice(0, 3).toUpperCase()
  }
}

function formatTime(ts: string): string {
  try {
    const d = new Date(ts)
    const loc = locale.value === 'zh' ? 'zh-CN' : 'en-GB'
    return d.toLocaleTimeString(loc, { hour12: false })
  } catch {
    return ts
  }
}

async function fetchLogs() {
  if (!props.sessionId) return
  const typeParam = activeFilter.value ? `&log_type=${activeFilter.value}` : ''
  try {
    const resp = await api(`/api/sessions/${props.sessionId}/logs?tail=150${typeParam}`)
    const data = await resp.json()
    if (data.logs) {
      logs.value = data.logs.reverse()
      await nextTick()
      if (logContainer.value) {
        logContainer.value.scrollTop = 0
      }
    }
  } catch { /* container might be stopped */ }
}

function startPolling() {
  stopPolling()
  fetchLogs()
  pollTimer = setInterval(fetchLogs, 3000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function toggleExpanded() {
  expanded.value = !expanded.value
  if (expanded.value) {
    startPolling()
  } else {
    stopPolling()
  }
}

function clearLogs() {
  logs.value = []
}

function startResizePanel(e: MouseEvent) {
  e.preventDefault()
  isResizingPanel.value = true
  document.body.classList.add('resizing-panel')
  const startY = e.clientY
  const startH = panelHeight.value
  const onMove = (ev: MouseEvent) => {
    const delta = startY - ev.clientY
    panelHeight.value = Math.max(100, Math.min(500, startH + delta))
  }
  const onUp = () => {
    isResizingPanel.value = false
    document.body.classList.remove('resizing-panel')
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
  }
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
}

watch(() => props.sessionId, () => {
  logs.value = []
  if (expanded.value && props.sessionId) {
    startPolling()
  } else {
    stopPolling()
  }
})

onUnmounted(() => {
  stopPolling()
})
</script>

<template>
  <TooltipProvider :delay-duration="300">
    <div class="shrink-0 border-t border-border">
      <!-- Resize handle -->
      <div
        v-if="expanded"
        class="h-1 cursor-row-resize hover:bg-primary/20 active:bg-primary/40 transition-colors"
        :class="isResizingPanel ? 'bg-primary/40' : ''"
        @mousedown="startResizePanel"
      />

      <!-- Header bar -->
      <div
        class="flex items-center gap-2 px-3 py-1 cursor-pointer select-none"
        @click="toggleExpanded"
      >
        <component :is="expanded ? ChevronDown : ChevronUp" class="size-3.5 text-muted-foreground" />
        <span class="text-[11px] font-medium text-muted-foreground">{{ t('logs.title') }}</span>

        <template v-if="expanded">
          <div class="flex items-center gap-0.5 ml-2" @click.stop>
            <button
              v-for="f in FILTERS"
              :key="f.key ?? 'all'"
              @click="activeFilter = f.key; fetchLogs()"
              :aria-pressed="activeFilter === f.key"
              class="px-1.5 py-0.5 rounded text-[10px] font-medium transition-colors"
              :class="activeFilter === f.key
                ? 'bg-primary/15 text-primary'
                : 'text-muted-foreground hover:text-foreground'"
            >
              {{ f.label }}
            </button>
          </div>
        </template>

        <div class="ml-auto flex items-center gap-1" v-if="expanded" @click.stop>
          <span class="text-[10px] text-muted-foreground">{{ filteredLogs.length }}</span>
          <Tooltip>
            <TooltipTrigger as-child>
              <button
                @click="clearLogs"
                class="p-0.5 rounded text-muted-foreground hover:text-foreground transition-colors"
              >
                <Trash2 class="size-3" />
              </button>
            </TooltipTrigger>
            <TooltipContent>{{ t('logs.clear') }}</TooltipContent>
          </Tooltip>
        </div>
      </div>

      <!-- Log content -->
      <div
        v-if="expanded"
        ref="logContainer"
        class="overflow-y-auto bg-background font-mono text-[11px] leading-5"
        :style="{ height: panelHeight + 'px' }"
      >
        <div v-if="filteredLogs.length === 0" class="flex items-center justify-center h-full text-muted-foreground text-xs">
          {{ t('logs.empty') }}
        </div>
        <div
          v-for="(entry, i) in filteredLogs"
          :key="i"
          class="flex items-start gap-2 px-3 py-0.5 hover:bg-accent/50"
          :class="entry.type === 'error' ? 'bg-red-500/5' : ''"
        >
          <span class="shrink-0 text-muted-foreground/60 w-[60px]">{{ formatTime(entry.ts) }}</span>
          <span
            class="shrink-0 w-[28px] text-center font-bold"
            :class="typeColor(entry.type)"
          >{{ typeLabel(entry.type) }}</span>
          <span
            class="flex-1 break-all"
            :class="entry.type === 'error' ? 'text-red-400' : 'text-foreground'"
          >{{ entry.summary }}</span>
        </div>
      </div>
    </div>
  </TooltipProvider>
</template>
