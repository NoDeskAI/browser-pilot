import { reactive, readonly } from 'vue'
import type { Session, DevicePreset, DeleteSessionFileOptions, DeleteSessionResult } from '../types'
import i18n from '../i18n'
import { toast } from 'vue-sonner'
import { api } from '../lib/api'

interface SiteInfo {
  appTitle: string
  edition: string
  setupComplete: boolean
  features: { sso: boolean; multiTenantManagement: boolean }
  auth?: { accessTokenMinutes: number; rememberMeDays: number }
  cliCommandName: string
  cliInstallCommand: string
}

interface BrandConfig {
  appTitle: string
  edition: string
  setupComplete: boolean
  features: { sso: boolean; multiTenantManagement: boolean }
  auth: { accessTokenMinutes: number; rememberMeDays: number }
  cliCommandName: string
  cliInstallCommand: string
}

const brand = reactive<BrandConfig>({
  appTitle: 'Browser Pilot',
  edition: 'ce',
  setupComplete: false,
  features: { sso: false, multiTenantManagement: false },
  auth: { accessTokenMinutes: 30, rememberMeDays: 7 },
  cliCommandName: 'bpilot',
  cliInstallCommand: 'curl -fsSL http://localhost:8000/api/cli/install | bash',
})

async function fetchBrand(): Promise<void> {
  try {
    const res = await fetch('/api/site-info')
    const data: SiteInfo = await res.json()
    if (data.appTitle) brand.appTitle = data.appTitle
    if (data.edition) brand.edition = data.edition
    if (data.setupComplete !== undefined) brand.setupComplete = data.setupComplete
    if (data.features) brand.features = data.features
    if (data.auth) brand.auth = data.auth
    if (data.cliCommandName) brand.cliCommandName = data.cliCommandName
    if (data.cliInstallCommand) brand.cliInstallCommand = data.cliInstallCommand
  } catch {
    // keep defaults
  }
}

interface SessionsState {
  sessions: Session[]
  activeId: string | null
  activePorts: { seleniumPort: number; vncPort: number } | null
  containerLoading: boolean
  containerRestarting: boolean
  networkProfileRefreshing: boolean
  loading: boolean
  devicePresets: DevicePreset[]
}

const state = reactive<SessionsState>({
  sessions: [],
  activeId: null,
  activePorts: null,
  containerLoading: false,
  containerRestarting: false,
  networkProfileRefreshing: false,
  loading: false,
  devicePresets: [],
})

const startingSessionIds = new Set<string>()

function restoreContainerStatus(status: Session['containerStatus'] | undefined): Session['containerStatus'] {
  return status && status !== 'starting' ? status : 'not_found'
}

function applyNetworkEgressFields(target: Session, data: any): void {
  if (
    !('networkEgressId' in data) &&
    !('networkEgressType' in data) &&
    !('networkEgressName' in data) &&
    !('networkEgressProxyUrl' in data) &&
    !('proxyUrl' in data)
  ) {
    return
  }
  target.proxyUrl = data.proxyUrl || ''
  target.networkEgressId = data.networkEgressId ?? null
  target.networkEgressName = data.networkEgressName || 'Direct'
  target.networkEgressType = data.networkEgressType || 'direct'
  target.networkEgressStatus = data.networkEgressStatus || 'healthy'
  target.networkEgressProxyUrl = data.networkEgressProxyUrl || ''
  target.networkEgressHealthError = data.networkEgressHealthError || ''
}

function clearStartingSoon(id: string): void {
  window.setTimeout(() => {
    startingSessionIds.delete(id)
  }, 15000)
}

function assertContainerStarted(data: any): void {
  if (!data?.ok || !data?.ports) {
    throw new Error(data?.error || 'Container start failed')
  }
}

async function fetchDevicePresets(): Promise<void> {
  try {
    const res = await api('/api/device-presets')
    const data = await res.json()
    state.devicePresets = data.presets || []
  } catch {
    // silently ignore
  }
}

async function fetchSessions(): Promise<void> {
  try {
    const res = await api('/api/sessions')
    const data = await res.json()
    const localById = new Map(state.sessions.map(s => [s.id, s]))
    const seenIds = new Set<string>()
    state.sessions = (data.sessions || []).map((s: any) => {
      const local = localById.get(s.id)
      const containerStatus: Session['containerStatus'] = (() => {
        const backendStatus = s.containerStatus || 'not_found'
        seenIds.add(s.id)
        if (backendStatus === 'running') {
          startingSessionIds.delete(s.id)
          return backendStatus
        }
        if (startingSessionIds.has(s.id)) {
          return local?.containerStatus === 'running' ? 'running' : 'starting'
        }
        return backendStatus
      })()

      return {
        ...s,
        currentUrl: s.currentUrl || '',
        currentTitle: s.currentTitle || '',
        containerStatus,
        ports: containerStatus === 'running' ? (s.ports || local?.ports || null) : (s.ports || null),
        devicePreset: s.devicePreset || '',
        proxyUrl: s.proxyUrl || '',
        networkEgressId: s.networkEgressId ?? null,
        networkEgressName: s.networkEgressName || 'Direct',
        networkEgressType: s.networkEgressType || 'direct',
        networkEgressStatus: s.networkEgressStatus || 'healthy',
        networkEgressProxyUrl: s.networkEgressProxyUrl || '',
        networkEgressHealthError: s.networkEgressHealthError || '',
        fingerprintProfile: s.fingerprintProfile || local?.fingerprintProfile || null,
        browserLang: s.browserLang || 'zh-CN',
        browserRuntime: s.browserRuntime || 'standard_chrome',
        activeLease: s.activeLease || null,
      }
    })
    for (const id of [...startingSessionIds]) {
      if (!seenIds.has(id)) startingSessionIds.delete(id)
    }
  } catch {
    // silently ignore
  }
}

const _LOCALE_TO_BROWSER_LANG: Record<string, string> = { zh: 'zh-CN', en: 'en-US' }

async function fetchBrowserImages(): Promise<any[]> {
  const data = await fetchBrowserImageState()
  return (data.images || []).filter((i: any) => i.status === 'ready')
}

async function fetchBrowserImageState(): Promise<{ images: any[]; runtimeImages: any[] }> {
  try {
    const res = await api('/api/browser-images')
    const data = await res.json()
    return {
      images: data.images || [],
      runtimeImages: data.runtimeImages || [],
    }
  } catch {
    return { images: [], runtimeImages: [] }
  }
}

async function createSession(
  name?: string,
  chromeVersion?: string,
  networkEgressId?: string | null,
  browserRuntime: 'standard_chrome' | 'cloak_chromium' = 'standard_chrome',
): Promise<Session> {
  if (!name) name = i18n.global.t('session.defaultName')
  const browserLang = _LOCALE_TO_BROWSER_LANG[(i18n.global.locale as any).value] ?? 'en-US'

  let effectiveChromeVersion = chromeVersion
  if (browserRuntime === 'standard_chrome' && !effectiveChromeVersion) {
    const readyImages = await fetchBrowserImages()
    if (readyImages.length > 0) {
      effectiveChromeVersion = readyImages[0].chromeVersion || String(readyImages[0].chromeMajor)
    }
  }

  const body: Record<string, any> = { name, browserLang, browserRuntime }
  if (browserRuntime === 'standard_chrome' && effectiveChromeVersion) body.chromeVersion = effectiveChromeVersion
  if (networkEgressId) body.networkEgressId = networkEgressId

  const res = await api('/api/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await res.json().catch(() => null)
  if (!res.ok) {
    throw new Error(data?.detail || i18n.global.t('app.sessionCreateError'))
  }
  const session: Session = {
    id: data.id,
    name: data.name,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    currentUrl: '',
    currentTitle: '',
    containerStatus: 'starting',
    ports: null,
    proxyUrl: data.proxyUrl || '',
    networkEgressId: data.networkEgressId ?? null,
    networkEgressName: data.networkEgressName || 'Direct',
    networkEgressType: data.networkEgressType || 'direct',
    networkEgressStatus: data.networkEgressStatus || 'healthy',
    networkEgressProxyUrl: data.networkEgressProxyUrl || '',
    networkEgressHealthError: data.networkEgressHealthError || '',
    fingerprintProfile: data.fingerprintProfile || null,
    browserLang: data.browserLang || browserLang,
    browserRuntime: data.browserRuntime || browserRuntime,
    activeLease: data.agentDevice?.leaseId
      ? {
          id: data.agentDevice.leaseId,
          leaseId: data.agentDevice.leaseId,
          leaseMode: data.agentDevice.leaseMode,
          taskId: data.agentDevice.taskId,
          currentOperator: data.agentDevice.currentOperator || data.agentDevice.operator || '',
          operatorType: data.agentDevice.operator?.startsWith('user:') ? 'user' : 'unknown',
          operatorName: null,
          expiresAt: data.agentDevice.expiresAt,
          updatedAt: null,
        }
      : null,
  }
  state.sessions.unshift(session)
  return session
}

async function _startContainerForSession(id: string): Promise<void> {
  const sess = state.sessions.find(s => s.id === id)
  if (sess?.containerStatus === 'paused') return

  const previousStatus = sess?.containerStatus
  startingSessionIds.add(id)
  if (sess && sess.containerStatus !== 'running') {
    sess.containerStatus = 'starting'
  }
  state.containerLoading = true
  let started = false
  try {
    const res = await api(`/api/sessions/${id}/container/start`, { method: 'POST' })
    const data = await res.json()
    assertContainerStarted(data)
    started = true
    state.activePorts = {
      seleniumPort: data.ports.selenium_port,
      vncPort: data.ports.vnc_port,
    }
    const s = state.sessions.find(s => s.id === id)
    if (s) {
      s.containerStatus = 'running'
      s.ports = data.ports
      if (data.fingerprintProfile) s.fingerprintProfile = data.fingerprintProfile
      applyNetworkEgressFields(s, data)
    }
  } catch {
    const current = state.sessions.find(s => s.id === id)
    if (current) current.containerStatus = restoreContainerStatus(previousStatus)
    toast.error(i18n.global.t('app.containerStartError'))
  } finally {
    if (started) clearStartingSoon(id)
    else startingSessionIds.delete(id)
    state.containerLoading = false
  }
}

async function switchSession(id: string): Promise<void> {
  state.activeId = id
  await saveAppState('active_session_id', id)

  const s = state.sessions.find(s => s.id === id)
  if (s && s.containerStatus === 'running' && s.ports) {
    state.activePorts = {
      seleniumPort: s.ports.selenium_port,
      vncPort: s.ports.vnc_port,
    }
  } else if (s?.containerStatus === 'starting') {
    state.activePorts = null
    await _startContainerForSession(id)
  } else {
    state.activePorts = null
  }
}

async function startContainer(id: string): Promise<void> {
  const s = state.sessions.find(s => s.id === id)
  const isActive = state.activeId === id
  const previousStatus = s?.containerStatus

  startingSessionIds.add(id)
  if (s && s.containerStatus !== 'running') {
    s.containerStatus = 'starting'
  }
  if (isActive) state.containerLoading = true
  let started = false
  try {
    const res = await api(`/api/sessions/${id}/container/start`, { method: 'POST' })
    const data = await res.json()
    assertContainerStarted(data)
    started = true
    if (s) {
      s.containerStatus = 'running'
      s.ports = data.ports
      if (data.fingerprintProfile) s.fingerprintProfile = data.fingerprintProfile
      applyNetworkEgressFields(s, data)
    }
    if (isActive) {
      state.activePorts = {
        seleniumPort: data.ports.selenium_port,
        vncPort: data.ports.vnc_port,
      }
    }
  } catch (err) {
    const current = state.sessions.find(s => s.id === id)
    if (current) current.containerStatus = restoreContainerStatus(previousStatus)
    throw err
  } finally {
    if (started) clearStartingSoon(id)
    else startingSessionIds.delete(id)
    if (isActive) state.containerLoading = false
  }
}

async function pauseContainer(id: string): Promise<void> {
  try {
    await api(`/api/sessions/${id}/container/pause`, { method: 'POST' })
    startingSessionIds.delete(id)
    const s = state.sessions.find(s => s.id === id)
    if (s) {
      s.containerStatus = 'paused'
      s.ports = null
    }
    if (state.activeId === id) {
      state.activePorts = null
    }
  } catch {
    toast.error(i18n.global.t('app.containerPauseError'))
  }
}

async function stopContainer(id: string): Promise<void> {
  try {
    await api(`/api/sessions/${id}/container/stop`, { method: 'POST' })
    startingSessionIds.delete(id)
    const s = state.sessions.find(s => s.id === id)
    if (s) {
      s.containerStatus = 'exited'
      s.ports = null
    }
    if (state.activeId === id) {
      state.activePorts = null
    }
  } catch {
    toast.error(i18n.global.t('app.containerStopError'))
  }
}

async function changeDevicePreset(id: string, preset: string): Promise<void> {
  state.containerRestarting = true
  try {
    const res = await api(`/api/sessions/${id}/device-preset`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ preset }),
    })
    const data = await res.json()
    if (data.ok && data.ports) {
      const s = state.sessions.find(s => s.id === id)
      if (s) {
        s.devicePreset = data.devicePreset || preset
        s.ports = data.ports
        s.containerStatus = 'running'
        if (data.fingerprintProfile) s.fingerprintProfile = data.fingerprintProfile
        applyNetworkEgressFields(s, data)
      }
      if (state.activeId === id) {
        state.activePorts = {
          seleniumPort: data.ports.selenium_port,
          vncPort: data.ports.vnc_port,
        }
      }
    }
  } finally {
    state.containerRestarting = false
  }
}

async function changeNetworkEgress(id: string, networkEgressId: string | null): Promise<void> {
  state.containerRestarting = true
  try {
    const res = await api(`/api/sessions/${id}/network-egress`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ networkEgressId }),
    })
    const data = await res.json()
    if (data.ok && data.ports) {
      const s = state.sessions.find(s => s.id === id)
      if (s) {
        s.ports = data.ports
        s.containerStatus = 'running'
        if (data.fingerprintProfile) s.fingerprintProfile = data.fingerprintProfile
        applyNetworkEgressFields(s, data)
      }
      if (state.activeId === id) {
        state.activePorts = {
          seleniumPort: data.ports.selenium_port,
          vncPort: data.ports.vnc_port,
        }
      }
    } else if (data?.error) {
      throw new Error(data.error)
    }
  } finally {
    state.containerRestarting = false
  }
}

async function regenerateFingerprint(id: string): Promise<void> {
  state.containerRestarting = true
  try {
    const res = await api(`/api/sessions/${id}/fingerprint`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'regenerate' }),
    })
    const data = await res.json()
    if (data.ok) {
      const s = state.sessions.find(s => s.id === id)
      if (s) {
        s.fingerprintProfile = data.fingerprintProfile || null
        if (data.ports) {
          s.ports = data.ports
          s.containerStatus = 'running'
        }
        applyNetworkEgressFields(s, data)
      }
      if (state.activeId === id && data.ports) {
        state.activePorts = {
          seleniumPort: data.ports.selenium_port,
          vncPort: data.ports.vnc_port,
        }
      }
    }
  } finally {
    state.containerRestarting = false
  }
}

async function refreshNetworkProfile(id: string): Promise<void> {
  state.networkProfileRefreshing = true
  try {
    const res = await api(`/api/sessions/${id}/network-profile/refresh`, { method: 'POST' })
    const data = await res.json()
    if (data.ok) {
      const s = state.sessions.find(s => s.id === id)
      if (s) {
        if (data.fingerprintProfile) s.fingerprintProfile = data.fingerprintProfile
        if (data.ports) {
          s.ports = data.ports
          s.containerStatus = 'running'
        }
      }
    } else if (data?.error) {
      throw new Error(data.error)
    }
  } finally {
    state.networkProfileRefreshing = false
  }
}

async function syncObservedNetworkProfile(id: string): Promise<{ restartRequired: boolean }> {
  state.networkProfileRefreshing = true
  try {
    const res = await api(`/api/sessions/${id}/network-profile/sync`, { method: 'POST' })
    const data = await res.json()
    if (data.ok) {
      const s = state.sessions.find(s => s.id === id)
      if (s && data.fingerprintProfile) s.fingerprintProfile = data.fingerprintProfile
      return { restartRequired: Boolean(data.restartRequired) }
    }
    throw new Error(data?.error || 'Network profile sync failed')
  } finally {
    state.networkProfileRefreshing = false
  }
}

async function overrideNetworkProfile(id: string, network: Record<string, any>): Promise<{ restartRequired: boolean }> {
  state.networkProfileRefreshing = true
  try {
    const res = await api(`/api/sessions/${id}/network-profile`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ network }),
    })
    const data = await res.json()
    if (data.ok) {
      const s = state.sessions.find(s => s.id === id)
      if (s && data.fingerprintProfile) s.fingerprintProfile = data.fingerprintProfile
      return { restartRequired: Boolean(data.restartRequired) }
    }
    throw new Error(data?.error || 'Network profile override failed')
  } finally {
    state.networkProfileRefreshing = false
  }
}

async function deleteSession(id: string, options?: DeleteSessionFileOptions): Promise<DeleteSessionResult> {
  const request: RequestInit = { method: 'DELETE' }
  if (options) {
    request.headers = { 'Content-Type': 'application/json' }
    request.body = JSON.stringify(options)
  }
  const res = await api(`/api/sessions/${id}`, request)
  if (!res.ok) throw new Error(await res.text())
  const data = await res.json().catch(() => ({ ok: true })) as DeleteSessionResult
  if (data.ok === false) throw new Error('Session delete failed')
  startingSessionIds.delete(id)
  state.sessions = state.sessions.filter(s => s.id !== id)
  if (state.activeId === id) {
    state.activePorts = null
    state.activeId = null
  }
  return data
}

async function renameSession(id: string, name: string): Promise<void> {
  await api(`/api/sessions/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  const s = state.sessions.find(s => s.id === id)
  if (s) s.name = name
}

async function getAppState(key: string): Promise<string | null> {
  try {
    const res = await api(`/api/app-state/${key}`)
    const data = await res.json()
    return data.value ?? null
  } catch {
    return null
  }
}

async function saveAppState(key: string, value: string): Promise<void> {
  try {
    await api(`/api/app-state/${key}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value }),
    })
  } catch {
    // silently ignore
  }
}

async function init(): Promise<void> {
  state.loading = true
  await Promise.all([fetchSessions(), fetchBrand(), fetchDevicePresets()])
  state.loading = false
}

export function useSessions() {
  return {
    state: readonly(state),
    brand: readonly(brand),
    init,
    fetchBrand,
    fetchSessions,
    createSession,
    switchSession,
    deleteSession,
    renameSession,
    getAppState,
    saveAppState,
    startContainer,
    pauseContainer,
    stopContainer,
    changeDevicePreset,
    changeNetworkEgress,
    regenerateFingerprint,
    refreshNetworkProfile,
    syncObservedNetworkProfile,
    overrideNetworkProfile,
    fetchBrowserImages,
    fetchBrowserImageState,
  }
}
