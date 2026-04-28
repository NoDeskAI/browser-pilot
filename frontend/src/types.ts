export interface Session {
  id: string
  name: string
  createdAt: string
  updatedAt: string
  currentUrl: string
  currentTitle: string
  containerStatus: 'running' | 'paused' | 'exited' | 'not_found' | 'starting'
  ports?: { selenium_port: number; vnc_port: number } | null
  devicePreset?: string
  proxyUrl?: string
  networkEgressId?: string | null
  networkEgressName?: string
  networkEgressType?: string
  networkEgressStatus?: string
  networkEgressProxyUrl?: string
  networkEgressHealthError?: string
  fingerprintProfile?: Record<string, any> | null
  browserLang?: string
}

export interface DevicePreset {
  id: string
  label: string
  category: 'desktop' | 'mobile'
  width: number
  height: number
  dpr?: number
  default?: boolean
}

export interface NetworkEgressProfile {
  id: string | null
  name: string
  type: 'direct' | 'external_proxy' | 'clash' | 'openvpn'
  status: 'healthy' | 'unhealthy' | 'disabled' | 'unsupported' | 'unchecked'
  proxyUrl: string
  healthError?: string
  lastCheckedAt?: string
  managed?: boolean
}
