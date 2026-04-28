import { reactive, readonly } from 'vue'
import type { Session, DevicePreset } from '../types'
import i18n from '../i18n'
import { toast } from 'vue-sonner'
import { api } from '../lib/api'

interface SiteInfo {
  appTitle: string
  edition: string
  setupComplete: boolean
  features: { sso: boolean; multiTenantManagement: boolean }
  cliCommandName: string
  cliInstallCommand: string
}

interface BrandConfig {
  appTitle: string
  edition: string
  setupComplete: boolean
  features: { sso: boolean; multiTenantManagement: boolean }
  cliCommandName: string
  cliInstallCommand: string
}

const brand = reactive<BrandConfig>({
  appTitle: 'Browser Pilot',
  edition: 'ce',
  setupComplete: false,
  features: { sso: false, multiTenantManagement: false },
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
  loading: boolean
  devicePresets: DevicePreset[]
}

const state = reactive<SessionsState>({
  sessions: [],
  activeId: null,
  activePorts: null,
  containerLoading: false,
  containerRestarting: false,
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
  target.networkEgressName = data.networkEgressName || (data.proxyUrl ? 'Manual proxy' : 'Direct')
  target.networkEgressType = data.networkEgressType || (data.proxyUrl ? 'external_proxy' : 'direct')
  target.networkEgressStatus = data.networkEgressStatus || (data.proxyUrl ? 'unchecked' : 'healthy')
  target.networkEgressProxyUrl = data.networkEgressProxyUrl || data.proxyUrl || ''
  target.networkEgressHealthError = data.networkEgressHealthError || ''
}

function clearStartingSoon(id: string): void {
  window.setTimeout(() => {
    startingSessionIds.delete(id)
  }, 15000)
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
        networkEgressName: s.networkEgressName || (s.proxyUrl ? 'Manual proxy' : 'Direct'),
        networkEgressType: s.networkEgressType || (s.proxyUrl ? 'external_proxy' : 'direct'),
        networkEgressStatus: s.networkEgressStatus || (s.proxyUrl ? 'unchecked' : 'healthy'),
        networkEgressProxyUrl: s.networkEgressProxyUrl || s.proxyUrl || '',
        networkEgressHealthError: s.networkEgressHealthError || '',
        fingerprintProfile: s.fingerprintProfile || local?.fingerprintProfile || null,
        browserLang: s.browserLang || 'zh-CN',
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
  try {
    const res = await api('/api/browser-images')
    const data = await res.json()
    return (data.images || []).filter((i: any) => i.status === 'ready')
  } catch {
    return []
  }
}

async function createSession(name?: string, chromeVersion?: string, networkEgressId?: string | null): Promise<Session> {
  if (!name) name = i18n.global.t('session.defaultName')
  const browserLang = _LOCALE_TO_BROWSER_LANG[(i18n.global.locale as any).value] ?? 'en-US'

  let effectiveChromeVersion = chromeVersion
  if (!effectiveChromeVersion) {
    const readyImages = await fetchBrowserImages()
    if (readyImages.length > 0) {
      effectiveChromeVersion = readyImages[0].chromeVersion || String(readyImages[0].chromeMajor)
    }
  }

  const body: Record<string, any> = { name, browserLang }
  if (effectiveChromeVersion) body.chromeVersion = effectiveChromeVersion
  if (networkEgressId) body.networkEgressId = networkEgressId

  const res = await api('/api/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await res.json()
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
    networkEgressName: data.networkEgressName || (data.proxyUrl ? 'Manual proxy' : 'Direct'),
    networkEgressType: data.networkEgressType || (data.proxyUrl ? 'external_proxy' : 'direct'),
    networkEgressStatus: data.networkEgressStatus || (data.proxyUrl ? 'unchecked' : 'healthy'),
    networkEgressProxyUrl: data.networkEgressProxyUrl || data.proxyUrl || '',
    networkEgressHealthError: data.networkEgressHealthError || '',
    fingerprintProfile: data.fingerprintProfile || null,
    browserLang: data.browserLang || browserLang,
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
    if (data.ok && data.ports) {
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
    } else {
      const current = state.sessions.find(s => s.id === id)
      if (current) current.containerStatus = restoreContainerStatus(previousStatus)
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
  } else if (s && s.containerStatus === 'paused') {
    state.activePorts = null
  } else {
    state.activePorts = null
    await _startContainerForSession(id)
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
    if (data.ok && data.ports) {
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
    } else {
      const current = state.sessions.find(s => s.id === id)
      if (current) current.containerStatus = restoreContainerStatus(previousStatus)
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

async function changeProxy(id: string, proxyUrl: string): Promise<void> {
  state.containerRestarting = true
  try {
    const res = await api(`/api/sessions/${id}/proxy`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ proxyUrl }),
    })
    const data = await res.json()
    if (data.ok && data.ports) {
      const s = state.sessions.find(s => s.id === id)
      if (s) {
        s.proxyUrl = data.proxyUrl ?? proxyUrl
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

async function deleteSession(id: string): Promise<void> {
  await api(`/api/sessions/${id}`, { method: 'DELETE' })
  startingSessionIds.delete(id)
  state.sessions = state.sessions.filter(s => s.id !== id)
  if (state.activeId === id) {
    state.activePorts = null
    state.activeId = null
  }
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
    changeProxy,
    changeNetworkEgress,
    regenerateFingerprint,
    fetchBrowserImages,
  }
}
