<script setup lang="ts">
import { ref, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { Trash2, Play, Pause, SquareTerminal, Check } from 'lucide-vue-next'
import type { Session } from '../types'
import { useSessions } from '../composables/useSessions'

const { t } = useI18n()
const { brand } = useSessions()

const props = defineProps<{
  session: Session
  active: boolean
}>()

const emit = defineEmits<{
  (e: 'select'): void
  (e: 'delete'): void
  (e: 'rename', name: string): void
  (e: 'start'): void
  (e: 'stop'): void
  (e: 'pause'): void
}>()

const editing = ref(false)
const editName = ref('')
const inputRef = ref<HTMLInputElement>()

function startEdit() {
  editName.value = props.session.name
  editing.value = true
  nextTick(() => inputRef.value?.select())
}

function commitEdit() {
  const trimmed = editName.value.trim()
  editing.value = false
  if (trimmed && trimmed !== props.session.name) {
    emit('rename', trimmed)
  }
}

function formatUrl(raw: string): string {
  try {
    const u = new URL(raw)
    return u.hostname + u.pathname.replace(/\/$/, '')
  } catch {
    return raw
  }
}

const copied = ref(false)
const cliCopied = ref(false)

function copyId() {
  const short = props.session.id.slice(0, 8)
  navigator.clipboard.writeText(short).then(() => {
    copied.value = true
    setTimeout(() => { copied.value = false }, 1500)
  })
}

function copyCli() {
  navigator.clipboard.writeText(`${brand.cliCommandName} session use ${props.session.id}`).then(() => {
    cliCopied.value = true
    setTimeout(() => { cliCopied.value = false }, 1500)
  })
}

function formatRelativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return t('session.justNow')
  if (mins < 60) return t('session.minutesAgo', { n: mins })
  const hours = Math.floor(mins / 60)
  if (hours < 24) return t('session.hoursAgo', { n: hours })
  const days = Math.floor(hours / 24)
  return t('session.daysAgo', { n: days })
}
</script>

<template>
  <div
    class="group relative flex flex-col gap-0.5 px-3 py-2.5 rounded-lg cursor-pointer transition-colors"
    :class="active
      ? 'bg-[var(--color-accent)]/10 border-l-2 border-[var(--color-accent)]'
      : 'hover:bg-[var(--color-surface-hover)] border-l-2 border-transparent'"
    @click="emit('select')"
    @dblclick.stop="startEdit"
  >
    <div class="flex items-center gap-2 min-w-0">
      <span
        class="w-2 h-2 shrink-0 rounded-full"
        :class="{
          'bg-green-500': session.containerStatus === 'running',
          'bg-amber-400 animate-pulse': session.containerStatus === 'paused',
          'bg-gray-400': session.containerStatus !== 'running' && session.containerStatus !== 'paused',
        }"
        :title="session.containerStatus === 'running' ? t('session.running') : session.containerStatus === 'paused' ? t('session.paused') : t('session.stopped')"
      />
      <input
        v-if="editing"
        ref="inputRef"
        v-model="editName"
        class="flex-1 min-w-0 text-xs font-medium bg-transparent border-b border-[var(--color-accent)] outline-none text-[var(--color-text)]"
        @blur="commitEdit"
        @keydown.enter.prevent="commitEdit"
        @keydown.escape.prevent="editing = false"
        @click.stop
      />
      <span v-else class="flex-1 min-w-0 text-xs font-medium text-[var(--color-text)] truncate">
        {{ session.name }}
      </span>
      <div class="shrink-0 flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
        <button
          v-if="session.containerStatus === 'running'"
          @click.stop="emit('pause')"
          class="w-5 h-5 flex items-center justify-center rounded text-[var(--color-text-dim)] hover:text-amber-400 hover:bg-amber-400/10 transition-colors"
          :title="t('session.hibernate')"
        >
          <Pause class="w-3 h-3" />
        </button>
        <button
          v-else
          @click.stop="emit('start')"
          class="w-5 h-5 flex items-center justify-center rounded text-[var(--color-text-dim)] hover:text-green-400 hover:bg-green-400/10 transition-colors"
          :title="session.containerStatus === 'paused' ? t('session.resumeFromHibernate') : t('session.startContainer')"
        >
          <Play class="w-3 h-3" />
        </button>
        <button
          @click.stop="copyCli"
          class="w-5 h-5 flex items-center justify-center rounded transition-colors"
          :class="cliCopied
            ? 'text-green-400 bg-green-400/10'
            : 'text-[var(--color-text-dim)] hover:text-[var(--color-accent)] hover:bg-[var(--color-accent)]/10'"
          :title="t('session.copyCli')"
        >
          <Check v-if="cliCopied" class="w-3 h-3" />
          <SquareTerminal v-else class="w-3 h-3" />
        </button>
        <button
          @click.stop="emit('delete')"
          class="w-5 h-5 flex items-center justify-center rounded text-[var(--color-text-dim)] hover:text-red-400 hover:bg-red-400/10 transition-colors"
          :title="t('session.deleteSession')"
        >
          <Trash2 class="w-3 h-3" />
        </button>
      </div>
    </div>
    <div class="flex items-center gap-2 ml-4">
      <span
        class="shrink-0 text-[9px] font-mono cursor-pointer transition-colors"
        :class="copied ? 'text-green-400 opacity-80' : 'text-[var(--color-text-dim)] opacity-40 hover:opacity-80 hover:text-[var(--color-accent)]'"
        :title="t('session.copyId')"
        @click.stop="copyId"
      >{{ copied ? t('session.copied') : session.id.slice(0, 8) }}</span>
      <span v-if="session.currentUrl" class="flex-1 min-w-0 text-[10px] text-[var(--color-text-dim)] truncate" :title="session.currentUrl">{{ formatUrl(session.currentUrl) }}</span>
      <span class="shrink-0 text-[10px] text-[var(--color-text-dim)] opacity-60 ml-auto">{{ formatRelativeTime(session.updatedAt) }}</span>
    </div>
  </div>
</template>
