<script setup lang="ts">
import { computed, nextTick, ref, watch, type ComponentPublicInstance } from 'vue'
import { useI18n } from 'vue-i18n'
import { ChevronDown, ChevronRight, CornerDownLeft, FileText, Loader2, RefreshCw } from 'lucide-vue-next'
import { api } from '../lib/api'
import type { DeleteSessionFileOptions, SessionFile } from '../types'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Checkbox } from '@/components/ui/checkbox'
import {
  AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog'

const props = withDefaults(defineProps<{
  open: boolean
  sessionId: string | null
  sessionName?: string
  deleting?: boolean
}>(), {
  sessionName: '',
  deleting: false,
})

const emit = defineEmits<{
  'update:open': [value: boolean]
  confirm: [options: DeleteSessionFileOptions]
}>()

const { t } = useI18n()

const files = ref<SessionFile[]>([])
const loading = ref(false)
const loaded = ref(false)
const loadError = ref(false)
const expanded = ref(false)
const selectedIds = ref<string[]>([])
const deleteButtonRef = ref<Element | ComponentPublicInstance | null>(null)
let loadSeq = 0

const completedFiles = computed(() => files.value.filter(file => file.status === 'completed'))
const inProgressFiles = computed(() => files.value.filter(file => file.status !== 'completed'))
const previewFiles = computed(() => files.value.slice(0, 3))
const selectedCount = computed(() => selectedIds.value.length)
const allCompletedSelected = computed(() =>
  completedFiles.value.length > 0 &&
  completedFiles.value.every(file => selectedIds.value.includes(file.id)),
)
const confirmDisabled = computed(() => props.deleting || loading.value || !loaded.value || loadError.value)
const confirmLabel = computed(() => {
  if (props.deleting) return t('session.deleting')
  if (selectedCount.value > 0) return t('sessionDelete.deleteWithFiles', { count: selectedCount.value })
  if (loaded.value && files.value.length === 0) return t('sessionDelete.deleteSessionOnly')
  return t('sessionDelete.deleteKeepFiles')
})

watch(() => [props.open, props.sessionId] as const, ([open, sessionId]) => {
  if (open && sessionId) {
    resetState()
    void fetchFiles()
  }
  if (!open) resetState()
})

watch(completedFiles, (nextFiles) => {
  const completedIds = new Set(nextFiles.map(file => file.id))
  selectedIds.value = selectedIds.value.filter(id => completedIds.has(id))
})

function resetState() {
  files.value = []
  loading.value = false
  loaded.value = false
  loadError.value = false
  expanded.value = false
  selectedIds.value = []
}

function setOpen(value: boolean) {
  if (!value && props.deleting) return
  emit('update:open', value)
}

async function fetchFiles() {
  if (!props.sessionId) return
  const seq = ++loadSeq
  loading.value = true
  loadError.value = false
  try {
    const res = await api(`/api/sessions/${props.sessionId}/files`)
    if (!res.ok) throw new Error(await res.text())
    const data = await res.json()
    if (seq !== loadSeq) return
    files.value = data.files || []
    selectedIds.value = files.value
      .filter((file: SessionFile) => file.status === 'completed')
      .map((file: SessionFile) => file.id)
    loaded.value = true
  } catch {
    if (seq !== loadSeq) return
    loadError.value = true
    loaded.value = false
  } finally {
    if (seq === loadSeq) loading.value = false
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

function statusLabel(file: SessionFile): string {
  return file.status === 'completed' ? t('sessionFiles.completed') : t('sessionFiles.downloading')
}

function statusVariant(file: SessionFile): 'outline' | 'secondary' {
  return file.status === 'completed' ? 'outline' : 'secondary'
}

function isSelected(fileId: string): boolean {
  return selectedIds.value.includes(fileId)
}

function setSelected(fileId: string, checked: boolean | 'indeterminate') {
  const isChecked = checked === true
  if (isChecked && !selectedIds.value.includes(fileId)) {
    selectedIds.value = [...selectedIds.value, fileId]
  } else if (!isChecked) {
    selectedIds.value = selectedIds.value.filter(id => id !== fileId)
  }
}

function setAllCompleted(checked: boolean | 'indeterminate') {
  selectedIds.value = checked === true ? completedFiles.value.map(file => file.id) : []
}

function confirmDelete() {
  if (confirmDisabled.value) return
  const mode: DeleteSessionFileOptions['fileDeleteMode'] =
    selectedCount.value === 0
      ? 'none'
      : selectedCount.value === completedFiles.value.length
        ? 'all'
        : 'selected'
  emit('confirm', {
    fileDeleteMode: mode,
    deleteFileIds: mode === 'selected' ? selectedIds.value : undefined,
  })
}

function focusDeleteButton() {
  const focus = () => {
    const target = deleteButtonRef.value
    const el = target instanceof HTMLElement
      ? target
      : target && '$el' in target
        ? target.$el
        : null
    if (el instanceof HTMLElement) el.focus()
  }
  nextTick(() => {
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(focus)
    else setTimeout(focus, 0)
  })
}
</script>

<template>
  <AlertDialog :open="open" @update:open="setOpen">
    <AlertDialogContent class="sm:max-w-xl" @click.stop @open-auto-focus.prevent="focusDeleteButton">
      <AlertDialogHeader>
        <AlertDialogTitle>{{ t('session.deleteConfirm') }}</AlertDialogTitle>
        <AlertDialogDescription>{{ t('session.deleteDescription') }}</AlertDialogDescription>
      </AlertDialogHeader>

      <section class="space-y-3 rounded-md border border-border bg-muted/20 p-3">
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <p class="text-sm font-medium text-foreground">{{ t('sessionDelete.filesTitle') }}</p>
            <p class="mt-1 text-xs text-muted-foreground">
              <template v-if="loading && !loaded">{{ t('sessionDelete.loadingFiles') }}</template>
              <template v-else-if="loadError">{{ t('sessionDelete.loadFailed') }}</template>
              <template v-else>
                {{ t('sessionDelete.summary', { completed: completedFiles.length, processing: inProgressFiles.length }) }}
              </template>
            </p>
          </div>
          <Button v-if="loadError" variant="outline" size="sm" :disabled="loading" @click="fetchFiles">
            <RefreshCw class="size-3.5" :class="{ 'animate-spin': loading }" />
            {{ t('sessionDelete.retry') }}
          </Button>
        </div>

        <div v-if="loading && !loaded" class="flex h-24 items-center justify-center text-sm text-muted-foreground">
          <Loader2 class="mr-2 size-4 animate-spin" />
          {{ t('sessionDelete.loadingFiles') }}
        </div>

        <template v-else-if="!loadError">
          <label
            class="flex items-center gap-2 rounded-md border border-border bg-background px-3 py-2 text-sm"
            :class="completedFiles.length ? 'cursor-pointer' : 'opacity-60'"
          >
            <Checkbox
              :model-value="allCompletedSelected"
              :disabled="!completedFiles.length"
              @update:model-value="setAllCompleted"
            />
            <span class="min-w-0 flex-1">{{ t('sessionDelete.deleteAllCompleted') }}</span>
            <span class="text-xs text-muted-foreground">{{ completedFiles.length }}</span>
          </label>

          <div v-if="files.length" class="space-y-2">
            <div
              v-for="file in (expanded ? files : previewFiles)"
              :key="file.id"
              class="flex items-center gap-2 rounded-md bg-background px-3 py-2"
            >
              <Checkbox
                v-if="file.status === 'completed' && expanded"
                :model-value="isSelected(file.id)"
                :aria-label="t('sessionDelete.selectFile', { name: file.name })"
                @update:model-value="checked => setSelected(file.id, checked)"
              />
              <span v-else-if="expanded" class="flex size-4 shrink-0 items-center justify-center text-xs text-muted-foreground">-</span>
              <FileText class="size-4 shrink-0 text-muted-foreground" />
              <div class="min-w-0 flex-1">
                <p class="truncate text-sm font-medium text-foreground">{{ file.name }}</p>
                <p class="text-xs text-muted-foreground">
                  {{ formatSize(file.size ?? file.receivedBytes) }}
                </p>
              </div>
              <Badge :variant="statusVariant(file)" class="shrink-0">
                {{ statusLabel(file) }}
              </Badge>
              <span v-if="expanded" class="hidden min-w-[92px] text-right text-xs text-muted-foreground sm:inline">
                {{ file.status === 'completed' ? (isSelected(file.id) ? t('sessionDelete.deleteAction') : t('sessionDelete.keepAction')) : t('sessionDelete.processingAction') }}
              </span>
            </div>

            <Button
              v-if="files.length"
              variant="ghost"
              size="sm"
              class="w-full justify-center"
              @click="expanded = !expanded"
            >
              <ChevronDown v-if="expanded" class="size-4" />
              <ChevronRight v-else class="size-4" />
              {{ expanded ? t('sessionDelete.collapseFiles') : t('sessionDelete.expandFiles') }}
            </Button>
          </div>
          <div v-else class="rounded-md bg-background px-3 py-6 text-center text-sm text-muted-foreground">
            {{ t('sessionDelete.noFiles') }}
          </div>
        </template>
      </section>

      <AlertDialogFooter>
        <AlertDialogCancel :disabled="deleting">
          {{ t('session.cancel') }}
          <kbd v-if="!deleting" data-slot="kbd">
            {{ t('session.shortcutEscape') }}
          </kbd>
        </AlertDialogCancel>
        <Button ref="deleteButtonRef" variant="destructive" :disabled="confirmDisabled" @click="confirmDelete">
          <Loader2 v-if="deleting" class="size-4 animate-spin" />
          {{ confirmLabel }}
          <kbd v-if="!deleting && !confirmDisabled" data-slot="kbd" data-icon="true">
            <CornerDownLeft aria-hidden="true" />
            <span class="sr-only">{{ t('session.shortcutEnter') }}</span>
          </kbd>
        </Button>
      </AlertDialogFooter>
    </AlertDialogContent>
  </AlertDialog>
</template>
