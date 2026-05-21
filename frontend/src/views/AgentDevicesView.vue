<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Activity, Loader2, Lock, RefreshCw, RotateCcw, ShieldCheck, Unlock } from 'lucide-vue-next'
import { toast } from 'vue-sonner'
import { useAuth } from '../composables/useAuth'
import { api } from '../lib/api'
import type { AgentDeviceAuditEvent, AgentDeviceVisibility } from '../types'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'

const { t } = useI18n()
const { user } = useAuth()

const devices = ref<AgentDeviceVisibility[]>([])
const audit = ref<AgentDeviceAuditEvent[]>([])
const loading = ref(false)
const auditLoading = ref(false)
const actionLoading = ref('')
const selectedId = ref('')

const leaseMode = ref<'session_bound' | 'task_bound'>('session_bound')
const taskId = ref('')
const ttlSeconds = ref('')
const expiresAt = ref('')

const selectedDevice = computed(() =>
  devices.value.find(device => device.device_instance_id === selectedId.value) || null,
)
const isAdmin = computed(() => user.value?.role === 'admin' || user.value?.role === 'superadmin')
const canManageSelected = computed(() => {
  const device = selectedDevice.value
  if (!device) return false
  return isAdmin.value || device.owner_user_id === user.value?.id || device.operator_owner_user_id === user.value?.id
})

onMounted(() => {
  void fetchDevices()
})

watch(selectedId, () => {
  void fetchAudit()
})

async function fetchDevices() {
  loading.value = true
  try {
    const res = await api('/api/agent-devices')
    if (!res.ok) throw new Error(await res.text())
    const data = await res.json()
    devices.value = data.devices || []
    if (!selectedId.value || !devices.value.some(device => device.device_instance_id === selectedId.value)) {
      selectedId.value = devices.value[0]?.device_instance_id || ''
    }
  } catch {
    toast.error(t('agentDevices.loadFailed'))
  } finally {
    loading.value = false
  }
}

async function fetchAudit() {
  if (!selectedId.value) {
    audit.value = []
    return
  }
  auditLoading.value = true
  try {
    const res = await api(`/api/agent-devices/${encodeURIComponent(selectedId.value)}/audit?limit=80`)
    if (!res.ok) throw new Error(await res.text())
    const data = await res.json()
    audit.value = data.events || []
  } catch {
    toast.error(t('agentDevices.auditLoadFailed'))
  } finally {
    auditLoading.value = false
  }
}

function leasePayload() {
  const payload: Record<string, unknown> = { leaseMode: leaseMode.value }
  const ttl = Number(ttlSeconds.value)
  if (Number.isFinite(ttl) && ttl > 0) payload.ttlSeconds = ttl
  if (expiresAt.value) {
    const parsed = new Date(expiresAt.value)
    if (!Number.isNaN(parsed.getTime())) payload.expiresAt = parsed.toISOString()
  }
  if (leaseMode.value === 'task_bound' && taskId.value.trim()) {
    payload.taskId = taskId.value.trim()
  }
  return payload
}

async function runDeviceAction(kind: 'acquire' | 'renew' | 'release' | 'reclaim') {
  const device = selectedDevice.value
  if (!device) return
  const leaseId = device.lease_id
  actionLoading.value = kind
  try {
    let path = `/api/agent-devices/${encodeURIComponent(device.device_instance_id)}`
    let method = 'POST'
    let body: Record<string, unknown> | null = null
    if (kind === 'acquire') {
      path += '/leases'
      body = leasePayload()
    } else if (kind === 'renew') {
      if (!leaseId) return
      path += `/leases/${encodeURIComponent(leaseId)}`
      method = 'PATCH'
      body = leasePayload()
    } else if (kind === 'release') {
      if (!leaseId) return
      path += `/leases/${encodeURIComponent(leaseId)}/release`
    } else {
      path += '/reclaim'
      body = leasePayload()
    }
    const res = await api(path, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
    if (!res.ok) throw new Error(await res.text())
    toast.success(t(`agentDevices.${kind === 'acquire' ? 'acquired' : kind === 'renew' ? 'renewed' : kind === 'release' ? 'released' : 'reclaimed'}`))
    await fetchDevices()
    await fetchAudit()
  } catch {
    toast.error(t('agentDevices.actionFailed'))
  } finally {
    actionLoading.value = ''
  }
}

function formatTime(value?: string | null): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  return date.toLocaleString()
}

function shortId(value?: string | null): string {
  if (!value) return '-'
  return value.length > 12 ? `${value.slice(0, 8)}...` : value
}

function stateLabel(value?: string | null): string {
  if (value === 'IDLE' || value === 'idle') return t('agentDevices.idle')
  if (value === 'OCCUPIED' || value === 'leased') return t('agentDevices.occupied')
  if (value === 'RELEASING') return t('agentDevices.releasing')
  if (value === 'ERROR') return t('agentDevices.error')
  if (value === 'QUARANTINED') return t('agentDevices.quarantined')
  if (value === 'expired') return t('agentDevices.expired')
  return value || t('agentDevices.unknown')
}

function modeLabel(value?: string | null): string {
  if (value === 'session_bound') return t('agentDevices.sessionBound')
  if (value === 'task_bound') return t('agentDevices.taskBound')
  return value || '-'
}

function statusLabel(value?: string | null): string {
  if (value === 'succeeded') return t('agentDevices.succeeded')
  if (value === 'failed') return t('agentDevices.failed')
  if (value === 'rejected') return t('agentDevices.rejected')
  if (value === 'active') return t('agentDevices.active')
  if (value === 'recorded') return t('agentDevices.recorded')
  if (value === 'not_recorded') return t('agentDevices.notRecorded')
  if (value === 'captured') return t('agentDevices.captured')
  if (value === 'not_captured') return t('agentDevices.notCaptured')
  if (value === 'not_required') return t('agentDevices.notRequired')
  if (value === 'applied') return t('agentDevices.applied')
  if (value === 'not_applied') return t('agentDevices.notApplied')
  if (value === 'not_applicable') return t('agentDevices.notApplicable')
  if (value === 'unknown') return t('agentDevices.unknown')
  return value || '-'
}

function stateBadgeVariant(value?: string | null) {
  if (value === 'OCCUPIED' || value === 'leased') return 'default' as const
  if (value === 'IDLE' || value === 'idle') return 'secondary' as const
  if (value === 'ERROR' || value === 'QUARANTINED' || value === 'expired') return 'destructive' as const
  return 'outline' as const
}

function auditBadgeVariant(value?: string | null) {
  if (value === 'succeeded') return 'secondary' as const
  if (value === 'failed' || value === 'rejected') return 'destructive' as const
  return 'outline' as const
}

function complianceLabel(value?: string | null): string {
  if (value === 'level1_device_governance') return t('agentDevices.level1')
  return value || '-'
}

function surfaceLabel(value?: string | null): string {
  if (value === 'not_required_level1') return t('agentDevices.surfaceNotRequiredLevel1')
  return value || '-'
}

function policySummary(device: AgentDeviceVisibility): string {
  const policy = device.policy || {}
  const parts: string[] = []
  if (policy.leaseRequired) parts.push(t('agentDevices.leaseRequired'))
  if (policy.exclusiveLease) parts.push(t('agentDevices.exclusiveLease'))
  if (policy.ownerlessActiveLeaseAllowed === false) parts.push(t('agentDevices.ownerlessBlocked'))
  if (policy.controlTransfer) parts.push(`${t('agentDevices.controlTransfer')}: ${policy.controlTransfer}`)
  return parts.length ? parts.join(' · ') : '-'
}

function shortList(values?: string[] | null): string {
  if (!values?.length) return '-'
  return values.join(', ')
}
</script>

<template>
  <div class="flex-1 overflow-y-auto bg-background">
    <div class="mx-auto flex max-w-7xl flex-col gap-5 px-6 py-8">
      <header class="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 class="text-xl font-semibold text-foreground">{{ t('agentDevices.title') }}</h2>
          <p class="mt-1 text-sm text-muted-foreground">
            {{ t('agentDevices.description', { count: devices.length }) }}
          </p>
        </div>
        <Button variant="outline" size="sm" :disabled="loading" @click="fetchDevices">
          <RefreshCw class="size-4" :class="{ 'animate-spin': loading }" />
          {{ t('agentDevices.refresh') }}
        </Button>
      </header>

      <div class="grid gap-5 2xl:grid-cols-[minmax(0,1fr)_360px]">
        <div class="max-w-full space-y-2 overflow-hidden md:hidden">
          <div v-if="loading && !devices.length" class="rounded-md border border-border px-4 py-8 text-center text-muted-foreground">
            <Loader2 class="mx-auto mb-2 size-5 animate-spin" />
            {{ t('agentDevices.loading') }}
          </div>
          <div v-else-if="!devices.length" class="rounded-md border border-border px-4 py-8 text-center text-muted-foreground">
            {{ t('agentDevices.empty') }}
          </div>
          <button
            v-for="device in devices"
            :key="device.device_instance_id"
            class="flex w-full max-w-full items-start gap-3 overflow-hidden rounded-md border border-border bg-background px-3 py-3 text-left"
            :class="selectedId === device.device_instance_id ? 'border-foreground/30 bg-muted/60' : ''"
            @click="selectedId = device.device_instance_id"
          >
            <ShieldCheck class="mt-1 size-4 shrink-0 text-muted-foreground" />
            <span class="min-w-0 flex-1 overflow-hidden">
              <span class="block truncate font-medium">{{ device.session_name || device.session_id }}</span>
              <span class="block truncate text-xs text-muted-foreground">{{ device.device_instance_id }}</span>
              <span class="mt-2 block max-w-full overflow-hidden text-ellipsis whitespace-nowrap text-xs text-muted-foreground">
                {{ device.provider || '-' }} · {{ complianceLabel(device.compliance_level) }}
              </span>
              <span class="mt-1 block max-w-full overflow-hidden text-ellipsis whitespace-nowrap text-xs text-muted-foreground">
                {{ device.runtime_state || device.containerStatus || '-' }} · {{ device.current_operator || t('agentDevices.noLease') }}
              </span>
            </span>
            <Badge :variant="stateBadgeVariant(device.state)" class="shrink-0">
              {{ stateLabel(device.state) }}
            </Badge>
          </button>
        </div>

        <div class="hidden min-w-0 overflow-hidden rounded-md border border-border bg-background md:block">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{{ t('agentDevices.device') }}</TableHead>
                <TableHead class="w-[96px]">{{ t('agentDevices.state') }}</TableHead>
                <TableHead class="w-[112px]">{{ t('agentDevices.provider') }}</TableHead>
                <TableHead class="w-[136px]">{{ t('agentDevices.compliance') }}</TableHead>
                <TableHead class="w-[156px]">{{ t('agentDevices.operator') }}</TableHead>
                <TableHead class="w-[128px]">{{ t('agentDevices.updated') }}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow v-if="loading && !devices.length">
                <TableCell :colspan="6" class="h-32 text-center text-muted-foreground">
                  <Loader2 class="mx-auto mb-2 size-5 animate-spin" />
                  {{ t('agentDevices.loading') }}
                </TableCell>
              </TableRow>
              <TableRow v-else-if="!devices.length">
                <TableCell :colspan="6" class="h-32 text-center text-muted-foreground">
                  {{ t('agentDevices.empty') }}
                </TableCell>
              </TableRow>
              <TableRow
                v-for="device in devices"
                :key="device.device_instance_id"
                class="cursor-pointer"
                :class="selectedId === device.device_instance_id ? 'bg-muted/60' : ''"
                @click="selectedId = device.device_instance_id"
              >
                <TableCell class="min-w-[220px]">
                  <div class="flex min-w-0 items-center gap-2">
                    <ShieldCheck class="size-4 shrink-0 text-muted-foreground" />
                    <div class="min-w-0">
                      <div class="truncate font-medium">{{ device.session_name || device.session_id }}</div>
                      <div class="truncate text-xs text-muted-foreground">{{ device.device_instance_id }}</div>
                    </div>
                  </div>
                </TableCell>
                <TableCell>
                  <Badge :variant="stateBadgeVariant(device.state)">
                    {{ stateLabel(device.state) }}
                  </Badge>
                </TableCell>
                <TableCell class="text-sm text-muted-foreground">
                  {{ device.provider || '-' }}
                </TableCell>
                <TableCell class="text-sm text-muted-foreground">
                  {{ complianceLabel(device.compliance_level) }}
                </TableCell>
                <TableCell class="text-sm text-muted-foreground">
                  <span class="block truncate" :title="device.current_operator || ''">
                    {{ device.current_operator || t('agentDevices.noLease') }}
                  </span>
                </TableCell>
                <TableCell class="text-xs text-muted-foreground">
                  {{ formatTime(device.updated_at) }}
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </div>

        <aside class="min-w-0 space-y-5">
          <section class="rounded-md border border-border bg-background p-4">
            <div class="mb-4 flex items-center justify-between gap-3">
              <h3 class="text-sm font-semibold">{{ t('agentDevices.details') }}</h3>
              <Badge v-if="selectedDevice" :variant="stateBadgeVariant(selectedDevice.state)">
                {{ stateLabel(selectedDevice.state) }}
              </Badge>
            </div>
            <div v-if="selectedDevice" class="space-y-3 text-sm">
              <div class="grid grid-cols-[112px_minmax(0,1fr)] gap-2">
                <span class="text-muted-foreground">{{ t('agentDevices.device') }}</span>
                <span class="truncate" :title="selectedDevice.device_instance_id">{{ selectedDevice.device_instance_id }}</span>
                <span class="text-muted-foreground">{{ t('agentDevices.session') }}</span>
                <span class="truncate" :title="selectedDevice.session_id">{{ selectedDevice.session_name || selectedDevice.session_id }}</span>
                <span class="text-muted-foreground">{{ t('agentDevices.provider') }}</span>
                <span class="truncate">{{ selectedDevice.provider || '-' }}</span>
                <span class="text-muted-foreground">{{ t('agentDevices.profile') }}</span>
                <span class="truncate">{{ selectedDevice.device_profile || selectedDevice.device_type }}</span>
                <span class="text-muted-foreground">{{ t('agentDevices.context') }}</span>
                <span class="truncate" :title="selectedDevice.context_id || ''">{{ selectedDevice.context_id || '-' }}</span>
                <span class="text-muted-foreground">{{ t('agentDevices.compliance') }}</span>
                <span class="truncate">{{ complianceLabel(selectedDevice.compliance_level) }}</span>
                <span class="text-muted-foreground">{{ t('agentDevices.concurrency') }}</span>
                <span class="truncate">{{ selectedDevice.concurrency_model || '-' }}</span>
                <span class="text-muted-foreground">{{ t('agentDevices.lease') }}</span>
                <span class="truncate" :title="selectedDevice.lease_id || ''">{{ shortId(selectedDevice.lease_id) }}</span>
                <span class="text-muted-foreground">{{ t('agentDevices.mode') }}</span>
                <span>{{ modeLabel(selectedDevice.lease?.lease_mode || selectedDevice.lease_mode) }}</span>
                <span class="text-muted-foreground">{{ t('agentDevices.operator') }}</span>
                <span class="truncate" :title="selectedDevice.current_operator || ''">{{ selectedDevice.current_operator || '-' }}</span>
                <span class="text-muted-foreground">{{ t('agentDevices.expires') }}</span>
                <span>{{ formatTime(selectedDevice.lease?.expires_at) }}</span>
                <span class="text-muted-foreground">{{ t('agentDevices.task') }}</span>
                <span class="truncate" :title="selectedDevice.task_id || ''">{{ selectedDevice.task_id || '-' }}</span>
                <span class="text-muted-foreground">{{ t('agentDevices.surface') }}</span>
                <span class="truncate" :title="selectedDevice.observable_surface_ref || ''">{{ selectedDevice.observable_surface_ref || '-' }}</span>
                <span class="text-muted-foreground">{{ t('agentDevices.surfaceStatus') }}</span>
                <span class="truncate">{{ surfaceLabel(selectedDevice.observable_surface_status) }}</span>
                <span class="text-muted-foreground">{{ t('agentDevices.policy') }}</span>
                <span class="truncate" :title="policySummary(selectedDevice)">{{ policySummary(selectedDevice) }}</span>
                <span class="text-muted-foreground">{{ t('agentDevices.unsupported') }}</span>
                <span class="truncate" :title="shortList(selectedDevice.unsupported_profiles)">{{ shortList(selectedDevice.unsupported_profiles) }}</span>
                <span class="text-muted-foreground">{{ t('agentDevices.lastAction') }}</span>
                <span class="truncate" :title="selectedDevice.last_action_summary?.action || ''">
                  {{ selectedDevice.last_action_summary?.action || '-' }}
                </span>
                <span class="text-muted-foreground">{{ t('agentDevices.evidence') }}</span>
                <span>{{ statusLabel(selectedDevice.last_action_summary?.evidenceStatus) }}</span>
                <span class="text-muted-foreground">{{ t('agentDevices.failure') }}</span>
                <span class="truncate" :title="selectedDevice.last_action_summary?.failureCategory || ''">
                  {{ selectedDevice.last_action_summary?.failureCategory || '-' }}
                </span>
                <span class="text-muted-foreground">{{ t('agentDevices.auditStatus') }}</span>
                <span>{{ statusLabel(selectedDevice.last_action_summary?.auditStatus) }}</span>
              </div>

              <div class="space-y-3 border-t border-border pt-4">
                <div class="grid grid-cols-2 gap-3">
                  <div class="space-y-1.5">
                    <Label for="agent-device-mode">{{ t('agentDevices.mode') }}</Label>
                    <Select v-model="leaseMode">
                      <SelectTrigger id="agent-device-mode">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="session_bound">{{ t('agentDevices.sessionBound') }}</SelectItem>
                        <SelectItem value="task_bound">{{ t('agentDevices.taskBound') }}</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div class="space-y-1.5">
                    <Label for="agent-device-ttl">{{ t('agentDevices.ttlSeconds') }}</Label>
                    <Input id="agent-device-ttl" v-model="ttlSeconds" inputmode="numeric" placeholder="3600" />
                  </div>
                </div>
                <div class="grid grid-cols-2 gap-3">
                  <div class="space-y-1.5">
                    <Label for="agent-device-expires">{{ t('agentDevices.expiresAt') }}</Label>
                    <Input id="agent-device-expires" v-model="expiresAt" type="datetime-local" />
                  </div>
                  <div class="space-y-1.5">
                    <Label for="agent-device-task">{{ t('agentDevices.taskId') }}</Label>
                    <Input id="agent-device-task" v-model="taskId" :disabled="leaseMode !== 'task_bound'" />
                  </div>
                </div>

                <div class="grid grid-cols-2 gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    :disabled="!!selectedDevice.lease_id || actionLoading === 'acquire' || (leaseMode === 'task_bound' && !taskId.trim())"
                    @click="runDeviceAction('acquire')"
                  >
                    <Loader2 v-if="actionLoading === 'acquire'" class="size-4 animate-spin" />
                    <Lock v-else class="size-4" />
                    {{ t('agentDevices.acquire') }}
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    :disabled="!selectedDevice.lease_id || actionLoading === 'renew' || (leaseMode === 'task_bound' && !taskId.trim())"
                    @click="runDeviceAction('renew')"
                  >
                    <Loader2 v-if="actionLoading === 'renew'" class="size-4 animate-spin" />
                    <RefreshCw v-else class="size-4" />
                    {{ t('agentDevices.renew') }}
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    :disabled="!selectedDevice.lease_id || !canManageSelected || actionLoading === 'release'"
                    @click="runDeviceAction('release')"
                  >
                    <Loader2 v-if="actionLoading === 'release'" class="size-4 animate-spin" />
                    <Unlock v-else class="size-4" />
                    {{ t('agentDevices.release') }}
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    :disabled="!canManageSelected || actionLoading === 'reclaim' || (leaseMode === 'task_bound' && !taskId.trim())"
                    @click="runDeviceAction('reclaim')"
                  >
                    <Loader2 v-if="actionLoading === 'reclaim'" class="size-4 animate-spin" />
                    <RotateCcw v-else class="size-4" />
                    {{ t('agentDevices.reclaim') }}
                  </Button>
                </div>
              </div>
            </div>
            <div v-else class="py-8 text-center text-sm text-muted-foreground">
              {{ t('agentDevices.empty') }}
            </div>
          </section>

          <section class="rounded-md border border-border bg-background">
            <div class="flex items-center justify-between border-b border-border px-4 py-3">
              <h3 class="text-sm font-semibold">{{ t('agentDevices.audit') }}</h3>
              <Loader2 v-if="auditLoading" class="size-4 animate-spin text-muted-foreground" />
            </div>
            <div class="max-h-[460px] overflow-y-auto">
              <div v-if="!audit.length && !auditLoading" class="px-4 py-8 text-center text-sm text-muted-foreground">
                {{ t('agentDevices.noAudit') }}
              </div>
              <div
                v-for="event in audit"
                :key="event.id"
                class="border-b border-border px-4 py-3 last:border-0"
              >
                <div class="flex items-center justify-between gap-3">
                  <div class="flex min-w-0 items-center gap-2">
                    <Activity class="size-4 shrink-0 text-muted-foreground" />
                    <span class="truncate text-sm font-medium" :title="event.action">{{ event.action }}</span>
                  </div>
                  <Badge :variant="auditBadgeVariant(event.status)">
                    {{ statusLabel(event.status) }}
                  </Badge>
                </div>
                <div class="mt-2 grid grid-cols-[96px_minmax(0,1fr)] gap-1 text-xs text-muted-foreground">
                  <span>{{ t('agentDevices.operator') }}</span>
                  <span class="truncate" :title="event.operator || ''">{{ event.operator || '-' }}</span>
                  <span>{{ t('agentDevices.lease') }}</span>
                  <span class="truncate" :title="event.lease_id || ''">{{ shortId(event.lease_id) }}</span>
                  <span>{{ t('agentDevices.sideEffect') }}</span>
                  <span>{{ statusLabel(event.sideEffectStatus || event.side_effect_level) }}</span>
                  <span>{{ t('agentDevices.evidence') }}</span>
                  <span>{{ statusLabel(event.evidenceStatus) }}</span>
                  <span>{{ t('agentDevices.failure') }}</span>
                  <span class="truncate" :title="event.failureCategory || event.error_code || ''">
                    {{ event.failureCategory || event.error_code || '-' }}
                  </span>
                  <span>{{ t('agentDevices.auditStatus') }}</span>
                  <span>{{ statusLabel(event.auditStatus) }}</span>
                  <span>{{ t('agentDevices.updated') }}</span>
                  <span>{{ formatTime(event.occurred_at) }}</span>
                </div>
              </div>
            </div>
          </section>
        </aside>
      </div>
    </div>
  </div>
</template>
