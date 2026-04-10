<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { setLocale, getLocale } from './i18n'
import { useSessions } from './composables/useSessions'
import { Settings, ArrowLeft } from 'lucide-vue-next'

const { locale } = useI18n()
const route = useRoute()
const router = useRouter()

function toggleLocale() {
  setLocale(getLocale() === 'zh' ? 'en' : 'zh')
}

const { brand, init: initSessions } = useSessions()

const titleParts = computed(() => {
  const words = brand.appTitle.split(' ')
  const accent = words.pop() || ''
  return { prefix: words.join(' '), accent }
})

const isSettings = computed(() => route.path === '/settings')

onMounted(async () => {
  await initSessions()
  document.title = brand.appTitle
})

watch(() => brand.appTitle, (title) => {
  document.title = title
})
</script>

<template>
  <div class="h-screen flex flex-col bg-[var(--color-bg)] overflow-hidden">
    <header class="shrink-0 flex items-center gap-3 px-5 py-2.5 border-b border-[var(--color-border)] bg-[var(--color-surface)]">
      <button
        v-if="isSettings"
        @click="router.push('/')"
        class="shrink-0 w-7 h-7 flex items-center justify-center rounded-md text-[var(--color-text-dim)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface-hover)] transition-colors"
      >
        <ArrowLeft class="w-4 h-4" />
      </button>

      <h1 class="text-lg font-bold tracking-tight whitespace-nowrap">
        {{ titleParts.prefix }}
        <span class="text-[var(--color-accent)]">{{ titleParts.accent }}</span>
      </h1>

      <div class="ml-auto flex items-center gap-2">
        <button
          v-if="!isSettings"
          @click="router.push('/settings')"
          class="shrink-0 w-7 h-7 flex items-center justify-center rounded-md text-[var(--color-text-dim)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface-hover)] transition-colors"
          title="Settings"
        >
          <Settings class="w-4 h-4" />
        </button>
        <button
          @click="toggleLocale"
          class="px-2.5 py-1.5 rounded-lg text-xs font-medium border border-[var(--color-border)] text-[var(--color-text-dim)] hover:text-[var(--color-text)] hover:border-[var(--color-text-dim)] transition-colors"
        >{{ locale === 'zh' ? '中文 | EN' : 'EN | 中文' }}</button>
      </div>
    </header>

    <router-view />
  </div>
</template>

<style>
body.resizing,
body.resizing * {
  cursor: col-resize !important;
  user-select: none !important;
}
body.resizing iframe {
  pointer-events: none !important;
}
</style>
