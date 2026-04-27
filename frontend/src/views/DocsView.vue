<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

const { t } = useI18n()
const route = useRoute()
</script>

<template>
  <div class="flex min-w-0 flex-1 overflow-hidden">
    <aside class="w-[200px] shrink-0 border-r border-border overflow-y-auto p-4 pt-6">
      <h2 class="text-sm font-semibold mb-4 px-3">{{ t('docs.title') }}</h2>
      <nav class="flex flex-col gap-1">
        <RouterLink to="/docs/cli"
          class="px-3 py-2 text-sm rounded-md transition-colors text-left"
          :class="route.path === '/docs/cli' ? 'bg-accent text-accent-foreground font-medium' : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground'"
        >{{ t('docs.cliManualAccess') }}</RouterLink>
        <RouterLink to="/docs/cli-agent"
          class="px-3 py-2 text-sm rounded-md transition-colors text-left"
          :class="route.path === '/docs/cli-agent' ? 'bg-accent text-accent-foreground font-medium' : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground'"
        >{{ t('docs.cliAgentAccess') }}</RouterLink>
        <RouterLink to="/docs/api-token"
          class="px-3 py-2 text-sm rounded-md transition-colors text-left"
          :class="route.path === '/docs/api-token' ? 'bg-accent text-accent-foreground font-medium' : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground'"
        >API Token</RouterLink>
      </nav>
    </aside>
    <div class="flex-1 min-w-0 overflow-x-hidden overflow-y-auto">
      <router-view />
    </div>
  </div>
</template>
