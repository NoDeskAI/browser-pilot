<script setup lang="ts">
import { computed, watch, ref, nextTick, type ComponentPublicInstance } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useSessions } from '../composables/useSessions'
import { useNotify } from '../composables/useNotify'
import { api } from '../lib/api'
import { Play, Pause, Trash2, ChevronRight, Monitor, Key, Loader2, Copy, Check, CornerDownLeft } from 'lucide-vue-next'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import NoVNCViewer from '../components/NoVNCViewer.vue'
import BrowserLogPanel from '../components/BrowserLogPanel.vue'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import {
  AlertDialog, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from '@/components/ui/alert-dialog'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const notify = useNotify()
const { state: sessions, switchSession, startContainer, pauseContainer, deleteSession, renameSession, fetchSessions } = useSessions()

const activeSession = computed(() => sessions.sessions.find(s => s.id === sessions.activeId))
const vncUrl = computed(() => {
  if (!sessions.activePorts?.vncPort) return null
  return `ws://${location.hostname}:${sessions.activePorts.vncPort}/websockify`
})

const editing = ref(false)
const editName = ref('')
const inputRef = ref<HTMLInputElement>()
const deleteDialogOpen = ref(false)
const deleting = ref(false)
const containerActionLoading = ref(false)
const deleteButtonRef = ref<Element | ComponentPublicInstance | null>(null)

// Session Token
const tokenDialogOpen = ref(false)
const tokenName = ref('')
const tokenLoading = ref(false)
const createdToken = ref<string | null>(null)
const tokenCopied = ref(false)

async function handleCreateSessionToken() {
  if (!activeSession.value || !tokenName.value.trim()) return
  tokenLoading.value = true
  try {
    const res = await api('/api/auth/tokens', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: tokenName.value.trim(), sessionId: activeSession.value.id }),
    })
    if (!res.ok) {
      notify.error(t('account.tokenCreateError'))
      return
    }
    const data = await res.json()
    createdToken.value = data.token
  } catch {
    notify.error(t('account.tokenCreateError'))
  } finally {
    tokenLoading.value = false
  }
}

function closeTokenDialog() {
  tokenDialogOpen.value = false
  createdToken.value = null
  tokenName.value = ''
  tokenCopied.value = false
}

async function copySessionToken() {
  if (!createdToken.value) return
  await navigator.clipboard.writeText(createdToken.value)
  tokenCopied.value = true
  notify.success(t('session.copied'))
  setTimeout(() => { tokenCopied.value = false }, 2000)
}

watch(() => route.params.id as string | undefined, async (id) => {
  if (!id) return
  if (!sessions.sessions.some(s => s.id === id)) {
    await fetchSessions()
    if (!sessions.sessions.some(s => s.id === id)) {
      notify.error(t('app.sessionNotFound'))
      router.replace('/')
      return
    }
  }
  if (sessions.activeId !== id) {
    await switchSession(id)
  }
}, { immediate: true })

function startEdit() {
  if (!activeSession.value) return
  editName.value = activeSession.value.name
  editing.value = true
  nextTick(() => inputRef.value?.select())
}

function commitEdit() {
  if (!activeSession.value) return
  const trimmed = editName.value.trim()
  editing.value = false
  if (trimmed && trimmed !== activeSession.value.name) {
    renameSession(activeSession.value.id, trimmed)
  }
}

async function onDeleteSession() {
  if (!activeSession.value || deleting.value) return
  deleting.value = true
  try {
    await deleteSession(activeSession.value.id)
    notify.success(t('app.sessionDeleted'))
    deleteDialogOpen.value = false
    router.replace('/')
  } catch {
    notify.error(t('app.sessionDeleteError'))
  } finally {
    deleting.value = false
  }
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

  if (typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(focus)
  } else {
    setTimeout(focus, 0)
  }
}

async function onStartContainer() {
  if (!activeSession.value || containerActionLoading.value) return
  containerActionLoading.value = true
  try {
    await startContainer(activeSession.value.id)
    notify.success(t('app.sessionStarted'))
  } catch {
    notify.error(t('app.containerStartError'))
  } finally {
    containerActionLoading.value = false
  }
}

async function onPauseContainer() {
  if (!activeSession.value || containerActionLoading.value) return
  containerActionLoading.value = true
  try {
    await pauseContainer(activeSession.value.id)
    notify.success(t('app.sessionPaused'))
  } catch {
    notify.error(t('app.containerPauseError'))
  } finally {
    containerActionLoading.value = false
  }
}
</script>

<template>
  <div class="flex-1 flex flex-col overflow-hidden bg-background">
    <!-- Context Header -->
    <header v-if="activeSession" class="h-12 border-b border-border flex items-center justify-between px-4 shrink-0 bg-background/95 backdrop-blur z-10">
      <div class="flex items-center gap-2 min-w-0">
        <RouterLink to="/" class="text-sm text-muted-foreground hover:text-foreground transition-colors shrink-0 font-medium">
          {{ t('session.sessions') }}
        </RouterLink>
        <ChevronRight class="size-4 text-muted-foreground/50 shrink-0" />
        <input
          v-if="editing"
          ref="inputRef"
          v-model="editName"
          class="text-sm font-medium bg-transparent border-b border-foreground outline-none w-48 text-foreground"
          @blur="commitEdit"
          @keydown.enter.prevent="commitEdit"
          @keydown.escape.prevent="editing = false"
        />
        <h2
          v-else
          class="text-sm font-medium truncate cursor-pointer hover:underline decoration-muted-foreground/30 underline-offset-4"
          @dblclick="startEdit"
          :title="activeSession.name + '\n' + t('session.dblClickRename')"
        >
          {{ activeSession.name }}
        </h2>
        <Badge
          variant="outline"
          class="ml-2 font-normal uppercase text-[10px] px-1.5"
          :class="{
            'bg-green-500/10 text-green-500 border-green-500/20': activeSession.containerStatus === 'running',
            'bg-blue-500/10 text-blue-500 border-blue-500/20': activeSession.containerStatus === 'starting',
            'bg-yellow-500/10 text-yellow-500 border-yellow-500/20': activeSession.containerStatus === 'paused',
            'text-muted-foreground': activeSession.containerStatus !== 'running' && activeSession.containerStatus !== 'paused' && activeSession.containerStatus !== 'starting'
          }"
        >
          {{ activeSession.containerStatus === 'running' ? t('session.running') : activeSession.containerStatus === 'starting' ? t('session.starting') : activeSession.containerStatus === 'paused' ? t('session.paused') : t('session.stopped') }}
        </Badge>
      </div>

      <div class="flex items-center gap-1.5 shrink-0 ml-4">
        <Tooltip v-if="activeSession.containerStatus === 'running'">
          <TooltipTrigger as-child>
            <Button
              variant="outline" size="sm" class="h-8 gap-1.5 text-muted-foreground hover:text-foreground"
              :disabled="containerActionLoading"
              @click="onPauseContainer"
            >
              <Loader2 v-if="containerActionLoading" class="size-3.5 animate-spin" />
              <Pause v-else class="size-3.5" />
              {{ containerActionLoading ? t('session.pausing') : t('session.hibernate') }}
            </Button>
          </TooltipTrigger>
          <TooltipContent>{{ t('session.hibernateHint') }}</TooltipContent>
        </Tooltip>
        
        <Tooltip v-else>
          <TooltipTrigger as-child>
            <Button
              variant="default" size="sm" class="h-8 gap-1.5"
              :disabled="containerActionLoading || activeSession.containerStatus === 'starting'"
              @click="onStartContainer"
            >
              <Loader2 v-if="containerActionLoading || activeSession.containerStatus === 'starting'" class="size-3.5 animate-spin" />
              <Play v-else class="size-3.5" />
              {{ containerActionLoading || activeSession.containerStatus === 'starting' ? t('session.starting') : (activeSession.containerStatus === 'paused' ? t('session.resumeFromHibernate') : t('session.startContainer')) }}
            </Button>
          </TooltipTrigger>
          <TooltipContent>{{ activeSession.containerStatus === 'starting' ? t('session.starting') : activeSession.containerStatus === 'paused' ? t('session.resumeFromHibernate') : t('session.startContainer') }}</TooltipContent>
        </Tooltip>

        <Tooltip>
          <TooltipTrigger as-child>
            <Button
              variant="outline" size="sm" class="h-8 gap-1.5 text-muted-foreground hover:text-foreground"
              @click="tokenDialogOpen = true"
            >
              <Key class="size-3.5" />
              API Token
            </Button>
          </TooltipTrigger>
          <TooltipContent>{{ t('session.generateTokenHint') }}</TooltipContent>
        </Tooltip>

        <AlertDialog :open="deleteDialogOpen" @update:open="v => { if (v || !deleting) deleteDialogOpen = v }">
          <AlertDialogTrigger as-child>
            <Button variant="outline" size="sm" class="h-8 px-2.5 text-muted-foreground hover:text-destructive hover:bg-destructive/10 hover:border-destructive/20 transition-colors">
              <Trash2 class="size-3.5" />
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent @open-auto-focus.prevent="focusDeleteButton">
            <AlertDialogHeader>
              <AlertDialogTitle>{{ t('session.deleteConfirm') }}</AlertDialogTitle>
              <AlertDialogDescription>{{ t('session.deleteDescription') }}</AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel :disabled="deleting">
                {{ t('session.cancel') }}
                <kbd v-if="!deleting" data-slot="kbd">
                  {{ t('session.shortcutEscape') }}
                </kbd>
              </AlertDialogCancel>
              <Button ref="deleteButtonRef" variant="destructive" :disabled="deleting" @click="onDeleteSession">
                <Loader2 v-if="deleting" class="size-4 animate-spin" />
                {{ deleting ? t('session.deleting') : t('session.confirmDelete') }}
                <kbd v-if="!deleting" data-slot="kbd" data-icon="true">
                  <CornerDownLeft aria-hidden="true" />
                  <span class="sr-only">{{ t('session.shortcutEnter') }}</span>
                </kbd>
              </Button>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    </header>

    <!-- Session Token Dialog -->
    <Dialog :open="tokenDialogOpen" @update:open="(v: boolean) => { if (!v) closeTokenDialog() }">
      <DialogContent class="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{{ t('session.generateToken') }}</DialogTitle>
          <DialogDescription>{{ t('session.generateTokenHint') }}</DialogDescription>
        </DialogHeader>
        <template v-if="!createdToken">
          <form @submit.prevent="handleCreateSessionToken" class="space-y-4">
            <div class="space-y-2">
              <Label>{{ t('account.tokenName') }}</Label>
              <Input v-model="tokenName" :placeholder="t('account.tokenNamePlaceholder')" required autofocus />
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" @click="closeTokenDialog">{{ t('session.cancel') }}</Button>
              <Button type="submit" :disabled="tokenLoading || !tokenName.trim()">
                <Loader2 v-if="tokenLoading" class="size-4 mr-2 animate-spin" />
                {{ t('account.tokenCreate') }}
              </Button>
            </DialogFooter>
          </form>
        </template>
        <template v-else>
          <div class="space-y-3">
            <p class="text-sm text-amber-500 dark:text-amber-400 font-medium">{{ t('account.tokenCopyWarning') }}</p>
            <div class="flex items-center gap-2">
              <code class="flex-1 bg-muted px-3 py-2 rounded text-xs font-mono break-all select-all">{{ createdToken }}</code>
              <Button variant="outline" size="icon" class="shrink-0" @click="copySessionToken">
                <Check v-if="tokenCopied" class="size-4 text-green-500" />
                <Copy v-else class="size-4" />
              </Button>
            </div>
          </div>
          <DialogFooter>
            <Button @click="closeTokenDialog">{{ t('account.tokenDone') }}</Button>
          </DialogFooter>
        </template>
      </DialogContent>
    </Dialog>

    <div class="flex-1 relative overflow-hidden min-h-0 bg-muted/10">
      <NoVNCViewer v-if="vncUrl && sessions.activeId" :key="vncUrl" :ws-url="vncUrl" :session-id="sessions.activeId" />
      
      <div v-else-if="sessions.activeId && !vncUrl && !sessions.containerLoading" class="absolute inset-0 flex flex-col items-center justify-center text-muted-foreground">
        <Monitor class="size-10 mb-3 opacity-20" :stroke-width="1.5" />
        <p class="text-sm">{{ activeSession?.containerStatus === 'paused' ? t('app.browserHibernated') : t('app.containerStopped') }}</p>
        <p class="text-xs mt-1 opacity-60 mb-4 max-w-sm text-center">
          {{ activeSession?.containerStatus === 'paused' ? t('app.browserHibernatedHint') : t('app.containerStoppedHint') }}
        </p>
      </div>
      <div v-else-if="sessions.activeId && !vncUrl && sessions.containerLoading" class="absolute inset-0 flex flex-col items-center justify-center text-muted-foreground">
        <Loader2 class="size-8 mb-3 animate-spin opacity-70" />
        <p class="text-sm">{{ t('app.startingBrowser') }}</p>
        <p class="text-xs mt-1 opacity-60 max-w-sm text-center">{{ t('app.startingBrowserHint') }}</p>
      </div>
    </div>
    <BrowserLogPanel :session-id="sessions.activeId" />
  </div>
</template>
