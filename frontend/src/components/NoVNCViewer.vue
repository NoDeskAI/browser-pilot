<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import RFB from '@novnc/novnc'

const props = defineProps<{
  wsUrl: string
  initialUrl: string
  solutionId: string
}>()

const emit = defineEmits<{
  urlChange: [url: string]
}>()

const vncContainer = ref<HTMLDivElement | null>(null)
const viewerRoot = ref<HTMLDivElement | null>(null)
const connected = ref(false)
const desktopName = ref('')
const qualityLevel = ref(6)
const compressionLevel = ref(2)
const scaleMode = ref<'scale' | 'resize'>('scale')
const viewOnly = ref(false)
const clipboardText = ref('')
const clipboardOpen = ref(false)
const clipBtnRef = ref<HTMLElement>()
const clipPanelRef = ref<HTMLElement>()
const clipPos = ref({ top: 0, left: 0 })
const clipLoading = ref(false)
const isFullscreen = ref(false)
const totalRecv = ref(0)
const totalSent = ref(0)
const currentRate = ref(0)

let rfb: RFB | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let bytesWindow: number[] = []
let rateTimer: ReturnType<typeof setInterval> | null = null
let suppressRfbClipboard = false

function fmtBytes(b: number): string {
  if (b < 1024) return b + ' B'
  if (b < 1048576) return (b / 1024).toFixed(1) + ' KB'
  return (b / 1048576).toFixed(1) + ' MB'
}

function fmtRate(bps: number): string {
  if (bps < 1024) return bps.toFixed(0) + ' B/s'
  if (bps < 1048576) return (bps / 1024).toFixed(1) + ' KB/s'
  return (bps / 1048576).toFixed(1) + ' MB/s'
}

function clearContainer() {
  const el = vncContainer.value
  if (!el) return
  while (el.firstChild) el.removeChild(el.firstChild)
}

function connectRFB() {
  if (rfb) {
    try { rfb.disconnect() } catch { /* already disconnected */ }
    rfb = null
  }
  clearContainer()

  const el = vncContainer.value
  if (!el) return

  const OrigWS = window.WebSocket
  const recvRef = totalRecv
  const sentRef = totalSent
  const bw = bytesWindow
  ;(window as any).WebSocket = new Proxy(OrigWS, {
    construct(target, args) {
      const ws = new target(...(args as [string, string?]))
      ws.addEventListener('message', (e: MessageEvent) => {
        const size = e.data instanceof ArrayBuffer ? e.data.byteLength
          : e.data instanceof Blob ? e.data.size
          : new Blob([e.data]).size
        recvRef.value += size
        bw.push(size)
      })
      const origSend = ws.send.bind(ws)
      ws.send = (data: any) => {
        const size = data instanceof ArrayBuffer ? data.byteLength
          : data instanceof Blob ? data.size
          : new Blob([data]).size
        sentRef.value += size
        origSend(data)
      }
      return ws
    },
  })

  try {
    rfb = new RFB(el, props.wsUrl)
  } catch {
    window.WebSocket = OrigWS
    scheduleReconnect()
    return
  }

  window.WebSocket = OrigWS

  rfb.scaleViewport = scaleMode.value === 'scale'
  rfb.resizeSession = scaleMode.value === 'resize'
  rfb.qualityLevel = qualityLevel.value
  rfb.compressionLevel = compressionLevel.value
  rfb.viewOnly = viewOnly.value
  rfb.focusOnClick = true

  rfb.addEventListener('connect', () => {
    connected.value = true
  })

  rfb.addEventListener('disconnect', (e: CustomEvent<{ clean: boolean }>) => {
    connected.value = false
    if (!e.detail.clean) scheduleReconnect()
  })

  rfb.addEventListener('clipboard', (e: CustomEvent<{ text: string }>) => {
    if (!suppressRfbClipboard) clipboardText.value = e.detail.text
  })

  rfb.addEventListener('desktopname', (e: CustomEvent<{ name: string }>) => {
    desktopName.value = e.detail.name
  })

  rfb.addEventListener('credentialsrequired', () => {
    if (rfb) rfb.sendCredentials({ password: '' })
  })
}

function scheduleReconnect() {
  if (reconnectTimer) clearTimeout(reconnectTimer)
  reconnectTimer = setTimeout(() => {
    if (!connected.value) connectRFB()
  }, 3000)
}

async function navigate(url: string) {
  totalRecv.value = 0
  totalSent.value = 0
  currentRate.value = 0
  bytesWindow = []
  try {
    const resp = await fetch('/api/docker/navigate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ solutionId: props.solutionId, url }),
    })
    const data = await resp.json()
    if (data.url) emit('urlChange', data.url)
  } catch { /* ignore */ }
}

function sendCtrlAltDel() {
  rfb?.sendCtrlAltDel()
}

async function pasteClipboard() {
  if (!clipboardText.value || clipLoading.value) return
  clipLoading.value = true
  suppressRfbClipboard = true
  try {
    await fetch('/api/docker/clipboard', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ solutionId: props.solutionId, action: 'paste', text: clipboardText.value }),
    })
  } catch { /* ignore */ }
  clipLoading.value = false
  setTimeout(() => { suppressRfbClipboard = false }, 3000)
}

async function getRemoteClipboard() {
  if (clipLoading.value) return
  clipLoading.value = true
  try {
    const resp = await fetch('/api/docker/clipboard', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ solutionId: props.solutionId, action: 'get' }),
    })
    const data = await resp.json()
    if (data.ok && data.text != null) clipboardText.value = data.text
  } catch { /* ignore */ }
  clipLoading.value = false
}

function toggleClipboard() {
  if (!clipboardOpen.value && clipBtnRef.value) {
    const rect = clipBtnRef.value.getBoundingClientRect()
    clipPos.value = { top: rect.bottom + 4, left: rect.left }
  }
  clipboardOpen.value = !clipboardOpen.value
}

function handleClipClickOutside(e: MouseEvent) {
  if (!clipboardOpen.value) return
  const t = e.target as Node
  if (clipBtnRef.value?.contains(t)) return
  if (clipPanelRef.value?.contains(t)) return
  clipboardOpen.value = false
}

function toggleScaleMode() {
  scaleMode.value = scaleMode.value === 'scale' ? 'resize' : 'scale'
  if (rfb) {
    rfb.scaleViewport = scaleMode.value === 'scale'
    rfb.resizeSession = scaleMode.value === 'resize'
  }
}

function toggleViewOnly() {
  viewOnly.value = !viewOnly.value
  if (rfb) rfb.viewOnly = viewOnly.value
}

function applyQuality() {
  if (rfb) {
    rfb.qualityLevel = qualityLevel.value
    rfb.compressionLevel = compressionLevel.value
  }
}

function toggleFullscreen() {
  const el = viewerRoot.value
  if (!el) return
  if (!document.fullscreenElement) {
    el.requestFullscreen().then(() => { isFullscreen.value = true }).catch(() => {})
  } else {
    document.exitFullscreen().then(() => { isFullscreen.value = false }).catch(() => {})
  }
}

function onFullscreenChange() {
  isFullscreen.value = !!document.fullscreenElement
}

defineExpose({ navigate })

onMounted(() => {
  rateTimer = setInterval(() => {
    currentRate.value = bytesWindow.reduce((a, b) => a + b, 0)
    bytesWindow = []
  }, 1000)
  connectRFB()
  if (props.initialUrl) navigate(props.initialUrl)
  document.addEventListener('fullscreenchange', onFullscreenChange)
  document.addEventListener('click', handleClipClickOutside)
})

onUnmounted(() => {
  if (rfb) { try { rfb.disconnect() } catch { /* noop */ } rfb = null }
  if (reconnectTimer) clearTimeout(reconnectTimer)
  if (rateTimer) clearInterval(rateTimer)
  document.removeEventListener('fullscreenchange', onFullscreenChange)
  document.removeEventListener('click', handleClipClickOutside)
})

watch(() => props.wsUrl, () => {
  connected.value = false
  connectRFB()
})

watch(qualityLevel, applyQuality)
watch(compressionLevel, applyQuality)
</script>

<template>
  <div ref="viewerRoot" class="relative w-full h-full flex flex-col">
    <!-- Status bar + toolbar -->
    <div class="shrink-0 flex items-center gap-2 px-3 py-1 bg-[var(--color-surface)] border-b border-[var(--color-border)] text-[11px] font-mono select-none overflow-x-auto">
      <!-- Connection status -->
      <span class="flex items-center gap-1.5 shrink-0">
        <span class="w-1.5 h-1.5 rounded-full" :class="connected ? 'bg-emerald-400' : 'bg-red-400 animate-pulse'" />
        <span :class="connected ? 'text-emerald-400' : 'text-red-400'">{{ connected ? 'Connected' : 'Disconnected' }}</span>
      </span>

      <span v-if="desktopName" class="text-[var(--color-text-dim)] shrink-0 truncate max-w-32" :title="desktopName">{{ desktopName }}</span>

      <span class="w-px h-3.5 bg-[var(--color-border)] shrink-0" />

      <span class="text-[var(--color-text-dim)] shrink-0">↓ {{ fmtBytes(totalRecv) }}</span>
      <span class="text-[var(--color-text-dim)] shrink-0">↑ {{ fmtBytes(totalSent) }}</span>
      <span class="text-[var(--color-text-dim)] shrink-0">{{ fmtRate(currentRate) }}</span>

      <span class="w-px h-3.5 bg-[var(--color-border)] shrink-0" />

      <!-- Clipboard -->
      <button
        ref="clipBtnRef"
        @click="toggleClipboard"
        class="px-1.5 py-0.5 rounded text-[10px] transition-colors shrink-0"
        :class="clipboardOpen ? 'bg-lime-600/30 text-lime-300' : 'bg-[var(--color-surface-hover)] text-[var(--color-text-dim)] hover:text-[var(--color-text)]'"
        title="剪贴板"
      >Clip</button>

      <!-- Ctrl+Alt+Del -->
      <button
        @click="sendCtrlAltDel"
        :disabled="!connected"
        class="px-1.5 py-0.5 rounded text-[10px] bg-[var(--color-surface-hover)] text-[var(--color-text-dim)] hover:text-[var(--color-text)] transition-colors disabled:opacity-30 shrink-0"
        title="发送 Ctrl+Alt+Del"
      >C-A-D</button>

      <!-- Scale mode -->
      <button
        @click="toggleScaleMode"
        class="px-1.5 py-0.5 rounded text-[10px] transition-colors shrink-0"
        :class="scaleMode === 'scale' ? 'bg-blue-600/20 text-blue-400' : 'bg-cyan-600/20 text-cyan-400'"
        :title="scaleMode === 'scale' ? '缩放模式：适应容器' : '缩放模式：调整远程分辨率'"
      >{{ scaleMode === 'scale' ? '适应' : '原生' }}</button>

      <!-- Quality -->
      <span class="flex items-center gap-1 shrink-0">
        <span class="text-[var(--color-text-dim)] text-[10px]">Q</span>
        <input
          type="range"
          v-model.number="qualityLevel"
          min="0" max="9" step="1"
          class="w-12 h-2 accent-lime-500"
          title="画质 (0=低, 9=高)"
        />
        <span class="text-[var(--color-text-dim)] w-3 text-center">{{ qualityLevel }}</span>
      </span>

      <!-- View only -->
      <button
        @click="toggleViewOnly"
        class="px-1.5 py-0.5 rounded text-[10px] transition-colors shrink-0"
        :class="viewOnly ? 'bg-amber-600/20 text-amber-400' : 'bg-[var(--color-surface-hover)] text-[var(--color-text-dim)] hover:text-[var(--color-text)]'"
        :title="viewOnly ? '只读模式（点击切换为交互模式）' : '交互模式（点击切换为只读模式）'"
      >{{ viewOnly ? '只读' : '交互' }}</button>

      <!-- Fullscreen -->
      <button
        @click="toggleFullscreen"
        class="px-1.5 py-0.5 rounded text-[10px] bg-[var(--color-surface-hover)] text-[var(--color-text-dim)] hover:text-[var(--color-text)] transition-colors shrink-0"
        :title="isFullscreen ? '退出全屏' : '全屏'"
      >{{ isFullscreen ? '退出' : '全屏' }}</button>

      <span class="ml-auto text-lime-400/60 text-[10px] shrink-0">noVNC Mode</span>
    </div>

    <!-- VNC display area -->
    <div ref="vncContainer" class="flex-1 relative overflow-hidden bg-black" />

    <!-- Clipboard floating panel -->
    <Teleport to="body">
      <div
        v-if="clipboardOpen"
        ref="clipPanelRef"
        class="fixed z-[9990] w-64 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg shadow-xl p-2"
        :style="{ top: clipPos.top + 'px', left: clipPos.left + 'px' }"
      >
        <textarea
          v-model="clipboardText"
          rows="4"
          class="w-full bg-[var(--color-bg)] border border-[var(--color-border)] rounded text-xs text-[var(--color-text)] p-1.5 resize-none outline-none focus:border-[var(--color-accent)]"
          placeholder="双向剪贴板..."
        />
        <div class="flex gap-1.5 mt-1.5">
          <button
            @click="pasteClipboard"
            :disabled="clipLoading || !clipboardText"
            class="flex-1 px-2 py-1 rounded text-[10px] font-medium bg-lime-600/20 text-lime-400 border border-lime-600/30 hover:bg-lime-600/30 transition-colors disabled:opacity-40"
          >{{ clipLoading ? '...' : '发送到远程' }}</button>
          <button
            @click="getRemoteClipboard"
            :disabled="clipLoading"
            class="flex-1 px-2 py-1 rounded text-[10px] font-medium bg-sky-600/20 text-sky-400 border border-sky-600/30 hover:bg-sky-600/30 transition-colors disabled:opacity-40"
          >{{ clipLoading ? '...' : '从远程获取' }}</button>
          <button
            @click="clipboardOpen = false"
            class="px-2 py-1 rounded text-[10px] font-medium bg-[var(--color-surface-hover)] text-[var(--color-text-dim)] hover:text-[var(--color-text)] transition-colors"
          >关闭</button>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
input[type="range"] {
  -webkit-appearance: none;
  appearance: none;
  background: transparent;
  cursor: pointer;
}
input[type="range"]::-webkit-slider-runnable-track {
  height: 3px;
  border-radius: 2px;
  background: var(--color-border);
}
input[type="range"]::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #84cc16;
  margin-top: -3.5px;
}
</style>
