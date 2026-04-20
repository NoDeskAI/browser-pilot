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
import { Switch } from '@/components/ui/switch'
import { Badge } from '@/components/ui/badge'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

const { t } = useI18n()
const { user } = useAuth()
const isAdmin = computed(() => user.value?.role === 'superadmin' || user.value?.role === 'admin')

interface PoolEntry {
  id: string
  groupName: string
  label: string
  data: Record<string, any>
  tags: string[]
  enabled: boolean
  sortOrder: number
}

type GroupName = 'platform' | 'gpu' | 'hardware' | 'screen'

const GROUPS: { key: GroupName; label: string }[] = [
  { key: 'platform', label: 'fingerprintPool.groupPlatform' },
  { key: 'gpu', label: 'fingerprintPool.groupGpu' },
  { key: 'hardware', label: 'fingerprintPool.groupHardware' },
  { key: 'screen', label: 'fingerprintPool.groupScreen' },
]

const loading = ref(true)
const pool = ref<Record<GroupName, PoolEntry[]>>({ platform: [], gpu: [], hardware: [], screen: [] })

async function fetchPool() {
  try {
    const res = await api('/api/fingerprint-pool')
    const data = await res.json()
    pool.value = data.pool
  } catch { /* ignore */ } finally {
    loading.value = false
  }
}

onMounted(fetchPool)

// --- toggle enabled ---
async function toggleEnabled(entry: PoolEntry) {
  const next = !entry.enabled
  try {
    const res = await api(`/api/fingerprint-pool/${entry.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: next }),
    })
    if (!res.ok) throw new Error()
    entry.enabled = next
  } catch {
    toast.error(t('fingerprintPool.saveFailed'))
  }
}

// --- delete ---
const deleteTarget = ref<PoolEntry | null>(null)
async function confirmDelete() {
  if (!deleteTarget.value) return
  await api(`/api/fingerprint-pool/${deleteTarget.value.id}`, { method: 'DELETE' })
  toast.success(t('fingerprintPool.deleted'))
  deleteTarget.value = null
  fetchPool()
}

// --- reset ---
const showResetConfirm = ref(false)
async function resetPool() {
  await api('/api/fingerprint-pool/reset', { method: 'POST' })
  toast.success(t('fingerprintPool.resetDone'))
  showResetConfirm.value = false
  fetchPool()
}

// --- add / edit dialog ---
const showDialog = ref(false)
const dialogMode = ref<'add' | 'edit'>('add')
const dialogGroup = ref<GroupName>('platform')
const editingId = ref('')

const formLabel = ref('')
const formTags = ref<string[]>([])
const formEnabled = ref(true)

const formPlatformNav = ref({ userAgent: '', platform: 'Win32', appVersion: '' })
const formClientHints = ref({ platform: 'Windows', platformVersion: '', architecture: 'x86', bitness: '64', mobile: false, wow64: false })
const formGpu = ref({ vendor: '', renderer: '' })
const formHardware = ref({ hardwareConcurrency: 8, deviceMemory: 16 })
const formScreen = ref({ colorDepth: 24, pixelDepth: 24, devicePixelRatio: 1 })

function openAdd(group: GroupName) {
  dialogMode.value = 'add'
  dialogGroup.value = group
  editingId.value = ''
  formLabel.value = ''
  formTags.value = group === 'hardware' ? [] : ['windows']
  formEnabled.value = true
  formPlatformNav.value = { userAgent: '', platform: 'Win32', appVersion: '' }
  formClientHints.value = { platform: 'Windows', platformVersion: '15.0.0', architecture: 'x86', bitness: '64', mobile: false, wow64: false }
  formGpu.value = { vendor: '', renderer: '' }
  formHardware.value = { hardwareConcurrency: 8, deviceMemory: 16 }
  formScreen.value = { colorDepth: 24, pixelDepth: 24, devicePixelRatio: 1 }
  showDialog.value = true
}

function openEdit(entry: PoolEntry) {
  dialogMode.value = 'edit'
  dialogGroup.value = entry.groupName as GroupName
  editingId.value = entry.id
  formLabel.value = entry.label
  formTags.value = [...entry.tags]
  formEnabled.value = entry.enabled

  if (entry.groupName === 'platform') {
    formPlatformNav.value = { ...entry.data.navigator }
    formClientHints.value = { ...entry.data.clientHints }
  } else if (entry.groupName === 'gpu') {
    formGpu.value = { vendor: entry.data.vendor || '', renderer: entry.data.renderer || '' }
  } else if (entry.groupName === 'hardware') {
    formHardware.value = { hardwareConcurrency: entry.data.hardwareConcurrency ?? 8, deviceMemory: entry.data.deviceMemory ?? 16 }
  } else if (entry.groupName === 'screen') {
    formScreen.value = { colorDepth: entry.data.colorDepth ?? 24, pixelDepth: entry.data.pixelDepth ?? 24, devicePixelRatio: entry.data.devicePixelRatio ?? 1 }
  }
  showDialog.value = true
}

function buildData(): Record<string, any> {
  switch (dialogGroup.value) {
    case 'platform':
      return { navigator: { ...formPlatformNav.value }, clientHints: { ...formClientHints.value } }
    case 'gpu':
      return { ...formGpu.value }
    case 'hardware':
      return { ...formHardware.value }
    case 'screen':
      return { ...formScreen.value }
  }
}

const saving = ref(false)
async function saveEntry() {
  saving.value = true
  try {
    const data = buildData()
    if (dialogMode.value === 'add') {
      const res = await api('/api/fingerprint-pool', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ groupName: dialogGroup.value, label: formLabel.value, data, tags: formTags.value, enabled: formEnabled.value }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => null)
        toast.error(err?.detail || t('fingerprintPool.saveFailed'))
        return
      }
    } else {
      const res = await api(`/api/fingerprint-pool/${editingId.value}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ label: formLabel.value, data, tags: formTags.value, enabled: formEnabled.value }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => null)
        toast.error(err?.detail || t('fingerprintPool.saveFailed'))
        return
      }
    }
    toast.success(t('fingerprintPool.saved'))
    showDialog.value = false
    fetchPool()
  } finally {
    saving.value = false
  }
}

function entrySummary(entry: PoolEntry): string {
  const d = entry.data
  switch (entry.groupName) {
    case 'platform':
      return d.clientHints?.platform || d.navigator?.platform || '-'
    case 'gpu':
      return (d.renderer || '').slice(0, 60) + ((d.renderer || '').length > 60 ? '...' : '')
    case 'hardware':
      return `${d.hardwareConcurrency}C / ${d.deviceMemory}GB`
    case 'screen':
      return `${d.colorDepth}bit / DPR ${d.devicePixelRatio}`
    default:
      return '-'
  }
}

function onPlatformPresetChange(val: any) {
  if (val === 'windows') {
    formPlatformNav.value.platform = 'Win32'
    formClientHints.value.platform = 'Windows'
    formClientHints.value.architecture = 'x86'
    formTags.value = ['windows']
  } else {
    formPlatformNav.value.platform = 'MacIntel'
    formClientHints.value.platform = 'macOS'
    formTags.value = ['macos']
  }
}
</script>

<template>
  <div class="space-y-6">
    <div v-if="loading" class="flex justify-center py-12">
      <div class="animate-spin h-6 w-6 border-2 border-primary border-t-transparent rounded-full" />
    </div>

    <template v-else>
      <Card v-for="group in GROUPS" :key="group.key">
        <CardHeader class="flex flex-row items-center justify-between space-y-0 pb-3">
          <CardTitle class="text-base font-medium">{{ t(group.label) }}</CardTitle>
          <Button v-if="isAdmin" size="sm" variant="outline" @click="openAdd(group.key)">
            {{ t('fingerprintPool.add') }}
          </Button>
        </CardHeader>
        <CardContent>
          <div class="rounded-md border border-border overflow-hidden">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{{ t('fingerprintPool.name') }}</TableHead>
                  <TableHead>{{ t('fingerprintPool.info') }}</TableHead>
                  <TableHead v-if="group.key !== 'hardware'">{{ t('fingerprintPool.platform') }}</TableHead>
                  <TableHead class="w-[80px]">{{ t('fingerprintPool.enabled') }}</TableHead>
                  <TableHead v-if="isAdmin" class="w-[100px]" />
                </TableRow>
              </TableHeader>
              <TableBody>
                <TableRow v-for="entry in pool[group.key]" :key="entry.id">
                  <TableCell class="font-medium">{{ entry.label }}</TableCell>
                  <TableCell class="text-muted-foreground text-xs font-mono max-w-[300px] truncate">{{ entrySummary(entry) }}</TableCell>
                  <TableCell v-if="group.key !== 'hardware'">
                    <Badge v-for="tag in entry.tags" :key="tag" variant="secondary" class="mr-1 text-xs">{{ tag }}</Badge>
                    <span v-if="!entry.tags?.length" class="text-muted-foreground text-xs">{{ t('fingerprintPool.allPlatforms') }}</span>
                  </TableCell>
                  <TableCell>
                    <Switch :model-value="entry.enabled" :disabled="!isAdmin" @update:model-value="toggleEnabled(entry)" />
                  </TableCell>
                  <TableCell v-if="isAdmin" class="text-right space-x-1">
                    <Button size="sm" variant="ghost" @click="openEdit(entry)">{{ t('fingerprintPool.edit') }}</Button>
                    <Button size="sm" variant="ghost" class="text-destructive" @click="deleteTarget = entry">{{ t('fingerprintPool.delete') }}</Button>
                  </TableCell>
                </TableRow>
                <TableRow v-if="!pool[group.key]?.length">
                  <TableCell :colspan="(group.key === 'hardware' ? 3 : 4) + (isAdmin ? 1 : 0)" class="text-center text-muted-foreground py-6">
                    {{ t('fingerprintPool.empty') }}
                  </TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      <div v-if="isAdmin" class="flex justify-end">
        <Button variant="outline" class="text-destructive" @click="showResetConfirm = true">
          {{ t('fingerprintPool.resetBtn') }}
        </Button>
      </div>
    </template>

    <!-- Delete confirm -->
    <AlertDialog :open="!!deleteTarget" @update:open="v => { if (!v) deleteTarget = null }">
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{{ t('fingerprintPool.deleteTitle') }}</AlertDialogTitle>
          <AlertDialogDescription>{{ t('fingerprintPool.deleteConfirm', { name: deleteTarget?.label }) }}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{{ t('fingerprintPool.cancel') }}</AlertDialogCancel>
          <AlertDialogAction @click="confirmDelete">{{ t('fingerprintPool.delete') }}</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>

    <!-- Reset confirm -->
    <AlertDialog :open="showResetConfirm" @update:open="v => showResetConfirm = v">
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{{ t('fingerprintPool.resetTitle') }}</AlertDialogTitle>
          <AlertDialogDescription>{{ t('fingerprintPool.resetConfirm') }}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{{ t('fingerprintPool.cancel') }}</AlertDialogCancel>
          <AlertDialogAction @click="resetPool">{{ t('fingerprintPool.resetBtn') }}</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>

    <!-- Add / Edit dialog -->
    <Dialog :open="showDialog" @update:open="v => showDialog = v">
      <DialogContent class="max-w-lg max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{{ dialogMode === 'add' ? t('fingerprintPool.addTitle') : t('fingerprintPool.editTitle') }}</DialogTitle>
        </DialogHeader>
        <div class="space-y-4 py-2">
          <div>
            <Label>{{ t('fingerprintPool.name') }}</Label>
            <Input v-model="formLabel" class="mt-1" />
          </div>

          <!-- Platform form -->
          <template v-if="dialogGroup === 'platform'">
            <div>
              <Label>{{ t('fingerprintPool.osFamily') }}</Label>
              <Select :model-value="formClientHints.platform === 'Windows' ? 'windows' : 'macos'" @update:model-value="onPlatformPresetChange">
                <SelectTrigger class="mt-1"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="windows">Windows</SelectItem>
                  <SelectItem value="macos">macOS</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>User Agent</Label>
              <Input v-model="formPlatformNav.userAgent" class="mt-1 font-mono text-xs" />
            </div>
            <div>
              <Label>App Version</Label>
              <Input v-model="formPlatformNav.appVersion" class="mt-1 font-mono text-xs" />
            </div>
            <div class="grid grid-cols-2 gap-3">
              <div>
                <Label>Platform Version</Label>
                <Input v-model="formClientHints.platformVersion" class="mt-1" />
              </div>
              <div>
                <Label>Architecture</Label>
                <Select v-model="formClientHints.architecture">
                  <SelectTrigger class="mt-1"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="x86">x86</SelectItem>
                    <SelectItem value="arm">arm</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
          </template>

          <!-- GPU form -->
          <template v-if="dialogGroup === 'gpu'">
            <div>
              <Label>WebGL Vendor</Label>
              <Input v-model="formGpu.vendor" class="mt-1 font-mono text-xs" />
            </div>
            <div>
              <Label>WebGL Renderer</Label>
              <Input v-model="formGpu.renderer" class="mt-1 font-mono text-xs" />
            </div>
            <div>
              <Label>{{ t('fingerprintPool.compatPlatform') }}</Label>
              <div class="flex gap-3 mt-1">
                <label class="flex items-center gap-1.5 text-sm">
                  <input type="checkbox" :checked="formTags.includes('windows')" @change="e => { if ((e.target as HTMLInputElement).checked) { if (!formTags.includes('windows')) formTags.push('windows') } else { formTags = formTags.filter(t => t !== 'windows') } }" />
                  Windows
                </label>
                <label class="flex items-center gap-1.5 text-sm">
                  <input type="checkbox" :checked="formTags.includes('macos')" @change="e => { if ((e.target as HTMLInputElement).checked) { if (!formTags.includes('macos')) formTags.push('macos') } else { formTags = formTags.filter(t => t !== 'macos') } }" />
                  macOS
                </label>
              </div>
            </div>
          </template>

          <!-- Hardware form -->
          <template v-if="dialogGroup === 'hardware'">
            <div class="grid grid-cols-2 gap-3">
              <div>
                <Label>{{ t('fingerprintPool.cpuCores') }}</Label>
                <Input v-model.number="formHardware.hardwareConcurrency" type="number" class="mt-1" />
              </div>
              <div>
                <Label>{{ t('fingerprintPool.memoryGb') }}</Label>
                <Input v-model.number="formHardware.deviceMemory" type="number" class="mt-1" />
              </div>
            </div>
          </template>

          <!-- Screen form -->
          <template v-if="dialogGroup === 'screen'">
            <div class="grid grid-cols-3 gap-3">
              <div>
                <Label>Color Depth</Label>
                <Input v-model.number="formScreen.colorDepth" type="number" class="mt-1" />
              </div>
              <div>
                <Label>Pixel Depth</Label>
                <Input v-model.number="formScreen.pixelDepth" type="number" class="mt-1" />
              </div>
              <div>
                <Label>DPR</Label>
                <Input v-model.number="formScreen.devicePixelRatio" type="number" step="0.5" class="mt-1" />
              </div>
            </div>
            <div>
              <Label>{{ t('fingerprintPool.compatPlatform') }}</Label>
              <div class="flex gap-3 mt-1">
                <label class="flex items-center gap-1.5 text-sm">
                  <input type="checkbox" :checked="formTags.includes('windows')" @change="e => { if ((e.target as HTMLInputElement).checked) { if (!formTags.includes('windows')) formTags.push('windows') } else { formTags = formTags.filter(t => t !== 'windows') } }" />
                  Windows
                </label>
                <label class="flex items-center gap-1.5 text-sm">
                  <input type="checkbox" :checked="formTags.includes('macos')" @change="e => { if ((e.target as HTMLInputElement).checked) { if (!formTags.includes('macos')) formTags.push('macos') } else { formTags = formTags.filter(t => t !== 'macos') } }" />
                  macOS
                </label>
              </div>
            </div>
          </template>
        </div>
        <DialogFooter>
          <Button variant="outline" @click="showDialog = false">{{ t('fingerprintPool.cancel') }}</Button>
          <Button :disabled="saving || !formLabel.trim()" @click="saveEntry">
            {{ saving ? t('fingerprintPool.saving') : t('fingerprintPool.save') }}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
</template>
