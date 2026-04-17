<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAuth } from '../composables/useAuth'
import { api } from '../lib/api'
import { Trash2, UserPlus, Loader2, KeyRound } from 'lucide-vue-next'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Switch } from '@/components/ui/switch'
import { toast } from 'vue-sonner'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger,
} from '@/components/ui/dialog'
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'

const { t } = useI18n()
const { user: currentUser } = useAuth()

interface UserItem { id: string; email: string; name: string; role: string; isActive: boolean; createdAt: string }

const users = ref<UserItem[]>([])
const showInvite = ref(false)
const inviteEmail = ref('')
const inviteName = ref('')
const invitePassword = ref('')
const inviteRole = ref('member')
const inviteError = ref('')
const inviteLoading = ref(false)

const showResetPassword = ref(false)
const resetTargetUser = ref<UserItem | null>(null)
const resetPasswordValue = ref('')
const resetPasswordLoading = ref(false)
const resetPasswordError = ref('')

async function fetchUsers() {
  try { const res = await api('/api/users'); const data = await res.json(); users.value = data.users || [] } catch {}
}

async function handleInvite() {
  inviteError.value = ''
  inviteLoading.value = true
  try {
    const res = await api('/api/users', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: inviteEmail.value, name: inviteName.value, password: invitePassword.value, role: inviteRole.value }),
    })
    if (!res.ok) { const data = await res.json(); inviteError.value = data.detail || t('users.inviteError'); return }
    showInvite.value = false; inviteEmail.value = ''; inviteName.value = ''; invitePassword.value = ''; inviteRole.value = 'member'
    await fetchUsers()
  } catch { inviteError.value = t('users.inviteError') } finally { inviteLoading.value = false }
}

async function toggleActive(u: UserItem) {
  await api(`/api/users/${u.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ is_active: !u.isActive }) })
  await fetchUsers()
}

async function deleteUser(u: UserItem) {
  await api(`/api/users/${u.id}`, { method: 'DELETE' }); await fetchUsers()
}

function openResetPassword(u: UserItem) {
  resetTargetUser.value = u
  resetPasswordValue.value = ''
  resetPasswordError.value = ''
  showResetPassword.value = true
}

async function handleResetPassword() {
  if (!resetTargetUser.value || resetPasswordValue.value.length < 6) return
  resetPasswordLoading.value = true
  resetPasswordError.value = ''
  try {
    const res = await api(`/api/users/${resetTargetUser.value.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: resetPasswordValue.value }),
    })
    if (!res.ok) {
      const data = await res.json()
      resetPasswordError.value = data.detail || t('users.resetPasswordError')
      return
    }
    showResetPassword.value = false
    toast.success(t('users.resetPasswordSuccess'))
  } catch {
    resetPasswordError.value = t('users.resetPasswordError')
  } finally {
    resetPasswordLoading.value = false
  }
}

function roleBadgeVariant(role: string) {
  if (role === 'superadmin') return 'default' as const
  if (role === 'admin') return 'secondary' as const
  return 'outline' as const
}

onMounted(fetchUsers)
</script>

<template>
  <div class="flex-1 overflow-y-auto">
    <div class="max-w-2xl mx-auto px-6 py-8 space-y-6">
      <div class="flex items-center justify-between">
        <h2 class="text-lg font-semibold">{{ t('users.title') }}</h2>
        <Dialog v-model:open="showInvite">
          <DialogTrigger as-child>
            <Button size="sm" class="gap-1.5"><UserPlus class="size-4" /> {{ t('users.invite') }}</Button>
          </DialogTrigger>
          <DialogContent class="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>{{ t('users.invite') }}</DialogTitle>
              <DialogDescription>{{ t('users.inviteDescription') }}</DialogDescription>
            </DialogHeader>
            <form @submit.prevent="handleInvite" class="space-y-4 py-2">
              <div class="grid grid-cols-2 gap-4">
                <div class="space-y-2"><Label for="invite-email">{{ t('auth.email') }}</Label><Input id="invite-email" v-model="inviteEmail" type="email" required /></div>
                <div class="space-y-2"><Label for="invite-name">{{ t('auth.adminName') }}</Label><Input id="invite-name" v-model="inviteName" type="text" required /></div>
                <div class="space-y-2"><Label for="invite-password">{{ t('auth.password') }}</Label><Input id="invite-password" v-model="invitePassword" type="password" required minlength="6" /></div>
                <div class="space-y-2"><Label for="invite-role">{{ t('users.role') }}</Label><Select v-model="inviteRole"><SelectTrigger id="invite-role"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="member">{{ t('users.roleMember') }}</SelectItem><SelectItem value="admin">{{ t('users.roleAdmin') }}</SelectItem></SelectContent></Select></div>
              </div>
              <div v-if="inviteError" class="text-sm text-destructive">{{ inviteError }}</div>
              <DialogFooter>
                <Button type="submit" :disabled="inviteLoading || !inviteEmail || !inviteName || !invitePassword">
                  <Loader2 v-if="inviteLoading" class="size-4 mr-2 animate-spin" />
                  {{ inviteLoading ? '...' : t('users.invite') }}
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>

        <Dialog v-model:open="showResetPassword">
          <DialogContent class="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>{{ t('users.resetPassword') }}</DialogTitle>
              <DialogDescription>{{ t('users.resetPasswordDesc', { name: resetTargetUser?.name }) }}</DialogDescription>
            </DialogHeader>
            <form @submit.prevent="handleResetPassword" class="space-y-4 py-2">
              <div class="space-y-2">
                <Label for="reset-password">{{ t('account.newPassword') }}</Label>
                <Input id="reset-password" v-model="resetPasswordValue" type="password" required minlength="6" />
              </div>
              <div v-if="resetPasswordError" class="text-sm text-destructive">{{ resetPasswordError }}</div>
              <DialogFooter>
                <Button type="submit" :disabled="resetPasswordLoading || resetPasswordValue.length < 6">
                  <Loader2 v-if="resetPasswordLoading" class="size-4 mr-2 animate-spin" />
                  {{ resetPasswordLoading ? '...' : t('settings.save') }}
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>

      <div class="rounded-md border border-border overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{{ t('auth.email') }}</TableHead>
              <TableHead>{{ t('auth.adminName') }}</TableHead>
              <TableHead>{{ t('users.role') }}</TableHead>
              <TableHead>{{ t('users.status') }}</TableHead>
              <TableHead class="w-12" />
            </TableRow>
          </TableHeader>
          <TableBody>
            <TableRow v-for="u in users" :key="u.id">
              <TableCell class="font-medium">{{ u.email }}</TableCell>
              <TableCell>{{ u.name }}</TableCell>
              <TableCell><Badge :variant="roleBadgeVariant(u.role)">{{ u.role }}</Badge></TableCell>
              <TableCell>
                <div class="flex items-center gap-2">
                  <Switch v-if="u.role !== 'superadmin' && u.id !== currentUser?.id" :checked="u.isActive" @update:checked="toggleActive(u)" />
                  <span class="text-xs" :class="u.isActive ? 'text-green-500' : 'text-muted-foreground'">{{ u.isActive ? t('users.active') : t('users.disabled') }}</span>
                </div>
              </TableCell>
              <TableCell class="text-right">
                <div class="flex items-center justify-end gap-1">
                  <Button v-if="u.role !== 'superadmin' && u.id !== currentUser?.id" variant="ghost" size="sm" class="size-8 p-0 text-muted-foreground hover:text-foreground" @click="openResetPassword(u)" :title="t('users.resetPassword')">
                    <KeyRound class="size-3.5" />
                  </Button>
                  <AlertDialog v-if="u.role !== 'superadmin' && u.id !== currentUser?.id && currentUser?.role === 'superadmin'">
                    <AlertDialogTrigger as-child>
                      <Button variant="ghost" size="sm" class="size-8 p-0 text-muted-foreground hover:text-destructive"><Trash2 class="size-3.5" /></Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>{{ t('users.deleteConfirm', { name: u.name }) }}</AlertDialogTitle>
                        <AlertDialogDescription>{{ t('users.deleteDescription') }}</AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>{{ t('users.cancel') }}</AlertDialogCancel>
                        <AlertDialogAction variant="destructive" @click="deleteUser(u)">{{ t('users.confirmDelete') }}</AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                </div>
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </div>
    </div>
  </div>
</template>
