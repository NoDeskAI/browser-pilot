<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../lib/api'
import { Loader2, Copy } from 'lucide-vue-next'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { toast } from 'vue-sonner'
import { useAuth } from '../composables/useAuth'

const { t } = useI18n()
const { user } = useAuth()

interface OrgInfo {
  id: string
  name: string
  slug: string
  createdAt: string
}

const loading = ref(false)
const saving = ref(false)
const orgName = ref('')
const orgInfo = ref<OrgInfo | null>(null)

async function loadOrganization() {
  loading.value = true
  try {
    const res = await api('/api/settings/organization')
    if (!res.ok) {
      throw new Error('Failed to load organization info')
    }
    const data = await res.json()
    orgInfo.value = data
    orgName.value = data.name
  } catch (e) {
    toast.error(t('settings.orgLoadError', 'Failed to load organization info'))
  } finally {
    loading.value = false
  }
}

async function handleSave() {
  if (!orgName.value.trim() || !orgInfo.value) return
  
  saving.value = true
  try {
    const res = await api('/api/settings/organization', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: orgName.value.trim() }),
    })
    
    if (!res.ok) {
      const data = await res.json()
      toast.error(data.detail || t('settings.orgUpdateError', 'Failed to update organization name'))
      return
    }
    
    toast.success(t('settings.saved'))
    await loadOrganization()
  } catch (e) {
    toast.error(t('settings.orgUpdateError', 'Failed to update organization name'))
  } finally {
    saving.value = false
  }
}

async function copyTenantId() {
  if (!orgInfo.value?.id) return
  try {
    await navigator.clipboard.writeText(orgInfo.value.id)
    toast.success(t('session.copied'))
  } catch (e) {
    // ignore
  }
}

onMounted(() => {
  loadOrganization()
})

const isAdmin = user.value?.role === 'superadmin' || user.value?.role === 'admin'
</script>

<template>
  <div class="space-y-6">
    <div v-if="loading" class="flex justify-center p-8">
      <Loader2 class="size-6 animate-spin text-muted-foreground" />
    </div>
    <template v-else-if="orgInfo">
      <form @submit.prevent="handleSave">
        <Card>
          <CardHeader>
            <CardTitle>{{ t('settings.organization') }}</CardTitle>
            <CardDescription>{{ t('settings.orgDescription', 'Manage your organization details.') }}</CardDescription>
          </CardHeader>
          <CardContent class="space-y-4">
            <div class="space-y-2">
              <Label for="org-name">{{ t('settings.orgName', 'Organization Name') }}</Label>
              <Input 
                id="org-name" 
                v-model="orgName" 
                :disabled="!isAdmin" 
                required 
              />
            </div>
            
            <div class="space-y-2">
              <Label for="tenant-id">{{ t('settings.tenantId', 'Tenant ID') }}</Label>
              <div class="flex gap-2">
                <Input 
                  id="tenant-id" 
                  :model-value="orgInfo.id" 
                  disabled 
                  class="bg-muted/50 font-mono text-sm" 
                />
                <Button type="button" variant="outline" size="icon" @click="copyTenantId" :title="t('session.copyId')">
                  <Copy class="size-4" />
                </Button>
              </div>
              <p class="text-[0.8rem] text-muted-foreground">
                {{ t('settings.tenantIdDescription', 'Unique identifier for your organization. Treat this as a secret.') }}
              </p>
            </div>
          </CardContent>
          <CardFooter v-if="isAdmin" class="flex justify-end px-6 py-4">
            <Button type="submit" :disabled="saving || !orgName || orgName === orgInfo?.name">
              <Loader2 v-if="saving" class="size-4 mr-2 animate-spin" />
              {{ t('settings.save') }}
            </Button>
          </CardFooter>
        </Card>
      </form>
    </template>
  </div>
</template>
