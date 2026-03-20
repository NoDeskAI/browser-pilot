<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'

const props = defineProps<{
  wsUrl: string
  initialUrl: string
}>()

const emit = defineEmits<{
  urlChange: [url: string]
}>()

const viewerArea = ref<HTMLDivElement | null>(null)
const rrwebContainer = ref<HTMLDivElement | null>(null)
const connected = ref(false)
const totalBytes = ref(0)
const currentRate = ref(0)
const eventCount = ref(0)
const snapshotCount = ref(0)

let ws: WebSocket | null = null
let bytesWindow: number[] = []
let rateTimer: ReturnType<typeof setInterval> | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let mouseThrottle = 0
let resizeDebounce: ReturnType<typeof setTimeout> | null = null
let replayer: any = null
let rrwebLoaded = false
let pendingEvents: any[] = []

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

function loadScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    if (document.querySelector(`script[src="${src}"]`)) { resolve(); return }
    const el = document.createElement('script')
    el.src = src
    el.onload = () => resolve()
    el.onerror = reject
    document.head.appendChild(el)
  })
}

function loadCSS(href: string): Promise<void> {
  return new Promise((resolve, reject) => {
    if (document.querySelector(`link[href="${href}"]`)) { resolve(); return }
    const el = document.createElement('link')
    el.rel = 'stylesheet'
    el.href = href
    el.onload = () => resolve()
    el.onerror = reject
    document.head.appendChild(el)
  })
}

async function ensureRrweb(): Promise<boolean> {
  if (rrwebLoaded) return true
  try {
    await Promise.all([
      loadScript('https://cdn.jsdelivr.net/npm/rrweb@2.0.0-alpha.13/dist/rrweb.umd.cjs'),
      loadCSS('https://cdn.jsdelivr.net/npm/rrweb@2.0.0-alpha.13/dist/style.css'),
    ])
    rrwebLoaded = true
    return true
  } catch {
    try {
      await loadScript('https://cdn.jsdelivr.net/npm/rrweb@1.1.3/dist/rrweb.min.js')
      rrwebLoaded = true
      return true
    } catch (e) {
      console.error('[rrweb] Failed to load rrweb from CDN:', e)
      return false
    }
  }
}

function destroyReplayer() {
  if (replayer) {
    try { replayer.pause() } catch {}
    replayer = null
  }
  if (rrwebContainer.value) {
    rrwebContainer.value.innerHTML = ''
  }
}

function createReplayer() {
  destroyReplayer()
  if (!rrwebContainer.value || !(window as any).rrweb) return false
  try {
    const RrwebReplayer = (window as any).rrweb.Replayer
    replayer = new RrwebReplayer([], {
      root: rrwebContainer.value,
      liveMode: true,
      insertStyleRules: [
        'iframe { border: none !important; width: 100% !important; height: 100% !important; }',
      ],
    })
    replayer.startLive()
    return true
  } catch (e) {
    console.error('[rrweb] Failed to create Replayer:', e)
    return false
  }
}

function handleRrwebEvent(event: any) {
  if (event.type === 4 || event.type === 2) {
    snapshotCount.value++
    if (!replayer) {
      pendingEvents.push(event)
      if (event.type === 2) {
        if (createReplayer()) {
          for (const ev of pendingEvents) {
            try { replayer.addEvent(ev) } catch {}
          }
          pendingEvents = []
        }
      }
      return
    }
  }
  if (replayer) {
    try { replayer.addEvent(event) } catch {}
  } else {
    pendingEvents.push(event)
  }
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
    eventCount.value++

    try {
      const msg = JSON.parse(e.data as string)
      if (msg.type === 'rrweb-event') {
        handleRrwebEvent(msg.data)
      } else if (msg.type === 'rrweb-reset') {
        destroyReplayer()
        pendingEvents = []
      } else if (msg.type === 'url') {
        emit('urlChange', msg.data)
      }
    } catch (err) {
      console.error('[rrweb] Message parse error:', err)
    }
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
  eventCount.value = 0
  snapshotCount.value = 0
  currentRate.value = 0
  bytesWindow = []
  destroyReplayer()
  pendingEvents = []
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

onMounted(async () => {
  rateTimer = setInterval(() => {
    currentRate.value = bytesWindow.reduce((a, b) => a + b, 0)
    bytesWindow = []
  }, 1000)

  await ensureRrweb()
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
  destroyReplayer()
})

watch(() => props.wsUrl, () => {
  if (ws) { ws.close(); ws = null }
  connected.value = false
  destroyReplayer()
  pendingEvents = []
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
      <span class="text-[var(--color-text-dim)] shrink-0">{{ eventCount }} events</span>
      <span class="text-emerald-400/60 shrink-0">{{ snapshotCount }} snapshots</span>
      <span class="ml-auto text-green-400/60 text-[10px] shrink-0">rrweb Live Mode</span>
    </div>
    <div ref="viewerArea" class="flex-1 relative overflow-hidden">
      <div ref="rrwebContainer" class="absolute inset-0 overflow-hidden" />
      <div
        class="absolute inset-0 cursor-default z-10"
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
