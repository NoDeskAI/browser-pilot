<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAuth } from '../composables/useAuth'
import { api } from '../lib/api'
import { Trash2, UserPlus } from 'lucide-vue-next'

const { t } = useI18n()
const { user: currentUser } = useAuth()

interface UserItem {
  id: string
  email: string
  name: string
  role: string
  isActive: boolean
  createdAt: string
}

const users = ref<UserItem[]>([])
const showInvite = ref(false)
const inviteEmail = ref('')
const inviteName = ref('')
const invitePassword = ref('')
const inviteRole = ref('member')
const inviteError = ref('')
const inviteLoading = ref(false)

async function fetchUsers() {
  try {
    const res = await api('/api/users')
    const data = await res.json()
    users.value = data.users || []
  } catch {
    // ignore
  }
}

async function handleInvite() {
  inviteError.value = ''
  inviteLoading.value = true
  try {
    const res = await api('/api/users', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: inviteEmail.value,
        name: inviteName.value,
        password: invitePassword.value,
        role: inviteRole.value,
      }),
    })
    if (!res.ok) {
      const data = await res.json()
      inviteError.value = data.detail || t('users.inviteError')
      return
    }
    showInvite.value = false
    inviteEmail.value = ''
    inviteName.value = ''
    invitePassword.value = ''
    inviteRole.value = 'member'
    await fetchUsers()
  } catch {
    inviteError.value = t('users.inviteError')
  } finally {
    inviteLoading.value = false
  }
}

async function toggleActive(u: UserItem) {
  await api(`/api/users/${u.id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ is_active: !u.isActive }),
  })
  await fetchUsers()
}

async function deleteUser(u: UserItem) {
  if (!confirm(t('users.deleteConfirm', { name: u.name }))) return
  await api(`/api/users/${u.id}`, { method: 'DELETE' })
  await fetchUsers()
}

onMounted(fetchUsers)
</script>

<template>
  <div class="flex-1 overflow-y-auto p-6">
    <div class="max-w-2xl mx-auto space-y-6">
      <div class="flex items-center justify-between">
        <h2 class="text-xl font-bold text-[var(--color-text)]">{{ t('users.title') }}</h2>
        <button
          @click="showInvite = !showInvite"
          class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[var(--color-accent)] text-white text-sm font-medium hover:opacity-90 transition-opacity"
        >
          <UserPlus class="w-4 h-4" />
          {{ t('users.invite') }}
        </button>
      </div>

      <div v-if="showInvite" class="p-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] space-y-3">
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="block text-xs font-medium text-[var(--color-text-dim)] mb-1">{{ t('auth.email') }}</label>
            <input v-model="inviteEmail" type="email" required class="w-full px-3 py-1.5 text-sm rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]" />
          </div>
          <div>
            <label class="block text-xs font-medium text-[var(--color-text-dim)] mb-1">{{ t('auth.adminName') }}</label>
            <input v-model="inviteName" type="text" required class="w-full px-3 py-1.5 text-sm rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]" />
          </div>
          <div>
            <label class="block text-xs font-medium text-[var(--color-text-dim)] mb-1">{{ t('auth.password') }}</label>
            <input v-model="invitePassword" type="password" required minlength="6" class="w-full px-3 py-1.5 text-sm rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]" />
          </div>
          <div>
            <label class="block text-xs font-medium text-[var(--color-text-dim)] mb-1">{{ t('users.role') }}</label>
            <select v-model="inviteRole" class="w-full px-3 py-1.5 text-sm rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]">
              <option value="member">{{ t('users.roleMember') }}</option>
              <option value="admin">{{ t('users.roleAdmin') }}</option>
            </select>
          </div>
        </div>
        <div v-if="inviteError" class="text-sm text-red-500">{{ inviteError }}</div>
        <button
          @click="handleInvite"
          :disabled="inviteLoading || !inviteEmail || !inviteName || !invitePassword"
          class="px-4 py-1.5 rounded-lg bg-[var(--color-accent)] text-white text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
        >
          {{ inviteLoading ? '...' : t('users.invite') }}
        </button>
      </div>

      <div class="rounded-lg border border-[var(--color-border)] overflow-hidden">
        <table class="w-full text-sm">
          <thead class="bg-[var(--color-surface)]">
            <tr class="text-left text-[var(--color-text-dim)]">
              <th class="px-4 py-2 font-medium">{{ t('auth.email') }}</th>
              <th class="px-4 py-2 font-medium">{{ t('auth.adminName') }}</th>
              <th class="px-4 py-2 font-medium">{{ t('users.role') }}</th>
              <th class="px-4 py-2 font-medium">{{ t('users.status') }}</th>
              <th class="px-4 py-2 font-medium w-20"></th>
            </tr>
          </thead>
          <tbody class="divide-y divide-[var(--color-border)]">
            <tr v-for="u in users" :key="u.id" class="text-[var(--color-text)]">
              <td class="px-4 py-2.5">{{ u.email }}</td>
              <td class="px-4 py-2.5">{{ u.name }}</td>
              <td class="px-4 py-2.5">
                <span class="px-2 py-0.5 rounded-full text-xs font-medium"
                  :class="{
                    'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300': u.role === 'superadmin',
                    'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300': u.role === 'admin',
                    'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400': u.role === 'member',
                  }">
                  {{ u.role }}
                </span>
              </td>
              <td class="px-4 py-2.5">
                <button
                  v-if="u.role !== 'superadmin' && u.id !== currentUser?.id"
                  @click="toggleActive(u)"
                  class="text-xs px-2 py-0.5 rounded-full font-medium transition-colors"
                  :class="u.isActive ? 'bg-green-100 text-green-700 hover:bg-green-200 dark:bg-green-900/30 dark:text-green-300' : 'bg-red-100 text-red-700 hover:bg-red-200 dark:bg-red-900/30 dark:text-red-300'"
                >
                  {{ u.isActive ? t('users.active') : t('users.disabled') }}
                </button>
                <span v-else class="text-xs px-2 py-0.5 rounded-full font-medium bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300">
                  {{ t('users.active') }}
                </span>
              </td>
              <td class="px-4 py-2.5 text-right">
                <button
                  v-if="u.role !== 'superadmin' && u.id !== currentUser?.id && currentUser?.role === 'superadmin'"
                  @click="deleteUser(u)"
                  class="p-1 rounded text-[var(--color-text-dim)] hover:text-red-500 hover:bg-[var(--color-surface-hover)] transition-colors"
                >
                  <Trash2 class="w-3.5 h-3.5" />
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
