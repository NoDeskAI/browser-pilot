<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{ streamUrl: string }>()

const VIEWPORT_W = 1280
const VIEWPORT_H = 720
const MOVE_THROTTLE = 80

const containerRef = ref<HTMLDivElement>()
const imgRef = ref<HTMLImageElement>()
const connected = ref(false)
let lastMoveTime = 0

const apiBase = props.streamUrl.replace(/\/stream$/, '')

function getCoords(e: MouseEvent): { x: number; y: number } | null {
  const img = imgRef.value
  if (!img) return null
  const rect = img.getBoundingClientRect()
  const scaleX = VIEWPORT_W / rect.width
  const scaleY = VIEWPORT_H / rect.height
  const x = Math.round((e.clientX - rect.left) * scaleX)
  const y = Math.round((e.clientY - rect.top) * scaleY)
  if (x < 0 || y < 0 || x > VIEWPORT_W || y > VIEWPORT_H) return null
  return { x, y }
}

function post(path: string, body: object) {
  fetch(`${apiBase}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).catch(() => {})
}

function onClick(e: MouseEvent) {
  const coords = getCoords(e)
  if (coords) post('/click', coords)
  containerRef.value?.focus()
}

function onMouseMove(e: MouseEvent) {
  const now = Date.now()
  if (now - lastMoveTime < MOVE_THROTTLE) return
  lastMoveTime = now
  const coords = getCoords(e)
  if (coords) post('/mousemove', coords)
}

function onWheel(e: WheelEvent) {
  e.preventDefault()
  const coords = getCoords(e)
  if (coords) {
    post('/scroll', { ...coords, deltaX: e.deltaX, deltaY: e.deltaY })
  }
}

function onKeyDown(e: KeyboardEvent) {
  if (e.key.length === 1) {
    post('/type', { text: e.key })
  } else {
    const keyMap: Record<string, string> = {
      Enter: 'Enter', Backspace: 'Backspace', Tab: 'Tab',
      Escape: 'Escape', ArrowUp: 'ArrowUp', ArrowDown: 'ArrowDown',
      ArrowLeft: 'ArrowLeft', ArrowRight: 'ArrowRight',
      Delete: 'Delete', Home: 'Home', End: 'End',
      PageUp: 'PageUp', PageDown: 'PageDown',
    }
    if (keyMap[e.key]) {
      post('/key', { key: keyMap[e.key] })
    }
  }
  e.preventDefault()
}

function onImgLoad() {
  connected.value = true
}

function onImgError() {
  connected.value = false
}
</script>

<template>
  <div class="h-full flex flex-col bg-black">
    <div class="shrink-0 flex items-center gap-2 px-3 py-1 bg-[var(--color-surface)] border-b border-[var(--color-border)] text-xs text-[var(--color-text-dim)]">
      <span class="w-2 h-2 rounded-full" :class="connected ? 'bg-green-500' : 'bg-orange-500'" />
      <span>MJPEG 交互流</span>
      <span class="ml-auto">~5 FPS · 点击/键盘/滚轮可交互</span>
    </div>

    <div
      ref="containerRef"
      class="flex-1 flex items-center justify-center overflow-hidden cursor-pointer outline-none"
      tabindex="0"
      @click="onClick"
      @mousemove="onMouseMove"
      @wheel.prevent="onWheel"
      @keydown="onKeyDown"
    >
      <img
        ref="imgRef"
        :src="streamUrl"
        class="max-w-full max-h-full object-contain pointer-events-none select-none"
        draggable="false"
        alt="MJPEG Browser Stream"
        @load="onImgLoad"
        @error="onImgError"
      />
    </div>
  </div>
</template>
