<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Download, FileText, Loader2, Pencil, RefreshCw, Trash2, Upload, X, Check } from 'lucide-vue-next'
import { api } from '../lib/api'
import { downloadSignedFile } from '../lib/fileDownload'
import { useNotify } from '../composables/useNotify'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import {
  AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

type SessionFile = {
  id: string
  name: string
  status: 'downloading' | 'completed' | string
  source?: string
  contentType?: string | null
  size?: number | null
  receivedBytes?: number | null
  totalBytes?: number | null
  percent?: number | null
  url?: string | null
  uploadedAt?: string | null
  createdAt?: string | null
  updatedAt?: string | null
}

type DeleteSessionFileResult = {
  ok?: boolean
  objectDeleted?: boolean
  recordDeleted?: boolean
  warning?: string | null
}

const props = defineProps<{
  open: boolean
  sessionId: string | null
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
}>()

const { t } = useI18n()
const notify = useNotify()

const files = ref<SessionFile[]>([])
const loading = ref(false)
const uploading = ref(false)
const actionId = ref('')
const editingId = ref('')
const editingName = ref('')
const deleteTarget = ref<SessionFile | null>(null)
const fileInputRef = ref<HTMLInputElement>()

const completedCount = computed(() => files.value.filter(f => f.status === 'completed').length)

watch(() => [props.open, props.sessionId] as const, ([open, sessionId]) => {
  if (open && sessionId) void fetchFiles()
  if (!open) cancelRename()
})

function closeDrawer() {
  emit('update:open', false)
}

function formatSize(size?: number | null): string {
  if (size == null) return '-'
  if (size < 1024) return `${size} B`
  const units = ['KB', 'MB', 'GB']
  let value = size / 1024
  let unit = units[0]
  for (let i = 1; i < units.length && value >= 1024; i++) {
    value /= 1024
    unit = units[i]
  }
  return `${value >= 10 ? value.toFixed(0) : value.toFixed(1)} ${unit}`
}

function formatTime(value?: string | null): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  return date.toLocaleString()
}

function sourceLabel(source?: string): string {
  if (source === 'user_upload') return t('sessionFiles.sourceUpload')
  if (source === 'browser_download') return t('sessionFiles.sourceBrowser')
  if (source === 'screenshot') return t('sessionFiles.sourceScreenshot')
  return source || '-'
}

async function fetchFiles() {
  if (!props.sessionId) return
  loading.value = true
  try {
    const res = await api(`/api/sessions/${props.sessionId}/files`)
    if (!res.ok) throw new Error(await res.text())
    const data = await res.json()
    if (!Array.isArray(data.files)) throw new Error(data.error || 'Invalid session files response')
    files.value = data.files
  } catch {
    notify.error(t('sessionFiles.loadFailed'))
  } finally {
    loading.value = false
  }
}

function openFilePicker() {
  fileInputRef.value?.click()
}

async function handleFileSelected(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || !props.sessionId) return

  uploading.value = true
  try {
    const form = new FormData()
    form.append('file', file)
    form.append('originalName', file.name)
    const res = await api(`/api/sessions/${props.sessionId}/files`, {
      method: 'POST',
      body: form,
    })
    if (!res.ok) throw new Error(await res.text())
    notify.success(t('sessionFiles.uploaded'))
    await fetchFiles()
  } catch {
    notify.error(t('sessionFiles.uploadFailed'))
  } finally {
    uploading.value = false
    input.value = ''
  }
}

async function saveFile(file: SessionFile) {
  if (!file.url || file.status !== 'completed') return
  actionId.value = file.id
  try {
    await downloadSignedFile(file.url, file.name || file.id)
  } catch {
    notify.error(t('sessionFiles.saveFailed'))
  } finally {
    actionId.value = ''
  }
}

function startRename(file: SessionFile) {
  editingId.value = file.id
  editingName.value = file.name
}

function cancelRename() {
  editingId.value = ''
  editingName.value = ''
}

async function submitRename(file: SessionFile) {
  if (!props.sessionId) return
  const name = editingName.value.trim()
  if (!name || name === file.name) {
    cancelRename()
    return
  }

  actionId.value = file.id
  try {
    const res = await api(`/api/sessions/${props.sessionId}/files/${file.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    })
    if (!res.ok) throw new Error(await res.text())
    notify.success(t('sessionFiles.renamed'))
    cancelRename()
    await fetchFiles()
  } catch {
    notify.error(t('sessionFiles.renameFailed'))
  } finally {
    actionId.value = ''
  }
}

async function confirmDelete() {
  if (!props.sessionId || !deleteTarget.value) return
  const target = deleteTarget.value
  actionId.value = target.id
  try {
    const res = await api(`/api/sessions/${props.sessionId}/files/${target.id}`, {
      method: 'DELETE',
    })
    if (!res.ok) throw new Error(await res.text())
    const data = await res.json().catch(() => ({})) as DeleteSessionFileResult
    if (data.warning === 'file_object_delete_failed') {
      notify.warning(t('sessionFiles.deleteObjectWarning'))
    } else {
      notify.success(t('sessionFiles.deleted'))
    }
    deleteTarget.value = null
    await fetchFiles()
  } catch {
    notify.error(t('sessionFiles.deleteFailed'))
  } finally {
    actionId.value = ''
  }
}
</script>

<template>
  <Transition
    enter-active-class="transition-opacity duration-150 ease-out"
    enter-from-class="opacity-0"
    enter-to-class="opacity-100"
    leave-active-class="transition-opacity duration-150 ease-in"
    leave-from-class="opacity-100"
    leave-to-class="opacity-0"
  >
    <div
      v-if="open"
      class="absolute inset-0 z-30 bg-background/70 supports-backdrop-filter:backdrop-blur-[1px]"
      aria-hidden="true"
      @click="closeDrawer"
    />
  </Transition>

  <Transition
    enter-active-class="transition-transform duration-200 ease-out"
    enter-from-class="translate-x-full"
    enter-to-class="translate-x-0"
    leave-active-class="transition-transform duration-150 ease-in"
    leave-from-class="translate-x-0"
    leave-to-class="translate-x-full"
  >
    <aside
      v-if="open"
      role="dialog"
      aria-modal="true"
      aria-labelledby="session-files-drawer-title"
      class="absolute inset-y-0 right-0 z-40 flex h-full w-[min(720px,calc(100%-1rem))] flex-col border-l border-border bg-background shadow-2xl outline-none"
    >
      <header class="shrink-0 border-b border-border bg-background px-4 py-3">
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <h2 id="session-files-drawer-title" class="text-sm font-semibold text-foreground">
              {{ t('sessionFiles.title') }}
            </h2>
            <p class="mt-1 text-xs text-muted-foreground">
              {{ t('sessionFiles.description', { count: completedCount }) }}
            </p>
          </div>
          <Button variant="ghost" size="icon-sm" :title="t('vnc.close')" @click="closeDrawer">
            <X class="size-4" />
            <span class="sr-only">{{ t('vnc.close') }}</span>
          </Button>
        </div>

        <div class="mt-3 flex flex-wrap items-center justify-between gap-3">
          <input ref="fileInputRef" type="file" class="hidden" @change="handleFileSelected" />
          <p class="min-w-0 flex-1 text-xs text-muted-foreground">
            {{ t('sessionFiles.statusHint') }}
          </p>
          <div class="flex items-center gap-2">
            <Button variant="outline" size="sm" :disabled="loading" @click="fetchFiles">
              <RefreshCw class="size-3.5" :class="{ 'animate-spin': loading }" />
              {{ t('sessionFiles.refresh') }}
            </Button>
            <Button size="sm" :disabled="uploading" @click="openFilePicker">
              <Loader2 v-if="uploading" class="size-3.5 animate-spin" />
              <Upload v-else class="size-3.5" />
              {{ uploading ? t('sessionFiles.uploading') : t('sessionFiles.upload') }}
            </Button>
          </div>
        </div>
      </header>

      <div class="min-h-0 flex-1 overflow-auto p-4">
        <div class="overflow-hidden rounded-md border border-border bg-background">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{{ t('sessionFiles.name') }}</TableHead>
                <TableHead class="w-[112px]">{{ t('sessionFiles.status') }}</TableHead>
                <TableHead class="w-[96px]">{{ t('sessionFiles.size') }}</TableHead>
                <TableHead class="w-[120px]">{{ t('sessionFiles.source') }}</TableHead>
                <TableHead class="w-[144px]">{{ t('sessionFiles.updated') }}</TableHead>
                <TableHead class="w-[120px] text-right">{{ t('sessionFiles.actions') }}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow v-if="loading && !files.length">
                <TableCell :colspan="6" class="h-28 text-center text-muted-foreground">
                  <Loader2 class="mx-auto mb-2 size-5 animate-spin" />
                  {{ t('sessionFiles.loading') }}
                </TableCell>
              </TableRow>
              <TableRow v-else-if="!files.length">
                <TableCell :colspan="6" class="h-28 text-center text-muted-foreground">
                  {{ t('sessionFiles.empty') }}
                </TableCell>
              </TableRow>
              <TableRow v-for="file in files" :key="file.id">
                <TableCell class="min-w-[220px]">
                  <div v-if="editingId === file.id" class="flex items-center gap-1.5">
                    <Input v-model="editingName" class="h-8 min-w-0" />
                    <Button size="icon" class="size-8" :disabled="actionId === file.id" :title="t('sessionFiles.rename')" @click="submitRename(file)">
                      <Check class="size-3.5" />
                    </Button>
                    <Button size="icon" variant="outline" class="size-8" :disabled="actionId === file.id" :title="t('session.cancel')" @click="cancelRename">
                      <X class="size-3.5" />
                    </Button>
                  </div>
                  <div v-else class="flex items-center gap-2 min-w-0">
                    <FileText class="size-4 shrink-0 text-muted-foreground" />
                    <span class="truncate font-medium">{{ file.name }}</span>
                  </div>
                </TableCell>
                <TableCell>
                  <Badge :variant="file.status === 'completed' ? 'outline' : 'secondary'" class="capitalize">
                    {{ file.status === 'completed' ? t('sessionFiles.completed') : t('sessionFiles.downloading') }}
                  </Badge>
                  <div v-if="file.status !== 'completed' && file.percent != null" class="mt-1 text-[11px] text-muted-foreground">
                    {{ Math.round(file.percent) }}%
                  </div>
                </TableCell>
                <TableCell class="text-muted-foreground text-sm">
                  {{ formatSize(file.size ?? file.receivedBytes) }}
                </TableCell>
                <TableCell class="text-muted-foreground text-sm">
                  {{ sourceLabel(file.source) }}
                </TableCell>
                <TableCell class="text-muted-foreground text-xs">
                  {{ formatTime(file.uploadedAt || file.updatedAt || file.createdAt) }}
                </TableCell>
                <TableCell class="text-right">
                  <div v-if="file.status === 'completed'" class="inline-flex items-center gap-1">
                    <Button
                      size="icon"
                      variant="ghost"
                      class="size-8"
                      :disabled="!file.url || actionId === file.id"
                      :title="t('sessionFiles.save')"
                      @click="saveFile(file)"
                    >
                      <Loader2 v-if="actionId === file.id" class="size-3.5 animate-spin" />
                      <Download v-else class="size-3.5" />
                    </Button>
                    <Button
                      size="icon"
                      variant="ghost"
                      class="size-8"
                      :disabled="actionId === file.id"
                      :title="t('sessionFiles.rename')"
                      @click="startRename(file)"
                    >
                      <Pencil class="size-3.5" />
                    </Button>
                    <Button
                      size="icon"
                      variant="ghost"
                      class="size-8 text-destructive hover:text-destructive hover:bg-destructive/10"
                      :disabled="actionId === file.id"
                      :title="t('sessionFiles.delete')"
                      @click="deleteTarget = file"
                    >
                      <Trash2 class="size-3.5" />
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </div>
      </div>
    </aside>
  </Transition>

  <AlertDialog :open="!!deleteTarget" @update:open="v => { if (!v && !actionId) deleteTarget = null }">
    <AlertDialogContent>
      <AlertDialogHeader>
        <AlertDialogTitle>{{ t('sessionFiles.deleteTitle') }}</AlertDialogTitle>
        <AlertDialogDescription>
          {{ t('sessionFiles.deleteConfirm', { name: deleteTarget?.name || '' }) }}
        </AlertDialogDescription>
      </AlertDialogHeader>
      <AlertDialogFooter>
        <AlertDialogCancel :disabled="!!actionId">{{ t('session.cancel') }}</AlertDialogCancel>
        <Button variant="destructive" :disabled="!!actionId" @click="confirmDelete">
          <Loader2 v-if="actionId" class="size-4 animate-spin" />
          {{ actionId ? t('session.deleting') : t('session.confirmDelete') }}
        </Button>
      </AlertDialogFooter>
    </AlertDialogContent>
  </AlertDialog>
</template>
