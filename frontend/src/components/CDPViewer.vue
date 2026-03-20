<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'

const props = defineProps<{
  wsUrl: string
  initialUrl: string
}>()

const emit = defineEmits<{
  urlChange: [url: string]
}>()

const canvasRef = ref<HTMLCanvasElement | null>(null)
const viewerArea = ref<HTMLDivElement | null>(null)
const connected = ref(false)
const totalBytes = ref(0)
const currentRate = ref(0)
const frameCount = ref(0)

let ws: WebSocket | null = null
let bytesWindow: number[] = []
let rateTimer: ReturnType<typeof setInterval> | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let mouseThrottle = 0
let resizeDebounce: ReturnType<typeof setTimeout> | null = null

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

function connect() {
  if (ws) { ws.close(); ws = null }
  try { ws = new WebSocket(props.wsUrl) } catch { scheduleReconnect(); return }

  ws.onopen = () => {
    connected.value = true
    sendResize()
    if (props.initialUrl) navigate(props.initialUrl)
  }

  ws.onmessage = (e) => {
    const size = typeof e.data === 'string' ? new Blob([e.data]).size : (e.data as ArrayBuffer).byteLength
    totalBytes.value += size
    bytesWindow.push(size)

    try {
      const msg = JSON.parse(e.data as string)
      if (msg.type === 'frame') {
        frameCount.value++
        const img = new Image()
        img.onload = () => {
          const canvas = canvasRef.value
          if (!canvas) return
          const ctx = canvas.getContext('2d')
          if (!ctx) return
          if (canvas.width !== img.width || canvas.height !== img.height) {
            canvas.width = img.width
            canvas.height = img.height
          }
          ctx.drawImage(img, 0, 0)
        }
        img.src = 'data:image/jpeg;base64,' + msg.data
      } else if (msg.type === 'url') {
        emit('urlChange', msg.data)
      }
    } catch {}
  }

  ws.onclose = () => { connected.value = false; scheduleReconnect() }
  ws.onerror = () => { connected.value = false }
}

function scheduleReconnect() {
  if (reconnectTimer) clearTimeout(reconnectTimer)
  reconnectTimer = setTimeout(() => { if (!connected.value) connect() }, 3000)
}

function send(msg: any) {
  if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg))
}

function navigate(url: string) {
  totalBytes.value = 0
  frameCount.value = 0
  currentRate.value = 0
  bytesWindow = []
  send({ type: 'navigate', url })
}

function sendResize() {
  const el = viewerArea.value
  if (!el) return
  send({ type: 'resize', width: el.clientWidth, height: el.clientHeight })
}

function onMouseDown(e: MouseEvent) {
  ;(e.currentTarget as HTMLElement)?.focus()
  send({ type: 'mousedown', x: e.offsetX, y: e.offsetY, button: e.button })
}
function onMouseUp(e: MouseEvent) { send({ type: 'mouseup', x: e.offsetX, y: e.offsetY, button: e.button }) }
function onMouseMove(e: MouseEvent) {
  const now = Date.now()
  if (now - mouseThrottle < 50) return
  mouseThrottle = now
  send({ type: 'mousemove', x: e.offsetX, y: e.offsetY })
}
function onWheel(e: WheelEvent) { send({ type: 'wheel', dx: e.deltaX, dy: e.deltaY }) }
function onKeyDown(e: KeyboardEvent) { e.preventDefault(); send({ type: 'keydown', key: e.key, code: e.code }) }
function onKeyUp(e: KeyboardEvent) { e.preventDefault(); send({ type: 'keyup', key: e.key, code: e.code }) }

defineExpose({ navigate })

let resizeObs: ResizeObserver | null = null

onMounted(() => {
  rateTimer = setInterval(() => {
    currentRate.value = bytesWindow.reduce((a, b) => a + b, 0)
    bytesWindow = []
  }, 1000)
  connect()
  if (viewerArea.value) {
    resizeObs = new ResizeObserver(() => {
      if (resizeDebounce) clearTimeout(resizeDebounce)
      resizeDebounce = setTimeout(sendResize, 300)
    })
    resizeObs.observe(viewerArea.value)
  }
})

onUnmounted(() => {
  if (ws) { ws.close(); ws = null }
  if (rateTimer) clearInterval(rateTimer)
  if (reconnectTimer) clearTimeout(reconnectTimer)
  if (resizeDebounce) clearTimeout(resizeDebounce)
  resizeObs?.disconnect()
})

watch(() => props.wsUrl, () => {
  if (ws) { ws.close(); ws = null }
  connected.value = false
  connect()
})
</script>

<template>
  <div class="relative w-full h-full flex flex-col">
    <div class="shrink-0 flex items-center gap-3 px-3 py-1 bg-[var(--color-surface)] border-b border-[var(--color-border)] text-[11px] font-mono select-none overflow-x-auto">
      <span class="flex items-center gap-1.5 shrink-0">
        <span class="w-1.5 h-1.5 rounded-full" :class="connected ? 'bg-emerald-400' : 'bg-red-400 animate-pulse'" />
        <span :class="connected ? 'text-emerald-400' : 'text-red-400'">{{ connected ? 'Connected' : 'Disconnected' }}</span>
      </span>
      <span class="text-[var(--color-text-dim)] shrink-0">↓ {{ fmtBytes(totalBytes) }}</span>
      <span class="text-[var(--color-text-dim)] shrink-0">{{ fmtRate(currentRate) }}</span>
      <span class="text-[var(--color-text-dim)] shrink-0">{{ frameCount }} frames</span>
      <span v-if="totalBytes > 0" class="text-amber-400/50 shrink-0" title="JPEG 截图参考带宽（1280x720, ~5fps, quality 50）">~150-500 KB/s typical</span>
      <span class="ml-auto text-violet-400/60 text-[10px] shrink-0">CDP Screenshot Mode</span>
    </div>
    <div ref="viewerArea" class="flex-1 relative overflow-hidden bg-black">
      <canvas ref="canvasRef" class="absolute inset-0 w-full h-full object-contain" />
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
