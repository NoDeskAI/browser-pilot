<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '@/lib/api'
import { toast } from 'vue-sonner'
import { useAuth } from '@/composables/useAuth'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Tooltip, TooltipContent, TooltipTrigger, TooltipProvider } from '@/components/ui/tooltip'
import { Trash2, Search, RefreshCw, Loader2, Check, AlertTriangle, Package, ShieldCheck } from 'lucide-vue-next'

const { t } = useI18n()
const { user } = useAuth()
const isAdmin = computed(() => user.value?.role === 'superadmin' || user.value?.role === 'admin')

type ImageRuntime = 'standard_chrome' | 'cloak_chromium'

interface ImageRow {
  id: string
  runtime: ImageRuntime
  name?: string
  chromeMajor: number | null
  chromeVersion: string | null
  baseImage: string
  imageTag: string
  status: string
  buildLog: string
  createdAt: string | null
  sessionCount: number
  buildProgress?: BuildProgress
}

interface BuildProgress {
  stage: string
  progress: number
  elapsedSeconds: number
  startedAt: string | null
  updatedAt: string | null
  log: string
  manualCommand: string
  indeterminate: boolean
}

interface AvailableVersion {
  tag: string
  major: number
}

const images = ref<ImageRow[]>([])
const loading = ref(false)
const showBuildDialog = ref(false)
const buildVersion = ref('')
const buildRuntime = ref<ImageRuntime>('standard_chrome')
const building = ref(false)
const deleteTarget = ref<ImageRow | null>(null)
const imageDeleting = ref(false)
let pollTimer: ReturnType<typeof setInterval> | null = null

const availableVersions = ref<AvailableVersion[]>([])
const versionsLoading = ref(false)
const versionsError = ref(false)
const searchQuery = ref('')

const builtMajors = computed(() => {
  const map = new Map<number, string>()
  for (const img of images.value) {
    if (img.runtime !== 'standard_chrome' || img.chromeMajor == null) continue
    const existing = map.get(img.chromeMajor)
    if (!existing || img.status === 'ready') {
      map.set(img.chromeMajor, img.status)
    }
  }
  return map
})

const standardImageCount = computed(() => images.value.filter(img => img.runtime === 'standard_chrome').length)
const cloakImage = computed(() => images.value.find(img => img.runtime === 'cloak_chromium') || null)
const cloakStatus = computed(() => cloakImage.value?.status || 'missing')
const cloakImageTag = computed(() => cloakImage.value?.imageTag || 'browser-pilot-cloak:latest')
const cloakBaseImage = computed(() => cloakImage.value?.baseImage || 'services/cloak-chromium-runtime')

const filteredVersions = computed(() => {
  const q = searchQuery.value.trim()
  if (!q) return availableVersions.value
  return availableVersions.value.filter(v => String(v.major).includes(q))
})

async function fetchImages() {
  loading.value = true
  try {
    const res = await api('/api/browser-images')
    const data = await res.json()
    images.value = data.images || []
    if (images.value.some(i => i.status === 'building' || i.status === 'pending')) {
      startPolling()
    }
  } catch {
    // ignore
  } finally {
    loading.value = false
  }
}

async function fetchAvailableVersions(refresh = false) {
  versionsLoading.value = true
  versionsError.value = false
  try {
    const url = refresh
      ? '/api/browser-images/available-versions?refresh=true'
      : '/api/browser-images/available-versions'
    const res = await api(url)
    const data = await res.json()
    availableVersions.value = data.versions || []
  } catch {
    versionsError.value = true
  } finally {
    versionsLoading.value = false
  }
}

watch(showBuildDialog, (open) => {
  if (open) {
    if (buildRuntime.value === 'standard_chrome') {
      fetchAvailableVersions()
    }
  } else {
    buildRuntime.value = 'standard_chrome'
    buildVersion.value = ''
    searchQuery.value = ''
    versionsError.value = false
  }
})

watch(buildRuntime, (runtime) => {
  buildVersion.value = ''
  searchQuery.value = ''
  versionsError.value = false
  if (showBuildDialog.value && runtime === 'standard_chrome' && availableVersions.value.length === 0) {
    fetchAvailableVersions()
  }
})

const canSubmitBuild = computed(() => {
  if (building.value) return false
  if (buildRuntime.value === 'standard_chrome') return !!buildVersion.value.trim()
  return !cloakImage.value || canBuildRuntimeImage(cloakImage.value)
})

const buildSubmitLabel = computed(() => {
  if (building.value) return t('browserImages.building')
  if (buildRuntime.value === 'cloak_chromium') {
    if (cloakStatus.value === 'ready') return t('browserImages.ready')
    if (cloakStatus.value === 'building' || cloakStatus.value === 'pending') return t('browserImages.isBuilding')
  }
  return t('browserImages.buildNew')
})

function startPolling() {
  stopPolling()
  pollTimer = setInterval(async () => {
    const hasBuilding = images.value.some(i => i.status === 'building' || i.status === 'pending')
    if (!hasBuilding) {
      stopPolling()
      return
    }
    await fetchImages()
  }, 3000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function requestBuild(runtime: ImageRuntime, chromeVersion = '') {
  if (runtime === 'standard_chrome' && !chromeVersion.trim()) return
  building.value = true
  try {
    const body = runtime === 'cloak_chromium'
      ? { runtime }
      : { runtime, chromeVersion: chromeVersion.trim() }
    const res = await api('/api/browser-images/build', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) {
      const data = await res.json().catch(() => null)
      toast.error(data?.detail || 'Build failed')
      return
    }
    toast.success(runtime === 'cloak_chromium' ? t('browserImages.cloakBuildStarted') : t('browserImages.buildStarted'))
    showBuildDialog.value = false
    await fetchImages()
    startPolling()
  } catch (e: any) {
    toast.error(e?.message || 'Build failed')
  } finally {
    building.value = false
  }
}

async function handleBuild() {
  await requestBuild(buildRuntime.value, buildVersion.value)
}

async function handleBuildCloak() {
  await requestBuild('cloak_chromium')
}

async function handleDelete(img: ImageRow) {
  imageDeleting.value = true
  try {
    const res = await api(`/api/browser-images/${img.id}`, { method: 'DELETE' })
    if (res.status === 409) {
      const data = await res.json().catch(() => null)
      toast.error(data?.detail || t('browserImages.inUseCannotDelete'))
      return
    }
    if (!res.ok) {
      const data = await res.json().catch(() => null)
      toast.error(data?.detail || 'Delete failed')
      return
    }
    toast.success(t('browserImages.deleted'))
    await fetchImages()
  } catch {
    toast.error('Delete failed')
  } finally {
    imageDeleting.value = false
    deleteTarget.value = null
  }
}

function statusVariant(status: string) {
  if (status === 'ready') return 'default'
  if (status === 'building' || status === 'pending') return 'secondary'
  return 'destructive'
}

function statusLabel(status: string) {
  const key = `browserImages.${status}` as any
  return t(key, status)
}

function stageLabel(stage?: string) {
  const key = `browserImages.stage.${stage || 'unknown'}` as any
  return t(key, stage || 'unknown')
}

function formatElapsed(seconds?: number) {
  const total = Math.max(0, Number(seconds || 0))
  const mins = Math.floor(total / 60)
  const secs = total % 60
  if (mins <= 0) return `${secs}s`
  return `${mins}m ${String(secs).padStart(2, '0')}s`
}

function progressValue(progress?: BuildProgress) {
  return Math.max(0, Math.min(100, Math.round(progress?.progress || 0)))
}

function imageLabel(img: ImageRow) {
  if (img.runtime === 'cloak_chromium') return img.name || t('browserImages.cloakRuntime')
  return `Chrome ${img.chromeMajor ?? ''}`.trim()
}

function runtimeLabel(runtime: ImageRuntime) {
  return runtime === 'cloak_chromium' ? t('browserImages.buildRuntimeCloak') : t('browserImages.buildRuntimeNative')
}

function showBuildProgress(img: ImageRow) {
  return !!img.buildProgress && (
    img.status === 'building' ||
    img.status === 'pending' ||
    (img.runtime === 'cloak_chromium' && img.status === 'failed')
  )
}

function canBuildRuntimeImage(img: ImageRow) {
  return img.runtime === 'cloak_chromium' && !['ready', 'building', 'pending'].includes(img.status)
}

function buildRuntimeActionLabel(img: ImageRow) {
  if (img.status === 'ready') return t('browserImages.ready')
  if (img.status === 'failed') return t('browserImages.rebuildCloak')
  return t('browserImages.buildCloak')
}

function buildRuntimeOptionClass(runtime: ImageRuntime) {
  const selected = buildRuntime.value === runtime
  return [
    'min-h-[92px] rounded-lg border p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60',
    selected ? 'border-primary bg-primary/5 ring-1 ring-primary/25' : 'border-border hover:bg-accent/50',
  ]
}

onMounted(() => {
  fetchImages()
})
onUnmounted(stopPolling)
</script>

<template>
  <Card>
    <CardHeader class="flex flex-row items-center justify-between">
      <div>
        <CardTitle>{{ t('browserImages.title') }}</CardTitle>
        <p class="text-sm text-muted-foreground mt-1">{{ t('browserImages.description') }}</p>
      </div>
      <div class="flex items-center gap-2">
        <Button variant="outline" size="icon" class="size-8" :disabled="loading" @click="fetchImages">
          <RefreshCw class="size-3.5" :class="loading && 'animate-spin'" />
        </Button>
        <Button v-if="isAdmin" size="sm" @click="showBuildDialog = true">
          {{ t('browserImages.buildNew') }}
        </Button>
      </div>
    </CardHeader>
    <CardContent>
      <div v-if="images.length === 0 && !loading" class="text-center text-muted-foreground py-8">
        {{ t('browserImages.noImages') }}
      </div>
      <Table v-else class="min-w-[920px]">
        <TableHeader>
          <TableRow>
            <TableHead class="min-w-[180px]">{{ t('browserImages.image') }}</TableHead>
            <TableHead class="w-[120px]">{{ t('browserImages.runtimeType') }}</TableHead>
            <TableHead class="w-[110px]">{{ t('browserImages.sessionCountHeader') }}</TableHead>
            <TableHead class="w-[150px]">{{ t('browserImages.status') }}</TableHead>
            <TableHead class="min-w-[240px]">{{ t('browserImages.baseImage') }}</TableHead>
            <TableHead class="w-[150px]">{{ t('browserImages.createdAt') }}</TableHead>
            <TableHead v-if="isAdmin" class="w-[110px]">{{ t('browserImages.actions') }}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow v-for="img in images" :key="img.id">
            <TableCell class="font-medium">
              <div>{{ imageLabel(img) }}</div>
              <div v-if="img.runtime === 'standard_chrome' && img.chromeVersion" class="mt-1 text-xs text-muted-foreground">
                {{ img.chromeVersion }}
              </div>
              <div v-else-if="img.runtime === 'cloak_chromium'" class="mt-1 truncate font-mono text-xs text-muted-foreground">
                {{ img.imageTag }}
              </div>
            </TableCell>
            <TableCell class="text-sm text-muted-foreground">
              {{ runtimeLabel(img.runtime) }}
            </TableCell>
            <TableCell class="text-sm text-muted-foreground">
              {{ t('browserImages.sessionCount', { n: img.sessionCount }) }}
            </TableCell>
            <TableCell>
              <TooltipProvider v-if="img.status === 'failed' && img.buildLog">
                <Tooltip>
                  <TooltipTrigger as-child>
                    <Badge :variant="statusVariant(img.status) as any" class="cursor-help">{{ statusLabel(img.status) }}</Badge>
                  </TooltipTrigger>
                  <TooltipContent side="bottom" class="max-w-xs text-xs whitespace-pre-wrap">{{ img.buildLog }}</TooltipContent>
                </Tooltip>
              </TooltipProvider>
              <Badge v-else :variant="statusVariant(img.status) as any">{{ statusLabel(img.status) }}</Badge>
              <div
                v-if="showBuildProgress(img)"
                class="mt-2 w-48 max-w-full space-y-1"
              >
                <div class="flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
                  <span>{{ stageLabel(img.buildProgress?.stage) }}</span>
                  <span>
                    {{ progressValue(img.buildProgress) }}%
                    <template v-if="img.runtime === 'cloak_chromium'">
                      · {{ formatElapsed(img.buildProgress?.elapsedSeconds) }}
                    </template>
                  </span>
                </div>
                <div class="h-1.5 overflow-hidden rounded-full bg-muted">
                  <div
                    class="h-full rounded-full bg-primary transition-all"
                    :class="img.buildProgress?.indeterminate && 'animate-pulse'"
                    :style="{ width: `${progressValue(img.buildProgress)}%` }"
                  />
                </div>
                <div v-if="img.status === 'failed' && img.buildProgress?.manualCommand" class="text-[11px] text-muted-foreground">
                  {{ t('browserImages.manualBuildHint') }}
                  <code class="rounded bg-muted px-1 py-0.5 font-mono">{{ img.buildProgress?.manualCommand }}</code>
                </div>
              </div>
            </TableCell>
            <TableCell class="text-xs text-muted-foreground font-mono">{{ img.baseImage }}</TableCell>
            <TableCell class="text-xs text-muted-foreground">
              {{ img.createdAt ? new Date(img.createdAt).toLocaleString() : '-' }}
            </TableCell>
            <TableCell v-if="isAdmin">
              <Button
                v-if="img.runtime === 'cloak_chromium' && canBuildRuntimeImage(img)"
                size="sm"
                :disabled="building"
                @click="handleBuildCloak"
              >
                <Loader2 v-if="building" class="size-4 animate-spin" />
                {{ buildRuntimeActionLabel(img) }}
              </Button>
              <Button
                v-else-if="img.runtime === 'cloak_chromium' && (img.status === 'building' || img.status === 'pending')"
                size="sm"
                disabled
              >
                <Loader2 class="size-4 animate-spin" />
                {{ statusLabel(img.status) }}
              </Button>
              <TooltipProvider v-else-if="img.sessionCount > 0">
                <Tooltip>
                  <TooltipTrigger as-child>
                    <span class="inline-flex">
                      <Button variant="ghost" size="sm" class="text-destructive" disabled>
                        <Trash2 class="size-3.5" />
                      </Button>
                    </span>
                  </TooltipTrigger>
                  <TooltipContent>{{ t('browserImages.inUseCannotDelete') }}</TooltipContent>
                </Tooltip>
              </TooltipProvider>
              <Button v-else variant="ghost" size="sm" class="text-destructive" @click="deleteTarget = img">
                <Trash2 class="size-3.5" />
              </Button>
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </CardContent>
  </Card>

  <!-- Build Dialog -->
  <Dialog v-model:open="showBuildDialog">
    <DialogContent class="sm:max-w-lg">
      <DialogHeader>
        <DialogTitle>{{ t('browserImages.buildNew') }}</DialogTitle>
      </DialogHeader>

      <div class="space-y-2 py-2">
        <div class="text-sm font-medium">{{ t('browserImages.buildRuntimeLabel') }}</div>
        <div class="grid gap-3 sm:grid-cols-2">
          <button
            type="button"
            :class="buildRuntimeOptionClass('standard_chrome')"
            :disabled="building"
            :aria-pressed="buildRuntime === 'standard_chrome'"
            @click="buildRuntime = 'standard_chrome'"
          >
            <div class="flex items-start gap-3">
              <div class="rounded-md border bg-background p-1.5">
                <Package class="size-4" :class="buildRuntime === 'standard_chrome' ? 'text-primary' : 'text-muted-foreground'" />
              </div>
              <div class="min-w-0 flex-1">
                <div class="flex items-center justify-between gap-2">
                  <span class="font-medium">{{ t('browserImages.buildRuntimeNative') }}</span>
                  <Check v-if="buildRuntime === 'standard_chrome'" class="size-4 text-primary" />
                </div>
                <div class="mt-1 text-xs text-muted-foreground">
                  {{ t('browserImages.nativeImageMeta', { n: standardImageCount }) }}
                </div>
              </div>
            </div>
          </button>
          <button
            type="button"
            :class="buildRuntimeOptionClass('cloak_chromium')"
            :disabled="building"
            :aria-pressed="buildRuntime === 'cloak_chromium'"
            @click="buildRuntime = 'cloak_chromium'"
          >
            <div class="flex items-start gap-3">
              <div class="rounded-md border bg-background p-1.5">
                <ShieldCheck class="size-4" :class="buildRuntime === 'cloak_chromium' ? 'text-primary' : 'text-muted-foreground'" />
              </div>
              <div class="min-w-0 flex-1">
                <div class="flex items-center justify-between gap-2">
                  <span class="font-medium">{{ t('browserImages.buildRuntimeCloak') }}</span>
                  <Check v-if="buildRuntime === 'cloak_chromium'" class="size-4 text-primary" />
                </div>
                <div class="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                  <Badge :variant="statusVariant(cloakStatus) as any" class="text-[11px]">{{ statusLabel(cloakStatus) }}</Badge>
                  <span class="truncate font-mono">{{ cloakImageTag }}</span>
                </div>
              </div>
            </div>
          </button>
        </div>
      </div>

      <div class="h-[304px] overflow-visible">
        <!-- Fallback: manual input on API failure -->
        <div v-if="buildRuntime === 'standard_chrome' && versionsError" class="flex h-full flex-col gap-3 p-1">
          <div class="flex items-center gap-2 text-sm text-muted-foreground">
            <AlertTriangle class="size-4 text-yellow-500 shrink-0" />
            <span>{{ t('browserImages.loadError') }}</span>
          </div>
          <Input
            v-model="buildVersion"
            :placeholder="t('browserImages.versionPlaceholder')"
            @keydown.enter="handleBuild"
          />
        </div>

        <!-- Normal: version picker -->
        <div v-else-if="buildRuntime === 'standard_chrome'" class="flex h-full min-h-0 flex-col gap-3 p-1">
          <div class="flex items-center gap-2">
            <div class="relative min-w-0 flex-1">
              <Search class="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
              <Input
                v-model="searchQuery"
                class="pl-8 hover:border-ring hover:ring-3 hover:ring-ring/20"
                :placeholder="t('browserImages.searchVersion')"
              />
            </div>
            <Button
              v-if="!versionsError"
              variant="outline"
              size="icon"
              class="size-8 shrink-0"
              :disabled="versionsLoading"
              :aria-label="t('browserImages.refreshVersions')"
              @click="fetchAvailableVersions(true)"
            >
              <RefreshCw class="size-3.5" :class="versionsLoading && 'animate-spin'" />
            </Button>
          </div>

          <!-- Loading -->
          <div v-if="versionsLoading && availableVersions.length === 0" class="flex min-h-0 flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
            <Loader2 class="size-4 animate-spin" />
            <span>{{ t('browserImages.loadingVersions') }}</span>
          </div>

          <!-- Version list -->
          <div v-else class="min-h-0 flex-1 overflow-y-auto rounded-md border">
            <div v-if="filteredVersions.length === 0" class="py-6 text-center text-sm text-muted-foreground">
              {{ t('browserImages.noMatch') }}
            </div>
            <button
              v-for="ver in filteredVersions"
              :key="ver.major"
              type="button"
              class="flex w-full items-center justify-between px-3 py-2 text-sm transition-colors hover:bg-accent"
              :class="buildVersion === ver.tag && 'bg-accent'"
              @click="buildVersion = ver.tag"
            >
              <span class="flex items-center gap-2">
                <Check v-if="buildVersion === ver.tag" class="size-3.5 text-primary" />
                <span v-else class="size-3.5" />
                Chrome {{ ver.major }}
              </span>
              <Badge v-if="builtMajors.get(ver.major) === 'ready'" variant="secondary" class="text-xs">
                {{ t('browserImages.alreadyBuilt') }}
              </Badge>
              <Badge v-else-if="builtMajors.get(ver.major) === 'building' || builtMajors.get(ver.major) === 'pending'" variant="outline" class="text-xs">
                {{ t('browserImages.isBuilding') }}
              </Badge>
            </button>
          </div>
        </div>

        <div v-else class="h-full overflow-y-auto rounded-lg border bg-muted/30 p-3">
          <div class="space-y-3">
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0">
                <div class="text-xs text-muted-foreground">{{ t('browserImages.imageTag') }}</div>
                <div class="mt-1 truncate font-mono text-xs">{{ cloakImageTag }}</div>
              </div>
              <Badge :variant="statusVariant(cloakStatus) as any" class="shrink-0">{{ statusLabel(cloakStatus) }}</Badge>
            </div>
            <div class="grid gap-2 sm:grid-cols-2">
              <div class="rounded-md border bg-background/60 px-3 py-2">
                <div class="text-xs text-muted-foreground">{{ t('browserImages.baseImage') }}</div>
                <div class="mt-1 truncate font-mono text-xs">{{ cloakBaseImage }}</div>
              </div>
              <div class="rounded-md border bg-background/60 px-3 py-2">
                <div class="text-xs text-muted-foreground">{{ t('browserImages.sessions') }}</div>
                <div class="mt-1 text-xs">{{ t('browserImages.sessionCount', { n: cloakImage?.sessionCount || 0 }) }}</div>
              </div>
            </div>
            <div
              v-if="cloakImage && showBuildProgress(cloakImage)"
              class="space-y-1.5"
            >
              <div class="flex items-center justify-between gap-3 text-xs text-muted-foreground">
                <span>{{ stageLabel(cloakImage.buildProgress?.stage) }}</span>
                <span>
                  {{ progressValue(cloakImage.buildProgress) }}%
                  · {{ formatElapsed(cloakImage.buildProgress?.elapsedSeconds) }}
                </span>
              </div>
              <div class="h-2 overflow-hidden rounded-full bg-muted">
                <div
                  class="h-full rounded-full bg-primary transition-all"
                  :class="cloakImage.buildProgress?.indeterminate && 'animate-pulse'"
                  :style="{ width: `${progressValue(cloakImage.buildProgress)}%` }"
                />
              </div>
            </div>
          </div>
        </div>
      </div>

      <DialogFooter>
        <Button variant="outline" @click="showBuildDialog = false">{{ t('common.cancel', 'Cancel') }}</Button>
        <Button :disabled="!canSubmitBuild" @click="handleBuild">
          {{ buildSubmitLabel }}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>

  <!-- Delete Confirm -->
  <AlertDialog :open="!!deleteTarget" @update:open="v => { if (!v && !imageDeleting) deleteTarget = null }">
    <AlertDialogContent>
      <AlertDialogHeader>
        <AlertDialogTitle>{{ t('browserImages.deleteConfirm') }}</AlertDialogTitle>
        <AlertDialogDescription v-if="deleteTarget">
          {{ imageLabel(deleteTarget) }} ({{ deleteTarget.imageTag }})
        </AlertDialogDescription>
      </AlertDialogHeader>
      <AlertDialogFooter>
        <AlertDialogCancel :disabled="imageDeleting">{{ t('common.cancel', 'Cancel') }}</AlertDialogCancel>
        <Button variant="destructive" :disabled="imageDeleting" @click="deleteTarget && handleDelete(deleteTarget)">
          <Loader2 v-if="imageDeleting" class="size-4 animate-spin" />
          {{ imageDeleting ? t('browserImages.deleting') : t('common.delete', 'Delete') }}
        </Button>
      </AlertDialogFooter>
    </AlertDialogContent>
  </AlertDialog>
</template>
