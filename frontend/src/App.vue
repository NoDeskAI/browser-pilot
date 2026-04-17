<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSessions } from './composables/useSessions'
import { useAuth } from './composables/useAuth'
import AppHeader from './components/AppHeader.vue'
import CliDocModal from './components/CliDocModal.vue'
import { Toaster } from '@/components/ui/sonner'

const route = useRoute()
const router = useRouter()
const { brand, init: initSessions, createSession } = useSessions()

const { isAuthenticated, fetchMe } = useAuth()

const isAuthPage = computed(() => route.path === '/login' || route.path === '/setup')
const cliDocOpen = ref(false)

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
})

onUnmounted(() => {
  document.removeEventListener('keydown', handleKeydown)
})

watch(() => brand.appTitle, (title) => {
  document.title = title
})
</script>

<template>
  <div class="h-screen w-screen flex flex-col bg-background overflow-hidden relative">
    <template v-if="isAuthPage">
      <router-view />
    </template>
    <template v-else>
      <AppHeader @open-cli-docs="cliDocOpen = true" />
      <main class="flex-1 flex flex-col overflow-hidden min-w-0 relative">
        <router-view />
      </main>
      <CliDocModal :open="cliDocOpen" @close="cliDocOpen = false" />
    </template>
    
    <!-- 全局组件 -->
    <Toaster position="top-center" />
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
body.resizing-panel,
body.resizing-panel * {
  cursor: row-resize !important;
  user-select: none !important;
}
</style>
