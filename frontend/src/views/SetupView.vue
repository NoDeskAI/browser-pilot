<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { useAuth } from '../composables/useAuth'
import { useSessions } from '../composables/useSessions'
import { invalidateSiteInfoCache } from '../router'

const { t } = useI18n()
const router = useRouter()
const { setAuth } = useAuth()
const { brand } = useSessions()

const tenantName = ref('')
const adminName = ref('')
const email = ref('')
const password = ref('')
const confirmPassword = ref('')
const loading = ref(false)
const error = ref('')

async function handleSetup() {
  error.value = ''
  if (password.value !== confirmPassword.value) {
    error.value = t('auth.passwordMismatch')
    return
  }
  loading.value = true
  try {
    const res = await fetch('/api/auth/setup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        tenantName: tenantName.value,
        name: adminName.value,
        email: email.value,
        password: password.value,
      }),
    })
    if (!res.ok) {
      error.value = t('auth.setupError')
      return
    }
    const data = await res.json()
    setAuth(data.access_token, data.user)
    invalidateSiteInfoCache()
    router.push('/')
  } catch {
    error.value = t('auth.setupError')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="min-h-screen flex items-center justify-center bg-[var(--color-bg)] px-4">
    <div class="w-full max-w-sm space-y-6">
      <div class="text-center">
        <h1 class="text-2xl font-bold text-[var(--color-text)]">{{ t('auth.setupTitle', { appTitle: brand.appTitle }) }}</h1>
        <p class="mt-2 text-sm text-[var(--color-text-dim)]">{{ t('auth.setupSubtitle') }}</p>
      </div>

      <form @submit.prevent="handleSetup" class="space-y-4">
        <div>
          <label class="block text-sm font-medium text-[var(--color-text-dim)] mb-1">{{ t('auth.tenantName') }}</label>
          <input
            v-model="tenantName"
            type="text"
            required
            autofocus
            class="w-full px-3 py-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)] focus:border-transparent"
          />
        </div>
        <div>
          <label class="block text-sm font-medium text-[var(--color-text-dim)] mb-1">{{ t('auth.adminName') }}</label>
          <input
            v-model="adminName"
            type="text"
            required
            class="w-full px-3 py-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)] focus:border-transparent"
          />
        </div>
        <div>
          <label class="block text-sm font-medium text-[var(--color-text-dim)] mb-1">{{ t('auth.email') }}</label>
          <input
            v-model="email"
            type="email"
            required
            class="w-full px-3 py-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)] focus:border-transparent"
          />
        </div>
        <div>
          <label class="block text-sm font-medium text-[var(--color-text-dim)] mb-1">{{ t('auth.password') }}</label>
          <input
            v-model="password"
            type="password"
            required
            minlength="6"
            class="w-full px-3 py-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)] focus:border-transparent"
          />
        </div>
        <div>
          <label class="block text-sm font-medium text-[var(--color-text-dim)] mb-1">{{ t('auth.confirmPassword') }}</label>
          <input
            v-model="confirmPassword"
            type="password"
            required
            class="w-full px-3 py-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)] focus:border-transparent"
          />
        </div>

        <div v-if="error" class="text-sm text-red-500 text-center">{{ error }}</div>

        <button
          type="submit"
          :disabled="loading"
          class="w-full py-2.5 rounded-lg bg-[var(--color-accent)] text-white font-medium hover:opacity-90 transition-opacity disabled:opacity-50"
        >
          {{ loading ? t('auth.settingUp') : t('auth.completeSetup') }}
        </button>
      </form>
    </div>
  </div>
</template>
