<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import RFB from '@novnc/novnc'

const { t } = useI18n()

const props = defineProps<{
  wsUrl: string
  sessionId: string
}>()

const vncContainer = ref<HTMLDivElement | null>(null)
const viewerRoot = ref<HTMLDivElement | null>(null)
const connected = ref(false)
const desktopName = ref('')
const qualityLevel = ref(9)
const compressionLevel = ref(0)
const scaleMode = ref<'scale' | 'resize'>('scale')
const viewOnly = ref(false)
const clipboardText = ref('')
const clipboardOpen = ref(false)
const clipBtnRef = ref<HTMLElement>()
const clipPanelRef = ref<HTMLElement>()
const clipPos = ref({ top: 0, left: 0 })
const clipLoading = ref(false)
const isFullscreen = ref(false)
const browserLang = ref('zh-CN')
const langLoading = ref(false)
const langError = ref('')
const LANG_OPTIONS = [
  { value: 'zh-CN', label: '中文' },
  { value: 'en-US', label: 'English' },
  { value: 'ja', label: '日本語' },
  { value: 'ko', label: '한국어' },
  { value: 'fr', label: 'Français' },
  { value: 'de', label: 'Deutsch' },
  { value: 'es', label: 'Español' },
  { value: 'ru', label: 'Русский' },
]
const totalRecv = ref(0)
const totalSent = ref(0)
const currentRate = ref(0)
const clickIndicator = ref<{ x: number; y: number; screenX: number; screenY: number; key: number } | null>(null)
let clickIndicatorTimer: ReturnType<typeof setTimeout> | null = null

let rfb: RFB | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let reconnectAttempts = 0
const MAX_RECONNECT_ATTEMPTS = 3
let bytesWindow: number[] = []
let rateTimer: ReturnType<typeof setInterval> | null = null

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
    reconnectAttempts = 0
  })

  rfb.addEventListener('disconnect', (e: CustomEvent<{ clean: boolean }>) => {
    connected.value = false
    if (!e.detail.clean && reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
      reconnectAttempts++
      scheduleReconnect()
    }
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
      body: JSON.stringify({ sessionId: props.sessionId, url }),
    })
    const data = await resp.json()
  } catch { /* ignore */ }
}

async function pasteClipboard() {
  if (!clipboardText.value || clipLoading.value) return
  clipLoading.value = true
  try {
    await fetch('/api/docker/clipboard', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sessionId: props.sessionId, action: 'paste', text: clipboardText.value }),
    })
  } catch { /* ignore */ }
  clipLoading.value = false
}

async function getRemoteClipboard() {
  if (clipLoading.value) return
  clipLoading.value = true
  try {
    const resp = await fetch('/api/docker/clipboard', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sessionId: props.sessionId, action: 'get' }),
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
    getRemoteClipboard()
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

async function changeLang(lang: string) {
  if (langLoading.value) return
  langLoading.value = true
  langError.value = ''
  try {
    const resp = await fetch('/api/docker/browser-lang', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sessionId: props.sessionId, lang }),
    })
    if (!resp.ok) {
      const data = await resp.json().catch(() => null)
      langError.value = data?.error || t('vnc.requestFailed', { status: resp.status })
      setTimeout(() => { langError.value = '' }, 4000)
      return
    }
    const data = await resp.json()
    if (data.ok) {
      browserLang.value = lang
    } else {
      langError.value = data.error || t('vnc.switchFailed')
      setTimeout(() => { langError.value = '' }, 4000)
    }
  } catch {
    langError.value = t('vnc.networkError')
    setTimeout(() => { langError.value = '' }, 4000)
  } finally {
    langLoading.value = false
  }
}

function highlightClick(x: number, y: number, offsetX = 0, offsetY = 0) {
  const canvas = vncContainer.value?.querySelector('canvas')
  if (!canvas) return

  const containerRect = vncContainer.value!.getBoundingClientRect()
  const canvasRect = canvas.getBoundingClientRect()

  const canvasOffsetX = canvasRect.left - containerRect.left
  const canvasOffsetY = canvasRect.top - containerRect.top
  const scaleX = canvasRect.width / canvas.width
  const scaleY = canvasRect.height / canvas.height

  clickIndicator.value = {
    x, y,
    screenX: canvasOffsetX + (x + offsetX) * scaleX,
    screenY: canvasOffsetY + (y + offsetY) * scaleY,
    key: Date.now(),
  }

  if (clickIndicatorTimer) clearTimeout(clickIndicatorTimer)
  clickIndicatorTimer = setTimeout(() => { clickIndicator.value = null }, 3000)
}

defineExpose({ navigate, highlightClick })

onMounted(() => {
  rateTimer = setInterval(() => {
    currentRate.value = bytesWindow.reduce((a, b) => a + b, 0)
    bytesWindow = []
  }, 1000)
  connectRFB()
  document.addEventListener('fullscreenchange', onFullscreenChange)
  document.addEventListener('click', handleClipClickOutside)
})

onUnmounted(() => {
  if (rfb) { try { rfb.disconnect() } catch { /* noop */ } rfb = null }
  if (reconnectTimer) clearTimeout(reconnectTimer)
  if (rateTimer) clearInterval(rateTimer)
  if (clickIndicatorTimer) clearTimeout(clickIndicatorTimer)
  document.removeEventListener('fullscreenchange', onFullscreenChange)
  document.removeEventListener('click', handleClipClickOutside)
})

watch(() => props.wsUrl, () => {
  connected.value = false
  reconnectAttempts = 0
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
        :title="t('vnc.clipboard')"
      >Clip</button>

      <!-- Scale mode -->
      <button
        @click="toggleScaleMode"
        class="px-1.5 py-0.5 rounded text-[10px] transition-colors shrink-0"
        :class="scaleMode === 'scale' ? 'bg-blue-600/20 text-blue-400' : 'bg-cyan-600/20 text-cyan-400'"
        :title="scaleMode === 'scale' ? t('vnc.scaleFitTitle') : t('vnc.scaleNativeTitle')"
      >{{ scaleMode === 'scale' ? t('vnc.scaleFit') : t('vnc.scaleNative') }}</button>

      <!-- Quality -->
      <span class="flex items-center gap-1 shrink-0">
        <span class="text-[var(--color-text-dim)] text-[10px]">Q</span>
        <input
          type="range"
          v-model.number="qualityLevel"
          min="0" max="9" step="1"
          class="w-12 h-2 accent-lime-500"
          :title="t('vnc.quality')"
        />
        <span class="text-[var(--color-text-dim)] w-3 text-center">{{ qualityLevel }}</span>
      </span>

      <!-- View only -->
      <button
        @click="toggleViewOnly"
        class="px-1.5 py-0.5 rounded text-[10px] transition-colors shrink-0"
        :class="viewOnly ? 'bg-amber-600/20 text-amber-400' : 'bg-[var(--color-surface-hover)] text-[var(--color-text-dim)] hover:text-[var(--color-text)]'"
        :title="viewOnly ? t('vnc.viewOnlyTitle') : t('vnc.interactiveTitle')"
      >{{ viewOnly ? t('vnc.viewOnly') : t('vnc.interactive') }}</button>

      <!-- Fullscreen -->
      <button
        @click="toggleFullscreen"
        class="px-1.5 py-0.5 rounded text-[10px] bg-[var(--color-surface-hover)] text-[var(--color-text-dim)] hover:text-[var(--color-text)] transition-colors shrink-0"
        :title="isFullscreen ? t('vnc.exitFullscreenTitle') : t('vnc.fullscreenTitle')"
      >{{ isFullscreen ? t('vnc.exitFullscreen') : t('vnc.fullscreen') }}</button>

      <!-- Language -->
      <select
        :value="browserLang"
        @change="changeLang(($event.target as HTMLSelectElement).value)"
        :disabled="langLoading"
        class="px-1 py-0.5 rounded text-[10px] bg-[var(--color-surface-hover)] text-[var(--color-text-dim)] border border-[var(--color-border)] outline-none cursor-pointer shrink-0 disabled:opacity-40"
        :title="t('vnc.browserLangTitle')"
      >
        <option v-for="opt in LANG_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
      </select>
      <span v-if="langError" class="text-[10px] text-yellow-400 shrink-0 whitespace-nowrap max-w-40 truncate" :title="langError">{{ langError }}</span>

      <span class="ml-auto text-lime-400/60 text-[10px] shrink-0">noVNC Mode</span>
    </div>

    <!-- VNC display area -->
    <div class="flex-1 relative overflow-hidden bg-black">
      <div ref="vncContainer" class="absolute inset-0" />
      <div v-if="clickIndicator" :key="clickIndicator.key" class="absolute inset-0 pointer-events-none z-10 click-indicator-lifecycle">
        <div class="absolute bg-red-500/30 h-px" :style="{ left: 0, right: 0, top: clickIndicator.screenY + 'px' }" />
        <div class="absolute bg-red-500/30 w-px" :style="{ top: 0, bottom: 0, left: clickIndicator.screenX + 'px' }" />
        <div class="click-ring" :style="{ left: clickIndicator.screenX + 'px', top: clickIndicator.screenY + 'px' }" />
        <div class="click-ring" :style="{ left: clickIndicator.screenX + 'px', top: clickIndicator.screenY + 'px' }" style="animation-delay: 0.4s" />
        <div class="click-dot" :style="{ left: clickIndicator.screenX + 'px', top: clickIndicator.screenY + 'px' }" />
        <div class="click-label" :style="{ left: (clickIndicator.screenX + 14) + 'px', top: (clickIndicator.screenY - 8) + 'px' }">{{ clickIndicator.x }}, {{ clickIndicator.y }}</div>
      </div>
    </div>

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
          :placeholder="t('vnc.clipPlaceholder')"
        />
        <div class="flex gap-1.5 mt-1.5">
          <button
            @click="pasteClipboard"
            :disabled="clipLoading || !clipboardText"
            class="flex-1 px-2 py-1 rounded text-[10px] font-medium bg-lime-600/20 text-lime-400 border border-lime-600/30 hover:bg-lime-600/30 transition-colors disabled:opacity-40"
          >{{ clipLoading ? '...' : t('vnc.sendToRemote') }}</button>
          <button
            @click="getRemoteClipboard"
            :disabled="clipLoading"
            class="flex-1 px-2 py-1 rounded text-[10px] font-medium bg-sky-600/20 text-sky-400 border border-sky-600/30 hover:bg-sky-600/30 transition-colors disabled:opacity-40"
          >{{ clipLoading ? '...' : t('vnc.getFromRemote') }}</button>
          <button
            @click="clipboardOpen = false"
            class="px-2 py-1 rounded text-[10px] font-medium bg-[var(--color-surface-hover)] text-[var(--color-text-dim)] hover:text-[var(--color-text)] transition-colors"
          >{{ t('vnc.close') }}</button>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.click-indicator-lifecycle {
  animation: click-lifecycle 3s ease-out forwards;
}
@keyframes click-lifecycle {
  0%, 60% { opacity: 1; }
  100% { opacity: 0; }
}
.click-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #ef4444;
  box-shadow: 0 0 8px rgba(239, 68, 68, 0.9);
  position: absolute;
  transform: translate(-50%, -50%);
}
.click-ring {
  position: absolute;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  border: 2px solid #ef4444;
  transform: translate(-50%, -50%);
  animation: click-ring-pulse 1.2s ease-out infinite;
}
@keyframes click-ring-pulse {
  0% { transform: translate(-50%, -50%) scale(0.3); opacity: 1; }
  100% { transform: translate(-50%, -50%) scale(2); opacity: 0; }
}
.click-label {
  position: absolute;
  font-size: 11px;
  font-family: monospace;
  font-weight: 600;
  color: #ef4444;
  background: rgba(0, 0, 0, 0.75);
  padding: 1px 6px;
  border-radius: 3px;
  white-space: nowrap;
}
select {
  -webkit-appearance: none;
  appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='8' height='8' viewBox='0 0 8 8'%3E%3Cpath fill='%23888' d='M0 2l4 4 4-4z'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 4px center;
  padding-right: 14px;
}
select option {
  background: #1a1a2e;
  color: #e0e0e0;
}
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
