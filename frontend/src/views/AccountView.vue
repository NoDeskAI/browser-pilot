<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAuth } from '../composables/useAuth'
import { api } from '../lib/api'
import { Loader2 } from 'lucide-vue-next'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { toast } from 'vue-sonner'

const { t, locale } = useI18n()
const { user, fetchMe } = useAuth()

// Profile Form
const profileName = ref(user.value?.name || '')
const profileLoading = ref(false)

watch(() => user.value, (newVal) => {
  if (newVal) {
    profileName.value = newVal.name
  }
})

async function handleProfileSubmit() {
  if (!profileName.value.trim()) return

  profileLoading.value = true
  try {
    const res = await api('/api/account/profile', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: profileName.value.trim() }),
    })
    
    if (!res.ok) {
      const data = await res.json()
      toast.error(data.detail || t('account.updateProfileError'))
      return
    }
    
    await fetchMe()
    toast.success(t('account.profileUpdated'))
  } catch (e) {
    toast.error(t('account.updateProfileError'))
  } finally {
    profileLoading.value = false
  }
}

// Password Form
const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const passwordLoading = ref(false)

async function handlePasswordSubmit() {
  if (newPassword.value !== confirmPassword.value) {
    toast.error(t('auth.passwordMismatch'))
    return
  }
  
  if (newPassword.value.length < 6) {
    toast.error(t('account.passwordMinLength'))
    return
  }

  passwordLoading.value = true
  try {
    const res = await api('/api/account/change-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ 
        currentPassword: currentPassword.value, 
        newPassword: newPassword.value 
      }),
    })
    
    if (!res.ok) {
      const data = await res.json()
      toast.error(data.detail || t('account.updatePasswordError'))
      return
    }
    
    toast.success(t('account.passwordUpdated'))
    currentPassword.value = ''
    newPassword.value = ''
    confirmPassword.value = ''
  } catch (e) {
    toast.error(t('account.updatePasswordError'))
  } finally {
    passwordLoading.value = false
  }
}

function formatDate(dateStr?: string) {
  if (!dateStr) return '-'
  try {
    return new Intl.DateTimeFormat(locale.value === 'zh' ? 'zh-CN' : 'en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    }).format(new Date(dateStr))
  } catch {
    return dateStr
  }
}

function roleBadgeVariant(role?: string) {
  if (role === 'superadmin') return 'default' as const
  if (role === 'admin') return 'secondary' as const
  return 'outline' as const
}
</script>

<template>
  <div class="flex-1 overflow-y-auto">
    <div class="max-w-2xl mx-auto px-6 py-8 space-y-6">
      <h2 class="text-lg font-semibold">{{ t('auth.accountSettings') }}</h2>
      
      <div class="grid gap-6">
        <!-- Profile Card -->
        <form @submit.prevent="handleProfileSubmit">
          <Card>
            <CardHeader>
              <CardTitle>{{ t('account.profileTitle') }}</CardTitle>
              <CardDescription>{{ t('account.profileDescription') }}</CardDescription>
            </CardHeader>
            <CardContent class="space-y-4">
              <div class="space-y-2">
                <Label for="email">{{ t('auth.email') }}</Label>
                <Input id="email" :model-value="user?.email" disabled class="bg-muted/50" />
              </div>
              
              <div class="space-y-2">
                <Label for="name">{{ t('auth.adminName') }}</Label>
                <Input id="name" v-model="profileName" required />
              </div>
              
              <div class="grid grid-cols-2 gap-4">
                <div class="space-y-2">
                  <Label>{{ t('users.role') }}</Label>
                  <div class="h-8 flex items-center">
                    <Badge :variant="roleBadgeVariant(user?.role)">{{ user?.role }}</Badge>
                  </div>
                </div>
                <div class="space-y-2">
                  <Label>{{ t('account.createdAt') }}</Label>
                  <div class="h-8 flex items-center text-sm text-muted-foreground">
                    {{ formatDate(user?.createdAt) }}
                  </div>
                </div>
              </div>
            </CardContent>
            <CardFooter class="flex justify-end px-6 py-4">
              <Button type="submit" :disabled="profileLoading || !profileName || profileName === user?.name">
                <Loader2 v-if="profileLoading" class="size-4 mr-2 animate-spin" />
                {{ t('settings.save') }}
              </Button>
            </CardFooter>
          </Card>
        </form>

        <!-- Change Password Card -->
        <form @submit.prevent="handlePasswordSubmit">
          <Card>
            <CardHeader>
              <CardTitle>{{ t('account.passwordTitle') }}</CardTitle>
              <CardDescription>{{ t('account.passwordDescription') }}</CardDescription>
            </CardHeader>
            <CardContent class="space-y-4">
              <div class="space-y-2">
                <Label for="current-password">{{ t('account.currentPassword') }}</Label>
                <Input id="current-password" v-model="currentPassword" type="password" required />
              </div>
              
              <div class="space-y-2">
                <Label for="new-password">{{ t('account.newPassword') }}</Label>
                <Input id="new-password" v-model="newPassword" type="password" required minlength="6" />
              </div>
              
              <div class="space-y-2">
                <Label for="confirm-password">{{ t('auth.confirmPassword') }}</Label>
                <Input id="confirm-password" v-model="confirmPassword" type="password" required minlength="6" />
              </div>
            </CardContent>
            <CardFooter class="flex justify-end px-6 py-4">
              <Button type="submit" :disabled="passwordLoading || !currentPassword || !newPassword || !confirmPassword">
                <Loader2 v-if="passwordLoading" class="size-4 mr-2 animate-spin" />
                {{ t('account.updatePasswordBtn') }}
              </Button>
            </CardFooter>
          </Card>
        </form>
      </div>
    </div>
  </div>
</template>
