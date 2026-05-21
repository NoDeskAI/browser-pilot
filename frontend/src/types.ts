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

export interface AgentDeviceLease {
  id: string
  device_instance_id: string
  device_type: 'browser_session' | string
  lease_mode: 'session_bound' | 'task_bound' | string
  status: 'active' | 'released' | 'expired' | 'reclaimed' | string
  operator: string
  operator_owner_user_id?: string | null
  task_id?: string | null
  expires_at?: string | null
  acquired_at?: string | null
  updated_at?: string | null
  released_at?: string | null
  release_reason?: string | null
}

export interface AgentDeviceActionSummary {
  action?: string
  status?: string
  executionStatus?: string
  auditEventId?: string
  auditStatus?: string
  evidenceStatus?: string
  sideEffectLevel?: string
  sideEffectStatus?: string
  failureCategory?: string | null
  occurredAt?: string
}

export interface AgentDeviceVisibility {
  device_instance_id: string
  device_type: 'browser_session' | string
  provider?: string
  device_profile?: string
  state: 'IDLE' | 'OCCUPIED' | 'RELEASING' | 'ERROR' | 'QUARANTINED' | string
  browser_pilot_state?: 'idle' | 'leased' | 'expired' | string
  lease_id?: string | null
  lease_mode?: string | null
  current_operator?: string | null
  task_id?: string | null
  session_id: string
  session_name?: string | null
  owner_user_id?: string | null
  operator_owner_user_id?: string | null
  context_id?: string | null
  compliance_level?: string
  concurrency_model?: string
  supported_lease_modes?: string[]
  unsupported_profiles?: string[]
  policy?: Record<string, any> | null
  admitted_by?: string | null
  admitted_at?: string | null
  capabilities?: string[]
  pause_capability: string
  needs_intervention: boolean
  observable_surface_ref?: string | null
  observable_surface_status?: string | null
  last_action_summary?: AgentDeviceActionSummary | null
  updated_at?: string | null
  runtime_state?: string | null
  containerStatus?: string | null
  lease?: AgentDeviceLease | null
}

export interface AgentDeviceAuditEvent {
  id: string
  device_instance_id: string
  device_type: string
  session_id?: string | null
  tenant_id?: string | null
  lease_id?: string | null
  operator?: string | null
  operator_owner_user_id?: string | null
  action: string
  status: 'succeeded' | 'failed' | 'rejected' | string
  executionStatus?: string
  side_effect_level?: string | null
  sideEffectLevel?: string | null
  sideEffectStatus?: string | null
  failureCategory?: string | null
  auditStatus?: string | null
  evidenceStatus?: string | null
  stateChanged?: boolean
  evidenceRefs?: Array<Record<string, any>>
  retry_safety?: string | null
  route?: string | null
  method?: string | null
  status_code?: number | null
  error_code?: string | null
  error_message?: string | null
  metadata?: Record<string, any> | null
  occurred_at: string
}
