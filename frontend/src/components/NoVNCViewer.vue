<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import RFB from '@novnc/novnc'
import { useSessions } from '../composables/useSessions'
import { api } from '../lib/api'
import {
  Clipboard, Maximize, Minimize, Eye, MousePointer,
  Globe, Network, Loader2, Fingerprint,
} from 'lucide-vue-next'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { Slider } from '@/components/ui/slider'
import { Input } from '@/components/ui/input'
import {
  Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import {
  Popover, PopoverContent, PopoverTrigger,
} from '@/components/ui/popover'
import {
  Tooltip, TooltipContent, TooltipProvider, TooltipTrigger,
} from '@/components/ui/tooltip'
import { Toggle } from '@/components/ui/toggle'

const { t } = useI18n()
const { state: sessState, changeDevicePreset, changeProxy, regenerateFingerprint } = useSessions()

const props = defineProps<{
  wsUrl: string
  sessionId: string
}>()

const vncContainer = ref<HTMLDivElement | null>(null)
const viewerRoot = ref<HTMLDivElement | null>(null)
const connected = ref(false)
const desktopName = ref('')
const qualityLevel = ref([9])
const compressionLevel = ref(0)
const scaleMode = ref<'scale' | 'resize'>('scale')
const viewOnly = ref(false)
const clipboardText = ref('')
const clipboardOpen = ref(false)
const clipLoading = ref(false)
const isFullscreen = ref(false)
const activeSession = computed(() => sessState.sessions.find(s => s.id === props.sessionId))
const browserLang = ref(activeSession.value?.browserLang || 'zh-CN')
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
const reconnectExhausted = ref(false)
const proxyOpen = ref(false)
const proxyInput = ref('')
const fpOpen = ref(false)

const currentSession = computed(() => sessState.sessions.find(s => s.id === props.sessionId))
const currentPreset = computed(() => currentSession.value?.devicePreset || 'desktop-1920x1080')
const currentProxy = computed(() => currentSession.value?.proxyUrl || '')
const fpProfile = computed(() => currentSession.value?.fingerprintProfile || null)
const desktopPresets = computed(() => sessState.devicePresets.filter(p => p.category === 'desktop'))
const mobilePresets = computed(() => sessState.devicePresets.filter(p => p.category === 'mobile'))

const totalRecv = ref(0)
const totalSent = ref(0)
const currentRate = ref(0)

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
  rfb.qualityLevel = qualityLevel.value[0] ?? 9
  rfb.compressionLevel = compressionLevel.value
  rfb.viewOnly = viewOnly.value
  rfb.focusOnClick = true

  rfb.addEventListener('connect', () => {
    connected.value = true
    reconnectAttempts = 0
    reconnectExhausted.value = false
  })

  rfb.addEventListener('disconnect', (e: CustomEvent<{ clean: boolean }>) => {
    connected.value = false
    if (!e.detail.clean && reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
      reconnectAttempts++
      scheduleReconnect()
    } else if (!e.detail.clean) {
      reconnectExhausted.value = true
    }
  })

  rfb.addEventListener('desktopname', (e: CustomEvent<{ name: string }>) => {
    desktopName.value = e.detail.name
  })

  rfb.addEventListener('credentialsrequired', () => {
    if (rfb) rfb.sendCredentials({ password: '' })
  })
}

function manualReconnect() {
  reconnectAttempts = 0
  reconnectExhausted.value = false
  connectRFB()
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
    const resp = await api('/api/docker/navigate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sessionId: props.sessionId, url }),
    })
    await resp.json()
  } catch { /* ignore */ }
}

async function pasteClipboard() {
  if (!clipboardText.value || clipLoading.value) return
  clipLoading.value = true
  try {
    await api('/api/docker/clipboard', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sessionId: props.sessionId, action: 'paste', text: clipboardText.value }),
    })
  } catch {
    const { toast } = await import('vue-sonner')
    toast.error(t('vnc.clipboardError'))
  }
  clipLoading.value = false
}

async function getRemoteClipboard() {
  if (clipLoading.value) return
  clipLoading.value = true
  try {
    const resp = await api('/api/docker/clipboard', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sessionId: props.sessionId, action: 'get' }),
    })
    const data = await resp.json()
    if (data.ok && data.text != null) clipboardText.value = data.text
  } catch { /* ignore */ }
  clipLoading.value = false
}

function onClipboardOpenChange(open: boolean) {
  clipboardOpen.value = open
  if (open) getRemoteClipboard()
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
    rfb.qualityLevel = qualityLevel.value[0] ?? 9
    rfb.compressionLevel = compressionLevel.value
  }
}

async function toggleFullscreen() {
  const el = viewerRoot.value
  if (!el) return
  try {
    if (!document.fullscreenElement) {
      await el.requestFullscreen()
      isFullscreen.value = true
    } else {
      await document.exitFullscreen()
      isFullscreen.value = false
    }
  } catch {
    const { toast } = await import('vue-sonner')
    toast.error(t('vnc.fullscreenUnavailable'))
  }
}

function onFullscreenChange() {
  isFullscreen.value = !!document.fullscreenElement
}

async function changeLang(rawLang: string | number | bigint | Record<string, any> | null) {
  const lang = String(rawLang ?? '')
  if (langLoading.value || !lang) return
  langLoading.value = true
  langError.value = ''
  try {
    const resp = await api('/api/docker/browser-lang', {
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

async function onDeviceChange(preset: string | number | bigint | Record<string, any> | null) {
  const val = String(preset ?? '')
  if (sessState.containerRestarting || val === currentPreset.value || !val) return
  await changeDevicePreset(props.sessionId, val)
}

function onProxyOpenChange(open: boolean) {
  proxyOpen.value = open
  if (open) proxyInput.value = currentProxy.value
}

async function saveProxy() {
  if (sessState.containerRestarting) return
  proxyOpen.value = false
  await changeProxy(props.sessionId, proxyInput.value.trim())
}

async function clearProxy() {
  if (sessState.containerRestarting) return
  proxyInput.value = ''
  proxyOpen.value = false
  await changeProxy(props.sessionId, '')
}

async function regenerateFp() {
  if (sessState.containerRestarting) return
  fpOpen.value = false
  await regenerateFingerprint(props.sessionId)
}

function fpPlatformLabel(profile: Record<string, any>): string {
  const p = profile?.navigator?.platform || ''
  if (p === 'Win32') return 'Windows'
  if (p === 'MacIntel') return 'macOS'
  if (p.startsWith('Linux')) return 'Linux'
  return p
}

defineExpose({ navigate })

onMounted(() => {
  rateTimer = setInterval(() => {
    currentRate.value = bytesWindow.reduce((a, b) => a + b, 0)
    bytesWindow = []
  }, 1000)
  connectRFB()
  document.addEventListener('fullscreenchange', onFullscreenChange)
})

onUnmounted(() => {
  if (rfb) { try { rfb.disconnect() } catch { /* noop */ } rfb = null }
  if (reconnectTimer) clearTimeout(reconnectTimer)
  if (rateTimer) clearInterval(rateTimer)
  document.removeEventListener('fullscreenchange', onFullscreenChange)
})

watch(() => props.wsUrl, () => {
  connected.value = false
  reconnectAttempts = 0
  connectRFB()
})

watch(() => activeSession.value?.browserLang, (lang) => {
  if (lang) browserLang.value = lang
}, { once: true })

watch(qualityLevel, applyQuality)
</script>

<template>
  <div ref="viewerRoot" class="relative w-full h-full flex flex-col">
    <TooltipProvider :delay-duration="300">
      <!-- Toolbar -->
      <div class="shrink-0 flex items-center gap-1.5 px-2 py-1 border-b border-border text-[11px] font-mono select-none overflow-x-auto">
        <!-- Connection status group -->
        <Tooltip>
          <TooltipTrigger as-child>
            <span class="flex items-center gap-1 shrink-0 px-1">
              <span class="size-1.5 rounded-full" :class="connected ? 'bg-emerald-400' : 'bg-red-400 animate-pulse'" />
              <span class="text-[10px]" :class="connected ? 'text-emerald-400' : 'text-red-400'">{{ connected ? t('vnc.connected') : t('vnc.disconnected') }}</span>
            </span>
          </TooltipTrigger>
          <TooltipContent v-if="desktopName">{{ desktopName }}</TooltipContent>
        </Tooltip>

        <Button
          v-if="!connected && reconnectExhausted"
          variant="ghost" size="sm"
          class="h-5 px-1.5 text-[10px] text-red-400 hover:text-red-300"
          @click="manualReconnect"
        >{{ t('vnc.reconnect') }}</Button>

        <Separator orientation="vertical" class="h-3.5" />

        <!-- Traffic stats -->
        <span class="flex items-center gap-1.5 text-[10px] text-muted-foreground shrink-0">
          <span>↓{{ fmtBytes(totalRecv) }}</span>
          <span>↑{{ fmtBytes(totalSent) }}</span>
          <span>{{ fmtRate(currentRate) }}</span>
        </span>

        <Separator orientation="vertical" class="h-3.5" />

        <!-- Clipboard popover -->
        <Popover :open="clipboardOpen" @update:open="onClipboardOpenChange">
          <PopoverTrigger as-child>
            <Button variant="ghost" size="sm" class="h-5 px-1.5 text-[10px] gap-1" :class="clipboardOpen ? 'text-lime-400' : ''" :title="t('vnc.clipboard')">
              <Clipboard class="size-3" />
              {{ t('vnc.clip') }}
            </Button>
          </PopoverTrigger>
          <PopoverContent class="w-64 p-2" align="start">
            <textarea
              v-model="clipboardText"
              rows="4"
              class="w-full bg-background border border-border rounded text-xs p-1.5 resize-none outline-none focus:border-ring"
              :placeholder="t('vnc.clipPlaceholder')"
            />
            <div class="flex gap-1.5 mt-1.5">
              <Button
                @click="pasteClipboard"
                :disabled="clipLoading || !clipboardText"
                variant="outline" size="sm"
                class="flex-1 h-6 text-[10px] border-lime-600/30 text-lime-400 hover:bg-lime-600/10"
              >{{ clipLoading ? '...' : t('vnc.sendToRemote') }}</Button>
              <Button
                @click="getRemoteClipboard"
                :disabled="clipLoading"
                variant="outline" size="sm"
                class="flex-1 h-6 text-[10px] border-sky-600/30 text-sky-400 hover:bg-sky-600/10"
              >{{ clipLoading ? '...' : t('vnc.getFromRemote') }}</Button>
            </div>
          </PopoverContent>
        </Popover>

        <!-- Scale mode toggle -->
        <Tooltip>
          <TooltipTrigger as-child>
            <Toggle
              :model-value="scaleMode === 'scale'"
              @update:model-value="toggleScaleMode"
              size="sm"
              class="h-5 px-1.5 text-[10px] data-[state=on]:text-blue-400"
            >{{ scaleMode === 'scale' ? t('vnc.scaleFit') : t('vnc.scaleNative') }}</Toggle>
          </TooltipTrigger>
          <TooltipContent>{{ scaleMode === 'scale' ? t('vnc.scaleFitTitle') : t('vnc.scaleNativeTitle') }}</TooltipContent>
        </Tooltip>

        <!-- Quality slider -->
        <Tooltip>
          <TooltipTrigger as-child>
            <span class="flex items-center gap-1 shrink-0 px-0.5">
              <span class="text-[10px] text-muted-foreground">Q</span>
              <Slider v-model="qualityLevel" :min="0" :max="9" :step="1" class="w-12" />
              <span class="text-[10px] text-muted-foreground w-3 text-center">{{ qualityLevel[0] }}</span>
            </span>
          </TooltipTrigger>
          <TooltipContent>{{ t('vnc.quality') }}</TooltipContent>
        </Tooltip>

        <!-- View-only toggle -->
        <Tooltip>
          <TooltipTrigger as-child>
            <Toggle
              :model-value="viewOnly"
              @update:model-value="toggleViewOnly"
              size="sm"
              class="h-5 px-1.5 text-[10px] gap-1 data-[state=on]:text-amber-400"
            >
              <Eye v-if="viewOnly" class="size-3" />
              <MousePointer v-else class="size-3" />
              {{ viewOnly ? t('vnc.viewOnly') : t('vnc.interactive') }}
            </Toggle>
          </TooltipTrigger>
          <TooltipContent>{{ viewOnly ? t('vnc.viewOnlyTitle') : t('vnc.interactiveTitle') }}</TooltipContent>
        </Tooltip>

        <!-- Fullscreen -->
        <Tooltip>
          <TooltipTrigger as-child>
            <Button variant="ghost" size="sm" class="h-5 px-1.5 text-[10px]" @click="toggleFullscreen">
              <Minimize v-if="isFullscreen" class="size-3" />
              <Maximize v-else class="size-3" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>{{ isFullscreen ? t('vnc.exitFullscreenTitle') : t('vnc.fullscreenTitle') }}</TooltipContent>
        </Tooltip>

        <Separator orientation="vertical" class="h-3.5" />

        <!-- Device preset -->
        <Select :model-value="currentPreset" @update:model-value="onDeviceChange" :disabled="sessState.containerRestarting">
          <Tooltip>
            <TooltipTrigger as-child>
              <SelectTrigger class="h-5 w-auto min-w-24 max-w-40 text-[10px] px-1.5 border-0 bg-transparent gap-1">
                <SelectValue :placeholder="t('vnc.device')" />
              </SelectTrigger>
            </TooltipTrigger>
            <TooltipContent>{{ t('vnc.device') }}</TooltipContent>
          </Tooltip>
          <SelectContent>
            <SelectGroup>
              <SelectLabel class="text-[10px]">{{ t('vnc.deviceDesktop') }}</SelectLabel>
              <SelectItem v-for="p in desktopPresets" :key="p.id" :value="p.id" class="text-xs">{{ p.label }}</SelectItem>
            </SelectGroup>
            <SelectGroup>
              <SelectLabel class="text-[10px]">{{ t('vnc.deviceMobile') }}</SelectLabel>
              <SelectItem v-for="p in mobilePresets" :key="p.id" :value="p.id" class="text-xs">{{ p.label }} — {{ p.width }}×{{ p.height }}</SelectItem>
            </SelectGroup>
          </SelectContent>
        </Select>

        <!-- Proxy popover -->
        <Popover :open="proxyOpen" @update:open="onProxyOpenChange">
          <PopoverTrigger as-child>
            <Button
              variant="ghost" size="sm"
              :disabled="sessState.containerRestarting"
              class="h-5 px-1.5 text-[10px] gap-1"
              :class="currentProxy ? 'text-emerald-400' : ''"
              :title="currentProxy ? t('vnc.proxyActive') + ': ' + currentProxy : t('vnc.proxy')"
            >
              <Network class="size-3" />
              {{ t('vnc.proxy') }}
            </Button>
          </PopoverTrigger>
          <PopoverContent class="w-64 p-2" align="start">
            <Input
              v-model="proxyInput"
              type="text"
              class="h-7 text-xs"
              :placeholder="t('vnc.proxyPlaceholder')"
              @keydown.enter="saveProxy"
            />
            <div class="flex gap-1.5 mt-1.5">
              <Button
                @click="saveProxy"
                :disabled="sessState.containerRestarting"
                variant="outline" size="sm"
                class="flex-1 h-6 text-[10px] border-lime-600/30 text-lime-400 hover:bg-lime-600/10"
              >{{ sessState.containerRestarting ? t('vnc.proxySaving') : t('vnc.proxySave') }}</Button>
              <Button
                @click="clearProxy"
                :disabled="sessState.containerRestarting || !currentProxy"
                variant="outline" size="sm"
                class="flex-1 h-6 text-[10px] border-destructive/30 text-destructive hover:bg-destructive/10"
              >{{ t('vnc.proxyClear') }}</Button>
            </div>
          </PopoverContent>
        </Popover>

        <!-- Fingerprint popover -->
        <Popover :open="fpOpen" @update:open="(o: boolean) => fpOpen = o">
          <PopoverTrigger as-child>
            <Button
              variant="ghost" size="sm"
              :disabled="sessState.containerRestarting"
              class="h-5 px-1.5 text-[10px] gap-1"
              :class="fpProfile ? 'text-violet-400' : ''"
              :title="t('vnc.fingerprintTitle')"
            >
              <Fingerprint class="size-3" />
              {{ t('vnc.fingerprint') }}
            </Button>
          </PopoverTrigger>
          <PopoverContent class="w-72 p-3" align="start">
            <template v-if="fpProfile">
              <div class="space-y-1.5 text-xs">
                <div class="flex justify-between">
                  <span class="text-muted-foreground">{{ t('vnc.fpPlatform') }}</span>
                  <span class="font-mono">{{ fpPlatformLabel(fpProfile) }}</span>
                </div>
                <div class="flex justify-between">
                  <span class="text-muted-foreground">{{ t('vnc.fpGpu') }}</span>
                  <span class="font-mono truncate max-w-[160px]" :title="fpProfile.webgl?.renderer">{{ fpProfile.webgl?.renderer?.split(',')[0] || '-' }}</span>
                </div>
                <div class="flex justify-between">
                  <span class="text-muted-foreground">{{ t('vnc.fpMemory') }}</span>
                  <span class="font-mono">{{ fpProfile.navigator?.deviceMemory }} GB · {{ fpProfile.navigator?.hardwareConcurrency }} cores</span>
                </div>
                <div class="flex justify-between">
                  <span class="text-muted-foreground">{{ t('vnc.fpTimezone') }}</span>
                  <span class="font-mono">{{ fpProfile.timezone }}</span>
                </div>
                <div class="flex justify-between">
                  <span class="text-muted-foreground">{{ t('vnc.fpSeed') }}</span>
                  <span class="font-mono text-muted-foreground">{{ fpProfile.seed }}</span>
                </div>
              </div>
              <Button
                @click="regenerateFp"
                :disabled="sessState.containerRestarting"
                variant="outline" size="sm"
                class="w-full mt-3 h-7 text-[11px] border-violet-600/30 text-violet-400 hover:bg-violet-600/10"
              >{{ sessState.containerRestarting ? t('vnc.fpRegenerating') : t('vnc.fpRegenerate') }}</Button>
            </template>
            <span v-else class="text-xs text-muted-foreground">-</span>
          </PopoverContent>
        </Popover>

        <span v-if="sessState.containerRestarting" class="text-[10px] text-amber-400 shrink-0 animate-pulse">
          <Loader2 class="size-3 inline animate-spin mr-0.5" />
          {{ t('vnc.switchingDevice') }}
        </span>

        <!-- Browser language -->
        <Select :model-value="browserLang" @update:model-value="changeLang" :disabled="langLoading">
          <Tooltip>
            <TooltipTrigger as-child>
              <SelectTrigger class="h-5 w-auto min-w-16 max-w-24 text-[10px] px-1.5 border-0 bg-transparent gap-1">
                <Globe class="size-3 shrink-0" />
                <SelectValue />
              </SelectTrigger>
            </TooltipTrigger>
            <TooltipContent>{{ t('vnc.browserLangTitle') }}</TooltipContent>
          </Tooltip>
          <SelectContent>
            <SelectItem v-for="opt in LANG_OPTIONS" :key="opt.value" :value="opt.value" class="text-xs">{{ opt.label }}</SelectItem>
          </SelectContent>
        </Select>

        <span v-if="langError" class="text-[10px] text-amber-400 shrink-0 truncate max-w-40">{{ langError }}</span>
      </div>
    </TooltipProvider>

    <!-- VNC display area -->
    <div class="flex-1 relative overflow-hidden bg-black">
      <div ref="vncContainer" class="absolute inset-0" />
    </div>
  </div>
</template>

<style scoped>
:deep(.slider-track) {
  height: 3px;
}
</style>
