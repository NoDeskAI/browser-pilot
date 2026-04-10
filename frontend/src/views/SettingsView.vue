<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import StorageSettings from '../components/StorageSettings.vue'

const { t } = useI18n()
</script>

<template>
  <div class="flex-1 overflow-y-auto p-6">
    <div class="max-w-2xl mx-auto space-y-6">
      <h2 class="text-xl font-bold text-[var(--color-text)]">{{ t('settings.title') }}</h2>
      <StorageSettings />
    </div>
  </div>
</template>
