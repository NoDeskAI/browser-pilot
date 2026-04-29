<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, reactive, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useSessions } from './composables/useSessions'
import { useAuth } from './composables/useAuth'
import AppHeader from './components/AppHeader.vue'
import { Toaster } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'
import { AlertTriangle, Database, Loader2 } from 'lucide-vue-next'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const { brand, init: initSessions, fetchBrand, createSession } = useSessions()

const { isAuthenticated, fetchMe } = useAuth()

const isAuthPage = computed(() => route.path === '/login' || route.path === '/setup')
const ready = ref(false)
const bootstrap = reactive({
  status: 'checking',
  error: '',
  currentRevision: '',
  targetRevision: '',
})
const bootstrapReady = computed(() => bootstrap.status === 'ready')
const bootstrapFailed = computed(() => bootstrap.status === 'migration_failed' || bootstrap.status === 'incompatible_schema')
const bootstrapTitle = computed(() => {
  if (bootstrap.status === 'migration_failed') return t('bootstrap.migrationFailedTitle')
  if (bootstrap.status === 'incompatible_schema') return t('bootstrap.incompatibleTitle')
  if (bootstrap.status === 'waiting_database') return t('bootstrap.waitingDatabaseTitle')
  return t('bootstrap.migratingTitle')
})
const bootstrapDescription = computed(() => {
  if (bootstrap.status === 'migration_failed') return t('bootstrap.migrationFailedDescription')
  if (bootstrap.status === 'incompatible_schema') return t('bootstrap.incompatibleDescription')
  if (bootstrap.status === 'waiting_database') return t('bootstrap.waitingDatabaseDescription')
  return t('bootstrap.migratingDescription')
})

function sleep(ms: number) {
  return new Promise(resolve => setTimeout(resolve, ms))
}

async function waitForBootstrap() {
  while (true) {
    try {
      const res = await fetch('/readyz')
      const data = await res.json().catch(() => ({}))
      const db = data.database || {}
      bootstrap.status = res.ok ? 'ready' : (db.status || data.status || 'waiting_database')
      bootstrap.error = db.error || ''
      bootstrap.currentRevision = db.currentRevision || ''
      bootstrap.targetRevision = db.targetRevision || ''
      if (res.ok || bootstrapFailed.value) return
    } catch (err: any) {
      bootstrap.status = 'waiting_database'
      bootstrap.error = err?.message || ''
    }
    await sleep(1500)
  }
}

async function syncRouteAfterBootstrap() {
  try {
    const res = await fetch('/api/site-info')
    const data = await res.json().catch(() => ({}))
    const setupComplete = !!data.setupComplete
    if (!setupComplete && route.path !== '/setup') {
      await router.replace('/setup')
      return
    }
    if (setupComplete && route.path === '/setup') {
      await router.replace(isAuthenticated.value ? '/' : '/login')
      return
    }
    if (route.meta.requiresAuth && !isAuthenticated.value) {
      await router.replace('/login')
    }
  } catch {
    // router guard will retry on the next navigation
  }
}

function handleKeydown(e: KeyboardEvent) {
  if ((e.metaKey || e.ctrlKey) && e.key === 'n') {
    e.preventDefault()
    if (isAuthenticated.value && !isAuthPage.value) {
      createSession().then(session => {
        if (session) router.push(`/s/${session.id}`)
      })
    }
  }
}

onMounted(async () => {
  document.addEventListener('keydown', handleKeydown)
  await waitForBootstrap()
  if (bootstrapFailed.value) return
  await fetchBrand()
  if (isAuthenticated.value) {
    await fetchMe()
  }
  await syncRouteAfterBootstrap()
  if (!isAuthPage.value && isAuthenticated.value) {
    await initSessions()
  }
  document.title = brand.appTitle
  ready.value = true
})

onUnmounted(() => {
  document.removeEventListener('keydown', handleKeydown)
})

watch(
  () => isAuthenticated.value && !isAuthPage.value,
  async (shouldInit, oldVal) => {
    if (shouldInit && !oldVal) {
      await initSessions()
    }
  }
)

watch(() => brand.appTitle, (title) => {
  document.title = title
})
</script>

<template>
  <TooltipProvider :delay-duration="300">
    <div class="h-screen w-screen flex flex-col bg-background overflow-hidden relative">
      <template v-if="!bootstrapReady || bootstrapFailed">
        <div class="flex-1 flex items-center justify-center p-6">
          <div class="w-full max-w-md rounded-lg border bg-card p-6 text-center shadow-sm">
            <div class="mx-auto mb-4 flex size-11 items-center justify-center rounded-full bg-muted">
              <AlertTriangle v-if="bootstrapFailed" class="size-5 text-destructive" />
              <Loader2 v-else-if="bootstrap.status === 'migrating' || bootstrap.status === 'checking'" class="size-5 animate-spin text-muted-foreground" />
              <Database v-else class="size-5 text-muted-foreground" />
            </div>
            <h1 class="text-base font-semibold">{{ bootstrapTitle }}</h1>
            <p class="mt-2 text-sm text-muted-foreground">{{ bootstrapDescription }}</p>
            <div v-if="bootstrap.currentRevision || bootstrap.targetRevision" class="mt-4 rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
              {{ t('bootstrap.revisionLabel', { current: bootstrap.currentRevision || '-', target: bootstrap.targetRevision || '-' }) }}
            </div>
            <p v-if="bootstrap.error" class="mt-4 break-words rounded-md bg-destructive/10 px-3 py-2 text-left text-xs text-destructive">
              {{ bootstrap.error }}
            </p>
            <p v-if="bootstrapFailed" class="mt-3 text-xs text-muted-foreground">{{ t('bootstrap.logsHint') }}</p>
          </div>
        </div>
      </template>
      <template v-else-if="isAuthPage">
        <router-view />
      </template>
      <template v-else-if="ready">
        <AppHeader />
        <main class="flex-1 flex flex-col overflow-hidden min-w-0 relative">
          <router-view />
        </main>
      </template>
      <template v-else>
        <div class="flex-1 flex items-center justify-center">
          <Loader2 class="size-6 animate-spin text-muted-foreground" />
        </div>
      </template>
      
      <!-- 全局组件 -->
      <Toaster position="top-center" />
    </div>
  </TooltipProvider>
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
body.resizing-panel,
body.resizing-panel * {
  cursor: row-resize !important;
  user-select: none !important;
}
</style>
