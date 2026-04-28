<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useNetworkEgress } from '../composables/useNetworkEgress'
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
import { AlertTriangle, Loader2, Plus, RefreshCw, Trash2, Upload } from 'lucide-vue-next'

const { t } = useI18n()
const notify = useNotify()
const {
  state,
  fetchNetworkEgress,
  createNetworkEgress,
  deleteNetworkEgress,
  checkNetworkEgress,
} = useNetworkEgress()

const dialogOpen = ref(false)
const saving = ref(false)
const loadingConfigFromUrl = ref(false)
const checking = ref<Record<string, boolean>>({})
const deleting = ref<Record<string, boolean>>({})
const configSource = ref('')
const configError = ref('')
const draggingConfig = ref(false)
const configInput = ref<HTMLInputElement | null>(null)
const form = reactive({
  name: '',
  type: 'external_proxy',
  proxyUrl: '',
  configText: '',
  configUrl: '',
  username: '',
  password: '',
})

const realProfiles = computed(() => state.profiles.filter(p => p.type !== 'direct'))
const managedConfigMissing = computed(() =>
  (form.type === 'clash' || form.type === 'openvpn') && !form.configText.trim() && !form.configUrl.trim(),
)

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
  Object.assign(form, {
    name: '',
    type: 'external_proxy',
    proxyUrl: '',
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
  if (!file) return
  try {
    const text = await file.text()
    if (!text.trim()) {
      throw new Error(t('networkEgress.configEmpty'))
    }
    form.configText = text
    setConfigSourceLabel(t('networkEgress.configSourceFile', { name: file.name, size: formatBytes(file.size) }))
  } catch (err: any) {
    configError.value = err?.message || t('networkEgress.configReadError')
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
  if (!url) return
  loadingConfigFromUrl.value = true
  configError.value = ''

  try {
    const res = await fetch(url)
    if (!res.ok) {
      throw new Error(t('networkEgress.configUrlFetchFailed', { status: res.status }))
    }
    const text = await res.text()
    if (!text.trim()) {
      throw new Error(t('networkEgress.configEmpty'))
    }
    form.configText = text
    setConfigSourceLabel(t('networkEgress.configSourceUrl', { url }))
    notify.success(t('networkEgress.configLoaded'))
  } catch (err: any) {
    configError.value = err?.message || t('networkEgress.configUrlFetchError')
  } finally {
    loadingConfigFromUrl.value = false
  }
}

async function handleCreate() {
  if (saving.value) return
  saving.value = true
  try {
    await createNetworkEgress({ ...form })
    notify.success(t('networkEgress.created'))
    dialogOpen.value = false
    resetForm()
  } catch (err: any) {
    notify.error(err?.message || t('networkEgress.createError'))
  } finally {
    saving.value = false
  }
}

async function handleCheck(profile: NetworkEgressProfile) {
  if (!profile.id || checking.value[profile.id]) return
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
  if (!profile.id || deleting.value[profile.id]) return
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
watch(
  () => form.type,
  handleTypeChange,
)
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
        <Button size="sm" @click="dialogOpen = true">
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
            <TableHead>{{ t('networkEgress.proxyUrl') }}</TableHead>
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
            <TableCell class="text-xs font-mono text-muted-foreground max-w-[220px] truncate">
              {{ profile.proxyUrl || '-' }}
            </TableCell>
            <TableCell class="text-xs text-muted-foreground">
              {{ profile.lastCheckedAt ? new Date(profile.lastCheckedAt).toLocaleString() : '-' }}
            </TableCell>
            <TableCell class="text-right">
              <div class="inline-flex items-center gap-1">
                <Button variant="ghost" size="sm" :disabled="!profile.id || checking[profile.id]" @click="handleCheck(profile)">
                  <Loader2 v-if="profile.id && checking[profile.id]" class="size-3.5 animate-spin" />
                  <RefreshCw v-else class="size-3.5" />
                </Button>
                <Button variant="ghost" size="sm" class="text-destructive" :disabled="!profile.id || deleting[profile.id]" @click="handleDelete(profile)">
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

  <Dialog :open="dialogOpen" @update:open="dialogOpen = $event">
    <DialogContent class="sm:max-w-lg">
      <DialogHeader>
        <DialogTitle>{{ t('networkEgress.add') }}</DialogTitle>
      </DialogHeader>
      <form class="space-y-4" @submit.prevent="handleCreate">
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div class="space-y-2">
            <Label for="egress-name">{{ t('networkEgress.name') }}</Label>
            <Input id="egress-name" v-model="form.name" :placeholder="t('networkEgress.namePlaceholder')" />
          </div>
          <div class="space-y-2">
            <Label for="egress-type">{{ t('networkEgress.typeLabel') }}</Label>
            <Select v-model="form.type">
              <SelectTrigger id="egress-type">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="external_proxy">{{ typeLabel('external_proxy') }}</SelectItem>
                <SelectItem value="clash">{{ typeLabel('clash') }}</SelectItem>
                <SelectItem value="openvpn">{{ typeLabel('openvpn') }}</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        <div v-if="form.type === 'external_proxy'" class="space-y-2">
          <Label for="egress-proxy">{{ t('networkEgress.proxyUrl') }}</Label>
          <Input id="egress-proxy" v-model="form.proxyUrl" placeholder="socks5://proxy.internal:1080" />
        </div>

        <div v-else class="space-y-3">
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
          <div class="space-y-2">
            <Label for="egress-user">{{ t('networkEgress.username') }}</Label>
            <Input id="egress-user" v-model="form.username" autocomplete="off" />
          </div>
          <div class="space-y-2">
            <Label for="egress-password">{{ t('networkEgress.password') }}</Label>
            <Input id="egress-password" v-model="form.password" type="password" autocomplete="new-password" />
          </div>
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" :disabled="saving" @click="dialogOpen = false">{{ t('session.cancel') }}</Button>
          <Button type="submit" :disabled="saving || !form.name.trim() || managedConfigMissing">
            <Loader2 v-if="saving" class="size-4 animate-spin" />
            {{ saving ? t('networkEgress.saving') : t('networkEgress.create') }}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  </Dialog>
</template>
