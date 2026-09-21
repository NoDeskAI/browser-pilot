<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useNetworkEgress } from '../composables/useNetworkEgress'
import { useAuth } from '../composables/useAuth'
import type { NetworkEgressProfile } from '../types'
import { useNotify } from '../composables/useNotify'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { AlertTriangle, Loader2, Pencil, Plus, RefreshCw, Trash2, Upload } from 'lucide-vue-next'

const { t } = useI18n()
const notify = useNotify()
const { user } = useAuth()
const canManage = computed(() => ['superadmin', 'admin'].includes(user.value?.role || ''))
const {
  state,
  fetchNetworkEgress,
  createNetworkEgress,
  fetchNetworkEgressDetail,
  updateNetworkEgress,
  deleteNetworkEgress,
  checkNetworkEgress,
} = useNetworkEgress()

const dialogOpen = ref(false)
const saving = ref(false)
const editingId = ref<string | null>(null)
const loadingDetailId = ref<string | null>(null)
const initialConfig = ref('')
const initialName = ref('')
const saveError = ref('')
const loadingConfigFile = ref(false)
let formVersion = 0
let configAbort: AbortController | null = null
const loadingConfigFromUrl = ref(false)
const checking = ref<Record<string, boolean>>({})
const deleting = ref<Record<string, boolean>>({})
const configSource = ref('')
const configError = ref('')
const draggingConfig = ref(false)
const configInput = ref<HTMLInputElement | null>(null)
const form = reactive({
  name: '',
  type: 'clash',
  configText: '',
  configUrl: '',
  username: '',
  password: '',
})

const realProfiles = computed(() => state.profiles.filter(p => p.type !== 'direct'))
const managedConfigMissing = computed(() =>
  (!editingId.value || configurationChanged.value) && !form.configText.trim() && !form.configUrl.trim(),
)
const credentialsChanged = computed(() => form.type === 'openvpn' && !!(form.username || form.password))
const credentialsIncomplete = computed(() => credentialsChanged.value && (!form.username.trim() || !form.password))
const configurationChanged = computed(() => !!editingId.value && (form.configText !== initialConfig.value || !!form.configUrl.trim() || credentialsChanged.value))
const formChanged = computed(() => !editingId.value || form.name.trim() !== initialName.value || configurationChanged.value)
const formBusy = computed(() => saving.value || loadingConfigFromUrl.value || loadingConfigFile.value)

function openCreate() {
  if (!canManage.value || loadingDetailId.value) return
  resetForm()
  dialogOpen.value = true
}

async function openEdit(profile: NetworkEgressProfile) {
  if (!canManage.value || !profile.id || loadingDetailId.value || saving.value) return
  loadingDetailId.value = profile.id
  const version = formVersion
  try {
    const detail = await fetchNetworkEgressDetail(profile.id)
    if (version !== formVersion) return
    resetForm()
    editingId.value = profile.id
    form.type = detail.type
    form.name = detail.name
    form.configText = detail.configText
    initialName.value = detail.name
    initialConfig.value = detail.configText
    dialogOpen.value = true
  } catch (err: any) {
    notify.error(err?.message || t('networkEgress.loadError'))
  } finally {
    loadingDetailId.value = null
  }
}

function setDialogOpen(open: boolean) {
  if (saving.value) return
  dialogOpen.value = open
}

function statusVariant(status: string) {
  if (status === 'healthy') return 'default'
  if (status === 'unchecked') return 'secondary'
  return 'destructive'
}

function typeLabel(type: string) {
  return t(`networkEgress.type.${type}`, type)
}

function statusLabel(status: string) {
  return t(`networkEgress.status.${status}`, status)
}

function resetForm() {
  formVersion++
  configAbort?.abort()
  configAbort = null
  loadingConfigFromUrl.value = false
  loadingConfigFile.value = false
  editingId.value = null
  initialConfig.value = ''
  initialName.value = ''
  saveError.value = ''
  Object.assign(form, {
    name: '',
    type: 'clash',
    configText: '',
    configUrl: '',
    username: '',
    password: '',
  })
  configSource.value = ''
  configError.value = ''
}

function clearConfigSource() {
  configSource.value = ''
  if (!form.configText.trim()) {
    configError.value = ''
  }
}

function setConfigSourceLabel(label: string) {
  configSource.value = label
  configError.value = ''
}

function formatBytes(size: number) {
  return `${size} B`
}

function triggerConfigFilePicker() {
  configInput.value?.click()
}

async function processConfigFile(file: File | null) {
  if (!file || formBusy.value) return
  const version = formVersion
  loadingConfigFile.value = true
  try {
    const text = await file.text()
    if (version !== formVersion) return
    if (!text.trim()) {
      throw new Error(t('networkEgress.configEmpty'))
    }
    form.configText = text
    form.configUrl = ''
    setConfigSourceLabel(t('networkEgress.configSourceFile', { name: file.name, size: formatBytes(file.size) }))
  } catch (err: any) {
    if (version !== formVersion) return
    configError.value = err?.message || t('networkEgress.configReadError')
  } finally {
    if (version === formVersion) loadingConfigFile.value = false
  }
}

async function handleConfigFileInputChange(event: Event) {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0] || null
  await processConfigFile(file)
  draggingConfig.value = false
  if (target) target.value = ''
}

function handleConfigDragOver(event: DragEvent) {
  event.preventDefault()
  draggingConfig.value = true
}

function handleConfigDragLeave(event: DragEvent) {
  const target = event.currentTarget as HTMLDivElement
  const related = event.relatedTarget as HTMLElement | null
  if (target && (!related || !target.contains(related))) {
    draggingConfig.value = false
  }
}

async function handleConfigDrop(event: DragEvent) {
  event.preventDefault()
  draggingConfig.value = false
  const file = event.dataTransfer?.files?.[0] || null
  await processConfigFile(file)
}

async function handleFetchConfigUrl() {
  const url = form.configUrl.trim()
  if (!url || formBusy.value) return
  const version = formVersion
  configAbort = new AbortController()
  loadingConfigFromUrl.value = true
  configError.value = ''

  try {
    const res = await fetch(url, { signal: configAbort.signal })
    if (!res.ok) {
      throw new Error(t('networkEgress.configUrlFetchFailed', { status: res.status }))
    }
    const text = await res.text()
    if (version !== formVersion) return
    if (!text.trim()) {
      throw new Error(t('networkEgress.configEmpty'))
    }
    form.configText = text
    form.configUrl = ''
    setConfigSourceLabel(t('networkEgress.configSourceUrl', { url }))
    notify.success(t('networkEgress.configLoaded'))
  } catch (err: any) {
    if (version !== formVersion) return
    configError.value = err?.message || t('networkEgress.configUrlFetchError')
  } finally {
    if (version === formVersion) loadingConfigFromUrl.value = false
  }
}

async function handleSave() {
  if (!canManage.value || formBusy.value || !form.name.trim() || managedConfigMissing.value || credentialsIncomplete.value || !formChanged.value) return
  saving.value = true
  saveError.value = ''
  try {
    if (editingId.value) {
      const body: Record<string, string> = { name: form.name.trim() }
      if (configurationChanged.value) {
        if (form.configUrl.trim()) body.configUrl = form.configUrl.trim()
        else body.configText = form.configText
        if (credentialsChanged.value) {
          body.username = form.username.trim()
          body.password = form.password
          // Credentials are applied by the existing config replacement API.
        }
      }
      await updateNetworkEgress(editingId.value, body)
      notify.success(t('networkEgress.updated'))
    } else {
      await createNetworkEgress({ ...form, name: form.name.trim(), configText: form.configUrl.trim() ? '' : form.configText })
      notify.success(t('networkEgress.created'))
    }
    dialogOpen.value = false
    resetForm()
  } catch (err: any) {
    saveError.value = err?.message || t(editingId.value ? 'networkEgress.updateError' : 'networkEgress.createError')
  } finally {
    saving.value = false
  }
}

async function handleCheck(profile: NetworkEgressProfile) {
  if (!canManage.value || !profile.id || checking.value[profile.id]) return
  checking.value[profile.id] = true
  try {
    await checkNetworkEgress(profile.id)
    notify.success(t('networkEgress.checked'))
  } catch (err: any) {
    notify.error(err?.message || t('networkEgress.checkError'))
  } finally {
    checking.value[profile.id] = false
  }
}

async function handleDelete(profile: NetworkEgressProfile) {
  if (!canManage.value || !profile.id || deleting.value[profile.id]) return
  deleting.value[profile.id] = true
  try {
    await deleteNetworkEgress(profile.id)
    notify.success(t('networkEgress.deleted'))
  } catch (err: any) {
    notify.error(err?.message || t('networkEgress.deleteError'))
  } finally {
    deleting.value[profile.id] = false
  }
}

function handleTypeChange() {
  form.configText = ''
  form.configUrl = ''
  clearConfigSource()
  configError.value = ''
}

onMounted(fetchNetworkEgress)
onBeforeUnmount(resetForm)
watch(
  () => form.type,
  handleTypeChange,
  { flush: 'sync' },
)
watch(dialogOpen, open => { if (!open) resetForm() })
</script>

<template>
  <Card>
    <CardHeader class="flex flex-row items-center justify-between">
      <div>
        <CardTitle>{{ t('networkEgress.title') }}</CardTitle>
        <p class="text-sm text-muted-foreground mt-1">{{ t('networkEgress.description') }}</p>
      </div>
      <div class="flex items-center gap-2">
        <Button variant="outline" size="icon" class="size-8" :disabled="state.loading" @click="fetchNetworkEgress">
          <RefreshCw class="size-3.5" :class="state.loading && 'animate-spin'" />
        </Button>
        <Button v-if="canManage" size="sm" :disabled="!!loadingDetailId" @click="openCreate">
          <Plus class="size-3.5 mr-1" />
          {{ t('networkEgress.add') }}
        </Button>
      </div>
    </CardHeader>
    <CardContent>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{{ t('networkEgress.name') }}</TableHead>
            <TableHead>{{ t('networkEgress.typeLabel') }}</TableHead>
            <TableHead>{{ t('networkEgress.statusLabel') }}</TableHead>
            <TableHead>{{ t('networkEgress.lastChecked') }}</TableHead>
            <TableHead class="text-right">{{ t('networkEgress.actions') }}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow>
            <TableCell class="font-medium">{{ t('networkEgress.direct') }}</TableCell>
            <TableCell>{{ typeLabel('direct') }}</TableCell>
            <TableCell><Badge variant="default">{{ statusLabel('healthy') }}</Badge></TableCell>
            <TableCell class="text-xs text-muted-foreground">-</TableCell>
            <TableCell />
          </TableRow>
          <TableRow v-for="profile in realProfiles" :key="profile.id || profile.name">
            <TableCell>
              <div class="font-medium">{{ profile.name }}</div>
              <div v-if="profile.healthError" class="mt-1 flex items-center gap-1 text-xs text-destructive">
                <AlertTriangle class="size-3" />
                <span class="truncate max-w-[240px]">{{ profile.healthError }}</span>
              </div>
            </TableCell>
            <TableCell>{{ typeLabel(profile.type) }}</TableCell>
            <TableCell>
              <Badge :variant="statusVariant(profile.status) as any">{{ statusLabel(profile.status) }}</Badge>
            </TableCell>
            <TableCell class="text-xs text-muted-foreground">
              {{ profile.lastCheckedAt ? new Date(profile.lastCheckedAt).toLocaleString() : '-' }}
            </TableCell>
            <TableCell class="text-right">
              <div v-if="canManage" class="inline-flex items-center gap-1">
                <Tooltip>
                  <TooltipTrigger as-child>
                    <Button variant="ghost" size="icon" class="size-8" :aria-label="t('networkEgress.edit')" :disabled="!!loadingDetailId || !!(profile.id && deleting[profile.id])" @click="openEdit(profile)">
                      <Loader2 v-if="loadingDetailId === profile.id" class="size-3.5 animate-spin" />
                      <Pencil v-else class="size-3.5" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>{{ t('networkEgress.edit') }}</TooltipContent>
                </Tooltip>
                <Button variant="ghost" size="icon" class="size-8" :title="t('networkEgress.check')" :aria-label="t('networkEgress.check')" :disabled="!profile.id || checking[profile.id] || !!loadingDetailId" @click="handleCheck(profile)">
                  <Loader2 v-if="profile.id && checking[profile.id]" class="size-3.5 animate-spin" />
                  <RefreshCw v-else class="size-3.5" />
                </Button>
                <Button variant="ghost" size="icon" class="size-8 text-destructive" :title="t('networkEgress.delete')" :aria-label="t('networkEgress.delete')" :disabled="!profile.id || deleting[profile.id] || !!loadingDetailId" @click="handleDelete(profile)">
                  <Loader2 v-if="profile.id && deleting[profile.id]" class="size-3.5 animate-spin" />
                  <Trash2 v-else class="size-3.5" />
                </Button>
              </div>
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </CardContent>
  </Card>

  <Dialog :open="dialogOpen" @update:open="setDialogOpen">
    <DialogContent class="sm:max-w-lg" :show-close-button="!saving" @escape-key-down="saving && $event.preventDefault()" @interact-outside="saving && $event.preventDefault()">
      <DialogHeader class="static m-0 p-0">
        <DialogTitle>{{ t(editingId ? 'networkEgress.edit' : 'networkEgress.add') }}</DialogTitle>
      </DialogHeader>
      <form class="space-y-4" @submit.prevent="handleSave">
        <fieldset :disabled="formBusy" class="min-w-0 space-y-4">
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div class="space-y-2">
            <Label for="egress-name">{{ t('networkEgress.name') }}</Label>
            <Input id="egress-name" v-model="form.name" maxlength="120" required :placeholder="t('networkEgress.namePlaceholder')" />
          </div>
          <div class="space-y-2">
            <Label for="egress-type">{{ t('networkEgress.typeLabel') }}</Label>
            <Select v-model="form.type" :disabled="!!editingId || formBusy">
              <SelectTrigger id="egress-type" class="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="clash">{{ typeLabel('clash') }}</SelectItem>
                <SelectItem value="openvpn">{{ typeLabel('openvpn') }}</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        <div class="space-y-3">
          <div class="space-y-2">
            <Label for="egress-config-url">{{ t('networkEgress.configUrl') }}</Label>
            <Input
              id="egress-config-url"
              v-model="form.configUrl"
              :placeholder="t('networkEgress.configUrlPlaceholder')"
              autocomplete="off"
            />
            <div class="flex flex-wrap items-center gap-2">
              <Button
                type="button"
                size="sm"
                variant="outline"
                :disabled="!form.configUrl.trim() || loadingConfigFromUrl"
                @click="handleFetchConfigUrl"
              >
                <Loader2 v-if="loadingConfigFromUrl" class="size-3.5 animate-spin" />
                <span v-else>{{ t('networkEgress.configFetch') }}</span>
              </Button>
              <span class="text-xs text-muted-foreground">{{ t('networkEgress.configUrlHelp') }}</span>
            </div>
          </div>

          <div class="space-y-2">
            <Label for="egress-config">{{ form.type === 'clash' ? t('networkEgress.clashConfig') : t('networkEgress.openvpnConfig') }}</Label>
            <div
              class="rounded-md border border-dashed px-3 py-2 cursor-pointer"
              :class="draggingConfig ? 'border-primary bg-muted' : 'border-input'"
              @dragover="handleConfigDragOver"
              @dragleave="handleConfigDragLeave"
              @drop="handleConfigDrop"
              @click="triggerConfigFilePicker"
            >
              <div class="text-sm text-muted-foreground">
                {{ configSource || t('networkEgress.configDropHint') }}
              </div>
              <div class="mt-2 flex items-center gap-2">
                <Button type="button" variant="outline" size="sm" @click.stop="triggerConfigFilePicker">
                  <Upload class="size-3.5" />
                  {{ t('networkEgress.configSelectFile') }}
                </Button>
                <span class="text-xs text-muted-foreground">{{ t('networkEgress.configSelectHint') }}</span>
              </div>
            </div>
            <input ref="configInput" class="hidden" type="file" accept=".yaml,.yml,.conf,.ovpn,.txt" @change="handleConfigFileInputChange" />
            <div v-if="configError" class="text-xs text-destructive">{{ configError }}</div>
          </div>

          <textarea
            id="egress-config"
            v-model="form.configText"
            class="min-h-36 w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm font-mono outline-none focus-visible:ring-2 focus-visible:ring-ring"
            :placeholder="form.type === 'clash' ? 'mixed-port: 7890' : 'client\\ndev tun\\nproto udp'"
          />
        </div>

        <div v-if="form.type === 'openvpn'" class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <p v-if="editingId" class="text-xs text-muted-foreground md:col-span-2">{{ t('networkEgress.credentialsKeep') }}</p>
          <div class="space-y-2">
            <Label for="egress-user">{{ t('networkEgress.username') }}</Label>
            <Input id="egress-user" v-model="form.username" autocomplete="off" />
          </div>
          <div class="space-y-2">
            <Label for="egress-password">{{ t('networkEgress.password') }}</Label>
            <Input id="egress-password" v-model="form.password" type="password" autocomplete="new-password" />
          </div>
        </div>
        </fieldset>
        <p v-if="credentialsIncomplete" role="alert" class="text-sm text-destructive">{{ t('networkEgress.credentialsRequired') }}</p>
        <p v-if="configurationChanged" class="flex items-start gap-2 text-sm text-muted-foreground"><AlertTriangle class="mt-0.5 size-4 shrink-0" />{{ t('networkEgress.configChangeWarning') }}</p>
        <p v-if="saveError" role="alert" class="text-sm text-destructive">{{ saveError }}</p>

        <DialogFooter>
          <Button type="button" variant="outline" :disabled="saving" @click="dialogOpen = false">{{ t('session.cancel') }}</Button>
          <Button type="submit" :disabled="formBusy || !form.name.trim() || managedConfigMissing || credentialsIncomplete || !formChanged">
            <Loader2 v-if="saving" class="size-4 animate-spin" />
            {{ saving ? t('networkEgress.saving') : t(editingId ? 'networkEgress.save' : 'networkEgress.create') }}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  </Dialog>
</template>
