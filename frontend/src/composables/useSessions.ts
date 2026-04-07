import { reactive, readonly } from 'vue'
import type { ChatMessage, Session } from '../types'

interface SessionsState {
  sessions: Session[]
  activeId: string | null
  activePorts: { seleniumPort: number; vncPort: number } | null
  containerLoading: boolean
  loading: boolean
}

const state = reactive<SessionsState>({
  sessions: [],
  activeId: null,
  activePorts: null,
  containerLoading: false,
  loading: false,
})

async function fetchSessions(): Promise<void> {
  try {
    const res = await fetch('/api/sessions')
    const data = await res.json()
    state.sessions = (data.sessions || []).map((s: any) => ({
      ...s,
      containerStatus: s.containerStatus || 'not_found',
      ports: s.ports || null,
    }))
  } catch {
    // silently ignore
  }
}

async function createSession(name = '新会话'): Promise<Session> {
  const res = await fetch('/api/sessions', {
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
    messageCount: 0,
    preview: '',
    containerStatus: 'not_found',
    ports: null,
  }
  state.sessions.unshift(session)
  state.activeId = session.id
  await saveAppState('active_session_id', session.id)

  await _startContainerForSession(session.id)
  return session
}

async function _startContainerForSession(id: string): Promise<void> {
  const sess = state.sessions.find(s => s.id === id)
  if (sess?.containerStatus === 'paused') return

  state.containerLoading = true
  try {
    const res = await fetch(`/api/sessions/${id}/container/start`, { method: 'POST' })
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
    const res = await fetch(`/api/sessions/${id}/container/start`, { method: 'POST' })
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
    await fetch(`/api/sessions/${id}/container/pause`, { method: 'POST' })
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
    await fetch(`/api/sessions/${id}/container/stop`, { method: 'POST' })
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

async function deleteSession(id: string): Promise<void> {
  await fetch(`/api/sessions/${id}`, { method: 'DELETE' })
  state.sessions = state.sessions.filter(s => s.id !== id)
  if (state.activeId === id) {
    state.activePorts = null
    state.activeId = state.sessions.length ? state.sessions[0].id : null
    if (state.activeId) {
      await saveAppState('active_session_id', state.activeId)
      await _startContainerForSession(state.activeId)
    }
  }
}

async function renameSession(id: string, name: string): Promise<void> {
  await fetch(`/api/sessions/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  const s = state.sessions.find(s => s.id === id)
  if (s) s.name = name
}

async function loadMessages(id: string): Promise<ChatMessage[]> {
  const res = await fetch(`/api/sessions/${id}`)
  const data = await res.json()
  return data.messages || []
}

async function saveMessages(id: string, messages: ChatMessage[]): Promise<void> {
  await fetch(`/api/sessions/${id}/messages`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages }),
  })
  const s = state.sessions.find(s => s.id === id)
  if (s) {
    s.messageCount = messages.length
    s.updatedAt = new Date().toISOString()
    const lastUserMsg = [...messages].reverse().find(m => m.role === 'user')
    if (lastUserMsg) {
      const textBlock = lastUserMsg.blocks.find(b => b.type === 'text' && b.content)
      if (textBlock?.content) s.preview = textBlock.content.slice(0, 80)
    }
  }
}

async function getAppState(key: string): Promise<string | null> {
  try {
    const res = await fetch(`/api/app-state/${key}`)
    const data = await res.json()
    return data.value ?? null
  } catch {
    return null
  }
}

async function saveAppState(key: string, value: string): Promise<void> {
  try {
    await fetch(`/api/app-state/${key}`, {
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
  await fetchSessions()
  const savedId = await getAppState('active_session_id')
  if (savedId && state.sessions.some(s => s.id === savedId)) {
    state.activeId = savedId
  } else if (state.sessions.length) {
    state.activeId = state.sessions[0].id
  }

  if (state.activeId) {
    await _startContainerForSession(state.activeId)
  }
  state.loading = false
}

export function useSessions() {
  return {
    state: readonly(state),
    init,
    fetchSessions,
    createSession,
    switchSession,
    deleteSession,
    renameSession,
    loadMessages,
    saveMessages,
    getAppState,
    saveAppState,
    startContainer,
    pauseContainer,
    stopContainer,
  }
}
