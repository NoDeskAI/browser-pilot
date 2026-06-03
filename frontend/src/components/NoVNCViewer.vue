<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import RFB from '@novnc/novnc'
import { useSessions } from '../composables/useSessions'
import { useNetworkEgress } from '../composables/useNetworkEgress'
import { api } from '../lib/api'
import { osVersionLabel } from '../lib/fingerprintDisplay'
import {
  Keyboard, Maximize, Minimize, Eye, MousePointer,
  Globe, Network, Loader2, Fingerprint,
  CornerDownLeft, ClipboardPaste, Check, RefreshCw, Save, Pencil,
  ScanSearch, X,
} from 'lucide-vue-next'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import { Slider } from '@/components/ui/slider'
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
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import TruncatedTooltipValue from '@/components/TruncatedTooltipValue.vue'

const { t } = useI18n()
const {
  state: sessState,
  brand,
  changeDevicePreset,
  changeNetworkEgress,
  regenerateFingerprint,
  refreshNetworkProfile,
  syncObservedNetworkProfile,
  overrideNetworkProfile,
  fetchSessions,
} = useSessions()
const { state: egressState, fetchNetworkEgress } = useNetworkEgress()

const props = defineProps<{
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
const inputText = ref('')
const inputBarOpen = ref(false)
const inputSending = ref(false)
const inputSent = ref(false)
const inputError = ref(false)
const inputRef = ref<HTMLInputElement | null>(null)
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
const viewerMode = ref<'control' | 'view'>('control')
const preferredViewerMode = ref<'control' | 'view'>('control')
const controlViewerError = ref('')
const controlSwitchError = ref('')
const switchingControl = ref(false)
const connectionError = ref('')
const networkOpen = ref(false)
const selectedNetworkEgressId = ref('__direct__')
const fpOpen = ref(false)
const fpLoading = ref(false)
const fpConfirmRegenerate = ref(false)
const fpNetworkEditOpen = ref(false)
const fpNetworkJson = ref('')
const fpNetworkError = ref('')
const localhostBridgeNotice = ref('')
const DIRECT_EGRESS_VALUE = '__direct__'
type ObserveMode = 'dom' | 'vision' | 'mix'
const observeMode = ref<ObserveMode>('dom')
const observeMaxCandidates = ref(180)
const observeThreshold = ref(0.01)
const observePanelOpen = ref(false)
const observeLoading = ref(false)
const observeError = ref('')
const observeResult = ref<Record<string, any> | null>(null)
const annotatedScreenshotOpen = ref(false)
const annotatedScreenshotFit = ref(true)

const observeModes: ObserveMode[] = ['dom', 'vision', 'mix']

const currentSession = computed(() => sessState.sessions.find(s => s.id === props.sessionId))
const currentPreset = computed(() => currentSession.value?.devicePreset || 'desktop-1920x1080')
const currentNetworkEgressId = computed(() => currentSession.value?.networkEgressId || DIRECT_EGRESS_VALUE)
const currentNetworkName = computed(() => currentSession.value?.networkEgressName || t('networkEgress.direct'))
const currentNetworkStatus = computed(() => currentSession.value?.networkEgressStatus || 'healthy')
const currentNetworkError = computed(() => currentSession.value?.networkEgressHealthError || '')
const runtimeShellToolsEnabled = computed(() => brand.features.runtimeShellTools !== false)
const fpProfile = computed(() => currentSession.value?.fingerprintProfile || null)
const desktopPresets = computed(() => sessState.devicePresets.filter(p => p.category === 'desktop'))
const mobilePresets = computed(() => sessState.devicePresets.filter(p => p.category === 'mobile'))
const domElements = computed(() => observeResult.value?.elements || [])
const axCandidates = computed(() => observeResult.value?.axCandidates || [])
const visionCandidates = computed(() => observeResult.value?.visionCandidates || [])
const visionGroups = computed(() => observeResult.value?.visionGroups || [])
const mixedCandidates = computed(() => observeResult.value?.mixedCandidates || [])
const visibleText = computed(() => String(observeResult.value?.visibleText || '').trim())
const annotatedScreenshot = computed(() => {
  const img = observeResult.value?.annotatedScreenshot
  return img ? `data:image/png;base64,${img}` : ''
})
const observeTrace = computed(() => observeResult.value?.trace || {})
const visionFrame = computed(() => observeResult.value?.visionFrame || null)
const visionFrameStats = computed(() => {
  const frame = visionFrame.value
  if (!frame) return []
  return [
    { label: t('vnc.visionRawSize'), value: fmtSize(frame.rawSize) },
    { label: t('vnc.visionInferenceSize'), value: fmtSize(frame.inferenceSize) },
    { label: t('vnc.visionClickViewport'), value: fmtSize(frame.clickViewport) },
    { label: t('vnc.visionCoordinateSpace'), value: frame.coordinateSpace || 'click-viewport' },
  ].filter(item => item.value)
})
const unknownRatio = computed(() => {
  const ratio = observeTrace.value?.vision_unknown_ratio
  if (typeof ratio === 'number') return `${Math.round(ratio * 100)}%`
  const total = visionCandidates.value.length
  if (!total) return ''
  const unknown = visionCandidates.value.filter((item: any) => item?.family === 'unknown').length
  return `${Math.round((unknown / total) * 100)}%`
})
const semanticSourceStats = computed(() => {
  const counts = new Map<string, number>()
  for (const item of visionCandidates.value) {
    const key = String(item?.semanticSource || 'unknown')
    counts.set(key, (counts.get(key) || 0) + 1)
  }
  return Array.from(counts.entries())
    .sort((a, b) => b[1] - a[1])
    .map(([label, value]) => ({ label, value }))
})
const observeSummary = computed(() => [
  { key: 'dom', label: 'DOM', value: domElements.value.length },
  { key: 'ax', label: 'AX', value: axCandidates.value.length },
  { key: 'vision', label: 'Vision', value: visionCandidates.value.length },
  { key: 'groups', label: 'Groups', value: visionGroups.value.length },
  { key: 'mix', label: 'Mixed', value: mixedCandidates.value.length },
].filter(item => item.value > 0 || observeMode.value === item.key))
const fusionStats = computed(() => {
  const trace = observeTrace.value || {}
  return [
    { label: 'strategy', value: trace.mix_strategy },
    { label: 'clusters', value: trace.fusion_cluster_count },
    { label: 'fusion ms', value: trace.fusion_ms },
  ].filter(item => item.value !== undefined && item.value !== null && item.value !== '')
})
const viewOnlyToggleTooltip = computed(() => {
  if (switchingControl.value) return t('vnc.switchingToControlTitle')
  if (viewerMode.value === 'view') {
    const reason = controlSwitchError.value || controlViewerError.value || t('vnc.viewOnlyLeaseRequired')
    return t('vnc.viewOnlyBlockedReason', { reason })
  }
  return viewOnly.value ? t('vnc.viewOnlyTitle') : t('vnc.interactiveTitle')
})

const totalRecv = ref(0)
const totalSent = ref(0)

let rfb: RFB | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let reconnectAttempts = 0
let connectSerial = 0
const MAX_RECONNECT_ATTEMPTS = 3

function fmtBytes(b: number): string {
  if (b < 1024) return b + ' B'
  if (b < 1048576) return (b / 1024).toFixed(1) + ' KB'
  return (b / 1048576).toFixed(1) + ' MB'
}

function fmtSize(size: any): string {
  const width = Number(size?.width || 0)
  const height = Number(size?.height || 0)
  if (!width || !height) return ''
  return `${width}×${height}`
}

function clearContainer() {
  const el = vncContainer.value
  if (!el) return
  while (el.firstChild) el.removeChild(el.firstChild)
}

function normalizeViewerUrl(raw: string): string {
  const url = new URL(raw, window.location.href)
  if (url.protocol === 'http:') url.protocol = 'ws:'
  if (url.protocol === 'https:') url.protocol = 'wss:'
  return url.toString()
}

async function requestViewerTicket(mode: 'control' | 'view'): Promise<{ url: string, mode: 'control' | 'view' }> {
  const resp = await api(`/api/sessions/${props.sessionId}/viewer-ticket`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode }),
  })
  const data = await resp.json().catch(() => null)
  if (!resp.ok || !data?.viewerUrl) {
    const err = new Error(data?.detail || data?.error || t('vnc.requestFailed', { status: resp.status }))
    ;(err as any).status = resp.status
    throw err
  }
  return { url: normalizeViewerUrl(data.viewerUrl), mode: data.mode === 'view' ? 'view' : 'control' }
}

async function readApiError(resp: Response, fallback: string): Promise<string> {
  const text = await resp.text().catch(() => '')
  if (!text) return fallback
  try {
    const data = JSON.parse(text)
    return data?.detail || data?.error || data?.message || fallback
  } catch {
    return text || fallback
  }
}

async function fetchViewerUrl(mode: 'control' | 'view' = preferredViewerMode.value): Promise<string> {
  if (mode === 'view') {
    const ticket = await requestViewerTicket('view')
    viewerMode.value = 'view'
    viewOnly.value = true
    inputBarOpen.value = false
    connectionError.value = ''
    return ticket.url
  }
  try {
    const ticket = await requestViewerTicket('control')
    viewerMode.value = ticket.mode
    controlViewerError.value = ''
    controlSwitchError.value = ''
    connectionError.value = ''
    return ticket.url
  } catch (err: any) {
    if (err?.status !== 409) throw err
    controlViewerError.value = err?.message || ''
    const ticket = await requestViewerTicket('view')
    viewerMode.value = 'view'
    viewOnly.value = true
    inputBarOpen.value = false
    connectionError.value = ''
    return ticket.url
  }
}

function retryConnect() {
  if (reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
    reconnectAttempts++
    scheduleReconnect()
  } else {
    reconnectExhausted.value = true
  }
}

async function connectRFB(mode: 'control' | 'view' = preferredViewerMode.value) {
  const serial = ++connectSerial
  if (rfb) {
    try { rfb.disconnect() } catch { /* already disconnected */ }
    rfb = null
  }
  clearContainer()

  const el = vncContainer.value
  if (!el) return

  let wsUrl = ''
  try {
    wsUrl = await fetchViewerUrl(mode)
  } catch (err: any) {
    connectionError.value = err?.message || t('vnc.requestFailed', { status: 0 })
    if (serial === connectSerial) retryConnect()
    return
  }
  if (serial !== connectSerial) return

  const OrigWS = window.WebSocket
  const recvRef = totalRecv
  const sentRef = totalSent
  ;(window as any).WebSocket = new Proxy(OrigWS, {
    construct(target, args) {
      const ws = new target(...(args as [string, string?]))
      ws.addEventListener('message', (e: MessageEvent) => {
        const size = e.data instanceof ArrayBuffer ? e.data.byteLength
          : e.data instanceof Blob ? e.data.size
          : new Blob([e.data]).size
        recvRef.value += size
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
    rfb = new RFB(el, wsUrl)
  } catch {
    window.WebSocket = OrigWS
    connectionError.value = t('vnc.requestFailed', { status: 0 })
    retryConnect()
    return
  }

  window.WebSocket = OrigWS

  rfb.scaleViewport = scaleMode.value === 'scale'
  rfb.resizeSession = scaleMode.value === 'resize'
  rfb.qualityLevel = qualityLevel.value[0] ?? 9
  rfb.compressionLevel = compressionLevel.value
  rfb.viewOnly = viewerMode.value === 'view' || viewOnly.value
  rfb.focusOnClick = true

  rfb.addEventListener('connect', () => {
    connected.value = true
    connectionError.value = ''
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
  void connectRFB()
}

function scheduleReconnect() {
  if (reconnectTimer) clearTimeout(reconnectTimer)
  reconnectTimer = setTimeout(() => {
    if (!connected.value) void connectRFB(preferredViewerMode.value)
  }, 3000)
}

async function navigate(url: string) {
  if (!runtimeShellToolsEnabled.value) return
  totalRecv.value = 0
  totalSent.value = 0
  try {
    const resp = await api('/api/docker/navigate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sessionId: props.sessionId, url }),
    })
    const data = await resp.json().catch(() => null)
    const bridge = data?.localhostBridge
    if (bridge?.enabled) {
      localhostBridgeNotice.value = t('vnc.localhostBridgeActive', { port: bridge.port })
      window.setTimeout(() => { localhostBridgeNotice.value = '' }, 8000)
    } else if (bridge?.warning) {
      localhostBridgeNotice.value = t('vnc.localhostBridgeFailed')
      window.setTimeout(() => { localhostBridgeNotice.value = '' }, 8000)
    }
  } catch { /* ignore */ }
}

async function sendInputText() {
  if (!runtimeShellToolsEnabled.value) return
  if (!inputText.value || inputSending.value) return
  inputSending.value = true
  try {
    await api('/api/docker/clipboard', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sessionId: props.sessionId, action: 'paste', text: inputText.value }),
    })
    inputText.value = ''
    inputSent.value = true
    setTimeout(() => { inputSent.value = false }, 300)
  } catch {
    inputError.value = true
    setTimeout(() => { inputError.value = false }, 1500)
    const { toast } = await import('vue-sonner')
    toast.error(t('vnc.clipboardError'))
  }
  inputSending.value = false
}

async function getRemoteClipboard() {
  if (!runtimeShellToolsEnabled.value) return
  if (inputSending.value) return
  inputSending.value = true
  try {
    const resp = await api('/api/docker/clipboard', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sessionId: props.sessionId, action: 'get' }),
    })
    const data = await resp.json()
    if (data.ok && data.text != null) inputText.value = data.text
  } catch { /* ignore */ }
  inputSending.value = false
}

function onInputKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.isComposing) {
    e.preventDefault()
    sendInputText()
  }
  if (e.key === 'Escape') {
    inputBarOpen.value = false
    rfb?.focus()
  }
}

function toggleScaleMode() {
  scaleMode.value = scaleMode.value === 'scale' ? 'resize' : 'scale'
  if (rfb) {
    rfb.scaleViewport = scaleMode.value === 'scale'
    rfb.resizeSession = scaleMode.value === 'resize'
  }
}

async function switchToControl() {
  if (switchingControl.value) return
  switchingControl.value = true
  controlSwitchError.value = ''
  try {
    preferredViewerMode.value = 'control'
    const resp = await api(`/api/agent-devices/${encodeURIComponent(props.sessionId)}/reclaim`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ leaseMode: 'session_bound' }),
    })
    if (!resp.ok) {
      throw new Error(await readApiError(resp, t('vnc.controlSwitchFailed')))
    }
    viewOnly.value = false
    await fetchSessions().catch(() => undefined)
    await connectRFB('control')
    if (viewerMode.value !== 'control') {
      throw new Error(controlViewerError.value || t('vnc.controlSwitchStillReadOnly'))
    }
  } catch (err: any) {
    preferredViewerMode.value = 'view'
    viewOnly.value = true
    if (rfb) rfb.viewOnly = true
    controlSwitchError.value = err?.message || t('vnc.controlSwitchFailed')
    const { toast } = await import('vue-sonner')
    toast.error(t('vnc.controlSwitchFailedReason', { reason: controlSwitchError.value }))
  } finally {
    switchingControl.value = false
  }
}

async function releaseControlToViewOnly() {
  if (switchingControl.value) return
  const leaseId = activeSession.value?.activeLease?.leaseId || activeSession.value?.activeLease?.id
  if (!leaseId) {
    preferredViewerMode.value = 'view'
    viewOnly.value = true
    await connectRFB('view')
    return
  }

  switchingControl.value = true
  controlSwitchError.value = ''
  try {
    const resp = await api(`/api/agent-devices/${encodeURIComponent(props.sessionId)}/leases/${encodeURIComponent(leaseId)}/release`, {
      method: 'POST',
    })
    if (!resp.ok) {
      throw new Error(await readApiError(resp, t('vnc.controlReleaseFailed')))
    }
    preferredViewerMode.value = 'view'
    viewOnly.value = true
    inputBarOpen.value = false
    await fetchSessions().catch(() => undefined)
    await connectRFB('view')
  } catch (err: any) {
    preferredViewerMode.value = 'control'
    viewOnly.value = false
    if (rfb) rfb.viewOnly = false
    controlSwitchError.value = err?.message || t('vnc.controlReleaseFailed')
    const { toast } = await import('vue-sonner')
    toast.error(t('vnc.controlReleaseFailedReason', { reason: controlSwitchError.value }))
  } finally {
    switchingControl.value = false
  }
}

function toggleViewOnly() {
  if (viewerMode.value === 'view') {
    void switchToControl()
    return
  }
  void releaseControlToViewOnly()
}

function onControlTogglePointerUp(event: PointerEvent) {
  if (event.button !== 0) return
  event.preventDefault()
  event.stopPropagation()
  toggleViewOnly()
}

function onControlToggleKeydown(event: KeyboardEvent) {
  if (event.key !== 'Enter' && event.key !== ' ') return
  event.preventDefault()
  event.stopPropagation()
  toggleViewOnly()
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
  if (!runtimeShellToolsEnabled.value) return
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

async function onNetworkOpenChange(open: boolean) {
  networkOpen.value = open
  if (open) {
    selectedNetworkEgressId.value = currentNetworkEgressId.value
    await fetchNetworkEgress()
  }
}

async function refreshFpSessionSnapshot() {
  if (fpLoading.value) return
  fpLoading.value = true
  try {
    await fetchSessions()
  } finally {
    fpLoading.value = false
  }
}

function onFpOpenChange(open: boolean) {
  fpOpen.value = open
  if (!open) {
    fpConfirmRegenerate.value = false
    fpNetworkEditOpen.value = false
    fpNetworkError.value = ''
    return
  }
  void refreshFpSessionSnapshot()
}

async function saveNetworkEgress() {
  if (sessState.containerRestarting) return
  networkOpen.value = false
  await changeNetworkEgress(
    props.sessionId,
    selectedNetworkEgressId.value === DIRECT_EGRESS_VALUE ? null : selectedNetworkEgressId.value,
  )
}

async function regenerateFp() {
  if (sessState.containerRestarting) return
  if (!fpConfirmRegenerate.value) {
    fpConfirmRegenerate.value = true
    return
  }
  fpOpen.value = false
  fpConfirmRegenerate.value = false
  await regenerateFingerprint(props.sessionId)
}

function fpPlatformLabel(profile: Record<string, any>): string {
  const p = profile?.navigator?.platform || ''
  if (p === 'Win32') return 'Windows'
  if (p === 'MacIntel') return 'macOS'
  if (p.startsWith('Linux')) return 'Linux'
  return p
}

function fpOsVersionLabel(profile: Record<string, any>): string {
  return osVersionLabel(profile?.clientHints)
}

function fpExitLocation(profile: Record<string, any>): string {
  const n = profile?.network
  if (!n) return '-'
  return [n.city, n.region, n.countryCode || n.country].filter(Boolean).join(' / ') || '-'
}

function fpObservedLocation(profile: Record<string, any>): string {
  const n = profile?.network?.observed
  if (!n) return '-'
  return [n.city, n.region, n.countryCode || n.country].filter(Boolean).join(' / ') || '-'
}

function fpDnsLabel(profile: Record<string, any>): string {
  const servers = profile?.network?.dnsServers
  return Array.isArray(servers) && servers.length ? servers.join(', ') : '-'
}

function fpFontPolicyLabel(profile: Record<string, any>): string {
  const policy = profile?.fontPolicy
  const exposed = Array.isArray(policy?.exposedFonts) ? policy.exposedFonts.length : 0
  return policy?.mode ? `${policy.mode} / ${exposed}` : '-'
}

function fpRuntimeHealthLabel(profile: Record<string, any>): string {
  const health = profile?.runtimeHealth
  if (!health) return '-'
  return health.ok ? 'OK' : (health.status || 'warning')
}

function fpReadiness(profile: Record<string, any>): Record<string, any> {
  const readiness = profile?.readiness
  if (readiness && typeof readiness === 'object') return readiness
  const warnings = [
    ...(Array.isArray(profile?.network?.warnings) ? profile.network.warnings : []),
    ...(Array.isArray(profile?.runtimeWarnings) ? profile.runtimeWarnings : []),
  ].map(String)
  if (warnings.includes('network_profile_unverified')) {
    return { ready: false, status: 'unverified_network', reason: 'direct_network_profile_unverified' }
  }
  if (profile?.fingerprintReady === false) {
    return { ready: false, status: 'not_ready', reason: '' }
  }
  return { ready: true, status: 'ready', reason: '' }
}

function fpReadinessOk(profile: Record<string, any>): boolean {
  return fpReadiness(profile).ready !== false
}

function fpReadinessLabel(profile: Record<string, any>): string {
  const readiness = fpReadiness(profile)
  if (readiness.ready !== false) return t('vnc.fpReady')
  if (readiness.reason === 'direct_network_profile_unverified') return t('vnc.fpNotReadyDirectUnverified')
  if (readiness.status === 'unverified_network') return t('vnc.fpNotReadyNetworkUnverified')
  return t('vnc.fpNotReady')
}

function fpReadinessHint(profile: Record<string, any>): string {
  const readiness = fpReadiness(profile)
  if (readiness.ready !== false) return ''
  if (readiness.reason === 'direct_network_profile_unverified') return t('vnc.fpDirectUnverifiedHint')
  if (readiness.status === 'unverified_network') return t('vnc.fpNetworkUnverifiedHint')
  return ''
}

function fpWebglRuntimeLabel(profile: Record<string, any>): string {
  const health = profile?.runtimeHealth
  const checks = health?.checks || {}
  if (checks['webgl.contextAvailable'] === false) return 'unavailable'
  if (checks['webgl2.contextAvailable'] === false) return 'webgl2-unavailable'
  if (checks['webgl.contextAvailable'] === true) {
    const mode = health?.expected?.webglRuntime?.resolved
    const suffix = checks['webgl2.contextAvailable'] === true ? '+webgl2' : ''
    return mode === 'swiftshader' ? `fallback-swiftshader${suffix}` : `${mode || 'available'}${suffix}`
  }
  return '-'
}

function fpWebglRuntimeOk(profile: Record<string, any>): boolean {
  return profile?.runtimeHealth?.checks?.['webgl.contextAvailable'] === true
    && profile?.runtimeHealth?.checks?.['webgl2.contextAvailable'] !== false
}

function fpWarnings(profile: Record<string, any>): string[] {
  const warnings = [
    ...(Array.isArray(profile?.warnings) ? profile.warnings : []),
    ...(Array.isArray(profile?.network?.warnings) ? profile.network.warnings : []),
    ...(Array.isArray(profile?.runtimeWarnings) ? profile.runtimeWarnings : []),
  ]
  return Array.from(new Set(warnings.filter(Boolean).map(String)))
}

async function refreshFpNetworkProfile() {
  try {
    await refreshNetworkProfile(props.sessionId)
    const { toast } = await import('vue-sonner')
    toast.success(t('vnc.fpNetworkRefreshDone'))
  } catch (err: any) {
    const { toast } = await import('vue-sonner')
    toast.error(err?.message || t('vnc.fpNetworkRefreshFailed'))
  }
}

async function syncFpObservedNetwork() {
  try {
    const result = await syncObservedNetworkProfile(props.sessionId)
    const { toast } = await import('vue-sonner')
    toast.success(result.restartRequired ? t('vnc.fpNetworkSyncedRestart') : t('vnc.fpNetworkSynced'))
  } catch (err: any) {
    const { toast } = await import('vue-sonner')
    toast.error(err?.message || t('vnc.fpNetworkSyncFailed'))
  }
}

function openFpNetworkEdit() {
  const network = fpProfile.value?.network || {}
  fpNetworkJson.value = JSON.stringify(network, null, 2)
  fpNetworkError.value = ''
  fpNetworkEditOpen.value = true
}

async function saveFpNetworkOverride() {
  fpNetworkError.value = ''
  try {
    const parsed = JSON.parse(fpNetworkJson.value || '{}')
    const result = await overrideNetworkProfile(props.sessionId, parsed)
    fpNetworkEditOpen.value = false
    const { toast } = await import('vue-sonner')
    toast.success(result.restartRequired ? t('vnc.fpNetworkSavedRestart') : t('vnc.fpNetworkSaved'))
  } catch (err: any) {
    fpNetworkError.value = err?.message || t('vnc.fpNetworkInvalidJson')
  }
}

async function runObserve() {
  if (observeLoading.value) return
  observePanelOpen.value = true
  observeLoading.value = true
  observeError.value = ''
  try {
    const resp = await api('/api/browser/observe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sessionId: props.sessionId,
        mode: observeMode.value,
        maxCandidates: observeMaxCandidates.value,
        threshold: observeThreshold.value,
        includeAnnotatedScreenshot: observeMode.value !== 'dom',
      }),
    })
    const data = await resp.json().catch(() => null)
    if (!resp.ok || !data?.ok) {
      observeError.value = data?.error || data?.detail || t('vnc.observeFailed')
      return
    }
    observeResult.value = data
  } catch {
    observeError.value = t('vnc.networkError')
  } finally {
    observeLoading.value = false
  }
}

function candidateTitle(candidate: any): string {
  const text = candidate?.textHint || candidate?.text || candidate?.domHint?.text || candidate?.axHint?.name || candidate?.attrs?.ariaLabel || candidate?.attrs?.placeholder
  if (text) return String(text).slice(0, 80)
  return candidate?.label || candidate?.family || candidate?.tag || candidate?.source || 'candidate'
}

function candidateSubtitle(candidate: any): string {
  const parts = [
    candidate?.sourceSummary,
    Array.isArray(candidate?.sources) ? candidate.sources.join('+') : '',
    candidate?.tag,
    candidate?.family,
    candidate?.label,
    candidate?.role,
    candidate?.rawLabel,
    candidate?.geometryHint ? `hint:${candidate.geometryHint}` : '',
    candidate?.semanticSource,
    candidate?.source,
  ].filter(Boolean)
  return parts.join(' · ') || 'candidate'
}

function formatPoint(candidate: any): string {
  const center = candidate?.center || (candidate?.x != null && candidate?.y != null ? { x: candidate.x, y: candidate.y } : null)
  if (!center) return ''
  return `(${Math.round(center.x)}, ${Math.round(center.y)})`
}

function formatBox(candidate: any): string {
  const box = candidate?.bbox
  if (!box) return ''
  return `${Math.round(box.x)},${Math.round(box.y)} ${Math.round(box.w)}×${Math.round(box.h)}`
}

function formatScore(score: unknown): string {
  return typeof score === 'number' ? score.toFixed(2) : ''
}

defineExpose({ navigate })

onMounted(() => {
  void connectRFB()
  document.addEventListener('fullscreenchange', onFullscreenChange)
})

onUnmounted(() => {
  connectSerial++
  if (rfb) { try { rfb.disconnect() } catch { /* noop */ } rfb = null }
  if (reconnectTimer) clearTimeout(reconnectTimer)
  document.removeEventListener('fullscreenchange', onFullscreenChange)
})

watch(() => props.sessionId, () => {
  connected.value = false
  reconnectAttempts = 0
  void connectRFB()
})

watch(() => activeSession.value?.browserLang, (lang) => {
  if (lang) browserLang.value = lang
}, { once: true })

watch(qualityLevel, applyQuality)

watch(inputBarOpen, (open) => {
  if (open) nextTick(() => inputRef.value?.focus())
})

watch(runtimeShellToolsEnabled, (enabled) => {
  if (!enabled) inputBarOpen.value = false
})

watch(annotatedScreenshotOpen, (open) => {
  if (open) annotatedScreenshotFit.value = true
})
</script>

<template>
  <div ref="viewerRoot" class="relative w-full h-full flex flex-col">
    <TooltipProvider :delay-duration="300">
      <!-- Toolbar -->
      <div class="shrink-0 flex items-center gap-1.5 px-2 py-1 border-b border-border text-xs font-mono select-none overflow-x-auto">
        <!-- Connection status group -->
        <Tooltip>
          <TooltipTrigger as-child>
            <span class="flex items-center gap-1 shrink-0 px-1">
              <span class="size-1.5 rounded-full" :class="connected ? 'bg-emerald-400' : 'bg-red-400 animate-pulse'" />
              <span class="text-xs" :class="connected ? 'text-emerald-400' : 'text-red-400'">{{ connected ? t('vnc.connected') : t('vnc.disconnected') }}</span>
            </span>
          </TooltipTrigger>
          <TooltipContent v-if="connectionError">{{ connectionError }}</TooltipContent>
          <TooltipContent v-else-if="desktopName">{{ desktopName }}</TooltipContent>
        </Tooltip>

        <Button
          v-if="!connected && reconnectExhausted"
          variant="ghost" size="sm"
          class="h-6 px-2 text-xs text-red-400 hover:text-red-300"
          @click="manualReconnect"
        >{{ t('vnc.reconnect') }}</Button>

        <Separator orientation="vertical" />

        <Tooltip v-if="localhostBridgeNotice">
          <TooltipTrigger as-child>
            <span class="inline-flex items-center gap-1 text-xs text-muted-foreground shrink-0">
              <Globe class="size-3.5" />
              {{ t('vnc.localhostBridgeShort') }}
            </span>
          </TooltipTrigger>
          <TooltipContent>{{ localhostBridgeNotice }}</TooltipContent>
        </Tooltip>

        <Separator v-if="localhostBridgeNotice" orientation="vertical" />

        <!-- Traffic stats -->
        <span class="flex items-center gap-1.5 text-xs text-muted-foreground shrink-0">
          <span>↓{{ fmtBytes(totalRecv) }}</span>
          <span>↑{{ fmtBytes(totalSent) }}</span>
        </span>

        <Separator orientation="vertical" />

        <!-- Observe mode -->
        <div class="flex items-center gap-1 shrink-0 rounded-md border border-border/80 bg-muted/30 p-0.5">
          <Tooltip v-for="mode in observeModes" :key="mode">
            <TooltipTrigger as-child>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                class="h-5 px-2 text-[10px] uppercase"
                :class="observeMode === mode ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground'"
                @click="observeMode = mode"
              >
                {{ mode }}
              </Button>
            </TooltipTrigger>
            <TooltipContent>{{ t(`vnc.observeMode_${mode}`) }}</TooltipContent>
          </Tooltip>
        </div>

        <Tooltip>
          <TooltipTrigger as-child>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              class="h-5 px-1.5 text-[10px] gap-1"
              :class="observePanelOpen ? 'text-cyan-400' : ''"
              :disabled="observeLoading"
              @click="runObserve"
            >
              <Loader2 v-if="observeLoading" class="size-3 animate-spin" />
              <ScanSearch v-else class="size-3" />
              {{ t('vnc.observe') }}
            </Button>
          </TooltipTrigger>
          <TooltipContent>{{ t('vnc.observeTitle') }}</TooltipContent>
        </Tooltip>

        <Separator v-if="runtimeShellToolsEnabled" orientation="vertical" class="h-3.5" />

        <!-- Input bar toggle -->
        <Button v-if="runtimeShellToolsEnabled" variant="ghost" size="sm"
          class="h-6 px-2 text-xs gap-1.5 transition-all duration-200"
          :class="inputBarOpen 
            ? 'bg-[#FFCB00] text-black hover:bg-[#e5b600] hover:text-black dark:hover:bg-[#e5b600] dark:hover:text-black shadow-sm font-bold' 
            : 'text-muted-foreground hover:text-foreground'"
          @click="inputBarOpen = !inputBarOpen">
          <Keyboard class="size-3.5" />
          {{ t('vnc.input') }}
        </Button>

        <!-- Scale mode toggle -->
        <Tooltip>
          <TooltipTrigger as-child>
            <span class="inline-flex" @click="toggleScaleMode">
              <Toggle
                :model-value="scaleMode === 'scale'"
                size="sm"
                class="h-6 px-2 text-xs data-[state=on]:text-blue-400"
              >{{ scaleMode === 'scale' ? t('vnc.scaleFit') : t('vnc.scaleNative') }}</Toggle>
            </span>
          </TooltipTrigger>
          <TooltipContent>{{ scaleMode === 'scale' ? t('vnc.scaleFitTitle') : t('vnc.scaleNativeTitle') }}</TooltipContent>
        </Tooltip>

        <!-- Quality slider -->
        <Tooltip>
          <TooltipTrigger as-child>
            <span class="flex items-center gap-1 shrink-0 px-0.5">
              <span class="text-xs text-muted-foreground">Q</span>
              <Slider v-model="qualityLevel" :min="0" :max="9" :step="1" class="w-12" />
              <span class="text-xs text-muted-foreground w-3 text-center">{{ qualityLevel[0] }}</span>
            </span>
          </TooltipTrigger>
          <TooltipContent>{{ t('vnc.quality') }}</TooltipContent>
        </Tooltip>

        <!-- View-only toggle -->
        <Tooltip>
          <TooltipTrigger as-child>
            <span class="inline-flex">
              <button
                type="button"
                :aria-pressed="viewOnly"
                :disabled="switchingControl"
                class="inline-flex h-6 items-center justify-center gap-1 rounded-[min(var(--radius-md),12px)] border border-transparent px-2 text-xs font-medium whitespace-nowrap transition-all outline-none select-none hover:bg-muted hover:text-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0"
                :class="viewOnly ? 'text-amber-400 hover:text-amber-300' : ''"
                @pointerup="onControlTogglePointerUp"
                @keydown="onControlToggleKeydown"
              >
                <Loader2 v-if="switchingControl" class="size-3.5 animate-spin" />
                <Eye v-else-if="viewOnly" class="size-3.5" />
                <MousePointer v-else class="size-3.5" />
                {{ switchingControl ? t('vnc.switchingControl') : (viewOnly ? t('vnc.viewOnly') : t('vnc.interactive')) }}
              </button>
            </span>
          </TooltipTrigger>
          <TooltipContent class="max-w-80">{{ viewOnlyToggleTooltip }}</TooltipContent>
        </Tooltip>

        <!-- Fullscreen -->
        <Tooltip>
          <TooltipTrigger as-child>
            <Button variant="ghost" size="sm" class="h-6 px-2 text-xs" @click="toggleFullscreen">
              <Minimize v-if="isFullscreen" class="size-3.5" />
              <Maximize v-else class="size-3.5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>{{ isFullscreen ? t('vnc.exitFullscreenTitle') : t('vnc.fullscreenTitle') }}</TooltipContent>
        </Tooltip>

        <Separator orientation="vertical" />

        <!-- Device preset -->
        <Select :model-value="currentPreset" @update:model-value="onDeviceChange" :disabled="sessState.containerRestarting">
          <SelectTrigger class="h-6 w-auto min-w-24 max-w-40 text-xs px-2 border-0 bg-transparent gap-1" :aria-label="t('vnc.device')">
            <SelectValue :placeholder="t('vnc.device')" />
          </SelectTrigger>
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

        <!-- Network egress popover -->
        <Popover :open="networkOpen" @update:open="onNetworkOpenChange">
          <PopoverTrigger as-child>
            <Button
              variant="ghost" size="sm"
              :disabled="sessState.containerRestarting"
              class="h-6 px-2 text-xs gap-1"
              :class="currentNetworkStatus === 'unhealthy' || currentNetworkStatus === 'unsupported' ? 'text-red-400' : currentNetworkEgressId !== DIRECT_EGRESS_VALUE ? 'text-emerald-400' : ''"
              :title="currentNetworkError || currentNetworkName"
            >
              <Network class="size-3.5" />
              {{ t('vnc.network') }}
            </Button>
          </PopoverTrigger>
          <PopoverContent class="w-72 p-2" align="start">
            <div class="space-y-1.5">
              <Select v-model="selectedNetworkEgressId" :disabled="sessState.containerRestarting">
                <SelectTrigger class="h-7 text-xs">
                  <SelectValue :placeholder="t('networkEgress.direct')" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem
                    v-for="profile in egressState.profiles"
                    :key="profile.id || DIRECT_EGRESS_VALUE"
                    :value="profile.id || DIRECT_EGRESS_VALUE"
                    :disabled="profile.status === 'disabled' || profile.status === 'unhealthy' || profile.status === 'unsupported'"
                    class="text-xs"
                  >
                    {{ profile.name }}
                    <span v-if="profile.type !== 'direct'" class="text-xs text-muted-foreground">({{ t(`networkEgress.type.${profile.type}`, profile.type) }})</span>
                  </SelectItem>
                </SelectContent>
              </Select>
              <p
                v-if="currentNetworkError"
                class="text-[10px] leading-tight text-destructive"
              >
                {{ currentNetworkError }}
              </p>
              <p v-else class="text-[10px] leading-tight text-muted-foreground">
                {{ currentNetworkName }}
              </p>
              <Button
                @click="saveNetworkEgress"
                :disabled="sessState.containerRestarting"
                variant="outline" size="sm"
                class="w-full h-6 text-[10px] border-lime-600/30 text-lime-400 hover:bg-lime-600/10"
              >{{ sessState.containerRestarting ? t('vnc.networkSaving') : t('vnc.networkSave') }}</Button>
            </div>
          </PopoverContent>
        </Popover>

        <!-- Fingerprint popover -->
        <Popover :open="fpOpen" @update:open="onFpOpenChange">
          <PopoverTrigger as-child>
            <Button
              variant="ghost" size="sm"
              :disabled="sessState.containerRestarting"
              class="h-6 px-2 text-xs gap-1"
              :class="fpProfile ? (fpReadinessOk(fpProfile) ? 'text-violet-400' : 'text-amber-400') : ''"
              :title="t('vnc.fingerprintTitle')"
            >
              <Fingerprint class="size-3.5" />
              {{ t('vnc.fingerprint') }}
            </Button>
          </PopoverTrigger>
          <PopoverContent class="w-72 p-3" align="start">
            <template v-if="fpLoading && !fpProfile">
              <div class="flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2 class="size-3.5 animate-spin" />
                {{ t('vnc.fpLoading') }}
              </div>
            </template>
            <template v-else-if="fpProfile">
              <div class="space-y-1.5 text-xs">
                <div class="flex justify-between">
                  <span class="text-muted-foreground">{{ t('vnc.fpPlatform') }}</span>
                  <TruncatedTooltipValue :display="fpPlatformLabel(fpProfile)" :content="fpPlatformLabel(fpProfile)" />
                </div>
                <div class="flex justify-between">
                  <span class="text-muted-foreground">{{ t('vnc.fpOsVersion') }}</span>
                  <TruncatedTooltipValue
                    :display="fpOsVersionLabel(fpProfile)"
                    :content="fpProfile.clientHints || {}"
                    json
                  />
                </div>
                <div v-if="fpProfile.profileFamily" class="flex justify-between">
                  <span class="text-muted-foreground">{{ t('vnc.fpProfileFamily') }}</span>
                  <TruncatedTooltipValue :display="fpProfile.profileFamily" :content="fpProfile.profileFamily" />
                </div>
                <div v-if="fpProfile.runtimeHealth" class="flex justify-between">
                  <span class="text-muted-foreground">{{ t('vnc.fpRuntimeHealth') }}</span>
                  <TruncatedTooltipValue
                    :display="fpRuntimeHealthLabel(fpProfile)"
                    :content="fpProfile.runtimeHealth"
                    json
                    :class="fpProfile.runtimeHealth?.ok ? 'text-emerald-400' : 'text-amber-500'"
                  />
                </div>
                <div class="flex justify-between">
                  <span class="text-muted-foreground">{{ t('vnc.fpReadiness') }}</span>
                  <TruncatedTooltipValue
                    :display="fpReadinessLabel(fpProfile)"
                    :content="fpReadiness(fpProfile)"
                    json
                    :class="fpReadinessOk(fpProfile) ? 'text-emerald-400' : 'text-amber-500'"
                  />
                </div>
                <p v-if="fpReadinessHint(fpProfile)" class="text-[10px] leading-tight text-amber-500">
                  {{ fpReadinessHint(fpProfile) }}
                </p>
                <div class="flex justify-between">
                  <span class="text-muted-foreground">{{ t('vnc.fpGpu') }}</span>
                  <TruncatedTooltipValue
                    :display="fpProfile.webgl?.renderer?.split(',')[0] || '-'"
                    :content="fpProfile.webgl?.renderer || '-'"
                  />
                </div>
                <div v-if="fpProfile.runtimeHealth" class="flex justify-between">
                  <span class="text-muted-foreground">{{ t('vnc.fpWebglRuntime') }}</span>
                  <TruncatedTooltipValue
                    :display="fpWebglRuntimeLabel(fpProfile)"
                    :content="fpProfile.runtimeHealth?.checks || {}"
                    json
                    :class="fpWebglRuntimeOk(fpProfile) ? 'text-emerald-400' : 'text-amber-500'"
                  />
                </div>
                <div class="flex justify-between">
                  <span class="text-muted-foreground">{{ t('vnc.fpCpu') }}</span>
                  <TruncatedTooltipValue
                    :display="`${fpProfile.navigator?.hardwareConcurrency || '-'} cores`"
                    :content="`${fpProfile.navigator?.hardwareConcurrency || '-'} cores`"
                  />
                </div>
                <div class="flex justify-between">
                  <span class="text-muted-foreground">{{ t('vnc.fpMemory') }}</span>
                  <TruncatedTooltipValue
                    :display="`${fpProfile.navigator?.deviceMemory || '-'} GB`"
                    :content="`${fpProfile.navigator?.deviceMemory || '-'} GB`"
                  />
                </div>
                <div class="flex justify-between">
                  <span class="text-muted-foreground">{{ t('vnc.fpScreen') }}</span>
                  <TruncatedTooltipValue
                    :display="`${fpProfile.screen?.colorDepth || '-'}bit / DPR ${fpProfile.devicePixelRatio || '-'}`"
                    :content="`${fpProfile.screen?.colorDepth || '-'}bit / DPR ${fpProfile.devicePixelRatio || '-'}`"
                  />
                </div>
                <div class="flex justify-between">
                  <span class="text-muted-foreground">{{ t('vnc.fpAudio') }}</span>
                  <TruncatedTooltipValue
                    :display="fpProfile.audio?.sampleRate ? `${fpProfile.audio.sampleRate} Hz / ${fpProfile.audio.baseLatency}s` : '-'"
                    :content="fpProfile.audio?.sampleRate ? `${fpProfile.audio.sampleRate} Hz / ${fpProfile.audio.baseLatency}s` : '-'"
                  />
                </div>
                <div class="flex justify-between">
                  <span class="text-muted-foreground">{{ t('vnc.fpNetwork') }}</span>
                  <TruncatedTooltipValue
                    :display="fpProfile.connection?.effectiveType ? `${fpProfile.connection.effectiveType} / ${fpProfile.connection.rtt}ms` : '-'"
                    :content="fpProfile.connection?.effectiveType ? `${fpProfile.connection.effectiveType} / ${fpProfile.connection.rtt}ms` : '-'"
                  />
                </div>
                <div class="flex justify-between">
                  <span class="text-muted-foreground">{{ t('vnc.fpFonts') }}</span>
                  <TruncatedTooltipValue
                    :display="fpProfile.fonts?.length ? t('vnc.fpFontsCount', { count: fpProfile.fonts.length }) : '-'"
                    :content="fpProfile.fonts?.length ? fpProfile.fonts.join(', ') : '-'"
                  />
                </div>
                <div v-if="fpProfile.fontPolicy" class="flex justify-between">
                  <span class="text-muted-foreground">{{ t('vnc.fpFontPolicy') }}</span>
                  <TruncatedTooltipValue
                    :display="fpFontPolicyLabel(fpProfile)"
                    :content="fpProfile.fontPolicy"
                    json
                  />
                </div>
                <div class="flex justify-between">
                  <span class="text-muted-foreground">{{ t('vnc.fpWebglParams') }}</span>
                  <TruncatedTooltipValue
                    v-if="fpProfile.webgl?.params && Object.keys(fpProfile.webgl.params).length"
                    :display="t('vnc.fpWebglParamsCount', { count: Object.keys(fpProfile.webgl.params).length })"
                    :content="fpProfile.webgl.params"
                    json
                  />
                  <TruncatedTooltipValue v-else display="-" content="-" />
                </div>
                <div class="flex justify-between">
                  <span class="text-muted-foreground">{{ t('vnc.fpTimezone') }}</span>
                  <TruncatedTooltipValue :display="fpProfile.timezone" :content="fpProfile.timezone" />
                </div>
                <div v-if="fpProfile.network" class="pt-1 mt-1 border-t border-border/60 space-y-1.5">
                  <div class="flex justify-between">
                    <span class="text-muted-foreground">{{ t('vnc.fpExitIp') }}</span>
                    <TruncatedTooltipValue :display="fpProfile.network.ip || '-'" :content="fpProfile.network.ip || '-'" />
                  </div>
                  <div class="flex justify-between">
                    <span class="text-muted-foreground">{{ t('vnc.fpExitLocation') }}</span>
                    <TruncatedTooltipValue :display="fpExitLocation(fpProfile)" :content="fpExitLocation(fpProfile)" />
                  </div>
                  <div class="flex justify-between">
                    <span class="text-muted-foreground">{{ t('vnc.fpDns') }}</span>
                    <TruncatedTooltipValue :display="fpDnsLabel(fpProfile)" :content="fpDnsLabel(fpProfile)" />
                  </div>
                  <div v-if="fpProfile.network.observed" class="flex justify-between">
                    <span class="text-muted-foreground">{{ t('vnc.fpObserved') }}</span>
                    <TruncatedTooltipValue
                      :display="`${fpProfile.network.observed.ip || '-'} / ${fpObservedLocation(fpProfile)}`"
                      :content="fpProfile.network.observed"
                      json
                    />
                  </div>
                  <div class="grid grid-cols-3 gap-1 pt-1">
                    <Button
                      variant="outline"
                      size="sm"
                      class="h-6 text-[10px] gap-1 px-1.5"
                      :disabled="sessState.networkProfileRefreshing"
                      @click="refreshFpNetworkProfile"
                    >
                      <RefreshCw class="size-3" :class="sessState.networkProfileRefreshing ? 'animate-spin' : ''" />
                      {{ t('vnc.fpNetworkRefresh') }}
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      class="h-6 text-[10px] gap-1 px-1.5"
                      :disabled="sessState.networkProfileRefreshing || !fpProfile.network.observed"
                      @click="syncFpObservedNetwork"
                    >
                      <Save class="size-3" />
                      {{ t('vnc.fpNetworkSync') }}
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      class="h-6 text-[10px] gap-1 px-1.5"
                      :disabled="sessState.networkProfileRefreshing"
                      @click="openFpNetworkEdit"
                    >
                      <Pencil class="size-3" />
                      {{ t('vnc.fpNetworkEdit') }}
                    </Button>
                  </div>
                  <div v-if="fpNetworkEditOpen" class="space-y-1.5 pt-1">
                    <textarea
                      v-model="fpNetworkJson"
                      class="w-full min-h-28 rounded-md border border-border bg-background px-2 py-1 font-mono text-[10px] outline-none focus:ring-1 focus:ring-violet-500"
                      spellcheck="false"
                    />
                    <p class="text-[10px] leading-tight text-amber-500">{{ t('vnc.fpNetworkEditHint') }}</p>
                    <p v-if="fpNetworkError" class="text-[10px] leading-tight text-destructive">{{ fpNetworkError }}</p>
                    <div class="flex gap-1.5">
                      <Button variant="outline" size="sm" class="flex-1 h-6 text-[10px]" @click="fpNetworkEditOpen = false">{{ t('session.cancel') }}</Button>
                      <Button
                        variant="outline"
                        size="sm"
                        class="flex-1 h-6 text-[10px] border-violet-600/30 text-violet-400 hover:bg-violet-600/10"
                        :disabled="sessState.networkProfileRefreshing"
                        @click="saveFpNetworkOverride"
                      >{{ t('fingerprintPool.save') }}</Button>
                    </div>
                  </div>
                </div>
                <div v-if="fpWarnings(fpProfile).length" class="text-[10px] text-amber-500 leading-snug">
                  {{ t('vnc.fpWarnings') }}: {{ fpWarnings(fpProfile).join('; ') }}
                </div>
                <div class="flex justify-between">
                  <span class="text-muted-foreground">{{ t('vnc.fpSeed') }}</span>
                  <TruncatedTooltipValue :display="fpProfile.seed" :content="fpProfile.seed" class="text-muted-foreground" />
                </div>
              </div>
              <div v-if="fpConfirmRegenerate" class="mt-3 p-2 bg-amber-500/10 border border-amber-500/20 rounded-md space-y-2">
                <p class="text-[10px] text-amber-500 leading-tight">{{ t('vnc.fpRegenerateWarn') }}</p>
                <div class="flex gap-1.5">
                  <Button variant="outline" size="sm" class="flex-1 h-6 text-[10px]" @click="fpConfirmRegenerate = false">{{ t('session.cancel') }}</Button>
                  <Button variant="outline" size="sm" class="flex-1 h-6 text-[10px] border-amber-500/30 text-amber-500 hover:bg-amber-500/10" @click="regenerateFp">{{ t('vnc.fpRegenerateConfirm') }}</Button>
                </div>
              </div>
              <Button v-else
                @click="regenerateFp"
                :disabled="sessState.containerRestarting"
                variant="outline" size="sm"
                class="w-full mt-3 h-7 text-[11px] border-violet-600/30 text-violet-400 hover:bg-violet-600/10"
              >{{ sessState.containerRestarting ? t('vnc.fpRegenerating') : t('vnc.fpRegenerate') }}</Button>
            </template>
            <p v-else class="text-xs text-muted-foreground leading-snug">{{ t('vnc.fpNoProfile') }}</p>
          </PopoverContent>
        </Popover>

        <span v-if="sessState.containerRestarting" class="text-xs text-amber-400 shrink-0 animate-pulse">
          <Loader2 class="size-3.5 inline animate-spin mr-0.5" />
          {{ t('vnc.switchingDevice') }}
        </span>

        <!-- Browser language -->
        <Select v-if="runtimeShellToolsEnabled" :model-value="browserLang" @update:model-value="changeLang" :disabled="langLoading">
          <SelectTrigger class="h-6 w-auto min-w-16 max-w-24 text-xs px-2 border-0 bg-transparent gap-1" :aria-label="t('vnc.browserLangTitle')">
            <Globe class="size-3.5 shrink-0" />
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem v-for="opt in LANG_OPTIONS" :key="opt.value" :value="opt.value" class="text-xs">{{ opt.label }}</SelectItem>
          </SelectContent>
        </Select>

        <span v-if="runtimeShellToolsEnabled && langError" class="text-xs text-amber-400 shrink-0 truncate max-w-40">{{ langError }}</span>
      </div>
    </TooltipProvider>

    <!-- VNC display area -->
    <div class="flex-1 relative overflow-hidden bg-black">
      <div ref="vncContainer" class="absolute inset-0" />

      <aside
        v-if="observePanelOpen"
        class="absolute right-3 top-3 bottom-3 z-20 w-[min(420px,calc(100%-24px))] overflow-hidden rounded-lg border border-border bg-background/95 shadow-2xl backdrop-blur"
      >
        <div class="flex items-center justify-between gap-3 border-b border-border px-3 py-2">
          <div class="min-w-0">
            <div class="flex items-center gap-2">
              <ScanSearch class="size-4 text-cyan-400" />
              <h3 class="text-sm font-medium">{{ t('vnc.observePanelTitle') }}</h3>
              <Badge variant="outline" class="uppercase text-[10px]">{{ observeResult?.mode || observeMode }}</Badge>
            </div>
            <p class="mt-0.5 truncate text-[11px] text-muted-foreground">{{ observeResult?.title || observeResult?.url || t('vnc.observeNoResult') }}</p>
          </div>
          <Button variant="ghost" size="sm" class="h-7 w-7 p-0" @click="observePanelOpen = false">
            <X class="size-4" />
          </Button>
        </div>

        <div class="h-[calc(100%-49px)] overflow-y-auto p-3 text-xs">
          <div class="mb-3 grid grid-cols-4 gap-2">
            <div v-for="item in observeSummary" :key="item.label" class="rounded-md border border-border bg-muted/30 px-2 py-1.5">
              <div class="text-[10px] uppercase text-muted-foreground">{{ item.label }}</div>
              <div class="mt-0.5 text-lg font-semibold leading-none">{{ item.value }}</div>
            </div>
          </div>

          <div v-if="observeLoading" class="flex items-center justify-center gap-2 rounded-md border border-border bg-muted/20 py-8 text-muted-foreground">
            <Loader2 class="size-4 animate-spin" />
            {{ t('vnc.observeRunning') }}
          </div>

          <div v-else-if="observeError" class="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-destructive">
            {{ observeError }}
          </div>

          <div v-else-if="observeResult" class="space-y-4">
            <section v-if="visionFrameStats.length">
              <div class="mb-2 flex items-center justify-between">
                <h4 class="font-medium">{{ t('vnc.visionFrame') }}</h4>
                <Badge variant="outline" class="text-[10px]">
                  {{ visionFrame?.preprocessEnabled ? t('vnc.visionPreprocessOn') : t('vnc.visionPreprocessOff') }}
                </Badge>
              </div>
              <div class="grid grid-cols-2 gap-2">
                <div
                  v-for="item in visionFrameStats"
                  :key="item.label"
                  class="rounded-md border border-border bg-muted/20 px-2 py-1.5"
                >
                  <div class="text-[10px] uppercase text-muted-foreground">{{ item.label }}</div>
                  <div class="mt-0.5 truncate font-mono text-[11px] text-foreground">{{ item.value }}</div>
                </div>
              </div>
            </section>

            <section v-if="annotatedScreenshot">
              <div class="mb-2 flex items-center justify-between">
                <h4 class="font-medium">{{ t('vnc.annotatedScreenshot') }}</h4>
                <span v-if="unknownRatio" class="text-[11px] text-muted-foreground">{{ t('vnc.unknownRatio') }} {{ unknownRatio }}</span>
              </div>
              <button
                type="button"
                class="group relative block w-full overflow-hidden rounded-md border border-border bg-black text-left outline-none transition hover:border-cyan-400/70 focus-visible:ring-2 focus-visible:ring-cyan-400"
                :aria-label="t('vnc.openAnnotatedScreenshot')"
                @click="annotatedScreenshotOpen = true"
              >
                <img
                  :src="annotatedScreenshot"
                  class="max-h-72 w-full object-contain"
                  :alt="t('vnc.annotatedScreenshot')"
                />
                <span class="absolute right-2 top-2 inline-flex items-center gap-1 rounded-md bg-black/70 px-2 py-1 text-[11px] text-white opacity-0 shadow transition group-hover:opacity-100 group-focus-visible:opacity-100">
                  <Maximize class="size-3" />
                  {{ t('vnc.openAnnotatedScreenshot') }}
                </span>
              </button>
              <div v-if="semanticSourceStats.length" class="mt-2 flex flex-wrap gap-1.5">
                <Badge
                  v-for="stat in semanticSourceStats"
                  :key="stat.label"
                  variant="outline"
                  class="text-[10px]"
                >
                  {{ stat.label }} {{ stat.value }}
                </Badge>
              </div>
            </section>

            <section v-if="visionGroups.length">
              <div class="mb-2 flex items-center justify-between">
                <h4 class="font-medium">{{ t('vnc.visionGroups') }}</h4>
                <span class="text-[11px] text-muted-foreground">{{ t('vnc.sortedByScore') }}</span>
              </div>
              <div class="space-y-1.5">
                <div
                  v-for="candidate in visionGroups.slice(0, 12)"
                  :key="candidate.id || `group-${candidate.family}-${formatBox(candidate)}`"
                  class="rounded-md border border-cyan-500/30 bg-cyan-500/10 p-2"
                >
                  <div class="flex items-start justify-between gap-2">
                    <div class="min-w-0">
                      <div class="truncate font-medium">{{ candidateTitle(candidate) }}</div>
                      <div class="mt-0.5 truncate text-[11px] text-muted-foreground">{{ candidateSubtitle(candidate) }}</div>
                    </div>
                    <Badge v-if="formatScore(candidate.score)" variant="outline" class="text-[10px]">{{ formatScore(candidate.score) }}</Badge>
                  </div>
                  <div class="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
                    <span v-if="formatPoint(candidate)">center {{ formatPoint(candidate) }}</span>
                    <span v-if="formatBox(candidate)">bbox {{ formatBox(candidate) }}</span>
                    <span v-if="candidate.textHint">{{ candidate.textHint }}</span>
                  </div>
                </div>
              </div>
            </section>

            <section v-if="axCandidates.length">
              <div class="mb-2 flex items-center justify-between">
                <h4 class="font-medium">{{ t('vnc.axCandidates') }}</h4>
                <span class="text-[11px] text-muted-foreground">{{ t('vnc.sortedByScore') }}</span>
              </div>
              <div class="space-y-1.5">
                <div
                  v-for="candidate in axCandidates.slice(0, 12)"
                  :key="candidate.id || `${candidate.role}-${formatBox(candidate)}`"
                  class="rounded-md border border-amber-500/30 bg-amber-500/10 p-2"
                >
                  <div class="flex items-start justify-between gap-2">
                    <div class="min-w-0">
                      <div class="truncate font-medium">{{ candidateTitle(candidate) }}</div>
                      <div class="mt-0.5 truncate text-[11px] text-muted-foreground">{{ candidateSubtitle(candidate) }}</div>
                    </div>
                    <Badge v-if="formatScore(candidate.score)" variant="outline" class="text-[10px]">{{ formatScore(candidate.score) }}</Badge>
                  </div>
                  <div class="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
                    <span v-if="formatPoint(candidate)">center {{ formatPoint(candidate) }}</span>
                    <span v-if="formatBox(candidate)">bbox {{ formatBox(candidate) }}</span>
                    <span v-if="candidate.axHint?.role">role {{ candidate.axHint.role }}</span>
                  </div>
                </div>
              </div>
            </section>

            <section v-if="visionCandidates.length">
              <div class="mb-2 flex items-center justify-between">
                <h4 class="font-medium">{{ t('vnc.visionCandidates') }}</h4>
                <span class="text-[11px] text-muted-foreground">{{ t('vnc.sortedByScore') }}</span>
              </div>
              <div class="space-y-1.5">
                <div
                  v-for="candidate in visionCandidates.slice(0, 12)"
                  :key="candidate.id || `${candidate.source}-${candidate.label}-${formatBox(candidate)}`"
                  class="rounded-md border border-border bg-muted/20 p-2"
                >
                  <div class="flex items-start justify-between gap-2">
                    <div class="min-w-0">
                      <div class="truncate font-medium">{{ candidateTitle(candidate) }}</div>
                      <div class="mt-0.5 truncate text-[11px] text-muted-foreground">{{ candidateSubtitle(candidate) }}</div>
                    </div>
                    <Badge v-if="formatScore(candidate.score)" variant="outline" class="text-[10px]">{{ formatScore(candidate.score) }}</Badge>
                  </div>
                  <div class="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
                    <span v-if="formatPoint(candidate)">center {{ formatPoint(candidate) }}</span>
                    <span v-if="formatBox(candidate)">bbox {{ formatBox(candidate) }}</span>
                    <span v-if="candidate.textHint">{{ candidate.textHint }}</span>
                    <span v-if="candidate.domHint">DOM hint</span>
                  </div>
                </div>
              </div>
            </section>

            <section v-if="mixedCandidates.length">
              <div class="mb-2 flex items-center justify-between gap-2">
                <h4 class="font-medium">{{ t('vnc.mixedCandidates') }}</h4>
                <div v-if="fusionStats.length" class="flex flex-wrap justify-end gap-1">
                  <Badge
                    v-for="stat in fusionStats"
                    :key="stat.label"
                    variant="outline"
                    class="text-[10px]"
                  >
                    {{ stat.label }} {{ stat.value }}
                  </Badge>
                </div>
              </div>
              <div class="space-y-1.5">
                <div
                  v-for="candidate in mixedCandidates.slice(0, 12)"
                  :key="candidate.id || `${candidate.kind || candidate.source}-${candidateTitle(candidate)}-${formatPoint(candidate)}`"
                  class="rounded-md border border-emerald-500/30 bg-emerald-500/10 p-2"
                >
                  <div class="flex items-start justify-between gap-2">
                    <div class="min-w-0">
                      <div class="truncate font-medium">{{ candidateTitle(candidate) }}</div>
                      <div class="mt-0.5 truncate text-[11px] text-muted-foreground">{{ candidateSubtitle(candidate) }}</div>
                    </div>
                    <Badge variant="outline" class="text-[10px]">{{ candidate.sourceSummary || candidate.kind || candidate.source || candidate.tag || 'item' }}</Badge>
                  </div>
                  <div class="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
                    <span v-if="formatPoint(candidate)">center {{ formatPoint(candidate) }}</span>
                    <span v-if="formatBox(candidate)">bbox {{ formatBox(candidate) }}</span>
                    <span v-if="candidate.textHint">{{ candidate.textHint }}</span>
                    <span v-if="candidate.axHint">AX hint</span>
                    <span v-if="candidate.domHint">DOM hint</span>
                    <span v-if="candidate.visionHint">Vision hint</span>
                  </div>
                </div>
              </div>
            </section>

            <section v-if="domElements.length">
              <h4 class="mb-2 font-medium">{{ t('vnc.domElements') }}</h4>
              <div class="space-y-1.5">
                <div
                  v-for="element in domElements.slice(0, 10)"
                  :key="`${element.tag}-${element.x}-${element.y}-${candidateTitle(element)}`"
                  class="rounded-md border border-border bg-muted/20 p-2"
                >
                  <div class="flex items-start justify-between gap-2">
                    <div class="min-w-0">
                      <div class="truncate font-medium">{{ candidateTitle(element) }}</div>
                      <div class="mt-0.5 truncate text-[11px] text-muted-foreground">{{ candidateSubtitle(element) }}</div>
                    </div>
                    <Badge variant="outline" class="text-[10px]">{{ element.tag || 'dom' }}</Badge>
                  </div>
                  <div class="mt-1 text-[11px] text-muted-foreground">center {{ formatPoint(element) }}</div>
                </div>
              </div>
            </section>

            <section v-if="visibleText">
              <h4 class="mb-2 font-medium">{{ t('vnc.visibleText') }}</h4>
              <pre class="max-h-40 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-muted/20 p-2 text-[11px] leading-relaxed text-muted-foreground">{{ visibleText.slice(0, 1200) }}</pre>
            </section>
          </div>

          <div v-else class="rounded-md border border-dashed border-border p-6 text-center text-muted-foreground">
            {{ t('vnc.observeEmpty') }}
          </div>
        </div>
      </aside>

      <Dialog v-model:open="annotatedScreenshotOpen">
        <DialogContent
          class="flex h-[94dvh] w-[96vw] max-w-[96vw] grid-rows-[auto_minmax(0,1fr)] flex-col gap-3 overflow-hidden p-3 sm:max-w-[96vw]"
        >
          <DialogHeader class="flex-row items-center justify-between gap-3 pr-9">
            <DialogTitle class="flex items-center gap-2 text-sm">
              <ScanSearch class="size-4 text-cyan-400" />
              {{ t('vnc.annotatedScreenshotPreview') }}
            </DialogTitle>
            <Button
              variant="outline"
              size="sm"
              class="h-7 shrink-0 text-xs"
              @click="annotatedScreenshotFit = !annotatedScreenshotFit"
            >
              <Maximize v-if="annotatedScreenshotFit" class="size-3.5" />
              <Minimize v-else class="size-3.5" />
              {{ annotatedScreenshotFit ? t('vnc.annotatedScreenshotOriginal') : t('vnc.annotatedScreenshotFit') }}
            </Button>
          </DialogHeader>
          <div
            class="min-h-0 flex-1 overflow-auto rounded-md border border-border bg-black p-2"
            :class="annotatedScreenshotFit ? 'flex items-center justify-center' : ''"
          >
            <img
              v-if="annotatedScreenshot"
              :src="annotatedScreenshot"
              :class="annotatedScreenshotFit ? 'max-h-full max-w-full object-contain' : 'mx-auto max-w-none'"
              :alt="t('vnc.annotatedScreenshotPreview')"
            />
          </div>
        </DialogContent>
      </Dialog>
    </div>

    <!-- Bottom input bar -->
    <TooltipProvider v-if="runtimeShellToolsEnabled && inputBarOpen && connected" :delay-duration="300">
      <div class="flex items-center gap-1.5 px-2 h-9 border-t border-border bg-background shrink-0">
        <Keyboard class="size-3.5 text-muted-foreground shrink-0" />
        <input
          ref="inputRef"
          v-model="inputText"
          type="text"
          class="flex-1 min-w-0 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          :class="inputError ? 'ring-1 ring-destructive rounded' : ''"
          :placeholder="t('vnc.inputPlaceholder')"
          @keydown="onInputKeydown"
        />
        <Tooltip>
          <TooltipTrigger as-child>
            <Button variant="ghost" size="icon"
              class="size-6 text-muted-foreground hover:text-sky-400"
              :disabled="inputSending"
              @click="getRemoteClipboard">
              <ClipboardPaste class="size-3.5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>{{ t('vnc.getFromRemote') }}</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger as-child>
            <Button variant="ghost" size="icon"
              class="size-6"
              :disabled="inputSending || !inputText"
              @click="sendInputText">
              <Loader2 v-if="inputSending" class="size-3.5 animate-spin" />
              <Check v-else-if="inputSent" class="size-3.5 text-green-400" />
              <CornerDownLeft v-else class="size-3.5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>{{ t('vnc.sendToRemote') }}</TooltipContent>
        </Tooltip>
      </div>
    </TooltipProvider>
  </div>
</template>

<style scoped>
:deep(.slider-track) {
  height: 3px;
}
</style>
