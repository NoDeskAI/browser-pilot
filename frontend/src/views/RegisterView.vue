<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { Loader2 } from 'lucide-vue-next'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useAuth } from '../composables/useAuth'
import { useSessions } from '../composables/useSessions'
import { setLocale, getLocale } from '../i18n'
import { useNotify } from '@/composables/useNotify'

const { t } = useI18n()
const router = useRouter()
const { setAuth } = useAuth()
const { brand, fetchBrand } = useSessions()
const notify = useNotify()

onMounted(() => fetchBrand())

const tenantName = ref('')
const name = ref('')
const email = ref('')
const password = ref('')
const confirmPassword = ref('')
const rememberMe = ref(false)
const loading = ref(false)
const error = ref('')

async function readErrorDetail(res: Response) {
  const contentType = res.headers.get('content-type') || ''
  if (!contentType.includes('application/json')) return ''
  try {
    const data = await res.json()
    return typeof data?.detail === 'string' ? data.detail : ''
  } catch {
    return ''
  }
}

function registerErrorMessage(status: number, detail: string) {
  if (status === 409 && detail === 'email_already_registered') {
    return t('auth.registerEmailExists')
  }
  if (status === 429) return t('auth.registerRateLimited')
  if (status === 400) return t('auth.registerValidationError')
  return t('auth.registerError')
}

async function handleRegister() {
  error.value = ''
  if (password.value !== confirmPassword.value) {
    error.value = t('auth.passwordMismatch')
    return
  }
  loading.value = true
  try {
    const res = await fetch('/api/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        tenantName: tenantName.value,
        name: name.value,
        email: email.value,
        password: password.value,
        rememberMe: rememberMe.value,
      }),
    })
    if (!res.ok) {
      error.value = registerErrorMessage(res.status, await readErrorDetail(res))
      return
    }
    const data = await res.json()
    setAuth(data.access_token, data.user)
    await router.push('/')
  } catch {
    notify.error(t('auth.loginNetworkError'))
  } finally {
    loading.value = false
  }
}

function toggleLocale() {
  setLocale(getLocale() === 'zh' ? 'en' : 'zh')
}
</script>

<template>
  <div class="min-h-screen flex items-center justify-center px-4 py-10">
    <div class="w-full max-w-[380px]">
      <div class="mb-8 flex items-start gap-3">
        <img
          src="/brand/browser-pilot.svg"
          alt=""
          class="mt-0.5 size-11 shrink-0 object-contain mix-blend-multiply dark:invert dark:mix-blend-screen"
        />
        <div class="min-w-0">
          <h1 class="text-2xl font-bold tracking-tight">{{ brand.appTitle }}</h1>
          <p class="mt-2 text-sm text-muted-foreground">{{ t('auth.registerSubtitle') }}</p>
        </div>
      </div>

      <form @submit.prevent="handleRegister" class="space-y-4">
        <div class="space-y-2">
          <Label for="tenantName">{{ t('auth.tenantName') }}</Label>
          <Input id="tenantName" v-model="tenantName" type="text" :placeholder="t('auth.tenantNamePlaceholder')" required autofocus />
        </div>
        <div class="space-y-2">
          <Label for="name">{{ t('auth.adminName') }}</Label>
          <Input id="name" v-model="name" type="text" :placeholder="t('auth.adminNamePlaceholder')" required />
        </div>
        <div class="space-y-2">
          <Label for="email">{{ t('auth.email') }}</Label>
          <Input id="email" v-model="email" type="email" :placeholder="t('auth.emailPlaceholder')" required />
        </div>
        <div class="space-y-2">
          <Label for="password">{{ t('auth.password') }}</Label>
          <Input id="password" v-model="password" type="password" :placeholder="t('auth.passwordPlaceholder')" required minlength="6" />
        </div>
        <div class="space-y-2">
          <Label for="confirmPassword">{{ t('auth.confirmPassword') }}</Label>
          <Input id="confirmPassword" v-model="confirmPassword" type="password" :placeholder="t('auth.confirmPasswordPlaceholder')" required minlength="6" />
        </div>

        <label for="rememberMe" class="flex items-center gap-2 select-none cursor-pointer">
          <input
            id="rememberMe" v-model="rememberMe" type="checkbox"
            class="size-4 rounded border-border accent-primary cursor-pointer"
          />
          <span class="text-sm text-muted-foreground">{{ t('auth.rememberMe', { days: brand.auth.rememberMeDays }) }}</span>
        </label>

        <div v-if="error" class="text-sm text-destructive">{{ error }}</div>

        <Button type="submit" class="w-full" :disabled="loading">
          <Loader2 v-if="loading" class="size-4 mr-2 animate-spin" />
          {{ loading ? t('auth.registering') : t('auth.register') }}
        </Button>
      </form>

      <div class="mt-4 text-center text-sm text-muted-foreground">
        {{ t('auth.haveAccount') }}
        <RouterLink to="/login" class="font-medium text-foreground hover:underline">
          {{ t('auth.login') }}
        </RouterLink>
      </div>

      <div class="mt-6 text-center">
        <button @click="toggleLocale" class="text-xs text-muted-foreground hover:text-foreground transition-colors">
          {{ getLocale() === 'zh' ? 'English' : '中文' }}
        </button>
      </div>
    </div>
  </div>
</template>
