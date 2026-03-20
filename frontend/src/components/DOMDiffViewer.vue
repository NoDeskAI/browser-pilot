<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'

const props = defineProps<{
  wsUrl: string
  initialUrl: string
}>()

const emit = defineEmits<{
  urlChange: [url: string]
}>()

const iframeRef = ref<HTMLIFrameElement | null>(null)
const viewerArea = ref<HTMLDivElement | null>(null)
const connected = ref(false)
const totalBytes = ref(0)
const currentRate = ref(0)
const eventCount = ref(0)
const pageUrl = ref('')
const snapshotBytes = ref(0)
const diffBytes = ref(0)
const snapshotCount = ref(0)
const mutationCount = ref(0)

let ws: WebSocket | null = null
const nodeMap = new Map<number, Node>()
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

function buildNode(doc: Document, data: any): Node | null {
  if (!data) return null

  if (data.t === 3) {
    const node = doc.createTextNode(data.d ?? '')
    nodeMap.set(data.id, node)
    return node
  }

  if (data.t === 8) {
    const node = doc.createComment(data.d ?? '')
    nodeMap.set(data.id, node)
    return node
  }

  if (data.t === 1) {
    let el: Element
    if (data.ns) {
      el = doc.createElementNS(data.ns, data.tag)
    } else {
      el = doc.createElement(data.tag)
    }
    for (const [k, v] of Object.entries(data.a || {})) {
      try { el.setAttribute(k, v as string) } catch {}
    }
    for (const child of data.c || []) {
      const cn = buildNode(doc, child)
      if (cn) el.appendChild(cn)
    }
    nodeMap.set(data.id, el)
    return el
  }

  return null
}

function applySnapshot(snapshot: any) {
  const iframe = iframeRef.value
  if (!iframe) return
  const doc = iframe.contentDocument
  if (!doc) return

  nodeMap.clear()
  doc.open()
  doc.write('<!DOCTYPE html><html><head></head><body></body></html>')
  doc.close()

  const html = buildNode(doc, snapshot.html)
  if (html && doc.documentElement) {
    try { doc.replaceChild(html, doc.documentElement) } catch {}
  }
}

function applyMutations(ops: any[]) {
  for (const op of ops) {
    switch (op.op) {
      case 'add': {
        const parent = nodeMap.get(op.pid)
        if (!parent) break
        const doc = (parent as Element).ownerDocument
        if (!doc) break
        const node = buildNode(doc, op.n)
        if (!node) break
        const ref = op.ref != null ? nodeMap.get(op.ref) : null
        try { parent.insertBefore(node, ref ?? null) } catch {}
        break
      }
      case 'rm': {
        const node = nodeMap.get(op.id)
        if (node?.parentNode) {
          try { node.parentNode.removeChild(node) } catch {}
          nodeMap.delete(op.id)
        }
        break
      }
      case 'attr': {
        const node = nodeMap.get(op.id) as Element
        if (!node?.setAttribute) break
        if (op.v === null) {
          try { node.removeAttribute(op.k) } catch {}
        } else {
          try { node.setAttribute(op.k, op.v) } catch {}
        }
        break
      }
      case 'text': {
        const node = nodeMap.get(op.id) as CharacterData
        if (node && 'data' in node) node.data = op.d ?? ''
        break
      }
    }
  }
}

function connect() {
  if (ws) { ws.close(); ws = null }

  try {
    ws = new WebSocket(props.wsUrl)
  } catch {
    scheduleReconnect()
    return
  }

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
      switch (msg.type) {
        case 'snapshot':
          snapshotBytes.value += size
          snapshotCount.value++
          applySnapshot(msg.data)
          if (msg.data.url) {
            pageUrl.value = msg.data.url
            emit('urlChange', msg.data.url)
          }
          break
        case 'mutations':
          diffBytes.value += size
          mutationCount.value += (msg.data?.length ?? 0)
          applyMutations(msg.data)
          break
        case 'scroll':
          iframeRef.value?.contentWindow?.scrollTo(msg.data.x, msg.data.y)
          break
        case 'url':
          pageUrl.value = msg.data
          emit('urlChange', msg.data)
          break
        case 'error':
          console.warn('[DOMDiff] Server error:', msg.data)
          break
      }
    } catch (err) {
      console.error('[DOMDiff] Message parse error:', err)
    }
  }

  ws.onclose = () => {
    connected.value = false
    scheduleReconnect()
  }

  ws.onerror = () => { connected.value = false }
}

function scheduleReconnect() {
  if (reconnectTimer) clearTimeout(reconnectTimer)
  reconnectTimer = setTimeout(() => {
    if (!connected.value) connect()
  }, 3000)
}

function send(msg: any) {
  if (ws?.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(msg))
  }
}

function navigate(url: string) {
  totalBytes.value = 0
  snapshotBytes.value = 0
  diffBytes.value = 0
  snapshotCount.value = 0
  mutationCount.value = 0
  eventCount.value = 0
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

function onMouseUp(e: MouseEvent) {
  send({ type: 'mouseup', x: e.offsetX, y: e.offsetY, button: e.button })
}

function onMouseMove(e: MouseEvent) {
  const now = Date.now()
  if (now - mouseThrottle < 50) return
  mouseThrottle = now
  send({ type: 'mousemove', x: e.offsetX, y: e.offsetY })
}

function onWheel(e: WheelEvent) {
  send({ type: 'wheel', dx: e.deltaX, dy: e.deltaY })
}

function onKeyDown(e: KeyboardEvent) {
  e.preventDefault()
  send({ type: 'keydown', key: e.key, code: e.code })
}

function onKeyUp(e: KeyboardEvent) {
  e.preventDefault()
  send({ type: 'keyup', key: e.key, code: e.code })
}

defineExpose({ navigate })

let resizeObs: ResizeObserver | null = null

onMounted(() => {
  rateTimer = setInterval(() => {
    const total = bytesWindow.reduce((a, b) => a + b, 0)
    currentRate.value = total
    bytesWindow = []
  }, 1000)

  const iframe = iframeRef.value
  if (iframe) {
    iframe.addEventListener('load', () => connect(), { once: true })
    iframe.src = 'about:blank'
  }

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
  const iframe = iframeRef.value
  if (iframe) {
    iframe.addEventListener('load', () => connect(), { once: true })
    iframe.src = 'about:blank'
  }
})
</script>

<template>
  <div class="relative w-full h-full flex flex-col">
    <!-- Stats bar -->
    <div class="shrink-0 flex items-center gap-3 px-3 py-1 bg-[var(--color-surface)] border-b border-[var(--color-border)] text-[11px] font-mono select-none overflow-x-auto">
      <span class="flex items-center gap-1.5 shrink-0">
        <span
          class="w-1.5 h-1.5 rounded-full"
          :class="connected ? 'bg-emerald-400' : 'bg-red-400 animate-pulse'"
        />
        <span :class="connected ? 'text-emerald-400' : 'text-red-400'">
          {{ connected ? 'Connected' : 'Disconnected' }}
        </span>
      </span>
      <span class="text-[var(--color-text-dim)] shrink-0">
        ↓ {{ fmtBytes(totalBytes) }}
      </span>
      <span class="text-cyan-400/80 shrink-0" :title="`Snapshot: ${fmtBytes(snapshotBytes)} (${snapshotCount}x) | Mutations: ${fmtBytes(diffBytes)} (${mutationCount} ops)`">
        snap {{ fmtBytes(snapshotBytes) }} + diff {{ fmtBytes(diffBytes) }}
      </span>
      <span class="text-[var(--color-text-dim)] shrink-0">
        {{ fmtRate(currentRate) }}
      </span>
      <span class="text-[var(--color-text-dim)] shrink-0">
        {{ eventCount }} events
      </span>
      <span
        v-if="totalBytes > 0"
        class="text-amber-400/50 shrink-0"
        title="VNC/noVNC 在相同分辨率下的参考带宽（1280x720, ~10fps）"
      >
        vs VNC ~2-10 Mbps
      </span>
      <span class="ml-auto text-cyan-400/60 text-[10px] shrink-0">DOM Diff Mode</span>
    </div>

    <!-- Viewer -->
    <div ref="viewerArea" class="flex-1 relative overflow-hidden">
      <iframe
        ref="iframeRef"
        class="absolute inset-0 w-full h-full border-0 bg-white"
      />
      <!-- Transparent interaction overlay -->
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
