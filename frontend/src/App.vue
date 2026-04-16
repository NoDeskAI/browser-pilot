<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { setLocale, getLocale } from './i18n'
import { useSessions } from './composables/useSessions'
import { useAuth } from './composables/useAuth'
import { Settings, ArrowLeft, LogOut, Users } from 'lucide-vue-next'

const { t, locale } = useI18n()
const route = useRoute()
const router = useRouter()

function toggleLocale() {
  setLocale(getLocale() === 'zh' ? 'en' : 'zh')
}

const { brand, init: initSessions } = useSessions()
const { user, isAuthenticated, logout, fetchMe } = useAuth()

const titleParts = computed(() => {
  const words = brand.appTitle.split(' ')
  const accent = words.pop() || ''
  return { prefix: words.join(' '), accent }
})

const isSettings = computed(() => route.path === '/settings' || route.path === '/users')
const isAuthPage = computed(() => route.path === '/login' || route.path === '/setup')
const showUsersLink = computed(() => user.value && (user.value.role === 'superadmin' || user.value.role === 'admin'))

function handleLogout() {
  logout()
  router.push('/login')
}

onMounted(async () => {
  if (isAuthenticated.value) {
    await fetchMe()
  }
  if (!isAuthPage.value && isAuthenticated.value) {
    await initSessions()
  }
  document.title = brand.appTitle
})

watch(() => brand.appTitle, (title) => {
  document.title = title
})
</script>

<template>
  <div class="h-screen flex flex-col bg-[var(--color-bg)] overflow-hidden">
    <header v-if="!isAuthPage" class="shrink-0 flex items-center gap-3 px-5 py-2.5 border-b border-[var(--color-border)] bg-[var(--color-surface)]">
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
          v-if="showUsersLink && !isSettings"
          @click="router.push('/users')"
          class="shrink-0 w-7 h-7 flex items-center justify-center rounded-md text-[var(--color-text-dim)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface-hover)] transition-colors"
          :title="t('users.title')"
        >
          <Users class="w-4 h-4" />
        </button>
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

        <span v-if="user" class="text-xs text-[var(--color-text-dim)] truncate max-w-[120px]">{{ user.email }}</span>
        <button
          v-if="isAuthenticated"
          @click="handleLogout"
          class="shrink-0 w-7 h-7 flex items-center justify-center rounded-md text-[var(--color-text-dim)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface-hover)] transition-colors"
          :title="t('auth.logout')"
        >
          <LogOut class="w-4 h-4" />
        </button>
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
