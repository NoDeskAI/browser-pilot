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
