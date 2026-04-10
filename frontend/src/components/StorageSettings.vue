<script setup lang="ts">
import { reactive, ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

const form = reactive({
  storage: 'builtin',
  s3Bucket: '',
  s3Region: '',
  s3AccessKey: '',
  s3SecretKey: '',
  s3Endpoint: '',
  s3Presign: true,
  s3PresignExpires: 3600,
})

const saving = ref(false)
const saved = ref(false)

onMounted(async () => {
  try {
    const res = await fetch('/api/settings/storage')
    if (res.ok) {
      const data = await res.json()
      Object.assign(form, data)
    }
  } catch { /* keep defaults */ }
})

async function saveSettings() {
  saving.value = true
  saved.value = false
  try {
    const res = await fetch('/api/settings/storage', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(form),
    })
    if (res.ok) {
      saved.value = true
      setTimeout(() => { saved.value = false }, 2000)
    }
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="bg-[var(--color-surface)] p-6 rounded-lg border border-[var(--color-border)]">
    <h3 class="text-base font-semibold mb-4 text-[var(--color-text)]">{{ t('settings.fileStorage') }}</h3>

    <!-- Storage mode -->
    <div class="mb-5">
      <label class="block text-sm font-medium text-[var(--color-text-dim)] mb-2">{{ t('settings.storageMode') }}</label>
      <div class="flex gap-4">
        <label class="flex items-center gap-2 cursor-pointer">
          <input type="radio" value="s3" v-model="form.storage" class="accent-[var(--color-accent)]" />
          <span class="text-sm text-[var(--color-text)]">{{ t('settings.s3') }}</span>
        </label>
        <label class="flex items-center gap-2 cursor-pointer">
          <input type="radio" value="builtin" v-model="form.storage" class="accent-[var(--color-accent)]" />
          <span class="text-sm text-[var(--color-text)]">{{ t('settings.builtin') }}</span>
        </label>
      </div>
    </div>

    <!-- S3 config -->
    <div v-if="form.storage === 's3'" class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-5">
      <div>
        <label class="block text-xs font-medium text-[var(--color-text-dim)] mb-1">{{ t('settings.s3Bucket') }}</label>
        <input v-model="form.s3Bucket" type="text" class="w-full px-3 py-2 rounded-lg text-sm bg-[var(--color-bg)] border border-[var(--color-border)] text-[var(--color-text)] focus:outline-none focus:border-[var(--color-accent)]" />
      </div>
      <div>
        <label class="block text-xs font-medium text-[var(--color-text-dim)] mb-1">{{ t('settings.s3Region') }}</label>
        <input v-model="form.s3Region" type="text" class="w-full px-3 py-2 rounded-lg text-sm bg-[var(--color-bg)] border border-[var(--color-border)] text-[var(--color-text)] focus:outline-none focus:border-[var(--color-accent)]" />
      </div>
      <div>
        <label class="block text-xs font-medium text-[var(--color-text-dim)] mb-1">{{ t('settings.s3AccessKey') }}</label>
        <input v-model="form.s3AccessKey" type="text" class="w-full px-3 py-2 rounded-lg text-sm bg-[var(--color-bg)] border border-[var(--color-border)] text-[var(--color-text)] focus:outline-none focus:border-[var(--color-accent)]" />
      </div>
      <div>
        <label class="block text-xs font-medium text-[var(--color-text-dim)] mb-1">{{ t('settings.s3SecretKey') }}</label>
        <input v-model="form.s3SecretKey" type="password" class="w-full px-3 py-2 rounded-lg text-sm bg-[var(--color-bg)] border border-[var(--color-border)] text-[var(--color-text)] focus:outline-none focus:border-[var(--color-accent)]" />
      </div>
      <div class="md:col-span-2">
        <label class="block text-xs font-medium text-[var(--color-text-dim)] mb-1">{{ t('settings.s3Endpoint') }}</label>
        <input v-model="form.s3Endpoint" type="text" placeholder="https://s3.example.com" class="w-full px-3 py-2 rounded-lg text-sm bg-[var(--color-bg)] border border-[var(--color-border)] text-[var(--color-text)] placeholder:text-[var(--color-text-dim)]/40 focus:outline-none focus:border-[var(--color-accent)]" />
      </div>
      <div class="md:col-span-2">
        <label class="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" v-model="form.s3Presign" class="accent-[var(--color-accent)]" />
          <span class="text-sm text-[var(--color-text)]">{{ t('settings.s3Presign') }}</span>
        </label>
      </div>
    </div>

    <!-- Builtin description -->
    <div v-if="form.storage === 'builtin'" class="mb-5">
      <p class="text-xs text-[var(--color-text-dim)] leading-relaxed">{{ t('settings.builtinDesc') }}</p>
    </div>

    <!-- S3 description -->
    <div v-if="form.storage === 's3'" class="mb-5">
      <p class="text-xs text-[var(--color-text-dim)] leading-relaxed">{{ t('settings.s3Desc') }}</p>
    </div>

    <button
      @click="saveSettings"
      :disabled="saving"
      class="px-4 py-2 rounded-lg text-sm font-medium bg-[var(--color-accent)] text-white hover:opacity-90 transition-opacity disabled:opacity-50"
    >
      {{ saved ? t('settings.saved') : t('settings.save') }}
    </button>
  </div>
</template>
