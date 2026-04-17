<script setup lang="ts">
import { reactive, ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../lib/api'
import { useNotify } from '../composables/useNotify'
import { Loader2, Check } from 'lucide-vue-next'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import {
  Card, CardContent, CardDescription, CardHeader, CardTitle,
} from '@/components/ui/card'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'

const { t } = useI18n()
const { success: notifySuccess, error: notifyError } = useNotify()

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
const touched = ref(false)

const s3RequiredFields = ['s3Bucket', 's3Region', 's3AccessKey', 's3SecretKey'] as const

const s3MissingFields = computed(() =>
  form.storage === 's3'
    ? s3RequiredFields.filter(f => !form[f].trim())
    : []
)

const canSave = computed(() =>
  form.storage === 'builtin' || s3MissingFields.value.length === 0
)

function isFieldMissing(field: string) {
  return touched.value && form.storage === 's3' && !form[field as keyof typeof form]?.toString().trim()
}

onMounted(async () => {
  try {
    const res = await api('/api/settings/storage')
    if (res.ok) {
      const data = await res.json()
      Object.assign(form, data)
    }
  } catch { /* keep defaults */ }
})

const _ERROR_MAP: Record<string, string> = {
  s3_missing_fields: 'settings.s3ErrMissing',
  s3_forbidden: 'settings.s3ErrForbidden',
  s3_bucket_not_found: 'settings.s3ErrBucketNotFound',
  s3_connect_failed: 'settings.s3ErrConnect',
}

async function saveSettings() {
  touched.value = true
  if (!canSave.value) return

  saving.value = true
  saved.value = false
  try {
    const res = await api('/api/settings/storage', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(form),
    })
    if (res.ok) {
      saved.value = true
      touched.value = false
      notifySuccess(t('settings.saved'))
      setTimeout(() => { saved.value = false }, 2000)
    } else {
      const body = await res.json().catch(() => null)
      const detail = body?.detail ?? ''
      const key = _ERROR_MAP[detail]
      notifyError(key ? t(key) : t('settings.s3ErrConnect'))
    }
  } catch {
    notifyError(t('settings.s3ErrConnect'))
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <Card>
    <CardHeader>
      <CardTitle class="text-base">{{ t('settings.fileStorage') }}</CardTitle>
      <CardDescription>{{ form.storage === 's3' ? t('settings.s3Desc') : t('settings.builtinDesc') }}</CardDescription>
    </CardHeader>
    <CardContent class="space-y-6">
      <!-- Storage mode -->
      <div class="space-y-3">
        <Label>{{ t('settings.storageMode') }}</Label>
        <RadioGroup v-model="form.storage" class="flex gap-6">
          <div class="flex items-center gap-2">
            <RadioGroupItem value="s3" id="storage-s3" />
            <Label for="storage-s3" class="cursor-pointer font-normal">{{ t('settings.s3') }}</Label>
          </div>
          <div class="flex items-center gap-2">
            <RadioGroupItem value="builtin" id="storage-builtin" />
            <Label for="storage-builtin" class="cursor-pointer font-normal">{{ t('settings.builtin') }}</Label>
          </div>
        </RadioGroup>
      </div>

      <!-- S3 config fields -->
      <div v-if="form.storage === 's3'" class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div v-for="field in (['s3Bucket', 's3Region', 's3AccessKey', 's3SecretKey'] as const)" :key="field" class="space-y-2">
          <Label :for="field">{{ t('settings.' + field) }}</Label>
          <Input
            :id="field"
            v-model="form[field]"
            :type="field === 's3SecretKey' ? 'password' : 'text'"
            :class="isFieldMissing(field) ? 'border-destructive' : ''"
          />
        </div>
        <div class="md:col-span-2 space-y-2">
          <Label for="s3Endpoint">{{ t('settings.s3Endpoint') }}</Label>
          <Input id="s3Endpoint" v-model="form.s3Endpoint" placeholder="https://s3.example.com" />
        </div>
        <div class="md:col-span-2 flex items-center gap-3">
          <Switch id="s3Presign" v-model:checked="form.s3Presign" />
          <Label for="s3Presign" class="cursor-pointer font-normal">{{ t('settings.s3Presign') }}</Label>
        </div>
      </div>

      <!-- Save button -->
      <Button
        @click="saveSettings"
        :disabled="saving || (touched && !canSave)"
      >
        <Loader2 v-if="saving" class="size-4 mr-2 animate-spin" />
        <Check v-else-if="saved" class="size-4 mr-2" />
        <template v-if="saving">{{ t('settings.s3Verifying') }}</template>
        <template v-else>{{ saved ? t('settings.saved') : t('settings.save') }}</template>
      </Button>
    </CardContent>
  </Card>
</template>
