<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAuth } from '../composables/useAuth'
import { api } from '../lib/api'
import { Loader2, Plus, Trash2, Copy, Check, Key, BookOpen } from 'lucide-vue-next'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { toast } from 'vue-sonner'

const { t, locale } = useI18n()
const { user, fetchMe } = useAuth()

// ---- API Tokens ----
interface ApiToken {
  id: string
  name: string
  createdAt: string
  lastUsedAt: string | null
  sessionId: string | null
  sessionName: string | null
}

const tokens = ref<ApiToken[]>([])
const tokensLoading = ref(false)
const createDialogOpen = ref(false)
const newTokenName = ref('')
const createLoading = ref(false)
const createdToken = ref<string | null>(null)
const tokenCopied = ref(false)
const deleteTarget = ref<ApiToken | null>(null)
const deleteLoading = ref(false)

async function fetchTokens() {
  tokensLoading.value = true
  try {
    const res = await api('/api/auth/tokens')
    if (res.ok) {
      const data = await res.json()
      tokens.value = data.tokens
    }
  } catch { /* ignore */ } finally {
    tokensLoading.value = false
  }
}

async function handleCreateToken() {
  if (!newTokenName.value.trim()) return
  createLoading.value = true
  try {
    const res = await api('/api/auth/tokens', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: newTokenName.value.trim() }),
    })
    if (!res.ok) {
      toast.error(t('account.tokenCreateError'))
      return
    }
    const data = await res.json()
    createdToken.value = data.token
    await fetchTokens()
  } catch {
    toast.error(t('account.tokenCreateError'))
  } finally {
    createLoading.value = false
  }
}

function handleCreateDialogClose() {
  createDialogOpen.value = false
  createdToken.value = null
  newTokenName.value = ''
  tokenCopied.value = false
}

async function copyToken() {
  if (!createdToken.value) return
  await navigator.clipboard.writeText(createdToken.value)
  tokenCopied.value = true
  toast.success(t('session.copied'))
  setTimeout(() => { tokenCopied.value = false }, 2000)
}

async function handleDeleteToken() {
  if (!deleteTarget.value) return
  deleteLoading.value = true
  try {
    const res = await api(`/api/auth/tokens/${deleteTarget.value.id}`, { method: 'DELETE' })
    if (!res.ok) {
      toast.error(t('account.tokenDeleteError'))
      return
    }
    toast.success(t('account.tokenDeleted'))
    await fetchTokens()
  } catch {
    toast.error(t('account.tokenDeleteError'))
  } finally {
    deleteLoading.value = false
    deleteTarget.value = null
  }
}

onMounted(fetchTokens)

// ---- Profile Form ----
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

        <!-- API Tokens Card -->
        <Card>
          <CardHeader>
            <div class="flex items-center justify-between">
              <div>
                <CardTitle>{{ t('account.tokensTitle') }}</CardTitle>
                <CardDescription>{{ t('account.tokensDescription') }}</CardDescription>
              </div>
              <div class="flex items-center gap-2">
                <RouterLink to="/docs/api-token">
                  <Button variant="outline" size="sm">
                    <BookOpen class="size-4 mr-1.5" />
                    {{ t('account.tokenDocs') }}
                  </Button>
                </RouterLink>
                <Button size="sm" @click="createDialogOpen = true">
                  <Plus class="size-4 mr-1.5" />
                  {{ t('account.tokenCreate') }}
                </Button>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            <div v-if="tokensLoading" class="flex justify-center py-6">
              <Loader2 class="size-5 animate-spin text-muted-foreground" />
            </div>
            <div v-else-if="tokens.length === 0" class="text-center py-6 text-sm text-muted-foreground">
              {{ t('account.tokensEmpty') }}
            </div>
            <div v-else class="divide-y divide-border">
              <div v-for="tk in tokens" :key="tk.id" class="flex items-center justify-between py-3 first:pt-0 last:pb-0">
                <div class="space-y-0.5 min-w-0">
                  <div class="flex items-center gap-2">
                    <Key class="size-3.5 text-muted-foreground shrink-0" />
                    <span class="text-sm font-medium truncate">{{ tk.name }}</span>
                    <Badge v-if="tk.sessionName" variant="secondary" class="text-[10px] px-1.5 font-normal shrink-0">
                      {{ t('account.tokenSessionBadge') }}: {{ tk.sessionName }}
                    </Badge>
                  </div>
                  <div class="text-xs text-muted-foreground pl-5.5">
                    {{ t('account.tokenCreated') }} {{ formatDate(tk.createdAt) }}
                    <template v-if="tk.lastUsedAt"> · {{ t('account.tokenLastUsed') }} {{ formatDate(tk.lastUsedAt) }}</template>
                  </div>
                </div>
                <Button variant="ghost" size="icon" class="shrink-0 text-destructive hover:text-destructive hover:bg-destructive/10" @click="deleteTarget = tk">
                  <Trash2 class="size-4" />
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>

        <!-- Create Token Dialog -->
        <Dialog :open="createDialogOpen" @update:open="(v: boolean) => { if (!v) handleCreateDialogClose() }">
          <DialogContent class="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>{{ t('account.tokenCreate') }}</DialogTitle>
              <DialogDescription>{{ t('account.tokenCreateHint') }}</DialogDescription>
            </DialogHeader>
            <template v-if="!createdToken">
              <form @submit.prevent="handleCreateToken" class="space-y-4">
                <div class="space-y-2">
                  <Label>{{ t('account.tokenName') }}</Label>
                  <Input v-model="newTokenName" :placeholder="t('account.tokenNamePlaceholder')" required autofocus />
                </div>
                <DialogFooter>
                  <Button type="button" variant="outline" @click="handleCreateDialogClose">{{ t('session.cancel') }}</Button>
                  <Button type="submit" :disabled="createLoading || !newTokenName.trim()">
                    <Loader2 v-if="createLoading" class="size-4 mr-2 animate-spin" />
                    {{ t('account.tokenCreate') }}
                  </Button>
                </DialogFooter>
              </form>
            </template>
            <template v-else>
              <div class="space-y-3">
                <p class="text-sm text-amber-500 dark:text-amber-400 font-medium">{{ t('account.tokenCopyWarning') }}</p>
                <div class="flex items-center gap-2">
                  <code class="flex-1 bg-muted px-3 py-2 rounded text-xs font-mono break-all select-all">{{ createdToken }}</code>
                  <Button variant="outline" size="icon" class="shrink-0" @click="copyToken">
                    <Check v-if="tokenCopied" class="size-4 text-green-500" />
                    <Copy v-else class="size-4" />
                  </Button>
                </div>
              </div>
              <DialogFooter>
                <Button @click="handleCreateDialogClose">{{ t('account.tokenDone') }}</Button>
              </DialogFooter>
            </template>
          </DialogContent>
        </Dialog>

        <!-- Delete Token Confirm -->
        <AlertDialog :open="!!deleteTarget" @update:open="(v: boolean) => { if (!v) deleteTarget = null }">
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>{{ t('account.tokenDeleteConfirm') }}</AlertDialogTitle>
              <AlertDialogDescription>{{ t('account.tokenDeleteDescription', { name: deleteTarget?.name }) }}</AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel :disabled="deleteLoading">{{ t('session.cancel') }}</AlertDialogCancel>
              <AlertDialogAction :disabled="deleteLoading" class="bg-destructive text-destructive-foreground hover:bg-destructive/90" @click="handleDeleteToken">
                <Loader2 v-if="deleteLoading" class="size-4 mr-2 animate-spin" />
                {{ t('session.confirmDelete') }}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>

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
