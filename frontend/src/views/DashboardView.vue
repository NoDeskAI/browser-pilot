<script setup lang="ts">
import { ref, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useSessions } from '../composables/useSessions'
import { useNetworkEgress } from '../composables/useNetworkEgress'
import { useNotify } from '../composables/useNotify'
import type { DeleteSessionFileOptions } from '../types'
import { Plus, Play, Pause, Trash2, Monitor, Globe, Hash, Clock, RefreshCw, Loader2, Network, ArrowUpRight, Bot } from 'lucide-vue-next'
import { formatSessionLeaseOperator } from '../lib/sessionLease'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { Card } from '@/components/ui/card'
import { Switch } from '@/components/ui/switch'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import SessionDeleteDialog from '../components/SessionDeleteDialog.vue'
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'

const { t } = useI18n()
const router = useRouter()
const notify = useNotify()
const {
  state: sessions,
  createSession, deleteSession, renameSession,
  startContainer, pauseContainer, fetchSessions,
  fetchBrowserImages,
} = useSessions()
const { state: egressState, fetchNetworkEgress } = useNetworkEgress()

const readyImages = ref<any[]>([])
const hasReadyImages = ref(false)
const createDialogOpen = ref(false)
const createName = ref('')
const createVersion = ref('')
const createNetworkEgressId = ref('__direct__')
const DIRECT_EGRESS_VALUE = '__direct__'

const isMac = navigator.platform.includes('Mac')
const shortcutLabel = isMac ? '⌘N' : 'Ctrl+N'
const browserImagesSettingsPath = '/settings/browser-images'

const autoRefresh = ref(localStorage.getItem('bp_auto_refresh') === 'true')
let refreshTimer: ReturnType<typeof setInterval> | null = null
let autoRefreshFetchInFlight = false

function setAutoRefresh(on: boolean) {
  autoRefresh.value = on
}

async function fetchSessionsForAutoRefresh() {
  if (autoRefreshFetchInFlight) return
  autoRefreshFetchInFlight = true
  try {
    await fetchSessions()
  } finally {
    autoRefreshFetchInFlight = false
  }
}

function startTimer() {
  stopTimer()
  void fetchSessionsForAutoRefresh()
  refreshTimer = setInterval(() => {
    void fetchSessionsForAutoRefresh()
  }, 3000)
}
function stopTimer() {
  if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null }
}

function refreshWhenVisible() {
  if (autoRefresh.value && document.visibilityState === 'visible') {
    void fetchSessionsForAutoRefresh()
  }
}

function refreshWhenFocused() {
  if (autoRefresh.value) {
    void fetchSessionsForAutoRefresh()
  }
}

function leaseOperatorLabel(lease: NonNullable<typeof sessions.sessions[number]['activeLease']>): string {
  return formatSessionLeaseOperator(lease, t)
}

watch(autoRefresh, (on) => {
  localStorage.setItem('bp_auto_refresh', String(on))
  on ? startTimer() : stopTimer()
})

onMounted(async () => {
  document.addEventListener('visibilitychange', refreshWhenVisible)
  window.addEventListener('focus', refreshWhenFocused)
  if (autoRefresh.value) {
    startTimer()
  } else {
    fetchSessions()
  }
  try {
    const [imgs] = await Promise.all([fetchBrowserImages(), fetchNetworkEgress()])
    readyImages.value = imgs
    hasReadyImages.value = imgs.length > 0
    if (imgs.length > 0 && !createVersion.value) {
      createVersion.value = imgs[0].chromeVersion || String(imgs[0].chromeMajor)
    }
  } catch {
    // keep optimistic default
  }
})
onUnmounted(() => {
  stopTimer()
  document.removeEventListener('visibilitychange', refreshWhenVisible)
  window.removeEventListener('focus', refreshWhenFocused)
})

const editingId = ref<string | null>(null)
const editName = ref('')
const inputRefs = ref<Record<string, HTMLInputElement>>({})
const copiedId = ref<string | null>(null)
const deleteDialogOpen = ref<Record<string, boolean>>({})
const deleting = ref<Record<string, boolean>>({})
const creating = ref(false)
const starting = ref<Record<string, boolean>>({})
const pausing = ref<Record<string, boolean>>({})

function formatUrl(raw: string): string {
  if (!raw) return ''
  try {
    const u = new URL(raw)
    return u.hostname + u.pathname.replace(/\/$/, '')
  } catch {
    return raw
  }
}

function copyId(id: string) {
  navigator.clipboard.writeText(id).then(() => {
    copiedId.value = id
    setTimeout(() => {
      if (copiedId.value === id) copiedId.value = null
    }, 1500)
  })
}

function formatRelativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return t('session.justNow')
  if (mins < 60) return t('session.minutesAgo', { n: mins })
  const hours = Math.floor(mins / 60)
  if (hours < 24) return t('session.hoursAgo', { n: hours })
  return t('session.daysAgo', { n: Math.floor(hours / 24) })
}

function openCreateDialog() {
  if (!hasReadyImages.value) return
  if (!createVersion.value && readyImages.value.length > 0) {
    createVersion.value = readyImages.value[0].chromeVersion || String(readyImages.value[0].chromeMajor)
  }
  createDialogOpen.value = true
}

async function handleCreateSession(name?: string, chromeVersion?: string, networkEgressId?: string) {
  if (creating.value) return
  creating.value = true
  try {
    const selectedEgress = networkEgressId && networkEgressId !== DIRECT_EGRESS_VALUE ? networkEgressId : null
    const session = await createSession(name?.trim() || undefined, chromeVersion || undefined, selectedEgress)
    if (session) {
      notify.success(t('app.sessionCreated'))
      createDialogOpen.value = false
      createName.value = ''
      createNetworkEgressId.value = DIRECT_EGRESS_VALUE
      router.push(`/s/${session.id}`)
    }
  } catch {
    notify.error(t('app.sessionCreateError'))
  } finally {
    creating.value = false
  }
}

function startEdit(id: string, name: string) {
  editName.value = name
  editingId.value = id
  nextTick(() => {
    const el = inputRefs.value[id]
    if (el) el.select()
  })
}

function commitEdit(id: string, oldName: string) {
  const trimmed = editName.value.trim()
  editingId.value = null
  if (trimmed && trimmed !== oldName) {
    renameSession(id, trimmed)
  }
}

async function onDeleteSession(id: string, options: DeleteSessionFileOptions) {
  if (deleting.value[id]) return
  deleting.value[id] = true
  try {
    const result = await deleteSession(id, options)
    if (result.files?.warning === 'file_object_delete_failed') {
      notify.warning(t('sessionFiles.deleteObjectWarning'))
    } else {
      notify.success(t('app.sessionDeleted'))
    }
    deleteDialogOpen.value[id] = false
  } catch {
    notify.error(t('app.sessionDeleteError'))
  } finally {
    deleting.value[id] = false
  }
}

async function onStartContainer(id: string) {
  if (starting.value[id]) return
  starting.value[id] = true
  try {
    await startContainer(id)
    notify.success(t('app.sessionStarted'))
  } catch {
    notify.error(t('app.containerStartError'))
  } finally {
    starting.value[id] = false
  }
}

async function onPauseContainer(id: string) {
  if (pausing.value[id]) return
  pausing.value[id] = true
  try {
    await pauseContainer(id)
    notify.success(t('app.sessionPaused'))
  } catch {
    notify.error(t('app.containerPauseError'))
  } finally {
    pausing.value[id] = false
  }
}
</script>

<template>
  <div class="flex-1 overflow-y-auto">
    <div class="max-w-6xl mx-auto px-6 py-8 space-y-6">
      <div class="flex items-center justify-between">
        <h2 class="text-xl font-semibold">{{ t('dashboard.title') }}</h2>
        <div class="flex items-center gap-3">
          <Button variant="outline" size="sm" :disabled="autoRefresh" @click="fetchSessions" :title="t('dashboard.refresh')">
            <RefreshCw class="size-4" />
          </Button>
          <div class="flex items-center gap-1.5 text-sm text-muted-foreground select-none">
            <Switch id="dashboard-auto-refresh" :model-value="autoRefresh" @update:model-value="setAutoRefresh" />
            <label for="dashboard-auto-refresh" class="cursor-pointer">
              {{ t('dashboard.autoRefresh') }}
            </label>
          </div>
          <Tooltip v-if="hasReadyImages">
            <TooltipTrigger as-child>
              <Button :disabled="creating" class="gap-2" @click="openCreateDialog">
                <Loader2 v-if="creating" class="size-4 animate-spin" />
                <Plus v-else class="size-4" />
                {{ creating ? t('session.creating') : t('dashboard.create') }}
                <kbd v-if="!creating" data-slot="kbd">{{ shortcutLabel }}</kbd>
              </Button>
            </TooltipTrigger>
            <TooltipContent>
              {{ t('dashboard.createHint') }}
              <kbd v-if="!creating" data-slot="kbd">{{ shortcutLabel }}</kbd>
            </TooltipContent>
          </Tooltip>
          <Tooltip v-else>
            <TooltipTrigger as-child>
              <span class="inline-flex">
                <Button disabled class="gap-2">
                  <Plus class="size-4" />
                  {{ t('dashboard.create') }}
                  <kbd data-slot="kbd">{{ shortcutLabel }}</kbd>
                </Button>
              </span>
            </TooltipTrigger>
            <TooltipContent class="gap-2">
              <span>{{ t('dashboard.noImageHint') }}</span>
              <RouterLink
                :to="browserImagesSettingsPath"
                class="inline-flex items-center gap-0.5 rounded-sm font-medium underline underline-offset-2 outline-none transition-opacity hover:opacity-80 focus-visible:ring-2 focus-visible:ring-background/60"
              >
                {{ t('dashboard.configureImages') }}
                <ArrowUpRight class="size-3" aria-hidden="true" />
              </RouterLink>
            </TooltipContent>
          </Tooltip>
        </div>
      </div>

      <div v-if="sessions.sessions.length > 0" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        <Card
          v-for="s in sessions.sessions" :key="s.id"
          class="flex flex-col cursor-pointer transition-all hover:shadow-md hover:border-primary/30 group p-0 gap-0"
          @click="router.push(`/s/${s.id}`)"
        >
          <div class="p-4 pb-3 flex items-start justify-between gap-3">
            <div class="min-w-0 flex-1">
              <input
                v-if="editingId === s.id"
                :ref="(el) => { if (el) inputRefs[s.id] = el as HTMLInputElement }"
                v-model="editName"
                class="w-full text-base font-medium bg-transparent border-b border-foreground outline-none"
                @blur="commitEdit(s.id, s.name)"
                @keydown.enter.prevent="commitEdit(s.id, s.name)"
                @keydown.escape.prevent="editingId = null"
                @click.stop
              />
              <h3
                v-else
                class="text-base font-medium truncate tracking-tight group-hover:text-primary transition-colors"
                @dblclick.stop="startEdit(s.id, s.name)"
                :title="s.name + '\n' + t('session.dblClickRename')"
              >
                {{ s.name }}
              </h3>
            </div>
            <Badge
              variant="outline"
              class="shrink-0 font-medium uppercase text-[10px] px-2 py-0.5"
              :class="{
                'bg-green-500/10 text-green-500 border-green-500/20': s.containerStatus === 'running',
                'bg-blue-500/10 text-blue-500 border-blue-500/20': s.containerStatus === 'starting',
                'bg-yellow-500/10 text-yellow-500 border-yellow-500/20': s.containerStatus === 'paused',
                'bg-muted/50 text-muted-foreground border-border': s.containerStatus !== 'running' && s.containerStatus !== 'paused' && s.containerStatus !== 'starting'
              }"
            >
              {{ s.containerStatus === 'running' ? t('session.running') : s.containerStatus === 'starting' ? t('session.starting') : s.containerStatus === 'paused' ? t('session.paused') : t('session.stopped') }}
            </Badge>
            <Badge
              v-if="s.activeLease"
              variant="outline"
              class="shrink-0 border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-700 dark:text-amber-300"
              :title="leaseOperatorLabel(s.activeLease)"
            >
              {{ t('sessionLease.occupied') }}
            </Badge>
          </div>

          <div class="px-4 pb-4 flex-1 flex flex-col gap-2.5 min-h-0 justify-center">
            <div class="flex items-center gap-2">
              <div class="flex items-center gap-1.5 text-muted-foreground shrink-0 w-12">
                <Hash class="size-3.5" />
                <span class="text-[11px] whitespace-nowrap">ID</span>
              </div>
              <span
                class="text-xs font-mono text-muted-foreground hover:text-foreground transition-colors cursor-pointer bg-muted/30 px-1.5 py-0.5 rounded"
                :class="copiedId === s.id ? 'text-green-500 bg-green-500/10' : ''"
                @click.stop="copyId(s.id)"
                :title="t('session.copyId')"
              >
                {{ copiedId === s.id ? t('session.copied') : s.id.slice(0, 8) }}
              </span>
            </div>
            <div class="flex items-center gap-2 min-w-0">
              <div class="flex items-center gap-1.5 text-muted-foreground shrink-0 w-12">
                <Globe class="size-3.5" />
                <span class="text-[11px] whitespace-nowrap">URL</span>
              </div>
              <span class="text-xs text-muted-foreground truncate flex-1 min-w-0" :title="s.currentUrl">
                {{ formatUrl(s.currentUrl) || '-' }}
              </span>
            </div>
            <div class="flex items-center gap-2 min-w-0">
              <div class="flex items-center gap-1.5 text-muted-foreground shrink-0 w-12">
                <Network class="size-3.5" />
                <span class="text-[11px] whitespace-nowrap">{{ t('networkEgress.shortLabel') }}</span>
              </div>
              <span
                class="text-xs truncate flex-1 min-w-0"
                :class="s.networkEgressStatus === 'unhealthy' || s.networkEgressStatus === 'unsupported' ? 'text-destructive' : 'text-muted-foreground'"
                :title="s.networkEgressHealthError || s.networkEgressProxyUrl || s.networkEgressName"
              >
                {{ s.networkEgressName || t('networkEgress.direct') }}
              </span>
            </div>
            <div v-if="s.activeLease" class="flex items-center gap-2 min-w-0">
              <div class="flex items-center gap-1.5 text-muted-foreground shrink-0 w-12">
                <Bot class="size-3.5" />
                <span class="text-[11px] whitespace-nowrap">Agent</span>
              </div>
              <span
                class="text-xs truncate flex-1 min-w-0 text-amber-700 dark:text-amber-300"
                :title="leaseOperatorLabel(s.activeLease)"
              >
                {{ leaseOperatorLabel(s.activeLease) }}
              </span>
            </div>
          </div>

          <div class="px-4 py-3 border-t border-border/50 flex items-center justify-between bg-muted/10">
            <div class="flex items-center gap-1.5 text-muted-foreground">
              <Clock class="size-3.5 opacity-70" />
              <span class="text-[11px] font-medium">{{ formatRelativeTime(s.updatedAt) }}</span>
            </div>
            <div class="flex items-center gap-1">
              <Tooltip v-if="s.containerStatus === 'running'">
                <TooltipTrigger as-child>
                  <Button
                    variant="ghost" size="sm" class="size-7 p-0 text-muted-foreground hover:text-foreground"
                    :disabled="pausing[s.id]"
                    @click.stop="onPauseContainer(s.id)"
                  >
                    <Loader2 v-if="pausing[s.id]" class="size-3.5 animate-spin" />
                    <Pause v-else class="size-3.5" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>{{ t('session.hibernateHint') }}</TooltipContent>
              </Tooltip>
              
              <Tooltip v-else>
                <TooltipTrigger as-child>
                  <Button
                    variant="ghost" size="sm" class="size-7 p-0 text-muted-foreground hover:text-foreground"
                    :disabled="starting[s.id] || s.containerStatus === 'starting'"
                    @click.stop="onStartContainer(s.id)"
                  >
                    <Loader2 v-if="starting[s.id] || s.containerStatus === 'starting'" class="size-3.5 animate-spin" />
                    <Play v-else class="size-3.5" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>{{ s.containerStatus === 'starting' ? t('session.starting') : s.containerStatus === 'paused' ? t('session.resumeFromHibernate') : t('session.startContainer') }}</TooltipContent>
              </Tooltip>

              <Button
                variant="ghost"
                size="sm"
                class="size-7 p-0 text-muted-foreground hover:text-destructive"
                :title="t('session.deleteSession')"
                :aria-label="t('session.deleteSession')"
                @click.stop="deleteDialogOpen[s.id] = true"
              >
                <Trash2 class="size-3.5" />
              </Button>
              <SessionDeleteDialog
                :open="!!deleteDialogOpen[s.id]"
                :session-id="s.id"
                :session-name="s.name"
                :deleting="!!deleting[s.id]"
                :active-lease="s.activeLease"
                @update:open="v => { if (v || !deleting[s.id]) deleteDialogOpen[s.id] = v }"
                @confirm="options => onDeleteSession(s.id, options)"
              />
            </div>
          </div>
        </Card>
      </div>

      <div v-else-if="!sessions.loading" class="py-32 flex flex-col items-center justify-center text-center">
        <Monitor class="size-12 mb-4 text-muted-foreground opacity-20" :stroke-width="1" />
        <h3 class="text-lg font-medium mb-2">{{ t('dashboard.empty') }}</h3>
        <p v-if="hasReadyImages" class="text-sm text-muted-foreground max-w-sm mb-6">{{ t('dashboard.emptyHint') }}</p>
        <p v-else class="text-sm text-muted-foreground max-w-sm mb-6">
          <span>{{ t('dashboard.noImageEmptyPrefix') }}</span>
          <RouterLink
            :to="browserImagesSettingsPath"
            class="inline-flex items-center gap-0.5 rounded-sm font-medium text-primary underline underline-offset-4 outline-none transition-colors hover:text-primary/80 focus-visible:ring-2 focus-visible:ring-ring/60"
          >
            {{ t('dashboard.configureImages') }}
            <ArrowUpRight class="size-3.5" aria-hidden="true" />
          </RouterLink>
          <span>{{ t('dashboard.noImageEmptySuffix') }}</span>
        </p>
        <Tooltip v-if="hasReadyImages">
          <TooltipTrigger as-child>
            <Button :disabled="creating" class="gap-2" @click="openCreateDialog">
              <Loader2 v-if="creating" class="size-4 animate-spin" />
              <Plus v-else class="size-4" />
              {{ creating ? t('session.creating') : t('dashboard.create') }}
              <kbd v-if="!creating" data-slot="kbd">{{ shortcutLabel }}</kbd>
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            {{ t('dashboard.createHint') }}
            <kbd v-if="!creating" data-slot="kbd">{{ shortcutLabel }}</kbd>
          </TooltipContent>
        </Tooltip>
        <Tooltip v-else>
          <TooltipTrigger as-child>
            <span class="inline-flex">
              <Button disabled class="gap-2">
                <Plus class="size-4" />
                {{ t('dashboard.create') }}
              </Button>
            </span>
          </TooltipTrigger>
          <TooltipContent class="gap-2">
            <span>{{ t('dashboard.noImageHint') }}</span>
            <RouterLink
              :to="browserImagesSettingsPath"
              class="inline-flex items-center gap-0.5 rounded-sm font-medium underline underline-offset-2 outline-none transition-opacity hover:opacity-80 focus-visible:ring-2 focus-visible:ring-background/60"
            >
              {{ t('dashboard.configureImages') }}
              <ArrowUpRight class="size-3" aria-hidden="true" />
            </RouterLink>
          </TooltipContent>
        </Tooltip>
      </div>
    </div>

    <Dialog :open="createDialogOpen" @update:open="createDialogOpen = $event">
      <DialogContent class="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{{ t('dashboard.create') }}</DialogTitle>
        </DialogHeader>
        <form class="space-y-4" @submit.prevent="handleCreateSession(createName, createVersion, createNetworkEgressId)">
          <div class="space-y-2">
            <Label for="create-session-name">{{ t('session.name') }}</Label>
            <Input
              id="create-session-name"
              v-model="createName"
              :placeholder="t('session.defaultName')"
              :disabled="creating"
              autofocus
            />
          </div>
          <div class="space-y-2">
            <Label for="create-session-version">{{ t('browserImages.version') }}</Label>
            <Select v-model="createVersion" :disabled="creating">
              <SelectTrigger id="create-session-version">
                <SelectValue :placeholder="t('browserImages.version')" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem
                  v-for="img in readyImages"
                  :key="img.id"
                  :value="img.chromeVersion || String(img.chromeMajor)"
                >
                  Chrome {{ img.chromeMajor }}
                  <span v-if="img.chromeVersion" class="text-xs text-muted-foreground">({{ img.chromeVersion }})</span>
                </SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div class="space-y-2">
            <Label for="create-session-network">{{ t('networkEgress.sessionNetwork') }}</Label>
            <Select v-model="createNetworkEgressId" :disabled="creating">
              <SelectTrigger id="create-session-network">
                <SelectValue :placeholder="t('networkEgress.direct')" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem
                  v-for="profile in egressState.profiles"
                  :key="profile.id || DIRECT_EGRESS_VALUE"
                  :value="profile.id || DIRECT_EGRESS_VALUE"
                  :disabled="profile.status === 'disabled' || profile.status === 'unhealthy' || profile.status === 'unsupported'"
                >
                  {{ profile.name }}
                  <span v-if="profile.type !== 'direct'" class="text-xs text-muted-foreground">({{ t(`networkEgress.type.${profile.type}`, profile.type) }})</span>
                </SelectItem>
              </SelectContent>
            </Select>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" :disabled="creating" @click="createDialogOpen = false">
              {{ t('session.cancel') }}
            </Button>
            <Button type="submit" :disabled="creating || !createVersion">
              <Loader2 v-if="creating" class="size-4 animate-spin" />
              {{ creating ? t('session.creating') : t('dashboard.create') }}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  </div>
</template>
