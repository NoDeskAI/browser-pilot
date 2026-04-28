import { reactive, readonly } from 'vue'
import type { NetworkEgressProfile } from '../types'
import { api } from '../lib/api'

interface NetworkEgressState {
  profiles: NetworkEgressProfile[]
  loading: boolean
}

const state = reactive<NetworkEgressState>({
  profiles: [],
  loading: false,
})

async function fetchNetworkEgress(): Promise<NetworkEgressProfile[]> {
  state.loading = true
  try {
    const res = await api('/api/network-egress')
    const data = await res.json()
    state.profiles = data.profiles || []
    return state.profiles
  } finally {
    state.loading = false
  }
}

async function createNetworkEgress(body: Record<string, any>): Promise<NetworkEgressProfile> {
  const res = await api('/api/network-egress', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data?.detail || 'Failed to create network egress')
  await fetchNetworkEgress()
  return data.profile
}

async function updateNetworkEgress(id: string, body: Record<string, any>): Promise<NetworkEgressProfile> {
  const res = await api(`/api/network-egress/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data?.detail || 'Failed to update network egress')
  await fetchNetworkEgress()
  return data.profile
}

async function deleteNetworkEgress(id: string): Promise<void> {
  const res = await api(`/api/network-egress/${id}`, { method: 'DELETE' })
  const data = await res.json().catch(() => null)
  if (!res.ok) throw new Error(data?.detail || 'Failed to delete network egress')
  await fetchNetworkEgress()
}

async function checkNetworkEgress(id: string): Promise<void> {
  const res = await api(`/api/network-egress/${id}/check`, { method: 'POST' })
  const data = await res.json().catch(() => null)
  if (!res.ok || data?.ok === false) throw new Error(data?.detail || data?.healthError || 'Health check failed')
  await fetchNetworkEgress()
}

export function useNetworkEgress() {
  return {
    state: readonly(state),
    fetchNetworkEgress,
    createNetworkEgress,
    updateNetworkEgress,
    deleteNetworkEgress,
    checkNetworkEgress,
  }
}
