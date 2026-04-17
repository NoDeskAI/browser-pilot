<script setup lang="ts">
import { ref, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useSessions } from '../composables/useSessions'
import { useNotify } from '../composables/useNotify'
import { Plus, Play, Pause, Trash2, Monitor } from 'lucide-vue-next'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from '@/components/ui/alert-dialog'

const { t } = useI18n()
const router = useRouter()
const notify = useNotify()
const {
  state: sessions,
  createSession, deleteSession, renameSession,
  startContainer, pauseContainer,
} = useSessions()

const editingId = ref<string | null>(null)
const editName = ref('')
const inputRefs = ref<Record<string, HTMLInputElement>>({})
const copiedId = ref<string | null>(null)
const deleteDialogOpen = ref<Record<string, boolean>>({})

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

async function handleCreateSession() {
  const session = await createSession()
  if (session) {
    notify.success(t('app.sessionCreated'))
    router.push(`/s/${session.id}`)
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

async function onDeleteSession(id: string) {
  await deleteSession(id)
  notify.success(t('app.sessionDeleted'))
}

async function onStartContainer(id: string) {
  await startContainer(id)
  notify.success(t('app.sessionStarted'))
}

async function onPauseContainer(id: string) {
  await pauseContainer(id)
  notify.success(t('app.sessionPaused'))
}
</script>

<template>
  <div class="flex-1 overflow-y-auto">
    <div class="max-w-6xl mx-auto px-6 py-8 space-y-6">
      <div class="flex items-center justify-between">
        <h2 class="text-xl font-semibold">{{ t('dashboard.title') }}</h2>
        <Button @click="handleCreateSession" class="gap-2">
          <Plus class="size-4" />
          {{ t('dashboard.create') }}
          <kbd class="ml-1 text-[10px] opacity-60 font-sans tracking-widest">⌘N</kbd>
        </Button>
      </div>

      <div v-if="sessions.sessions.length > 0" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        <Card
          v-for="s in sessions.sessions" :key="s.id"
          class="flex flex-col cursor-pointer hover:border-ring/50 transition-colors"
          @click="router.push(`/s/${s.id}`)"
        >
          <div class="p-4 flex items-start justify-between gap-2">
            <div class="min-w-0 flex-1">
              <input
                v-if="editingId === s.id"
                :ref="(el) => { if (el) inputRefs[s.id] = el as HTMLInputElement }"
                v-model="editName"
                class="w-full text-sm font-medium bg-transparent border-b border-foreground outline-none"
                @blur="commitEdit(s.id, s.name)"
                @keydown.enter.prevent="commitEdit(s.id, s.name)"
                @keydown.escape.prevent="editingId = null"
                @click.stop
              />
              <h3
                v-else
                class="text-sm font-medium truncate"
                @dblclick.stop="startEdit(s.id, s.name)"
                :title="s.name"
              >
                {{ s.name }}
              </h3>
            </div>
            <Badge
              variant="outline"
              class="shrink-0 font-normal uppercase text-[10px] px-1.5"
              :class="{
                'bg-green-500/10 text-green-500 border-green-500/20': s.containerStatus === 'running',
                'bg-yellow-500/10 text-yellow-500 border-yellow-500/20': s.containerStatus === 'paused',
                'text-muted-foreground': s.containerStatus !== 'running' && s.containerStatus !== 'paused'
              }"
            >
              {{ s.containerStatus === 'running' ? 'Running' : s.containerStatus === 'paused' ? 'Paused' : 'Stopped' }}
            </Badge>
          </div>

          <div class="px-4 pb-4 flex-1 flex flex-col gap-1 min-h-0">
            <div class="flex items-center gap-2">
              <span class="text-xs text-muted-foreground shrink-0 w-8">ID</span>
              <span
                class="text-xs font-mono text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
                :class="copiedId === s.id ? 'text-green-500' : ''"
                @click.stop="copyId(s.id)"
                :title="t('session.copyId')"
              >
                {{ copiedId === s.id ? t('session.copied') : s.id.slice(0, 8) }}
              </span>
            </div>
            <div class="flex items-center gap-2 min-w-0">
              <span class="text-xs text-muted-foreground shrink-0 w-8">URL</span>
              <span class="text-xs text-muted-foreground truncate flex-1 min-w-0" :title="s.currentUrl">
                {{ formatUrl(s.currentUrl) || '-' }}
              </span>
            </div>
          </div>

          <div class="p-3 border-t border-border flex items-center justify-between bg-muted/20">
            <span class="text-xs text-muted-foreground">{{ formatRelativeTime(s.updatedAt) }}</span>
            <div class="flex items-center gap-1">
              <Button
                v-if="s.containerStatus === 'running'"
                variant="ghost" size="sm" class="size-7 p-0 text-muted-foreground hover:text-foreground"
                @click.stop="onPauseContainer(s.id)"
                :title="t('session.hibernate')"
              >
                <Pause class="size-3.5" />
              </Button>
              <Button
                v-else
                variant="ghost" size="sm" class="size-7 p-0 text-muted-foreground hover:text-foreground"
                @click.stop="onStartContainer(s.id)"
                :title="s.containerStatus === 'paused' ? t('session.resumeFromHibernate') : t('session.startContainer')"
              >
                <Play class="size-3.5" />
              </Button>

              <AlertDialog v-model:open="deleteDialogOpen[s.id]">
                <AlertDialogTrigger as-child>
                  <Button variant="ghost" size="sm" class="size-7 p-0 text-muted-foreground hover:text-destructive" @click.stop>
                    <Trash2 class="size-3.5" />
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent @click.stop>
                  <AlertDialogHeader>
                    <AlertDialogTitle>{{ t('session.deleteConfirm') }}</AlertDialogTitle>
                    <AlertDialogDescription>{{ t('session.deleteDescription') }}</AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>{{ t('session.cancel') }}</AlertDialogCancel>
                    <AlertDialogAction class="bg-destructive text-destructive-foreground hover:bg-destructive/90" @click="onDeleteSession(s.id)">
                      {{ t('session.confirmDelete') }}
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            </div>
          </div>
        </Card>
      </div>

      <div v-else-if="!sessions.loading" class="py-32 flex flex-col items-center justify-center text-center">
        <Monitor class="size-12 mb-4 text-muted-foreground opacity-20" :stroke-width="1" />
        <h3 class="text-lg font-medium mb-2">{{ t('dashboard.empty') }}</h3>
        <p class="text-sm text-muted-foreground max-w-sm mb-6">{{ t('dashboard.emptyHint') }}</p>
        <Button @click="handleCreateSession" class="gap-2">
          <Plus class="size-4" />
          {{ t('dashboard.create') }}
          <kbd class="ml-1 text-[10px] opacity-60 font-sans tracking-widest">⌘N</kbd>
        </Button>
      </div>
    </div>
  </div>
</template>