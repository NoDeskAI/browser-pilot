import { reactive, readonly } from 'vue'
import type { Session, DevicePreset } from '../types'
import i18n from '../i18n'
import { api } from '../lib/api'

interface SiteInfo {
  appTitle: string
  edition: string
  setupComplete: boolean
  features: { sso: boolean; multiTenantManagement: boolean }
  cliCommandName: string
  cliInstallCommand: string
  cliPythonInstallCommand: string
}

interface BrandConfig {
  appTitle: string
  edition: string
  setupComplete: boolean
  features: { sso: boolean; multiTenantManagement: boolean }
  cliCommandName: string
  cliInstallCommand: string
  cliPythonInstallCommand: string
}

const brand = reactive<BrandConfig>({
  appTitle: 'Browser Pilot',
  edition: 'ce',
  setupComplete: false,
  features: { sso: false, multiTenantManagement: false },
  cliCommandName: 'bpilot',
  cliInstallCommand: 'pip install bpilot-cli',
  cliPythonInstallCommand: 'pip install bpilot-cli',
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
    if (data.cliPythonInstallCommand) brand.cliPythonInstallCommand = data.cliPythonInstallCommand
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
    state.sessions = (data.sessions || []).map((s: any) => ({
      ...s,
      currentUrl: s.currentUrl || '',
      currentTitle: s.currentTitle || '',
      containerStatus: s.containerStatus || 'not_found',
      ports: s.ports || null,
      devicePreset: s.devicePreset || '',
      proxyUrl: s.proxyUrl || '',
    }))
  } catch {
    // silently ignore
  }
}

async function createSession(name?: string): Promise<Session> {
  if (!name) name = i18n.global.t('session.defaultName')
  const res = await api('/api/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  const data = await res.json()
  const session: Session = {
    id: data.id,
    name: data.name,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    currentUrl: '',
    currentTitle: '',
    containerStatus: 'not_found',
    ports: null,
  }
  state.sessions.unshift(session)
  return session
}

async function _startContainerForSession(id: string): Promise<void> {
  const sess = state.sessions.find(s => s.id === id)
  if (sess?.containerStatus === 'paused') return

  state.containerLoading = true
  try {
    const res = await api(`/api/sessions/${id}/container/start`, { method: 'POST' })
    const data = await res.json()
    if (data.ok && data.ports) {
      state.activePorts = {
        seleniumPort: data.ports.selenium_port,
        vncPort: data.ports.vnc_port,
      }
      const s = state.sessions.find(s => s.id === id)
      if (s) {
        s.containerStatus = 'running'
        s.ports = data.ports
      }
    }
  } catch {
    // container start failed silently
  } finally {
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

  if (isActive) state.containerLoading = true
  try {
    const res = await api(`/api/sessions/${id}/container/start`, { method: 'POST' })
    const data = await res.json()
    if (data.ok && data.ports) {
      if (s) {
        s.containerStatus = 'running'
        s.ports = data.ports
      }
      if (isActive) {
        state.activePorts = {
          seleniumPort: data.ports.selenium_port,
          vncPort: data.ports.vnc_port,
        }
      }
    }
  } finally {
    if (isActive) state.containerLoading = false
  }
}

async function pauseContainer(id: string): Promise<void> {
  try {
    await api(`/api/sessions/${id}/container/pause`, { method: 'POST' })
    const s = state.sessions.find(s => s.id === id)
    if (s) {
      s.containerStatus = 'paused'
      s.ports = null
    }
    if (state.activeId === id) {
      state.activePorts = null
    }
  } catch {
    // silently ignore
  }
}

async function stopContainer(id: string): Promise<void> {
  try {
    await api(`/api/sessions/${id}/container/stop`, { method: 'POST' })
    const s = state.sessions.find(s => s.id === id)
    if (s) {
      s.containerStatus = 'exited'
      s.ports = null
    }
    if (state.activeId === id) {
      state.activePorts = null
    }
  } catch {
    // silently ignore
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

async function deleteSession(id: string): Promise<void> {
  await api(`/api/sessions/${id}`, { method: 'DELETE' })
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
  }
}
