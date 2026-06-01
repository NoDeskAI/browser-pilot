<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Activity, Loader2, RefreshCw } from 'lucide-vue-next'
import { api } from '../lib/api'
import { useNotify } from '../composables/useNotify'
import type { AgentDeviceAuditEvent } from '../types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'

const props = withDefaults(defineProps<{
  open: boolean
  sessionId?: string | null
  sessionName?: string | null
  limit?: number
}>(), {
  sessionId: null,
  sessionName: null,
  limit: 80,
})

const emit = defineEmits<{
  'update:open': [value: boolean]
}>()

const { t } = useI18n()
const notify = useNotify()

const events = ref<AgentDeviceAuditEvent[]>([])
const loading = ref(false)
const loadFailed = ref(false)
const loadedSessionId = ref<string | null>(null)
let requestSeq = 0

watch(() => [props.open, props.sessionId] as const, ([open]) => {
  if (!open) {
    requestSeq += 1
    loading.value = false
    return
  }
  void fetchAudit()
}, { immediate: true })

function onOpenChange(value: boolean) {
  if (!value) {
    requestSeq += 1
  }
  emit('update:open', value)
}

async function fetchAudit() {
  const sessionId = props.sessionId
  if (!props.open || !sessionId) {
    events.value = []
    loadedSessionId.value = null
    loadFailed.value = false
    return
  }

  const seq = requestSeq + 1
  requestSeq = seq
  if (loadedSessionId.value !== sessionId) {
    events.value = []
  }
  loading.value = true
  loadFailed.value = false

  try {
    const res = await api(`/api/agent-devices/${encodeURIComponent(sessionId)}/audit?limit=${props.limit}`)
    if (!res.ok) throw new Error(await res.text())
    const data = await res.json()
    if (seq !== requestSeq || !props.open || props.sessionId !== sessionId) return
    events.value = data.events || []
    loadedSessionId.value = sessionId
  } catch {
    if (seq !== requestSeq || !props.open || props.sessionId !== sessionId) return
    events.value = []
    loadedSessionId.value = sessionId
    loadFailed.value = true
    notify.error(t('agentDevices.auditLoadFailed'))
  } finally {
    if (seq === requestSeq && props.open && props.sessionId === sessionId) {
      loading.value = false
    }
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

function auditBadgeVariant(value?: string | null) {
  if (value === 'succeeded') return 'secondary' as const
  if (value === 'failed' || value === 'rejected') return 'destructive' as const
  return 'outline' as const
}
</script>

<template>
  <Dialog :open="open" @update:open="onOpenChange">
    <DialogContent class="sm:max-w-2xl">
      <DialogHeader>
        <DialogTitle>{{ t('session.auditTitle') }}</DialogTitle>
        <DialogDescription>
          {{ t('session.auditDescription', { name: sessionName || sessionId || '-' }) }}
        </DialogDescription>
      </DialogHeader>

      <div class="flex items-center justify-between gap-3">
        <div class="min-w-0 text-xs text-muted-foreground">
          <span class="font-mono">{{ sessionId || '-' }}</span>
        </div>
        <Button
          variant="outline"
          size="sm"
          class="h-8 gap-1.5"
          :disabled="loading || !sessionId"
          @click="fetchAudit"
        >
          <RefreshCw class="size-3.5" :class="{ 'animate-spin': loading }" />
          {{ t('session.auditRefresh') }}
        </Button>
      </div>

      <div class="max-h-[60vh] overflow-y-auto rounded-md border border-border">
        <div v-if="loading && !events.length" class="px-4 py-10 text-center text-sm text-muted-foreground">
          <Loader2 class="mx-auto mb-2 size-5 animate-spin" />
          {{ t('session.auditLoading') }}
        </div>
        <div v-else-if="loadFailed && !events.length" class="px-4 py-10 text-center text-sm text-muted-foreground">
          {{ t('agentDevices.auditLoadFailed') }}
        </div>
        <div v-else-if="!events.length" class="px-4 py-10 text-center text-sm text-muted-foreground">
          {{ t('agentDevices.noAudit') }}
        </div>
        <template v-else>
          <div
            v-for="event in events"
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
        </template>
      </div>
    </DialogContent>
  </Dialog>
</template>
