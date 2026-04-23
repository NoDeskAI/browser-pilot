<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '@/lib/api'
import { toast } from 'vue-sonner'
import { useAuth } from '@/composables/useAuth'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'

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
}

const images = ref<BrowserImage[]>([])
const loading = ref(false)
const showBuildDialog = ref(false)
const buildVersion = ref('')
const building = ref(false)
const deleteTarget = ref<BrowserImage | null>(null)
let pollTimer: ReturnType<typeof setInterval> | null = null

async function fetchImages() {
  loading.value = true
  try {
    const res = await api('/api/browser-images')
    const data = await res.json()
    images.value = data.images || []
  } catch {
    // ignore
  } finally {
    loading.value = false
  }
}

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
    await api('/api/browser-images/build', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chromeVersion: buildVersion.value.trim() }),
    })
    toast.success(t('browserImages.buildStarted'))
    showBuildDialog.value = false
    buildVersion.value = ''
    await fetchImages()
    startPolling()
  } catch (e: any) {
    toast.error(e?.message || 'Build failed')
  } finally {
    building.value = false
  }
}

async function handleDelete(img: BrowserImage) {
  try {
    await api(`/api/browser-images/${img.id}`, { method: 'DELETE' })
    toast.success(t('browserImages.deleted'))
    await fetchImages()
  } catch {
    toast.error('Delete failed')
  }
  deleteTarget.value = null
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
</script>

<template>
  <Card>
    <CardHeader class="flex flex-row items-center justify-between">
      <div>
        <CardTitle>{{ t('browserImages.title') }}</CardTitle>
        <p class="text-sm text-muted-foreground mt-1">{{ t('browserImages.description') }}</p>
      </div>
      <Button v-if="isAdmin" size="sm" @click="showBuildDialog = true">
        {{ t('browserImages.buildNew') }}
      </Button>
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
              Chrome {{ img.chromeMajor }}
              <span v-if="img.chromeVersion" class="text-xs text-muted-foreground ml-1">({{ img.chromeVersion }})</span>
            </TableCell>
            <TableCell>
              <Badge :variant="statusVariant(img.status) as any">{{ statusLabel(img.status) }}</Badge>
            </TableCell>
            <TableCell class="text-xs text-muted-foreground font-mono">{{ img.baseImage }}</TableCell>
            <TableCell class="text-xs text-muted-foreground">
              {{ img.createdAt ? new Date(img.createdAt).toLocaleString() : '-' }}
            </TableCell>
            <TableCell v-if="isAdmin">
              <Button variant="ghost" size="sm" class="text-destructive" @click="deleteTarget = img">
                {{ t('common.delete', 'Delete') }}
              </Button>
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </CardContent>
  </Card>

  <!-- Build Dialog -->
  <Dialog v-model:open="showBuildDialog">
    <DialogContent>
      <DialogHeader>
        <DialogTitle>{{ t('browserImages.buildNew') }}</DialogTitle>
      </DialogHeader>
      <div class="space-y-4 py-4">
        <div class="space-y-2">
          <Label>{{ t('browserImages.version') }}</Label>
          <Input
            v-model="buildVersion"
            :placeholder="t('browserImages.versionPlaceholder')"
            @keydown.enter="handleBuild"
          />
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
  <AlertDialog :open="!!deleteTarget" @update:open="v => { if (!v) deleteTarget = null }">
    <AlertDialogContent>
      <AlertDialogHeader>
        <AlertDialogTitle>{{ t('browserImages.deleteConfirm') }}</AlertDialogTitle>
        <AlertDialogDescription v-if="deleteTarget">
          Chrome {{ deleteTarget.chromeMajor }} ({{ deleteTarget.imageTag }})
        </AlertDialogDescription>
      </AlertDialogHeader>
      <AlertDialogFooter>
        <AlertDialogCancel @click="deleteTarget = null">{{ t('common.cancel', 'Cancel') }}</AlertDialogCancel>
        <AlertDialogAction @click="deleteTarget && handleDelete(deleteTarget)">{{ t('common.delete', 'Delete') }}</AlertDialogAction>
      </AlertDialogFooter>
    </AlertDialogContent>
  </AlertDialog>
</template>
