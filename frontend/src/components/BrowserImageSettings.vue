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
import { Trash2, Search, RefreshCw, Loader2, Check, AlertTriangle } from 'lucide-vue-next'

const { t } = useI18n()
const { user } = useAuth()
const isAdmin = computed(() => user.value?.role === 'superadmin' || user.value?.role === 'admin')

interface BrowserImage {
  id: string
  chromeMajor: number
  chromeVersion: string
  baseImage: string
  imageTag: string
  status: string
  buildLog: string
  createdAt: string | null
  sessionCount: number
}

interface AvailableVersion {
  tag: string
  major: number
}

const images = ref<BrowserImage[]>([])
const loading = ref(false)
const showBuildDialog = ref(false)
const buildVersion = ref('')
const building = ref(false)
const deleteTarget = ref<BrowserImage | null>(null)
const imageDeleting = ref(false)
let pollTimer: ReturnType<typeof setInterval> | null = null

const availableVersions = ref<AvailableVersion[]>([])
const versionsLoading = ref(false)
const versionsError = ref(false)
const searchQuery = ref('')

const builtMajors = computed(() => {
  const map = new Map<number, string>()
  for (const img of images.value) {
    const existing = map.get(img.chromeMajor)
    if (!existing || img.status === 'ready') {
      map.set(img.chromeMajor, img.status)
    }
  }
  return map
})

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
    fetchAvailableVersions()
  } else {
    buildVersion.value = ''
    searchQuery.value = ''
    versionsError.value = false
  }
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

async function handleBuild() {
  if (!buildVersion.value.trim()) return
  building.value = true
  try {
    const res = await api('/api/browser-images/build', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chromeVersion: buildVersion.value.trim() }),
    })
    if (!res.ok) {
      const data = await res.json().catch(() => null)
      toast.error(data?.detail || 'Build failed')
      return
    }
    toast.success(t('browserImages.buildStarted'))
    showBuildDialog.value = false
    await fetchImages()
    startPolling()
  } catch (e: any) {
    toast.error(e?.message || 'Build failed')
  } finally {
    building.value = false
  }
}

async function handleDelete(img: BrowserImage) {
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
      <Table v-else>
        <TableHeader>
          <TableRow>
            <TableHead>{{ t('browserImages.version') }}</TableHead>
            <TableHead>{{ t('browserImages.status') }}</TableHead>
            <TableHead>{{ t('browserImages.baseImage') }}</TableHead>
            <TableHead>{{ t('browserImages.createdAt') }}</TableHead>
            <TableHead v-if="isAdmin">{{ t('browserImages.actions') }}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow v-for="img in images" :key="img.id">
            <TableCell class="font-medium">
              <span>Chrome {{ img.chromeMajor }}</span>
              <span v-if="img.chromeVersion" class="text-xs text-muted-foreground ml-1">({{ img.chromeVersion }})</span>
              <Badge v-if="img.sessionCount > 0" variant="outline" class="ml-2 text-xs">
                {{ t('browserImages.sessionCount', { n: img.sessionCount }) }}
              </Badge>
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
            </TableCell>
            <TableCell class="text-xs text-muted-foreground font-mono">{{ img.baseImage }}</TableCell>
            <TableCell class="text-xs text-muted-foreground">
              {{ img.createdAt ? new Date(img.createdAt).toLocaleString() : '-' }}
            </TableCell>
            <TableCell v-if="isAdmin">
              <TooltipProvider v-if="img.sessionCount > 0">
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
    <DialogContent class="sm:max-w-md">
      <DialogHeader class="flex flex-row items-center justify-between pr-6">
        <DialogTitle>{{ t('browserImages.buildNew') }}</DialogTitle>
        <Button
          v-if="!versionsError"
          variant="ghost"
          size="icon"
          class="size-7 shrink-0"
          :disabled="versionsLoading"
          @click="fetchAvailableVersions(true)"
        >
          <RefreshCw class="size-3.5" :class="versionsLoading && 'animate-spin'" />
        </Button>
      </DialogHeader>

      <!-- Fallback: manual input on API failure -->
      <div v-if="versionsError" class="space-y-3 py-2">
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
      <div v-else class="space-y-3 py-2">
        <div class="relative">
          <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
          <Input
            v-model="searchQuery"
            class="pl-8"
            :placeholder="t('browserImages.searchVersion')"
          />
        </div>

        <!-- Loading -->
        <div v-if="versionsLoading && availableVersions.length === 0" class="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground">
          <Loader2 class="size-4 animate-spin" />
          <span>{{ t('browserImages.loadingVersions') }}</span>
        </div>

        <!-- Version list -->
        <div v-else class="max-h-60 overflow-y-auto rounded-md border">
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

      <DialogFooter>
        <Button variant="outline" @click="showBuildDialog = false">{{ t('common.cancel', 'Cancel') }}</Button>
        <Button :disabled="!buildVersion.trim() || building" @click="handleBuild">
          {{ building ? t('browserImages.building') : t('browserImages.buildNew') }}
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
          Chrome {{ deleteTarget.chromeMajor }} ({{ deleteTarget.imageTag }})
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
