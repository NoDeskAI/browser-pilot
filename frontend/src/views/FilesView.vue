<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Download, FileText, Loader2, Pencil, RefreshCw, Trash2, Check, X } from 'lucide-vue-next'
import { api } from '../lib/api'
import { downloadSignedFile } from '../lib/fileDownload'
import { useNotify } from '../composables/useNotify'
import type { SessionFile } from '../types'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import {
  AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

type DeleteFileResult = {
  warning?: string | null
}

const { t } = useI18n()
const notify = useNotify()

const files = ref<SessionFile[]>([])
const loading = ref(false)
const actionId = ref('')
const editingId = ref('')
const editingName = ref('')
const deleteTarget = ref<SessionFile | null>(null)

const completedCount = computed(() => files.value.length)
const archivedCount = computed(() => files.value.filter(file => file.archivedAt || !file.sessionId).length)

onMounted(() => {
  void fetchFiles()
})

async function fetchFiles() {
  loading.value = true
  try {
    const res = await api('/api/files')
    if (!res.ok) throw new Error(await res.text())
    const data = await res.json()
    files.value = data.files || []
  } catch {
    notify.error(t('files.loadFailed'))
  } finally {
    loading.value = false
  }
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

function sessionLabel(file: SessionFile): string {
  if (file.sessionId) return file.archivedSessionName || file.sessionId
  return file.archivedSessionName || file.archivedSessionId || t('files.archivedSession')
}

function statusLabel(file: SessionFile): string {
  return file.sessionId ? t('files.activeSessionFile') : t('files.archivedFile')
}

async function saveFile(file: SessionFile) {
  if (!file.url) return
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
  const name = editingName.value.trim()
  if (!name || name === file.name) {
    cancelRename()
    return
  }
  actionId.value = file.id
  try {
    const res = await api(`/api/files/${file.id}`, {
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
  if (!deleteTarget.value) return
  const target = deleteTarget.value
  actionId.value = target.id
  try {
    const res = await api(`/api/files/${target.id}`, { method: 'DELETE' })
    if (!res.ok) throw new Error(await res.text())
    const data = await res.json().catch(() => ({})) as DeleteFileResult
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
  <div class="flex-1 overflow-y-auto bg-background">
    <div class="mx-auto flex max-w-6xl flex-col gap-5 px-6 py-8">
      <header class="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 class="text-xl font-semibold text-foreground">{{ t('files.title') }}</h2>
          <p class="mt-1 text-sm text-muted-foreground">
            {{ t('files.description', { count: completedCount, archived: archivedCount }) }}
          </p>
        </div>
        <Button variant="outline" size="sm" :disabled="loading" @click="fetchFiles">
          <RefreshCw class="size-4" :class="{ 'animate-spin': loading }" />
          {{ t('sessionFiles.refresh') }}
        </Button>
      </header>

      <div class="overflow-hidden rounded-md border border-border bg-background">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{{ t('sessionFiles.name') }}</TableHead>
              <TableHead class="w-[132px]">{{ t('sessionFiles.status') }}</TableHead>
              <TableHead class="w-[120px]">{{ t('files.session') }}</TableHead>
              <TableHead class="w-[96px]">{{ t('sessionFiles.size') }}</TableHead>
              <TableHead class="w-[120px]">{{ t('sessionFiles.source') }}</TableHead>
              <TableHead class="w-[152px]">{{ t('sessionFiles.updated') }}</TableHead>
              <TableHead class="w-[120px] text-right">{{ t('sessionFiles.actions') }}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            <TableRow v-if="loading && !files.length">
              <TableCell :colspan="7" class="h-32 text-center text-muted-foreground">
                <Loader2 class="mx-auto mb-2 size-5 animate-spin" />
                {{ t('files.loading') }}
              </TableCell>
            </TableRow>
            <TableRow v-else-if="!files.length">
              <TableCell :colspan="7" class="h-32 text-center text-muted-foreground">
                {{ t('files.empty') }}
              </TableCell>
            </TableRow>
            <TableRow v-for="file in files" :key="file.id">
              <TableCell class="min-w-[240px]">
                <div v-if="editingId === file.id" class="flex items-center gap-1.5">
                  <Input v-model="editingName" class="h-8 min-w-0" />
                  <Button size="icon" class="size-8" :disabled="actionId === file.id" :title="t('sessionFiles.rename')" @click="submitRename(file)">
                    <Check class="size-3.5" />
                  </Button>
                  <Button size="icon" variant="outline" class="size-8" :disabled="actionId === file.id" :title="t('session.cancel')" @click="cancelRename">
                    <X class="size-3.5" />
                  </Button>
                </div>
                <div v-else class="flex min-w-0 items-center gap-2">
                  <FileText class="size-4 shrink-0 text-muted-foreground" />
                  <span class="truncate font-medium">{{ file.name }}</span>
                </div>
              </TableCell>
              <TableCell>
                <Badge :variant="file.sessionId ? 'outline' : 'secondary'">
                  {{ statusLabel(file) }}
                </Badge>
              </TableCell>
              <TableCell class="text-sm text-muted-foreground">
                <span class="block truncate" :title="sessionLabel(file)">{{ sessionLabel(file) }}</span>
              </TableCell>
              <TableCell class="text-sm text-muted-foreground">
                {{ formatSize(file.size) }}
              </TableCell>
              <TableCell class="text-sm text-muted-foreground">
                {{ sourceLabel(file.source) }}
              </TableCell>
              <TableCell class="text-xs text-muted-foreground">
                {{ formatTime(file.uploadedAt || file.createdAt) }}
              </TableCell>
              <TableCell class="text-right">
                <div class="inline-flex items-center gap-1">
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
  </div>
</template>
