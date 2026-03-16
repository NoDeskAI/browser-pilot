import { reactive, readonly } from 'vue'

interface DockerState {
  /** service name -> container state ('running' | 'exited' | ...) */
  statuses: Record<string, string>
  /** solution id -> is loading */
  loading: Record<string, boolean>
  /** global operations loading */
  globalLoading: boolean
  /** last error message */
  lastError: string
}

const state = reactive<DockerState>({
  statuses: {},
  loading: {},
  globalLoading: false,
  lastError: '',
})

let pollTimer: ReturnType<typeof setInterval> | null = null

async function fetchStatus() {
  try {
    const res = await fetch('/api/docker/status')
    const data = await res.json()
    state.statuses = data.statuses || {}
    state.lastError = ''
  } catch (e: any) {
    state.lastError = e.message
  }
}

async function startAll() {
  state.globalLoading = true
  state.lastError = ''
  try {
    const res = await fetch('/api/docker/start-all', { method: 'POST' })
    const data = await res.json()
    if (!data.ok) state.lastError = data.error || 'Start failed'
  } catch (e: any) {
    state.lastError = e.message
  } finally {
    state.globalLoading = false
    await fetchStatus()
  }
}

async function stopAll() {
  state.globalLoading = true
  state.lastError = ''
  try {
    const res = await fetch('/api/docker/stop-all', { method: 'POST' })
    const data = await res.json()
    if (!data.ok) state.lastError = data.error || 'Stop failed'
  } catch (e: any) {
    state.lastError = e.message
  } finally {
    state.globalLoading = false
    await fetchStatus()
  }
}

async function startService(solutionId: string) {
  state.loading[solutionId] = true
  state.lastError = ''
  try {
    const res = await fetch(`/api/docker/start/${solutionId}`, { method: 'POST' })
    const data = await res.json()
    if (!data.ok) state.lastError = data.error || 'Start failed'
  } catch (e: any) {
    state.lastError = e.message
  } finally {
    state.loading[solutionId] = false
    await fetchStatus()
  }
}

async function stopService(solutionId: string) {
  state.loading[solutionId] = true
  state.lastError = ''
  try {
    const res = await fetch(`/api/docker/stop/${solutionId}`, { method: 'POST' })
    const data = await res.json()
    if (!data.ok) state.lastError = data.error || 'Stop failed'
  } catch (e: any) {
    state.lastError = e.message
  } finally {
    state.loading[solutionId] = false
    await fetchStatus()
  }
}

function startPolling() {
  fetchStatus()
  if (!pollTimer) {
    pollTimer = setInterval(fetchStatus, 4000)
  }
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

export function useDocker() {
  return {
    state: readonly(state),
    fetchStatus,
    startAll,
    stopAll,
    startService,
    stopService,
    startPolling,
    stopPolling,
  }
}
