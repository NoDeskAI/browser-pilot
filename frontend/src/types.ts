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
  type: 'direct' | 'clash' | 'openvpn'
  status: 'healthy' | 'unhealthy' | 'disabled' | 'unsupported' | 'unchecked'
  proxyUrl: string
  healthError?: string
  lastCheckedAt?: string
  managed?: boolean
}

export interface SessionFile {
  id: string
  sessionId?: string | null
  archivedSessionId?: string | null
  archivedSessionName?: string | null
  name: string
  status: 'downloading' | 'completed' | string
  source?: string
  sourceId?: string | null
  contentType?: string | null
  size?: number | null
  receivedBytes?: number | null
  totalBytes?: number | null
  percent?: number | null
  url?: string | null
  uploadedAt?: string | null
  archivedAt?: string | null
  createdAt?: string | null
  updatedAt?: string | null
}

export interface DeleteSessionFileOptions {
  fileDeleteMode: 'none' | 'selected' | 'all'
  deleteFileIds?: string[]
}

export interface DeleteSessionFileHandlingResult {
  mode?: 'none' | 'selected' | 'all'
  completedFileCount?: number
  deletedFileIds?: string[]
  archivedFileIds?: string[]
  objectDeleteFailedFileIds?: string[]
  warning?: 'file_object_delete_failed' | string | null
}

export interface DeleteSessionResult {
  ok: boolean
  files?: DeleteSessionFileHandlingResult
}
