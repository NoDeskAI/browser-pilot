<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSessions } from './composables/useSessions'
import { useAuth } from './composables/useAuth'
import AppHeader from './components/AppHeader.vue'
import { Toaster } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'
import { Loader2 } from 'lucide-vue-next'

const route = useRoute()
const router = useRouter()
const { brand, init: initSessions, createSession } = useSessions()

const { isAuthenticated, fetchMe } = useAuth()

const isAuthPage = computed(() => route.path === '/login' || route.path === '/setup')
const ready = ref(false)

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
  if (isAuthenticated.value) {
    await fetchMe()
  }
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
      <template v-if="isAuthPage">
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
