<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { useAuth } from '../composables/useAuth'
import { useSessions } from '../composables/useSessions'
import { setLocale, getLocale } from '../i18n'
import { Loader2 } from 'lucide-vue-next'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

const { t } = useI18n()
const router = useRouter()
const { setAuth } = useAuth()
const { brand } = useSessions()

const email = ref('')
const password = ref('')
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
    setAuth(data.access_token, data.user)
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
            id="email" v-model="email" type="email"
            :placeholder="t('auth.emailPlaceholder')"
            required autofocus
          />
        </div>
        <div class="space-y-2">
          <Label for="password">{{ t('auth.password') }}</Label>
          <Input
            id="password" v-model="password" type="password"
            :placeholder="t('auth.passwordPlaceholder')"
            required
          />
        </div>

        <div v-if="error" class="text-sm text-destructive">{{ error }}</div>

        <Button type="submit" class="w-full" :disabled="loading">
          <Loader2 v-if="loading" class="size-4 mr-2 animate-spin" />
          {{ loading ? t('auth.loggingIn') : t('auth.login') }}
        </Button>
      </form>

      <div class="mt-6 text-center">
        <button @click="toggleLocale" class="text-xs text-muted-foreground hover:text-foreground transition-colors">
          {{ getLocale() === 'zh' ? 'English' : '中文' }}
        </button>
      </div>
    </div>
  </div>
</template>
