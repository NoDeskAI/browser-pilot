<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'

const props = defineProps<{ wsUrl: string }>()

const containerRef = ref<HTMLDivElement>()
const canvasRef = ref<HTMLCanvasElement>()
const connected = ref(false)
const fps = ref(0)
const stale = ref(false)

let ws: WebSocket | null = null
let frameCount = 0
let fpsTimer: ReturnType<typeof setInterval>
let reconnectTimer: ReturnType<typeof setTimeout>
let staleTimer: ReturnType<typeof setTimeout>

let imgWidth = 1280
let imgHeight = 720
let canvasCssW = 0
let canvasCssH = 0
let lastMoveTime = 0
const MOVE_THROTTLE = 50
const STALE_TIMEOUT = 3000

let ro: ResizeObserver | null = null

function fitCanvas() {
  const container = containerRef.value
  const canvas = canvasRef.value
  if (!container || !canvas || imgWidth === 0 || imgHeight === 0) return

  const cw = container.clientWidth
  const ch = container.clientHeight
  const imgRatio = imgWidth / imgHeight
  const containerRatio = cw / ch

  if (imgRatio > containerRatio) {
    canvasCssW = cw
    canvasCssH = Math.round(cw / imgRatio)
  } else {
    canvasCssH = ch
    canvasCssW = Math.round(ch * imgRatio)
  }
  canvas.style.width = canvasCssW + 'px'
  canvas.style.height = canvasCssH + 'px'
}

function markFresh() {
  stale.value = false
  clearTimeout(staleTimer)
  staleTimer = setTimeout(() => { stale.value = true }, STALE_TIMEOUT)
}

function connect() {
  if (ws) { try { ws.close() } catch {} }

  ws = new WebSocket(props.wsUrl)

  ws.onopen = () => {
    connected.value = true
    markFresh()
  }

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data)
    if (msg.type === 'frame') {
      frameCount++
      markFresh()
      drawFrame(msg.data)
    }
  }

  ws.onclose = () => {
    connected.value = false
    reconnectTimer = setTimeout(connect, 3000)
  }

  ws.onerror = () => { connected.value = false }
}

function drawFrame(base64: string) {
  const canvas = canvasRef.value
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  if (!ctx) return

  const img = new Image()
  img.onload = () => {
    const sizeChanged = canvas.width !== img.width || canvas.height !== img.height
    if (sizeChanged) {
      canvas.width = img.width
      canvas.height = img.height
      imgWidth = img.width
      imgHeight = img.height
      fitCanvas()
    }
    ctx.drawImage(img, 0, 0)
  }
  img.src = `data:image/jpeg;base64,${base64}`
}

function getScaledCoords(e: MouseEvent): { x: number; y: number } {
  const canvas = canvasRef.value!
  const rect = canvas.getBoundingClientRect()
  return {
    x: Math.round((e.clientX - rect.left) * (imgWidth / rect.width)),
    y: Math.round((e.clientY - rect.top) * (imgHeight / rect.height)),
  }
}

function sendEvent(data: object) {
  if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(data))
}

function onMouseMove(e: MouseEvent) {
  const now = Date.now()
  if (now - lastMoveTime < MOVE_THROTTLE) return
  lastMoveTime = now
  const { x, y } = getScaledCoords(e)
  sendEvent({ type: 'mousemove', x, y })
}

function onMouseDown(e: MouseEvent) {
  e.preventDefault()
  canvasRef.value?.focus()
  const { x, y } = getScaledCoords(e)
  sendEvent({ type: 'mousedown', x, y, button: e.button })
}

function onMouseUp(e: MouseEvent) {
  const { x, y } = getScaledCoords(e)
  sendEvent({ type: 'mouseup', x, y, button: e.button })
}

function onWheel(e: WheelEvent) {
  e.preventDefault()
  const { x, y } = getScaledCoords(e)
  sendEvent({ type: 'wheel', x, y, deltaX: e.deltaX, deltaY: e.deltaY })
}

function onKeyDown(e: KeyboardEvent) {
  e.preventDefault()
  sendEvent({ type: 'keydown', key: e.key, code: e.code, keyCode: e.keyCode })
}

function onKeyUp(e: KeyboardEvent) {
  e.preventDefault()
  sendEvent({ type: 'keyup', key: e.key, code: e.code, keyCode: e.keyCode })
}

onMounted(() => {
  connect()
  fpsTimer = setInterval(() => { fps.value = frameCount; frameCount = 0 }, 1000)
  ro = new ResizeObserver(fitCanvas)
  if (containerRef.value) ro.observe(containerRef.value)
})

onUnmounted(() => {
  clearInterval(fpsTimer)
  clearTimeout(reconnectTimer)
  clearTimeout(staleTimer)
  if (ws) ws.close()
  if (ro) ro.disconnect()
})
</script>

<template>
  <div class="h-full flex flex-col bg-black">
    <div class="shrink-0 flex items-center gap-2 px-3 py-1 bg-[var(--color-surface)] border-b border-[var(--color-border)] text-xs text-[var(--color-text-dim)]">
      <span class="w-2 h-2 rounded-full" :class="connected && !stale ? 'bg-green-500' : connected ? 'bg-yellow-500 animate-pulse' : 'bg-red-500'" />
      <span>{{ !connected ? '连接中...' : stale ? '重连中...' : 'CDP 已连接' }}</span>
      <span class="ml-auto">{{ fps }} FPS</span>
    </div>

    <div ref="containerRef" class="flex-1 flex items-center justify-center overflow-hidden relative">
      <canvas
        ref="canvasRef"
        v-show="connected"
        tabindex="0"
        class="cursor-default outline-none"
        @mousemove="onMouseMove"
        @mousedown="onMouseDown"
        @mouseup="onMouseUp"
        @wheel.prevent="onWheel"
        @keydown="onKeyDown"
        @keyup="onKeyUp"
        @contextmenu.prevent
      />

      <!-- Stale overlay — keeps last frame visible but shows reconnect message -->
      <div
        v-if="stale && connected"
        class="absolute inset-0 flex items-center justify-center bg-black/50 pointer-events-none"
      >
        <div class="text-center text-white/80">
          <div class="animate-pulse text-sm mb-1">画面中断，重连中...</div>
        </div>
      </div>

      <div v-if="!connected" class="text-center text-[var(--color-text-dim)]">
        <div class="animate-pulse text-lg mb-2">正在连接 CDP 代理...</div>
        <p class="text-sm">确保相关服务已启动</p>
      </div>
    </div>
  </div>
</template>
