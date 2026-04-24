<script setup lang="ts">
import { ref, defineAsyncComponent, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { useAuth } from '../composables/useAuth'
import { useSessions } from '../composables/useSessions'
import { setLocale, getLocale } from '../i18n'
import { Loader2 } from 'lucide-vue-next'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

const isEE = __EE__
const SsoLoginButton = isEE
  ? defineAsyncComponent(() => import('@ee/components/SsoLoginButton.vue'))
  : null

const { t } = useI18n()
const router = useRouter()
const { setAuth } = useAuth()
const { brand, fetchBrand } = useSessions()

onMounted(() => fetchBrand())

const _SAVED_EMAIL_KEY = 'saved_email'
const savedEmail = localStorage.getItem(_SAVED_EMAIL_KEY) ?? ''
const email = ref(savedEmail)
const password = ref('')
const rememberMe = ref(!!savedEmail || true)
const loading = ref(false)
const error = ref('')

async function handleLogin() {
  error.value = ''
  loading.value = true
  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email.value, password: password.value }),
    })
    if (!res.ok) {
      error.value = t('auth.loginError')
      return
    }
    const data = await res.json()
    if (rememberMe.value) {
      localStorage.setItem(_SAVED_EMAIL_KEY, email.value)
    } else {
      localStorage.removeItem(_SAVED_EMAIL_KEY)
    }
    setAuth(data.access_token, data.user, rememberMe.value)
    router.push('/')
  } catch {
    error.value = t('auth.loginError')
  } finally {
    loading.value = false
  }
}

function toggleLocale() {
  setLocale(getLocale() === 'zh' ? 'en' : 'zh')
}
</script>

<template>
  <div class="min-h-screen flex items-center justify-center px-4">
    <div class="w-full max-w-[340px]">
      <div class="mb-8">
        <h1 class="text-2xl font-bold tracking-tight">{{ brand.appTitle }}</h1>
        <p class="mt-2 text-sm text-muted-foreground">{{ t('auth.loginSubtitle') }}</p>
      </div>

      <form @submit.prevent="handleLogin" class="space-y-4">
        <div class="space-y-2">
          <Label for="email">{{ t('auth.email') }}</Label>
          <Input
            id="email" v-model="email" type="email" autocomplete="email"
            :placeholder="t('auth.emailPlaceholder')"
            required autofocus
          />
        </div>
        <div class="space-y-2">
          <Label for="password">{{ t('auth.password') }}</Label>
          <Input
            id="password" v-model="password" type="password" autocomplete="current-password"
            :placeholder="t('auth.passwordPlaceholder')"
            required
          />
        </div>

        <label for="rememberMe" class="flex items-center gap-2 select-none cursor-pointer">
          <input
            id="rememberMe" v-model="rememberMe" type="checkbox"
            class="size-4 rounded border-border accent-primary cursor-pointer"
          />
          <span class="text-sm text-muted-foreground">{{ t('auth.rememberMe') }}</span>
        </label>

        <div v-if="error" class="text-sm text-destructive">{{ error }}</div>

        <Button type="submit" class="w-full" :disabled="loading">
          <Loader2 v-if="loading" class="size-4 mr-2 animate-spin" />
          {{ loading ? t('auth.loggingIn') : t('auth.login') }}
        </Button>
      </form>

      <component v-if="isEE && brand.features.sso && SsoLoginButton" :is="SsoLoginButton" />

      <div class="mt-6 text-center">
        <button @click="toggleLocale" class="text-xs text-muted-foreground hover:text-foreground transition-colors">
          {{ getLocale() === 'zh' ? 'English' : '中文' }}
        </button>
      </div>
    </div>
  </div>
</template>
