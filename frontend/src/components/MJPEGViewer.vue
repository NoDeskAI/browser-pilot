<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue'

const props = defineProps<{
  serviceUrl: string
  initialUrl: string
}>()

const emit = defineEmits<{
  urlChange: [url: string]
}>()

const viewerArea = ref<HTMLDivElement | null>(null)
const imgRef = ref<HTMLImageElement | null>(null)
const streaming = ref(false)
const totalBytes = ref(0)
const frameCount = ref(0)
const currentRate = ref(0)
const avgFrameSize = ref(0)
const interactionWsConnected = ref(false)

const streamUrl = computed(() => `${props.serviceUrl}/stream`)

let statsTimer: ReturnType<typeof setInterval> | null = null
let mouseThrottle = 0
let interactionWs: WebSocket | null = null
let resizeDebounce: ReturnType<typeof setTimeout> | null = null
let resizeObs: ResizeObserver | null = null

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

async function fetchStats() {
  try {
    const res = await fetch(`${props.serviceUrl}/stats`)
    const data = await res.json()
    totalBytes.value = data.totalBytes
    frameCount.value = data.frames
    avgFrameSize.value = data.avgFrameSize
    const elapsed = data.elapsed || 1
    currentRate.value = elapsed > 0 ? data.totalBytes / elapsed : 0
  } catch {}
}

function sendResize() {
  const el = viewerArea.value
  if (!el) return
  sendInput({ type: 'resize', width: el.clientWidth, height: el.clientHeight })
}

function connectInteractionWs() {
  const wsUrl = props.serviceUrl.replace(/^http/, 'ws') + '/ws'
  try {
    interactionWs = new WebSocket(wsUrl)
    interactionWs.onopen = () => {
      interactionWsConnected.value = true
      sendResize()
    }
    interactionWs.onclose = () => {
      interactionWsConnected.value = false
      setTimeout(connectInteractionWs, 3000)
    }
    interactionWs.onerror = () => { interactionWsConnected.value = false }
  } catch {
    setTimeout(connectInteractionWs, 3000)
  }
}

function sendInput(msg: any) {
  if (interactionWs?.readyState === WebSocket.OPEN) {
    interactionWs.send(JSON.stringify(msg))
  }
}

async function navigate(url: string) {
  try {
    const res = await fetch(`${props.serviceUrl}/navigate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    })
    const data = await res.json()
    if (data.url) emit('urlChange', data.url)
    streaming.value = true
    if (imgRef.value) {
      imgRef.value.src = ''
      await new Promise((r) => setTimeout(r, 100))
      imgRef.value.src = streamUrl.value
    }
  } catch (e) {
    console.error('[MJPEG] Navigate error:', e)
  }
}

function onMouseDown(e: MouseEvent) {
  ;(e.currentTarget as HTMLElement)?.focus()
  sendInput({ type: 'mousedown', x: e.offsetX, y: e.offsetY, button: e.button })
}
function onMouseUp(e: MouseEvent) { sendInput({ type: 'mouseup', x: e.offsetX, y: e.offsetY, button: e.button }) }
function onMouseMove(e: MouseEvent) {
  const now = Date.now()
  if (now - mouseThrottle < 50) return
  mouseThrottle = now
  sendInput({ type: 'mousemove', x: e.offsetX, y: e.offsetY })
}
function onWheel(e: WheelEvent) { sendInput({ type: 'wheel', dx: e.deltaX, dy: e.deltaY }) }
function onKeyDown(e: KeyboardEvent) { e.preventDefault(); sendInput({ type: 'keydown', key: e.key, code: e.code }) }
function onKeyUp(e: KeyboardEvent) { e.preventDefault(); sendInput({ type: 'keyup', key: e.key, code: e.code }) }

defineExpose({ navigate })

onMounted(() => {
  statsTimer = setInterval(fetchStats, 1000)
  connectInteractionWs()
  if (props.initialUrl) navigate(props.initialUrl)

  if (viewerArea.value) {
    resizeObs = new ResizeObserver(() => {
      if (resizeDebounce) clearTimeout(resizeDebounce)
      resizeDebounce = setTimeout(sendResize, 300)
    })
    resizeObs.observe(viewerArea.value)
  }
})

onUnmounted(() => {
  if (statsTimer) clearInterval(statsTimer)
  if (interactionWs) interactionWs.close()
  if (resizeDebounce) clearTimeout(resizeDebounce)
  resizeObs?.disconnect()
})
</script>

<template>
  <div class="relative w-full h-full flex flex-col">
    <div class="shrink-0 flex items-center gap-3 px-3 py-1 bg-[var(--color-surface)] border-b border-[var(--color-border)] text-[11px] font-mono select-none overflow-x-auto">
      <span class="flex items-center gap-1.5 shrink-0">
        <span class="w-1.5 h-1.5 rounded-full" :class="streaming ? 'bg-emerald-400' : 'bg-red-400 animate-pulse'" />
        <span :class="streaming ? 'text-emerald-400' : 'text-red-400'">{{ streaming ? 'Streaming' : 'Idle' }}</span>
      </span>
      <span class="text-[var(--color-text-dim)] shrink-0">↓ {{ fmtBytes(totalBytes) }}</span>
      <span class="text-[var(--color-text-dim)] shrink-0">{{ fmtRate(currentRate) }}</span>
      <span class="text-[var(--color-text-dim)] shrink-0">{{ frameCount }} frames</span>
      <span v-if="avgFrameSize > 0" class="text-amber-400/50 shrink-0" :title="`平均帧大小 ${fmtBytes(avgFrameSize)}`">avg {{ fmtBytes(avgFrameSize) }}/frame</span>
      <span class="flex items-center gap-1 shrink-0" :class="interactionWsConnected ? 'text-emerald-400/60' : 'text-red-400/60'">
        <span class="w-1 h-1 rounded-full" :class="interactionWsConnected ? 'bg-emerald-400/60' : 'bg-red-400/60'" />
        WS {{ interactionWsConnected ? '✓' : '✗' }}
      </span>
      <span class="ml-auto text-orange-400/60 text-[10px] shrink-0">MJPEG Stream Mode</span>
    </div>
    <div ref="viewerArea" class="flex-1 relative overflow-hidden bg-black">
      <img
        ref="imgRef"
        class="absolute inset-0 w-full h-full object-contain"
        alt="MJPEG Stream"
      />
      <div
        class="absolute inset-0 cursor-default"
        @mousedown="onMouseDown"
        @mouseup="onMouseUp"
        @mousemove.passive="onMouseMove"
        @wheel.passive="onWheel"
        @keydown="onKeyDown"
        @keyup="onKeyUp"
        @contextmenu.prevent
        tabindex="0"
        style="outline: none;"
      />
    </div>
  </div>
</template>
