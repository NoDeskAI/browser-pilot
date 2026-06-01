<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  KeyRound,
  Layers,
  Loader2,
  LogOut,
  RefreshCw,
  ShieldCheck,
  UserPlus,
} from 'lucide-vue-next'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

type PlatformUser = {
  id: string
  email: string
  name: string
  role: 'platform_admin' | 'platform_operator'
}

type Tenant = {
  id: string
  name: string
  slug: string
  status: string
  runtimeNamespace: string
  activeSessionLimit: number
  maxSessionSeconds: number
  planId: string | null
  planCode: string | null
  sessionCount: number
  suspendReason?: string
  deleteReason?: string
  retentionUntil?: string | null
  purgeRequestedAt?: string | null
  purgeRequestReason?: string | null
}

type Plan = {
  id: string
  code: string
  name: string
  defaultActiveSessionLimit: number
  defaultMaxSessionSeconds: number
  isActive: boolean
}

type RuntimeImage = {
  id: string
  runtimeClass: string
  imageRef: string
  imageDigest: string
  scanStatus: string
  approvalStatus: string
  approvedAt?: string | null
}

type Placement = {
  id: string
  sessionId: string
  tenantId: string
  runtimeNamespace: string
  runtimePhase: string
  runtimeClass: string
  runtimePodName?: string | null
  egressGatewayPodName?: string | null
  failureReason?: string | null
  lastError?: string | null
}

type RuntimePool = {
  id: string
  name: string
  runtimeClasses: string[]
  activeSessionCapacity: number
  activeReservationCount: number
  isEnabled: boolean
  isDraining: boolean
  drainReason?: string | null
}

type RuntimeNode = {
  id: string
  runtimePoolId: string
  providerNodeName: string
  status: 'active' | 'draining' | 'disabled'
  labels: Record<string, any>
  allocatable: Record<string, any>
  drainReason?: string | null
  disabledReason?: string | null
}

type AuditEvent = {
  id: string
  action: string
  targetType: string
  targetId?: string | null
  tenantId?: string | null
  actorRole?: string | null
  outcome: 'success' | 'failure'
  reason?: string | null
  error?: string | null
  createdAt?: string | null
}

const platformToken = ref(sessionStorage.getItem('platform_auth_token') || '')
const user = ref<PlatformUser | null>(null)
const loading = ref(false)
const bootstrapping = ref(false)
const activeTab = ref<'tenants' | 'plans' | 'images' | 'capacity' | 'placements' | 'audit'>('tenants')
const error = ref('')
const notice = ref('')

const loginForm = reactive({ email: '', password: '' })
const setupForm = reactive({ email: '', password: '', name: '' })
const tenantForm = reactive({
  name: '',
  slug: '',
  activeSessionLimit: 3,
  maxSessionSeconds: 3600,
  initialAdminEmail: '',
  initialAdminPassword: '',
  initialAdminName: '',
  reason: 'platform_create_tenant',
})
const quotaForm = reactive({
  tenantId: '',
  activeSessionLimit: 3,
  maxSessionSeconds: 3600,
  reason: 'quota_update',
})
const planForm = reactive({
  code: '',
  name: '',
  defaultActiveSessionLimit: 3,
  defaultMaxSessionSeconds: 3600,
  reason: 'plan_create',
})
const imageForm = reactive({
  runtimeClass: 'standard_chrome',
  imageRef: '',
  imageDigest: '',
  scanStatus: 'passed',
  approvalStatus: 'approved',
  reason: 'runtime_image_create',
})
const poolForm = reactive({
  name: 'Default runtime worker pool',
  runtimeClasses: 'standard_chrome,cloak_chromium',
  activeSessionCapacity: 100,
  reason: 'runtime_pool_create',
})
const nodeForm = reactive({
  runtimePoolId: 'runtime_pool_default',
  providerNodeName: '',
  status: 'active' as 'active' | 'draining' | 'disabled',
  reason: 'runtime_node_register',
})

const tenants = ref<Tenant[]>([])
const plans = ref<Plan[]>([])
const images = ref<RuntimeImage[]>([])
const placements = ref<Placement[]>([])
const runtimePools = ref<RuntimePool[]>([])
const runtimeNodes = ref<RuntimeNode[]>([])
const auditEvents = ref<AuditEvent[]>([])
const selectedTenant = computed(() => tenants.value.find(t => t.id === quotaForm.tenantId) || null)
const isLoggedIn = computed(() => !!platformToken.value && !!user.value)
const canAdmin = computed(() => user.value?.role === 'platform_admin')

function setError(message: string) {
  error.value = message
  notice.value = ''
}

function setNotice(message: string) {
  notice.value = message
  error.value = ''
}

async function readError(res: Response) {
  try {
    const data = await res.json()
    if (typeof data?.detail === 'string') return data.detail
    if (data?.detail?.reason) return data.detail.reason
    return JSON.stringify(data?.detail || data)
  } catch {
    return res.statusText || '请求失败'
  }
}

async function platformApi(path: string, options: RequestInit = {}) {
  const headers = new Headers(options.headers)
  headers.set('Content-Type', 'application/json')
  if (platformToken.value) headers.set('Authorization', `Bearer ${platformToken.value}`)
  const res = await fetch(path, { ...options, headers })
  if (res.status === 401) {
    logout()
    throw new Error('平台登录已失效')
  }
  if (!res.ok) {
    throw new Error(await readError(res))
  }
  return res.json()
}

async function fetchMe() {
  if (!platformToken.value) return false
  const data = await platformApi('/api/platform/me', { method: 'GET' })
  user.value = data
  return true
}

async function handleLogin() {
  loading.value = true
  try {
    const data = await platformApi('/api/platform/login', {
      method: 'POST',
      body: JSON.stringify(loginForm),
    })
    platformToken.value = data.access_token
    sessionStorage.setItem('platform_auth_token', data.access_token)
    user.value = data.user
    await refreshAll()
    setNotice('已登录平台控制台')
  } catch (err: any) {
    setError(err?.message || '登录失败')
  } finally {
    loading.value = false
  }
}

async function handleSetup() {
  bootstrapping.value = true
  try {
    const data = await platformApi('/api/platform/setup', {
      method: 'POST',
      body: JSON.stringify(setupForm),
    })
    platformToken.value = data.access_token
    sessionStorage.setItem('platform_auth_token', data.access_token)
    user.value = data.user
    await refreshAll()
    setNotice('平台管理员已创建')
  } catch (err: any) {
    setError(err?.message || '初始化失败')
  } finally {
    bootstrapping.value = false
  }
}

function logout() {
  platformToken.value = ''
  user.value = null
  sessionStorage.removeItem('platform_auth_token')
}

async function refreshTenants() {
  const data = await platformApi('/api/platform/tenants', { method: 'GET' })
  tenants.value = data.tenants || []
  const tenant = tenants.value[0]
  if (!quotaForm.tenantId && tenant) {
    quotaForm.tenantId = tenant.id
    quotaForm.activeSessionLimit = tenant.activeSessionLimit
    quotaForm.maxSessionSeconds = tenant.maxSessionSeconds
  }
}

async function refreshPlans() {
  const data = await platformApi('/api/platform/plans', { method: 'GET' })
  plans.value = data.plans || []
}

async function refreshImages() {
  const data = await platformApi('/api/platform/runtime-images', { method: 'GET' })
  images.value = data.images || []
}

async function refreshPlacements() {
  const data = await platformApi('/api/platform/runtime-placements?limit=100', { method: 'GET' })
  placements.value = data.placements || []
}

async function refreshCapacity() {
  const [poolData, nodeData] = await Promise.all([
    platformApi('/api/platform/runtime-pools', { method: 'GET' }),
    platformApi('/api/platform/runtime-nodes', { method: 'GET' }),
  ])
  runtimePools.value = poolData.runtimePools || []
  runtimeNodes.value = nodeData.runtimeNodes || []
  if (!nodeForm.runtimePoolId && runtimePools.value[0]) {
    nodeForm.runtimePoolId = runtimePools.value[0].id
  }
}

async function refreshAudit() {
  const data = await platformApi('/api/platform/audit-events?limit=100', { method: 'GET' })
  auditEvents.value = data.events || []
}

async function refreshAll() {
  loading.value = true
  try {
    await Promise.all([refreshTenants(), refreshPlans(), refreshImages(), refreshCapacity(), refreshPlacements(), refreshAudit()])
  } catch (err: any) {
    setError(err?.message || '刷新失败')
  } finally {
    loading.value = false
  }
}

async function createTenant() {
  loading.value = true
  try {
    const body: Record<string, unknown> = {
      name: tenantForm.name,
      slug: tenantForm.slug || undefined,
      activeSessionLimit: Number(tenantForm.activeSessionLimit),
      maxSessionSeconds: Number(tenantForm.maxSessionSeconds),
      reason: tenantForm.reason,
    }
    if (tenantForm.initialAdminEmail && tenantForm.initialAdminPassword && tenantForm.initialAdminName) {
      body.initialAdmin = {
        email: tenantForm.initialAdminEmail,
        password: tenantForm.initialAdminPassword,
        name: tenantForm.initialAdminName,
      }
    }
    await platformApi('/api/platform/tenants', { method: 'POST', body: JSON.stringify(body) })
    tenantForm.name = ''
    tenantForm.slug = ''
    tenantForm.initialAdminEmail = ''
    tenantForm.initialAdminPassword = ''
    tenantForm.initialAdminName = ''
    await refreshTenants()
    await refreshAudit()
    setNotice('tenant 已创建')
  } catch (err: any) {
    setError(err?.message || '创建 tenant 失败')
  } finally {
    loading.value = false
  }
}

function pickTenant(tenant: Tenant) {
  quotaForm.tenantId = tenant.id
  quotaForm.activeSessionLimit = tenant.activeSessionLimit
  quotaForm.maxSessionSeconds = tenant.maxSessionSeconds
}

async function updateQuota() {
  if (!quotaForm.tenantId) return
  loading.value = true
  try {
    await platformApi(`/api/platform/tenants/${quotaForm.tenantId}/quota`, {
      method: 'PUT',
      body: JSON.stringify({
        activeSessionLimit: Number(quotaForm.activeSessionLimit),
        maxSessionSeconds: Number(quotaForm.maxSessionSeconds),
        runtimeClassLimits: {},
        reason: quotaForm.reason,
      }),
    })
    await refreshTenants()
    await refreshAudit()
    setNotice('quota 已更新')
  } catch (err: any) {
    setError(err?.message || '更新 quota 失败')
  } finally {
    loading.value = false
  }
}

async function tenantAction(tenant: Tenant, action: 'suspend' | 'resume' | 'delete' | 'runtime-revoke' | 'purge-request' | 'purge') {
  const reason = window.prompt('原因', action === 'delete' ? 'tenant_delete' : `tenant_${action}`)
  if (reason === null) return
  let retentionDays: number | undefined
  if (action === 'delete') {
    const retentionDaysText = window.prompt('保留天数', '30')
    if (retentionDaysText === null) return
    retentionDays = Number(retentionDaysText)
    if (!Number.isInteger(retentionDays) || retentionDays < 0) {
      setError('保留天数无效')
      return
    }
  }
  loading.value = true
  try {
    if (action === 'delete') {
      await platformApi(`/api/platform/tenants/${tenant.id}`, {
        method: 'DELETE',
        body: JSON.stringify({ reason, retentionDays }),
      })
    } else if (action === 'purge-request') {
      await platformApi(`/api/platform/tenants/${tenant.id}/purge-request`, {
        method: 'POST',
        body: JSON.stringify({ reason }),
      })
    } else if (action === 'purge') {
      await platformApi(`/api/platform/tenants/${tenant.id}/purge`, {
        method: 'POST',
        body: JSON.stringify({ reason }),
      })
    } else if (action === 'runtime-revoke') {
      await platformApi(`/api/platform/tenants/${tenant.id}/runtime-revoke`, {
        method: 'POST',
        body: JSON.stringify({ reason }),
      })
    } else {
      await platformApi(`/api/platform/tenants/${tenant.id}/${action}`, {
        method: 'POST',
        body: JSON.stringify({ reason }),
      })
    }
    await refreshAll()
    setNotice(`${tenant.name} 已${action === 'suspend' ? '停用' : action === 'resume' ? '恢复' : action === 'runtime-revoke' ? 'revoke runtime' : action === 'purge-request' ? '登记 purge request' : action === 'purge' ? '执行 purge' : '删除'}`)
  } catch (err: any) {
    setError(err?.message || 'tenant 操作失败')
  } finally {
    loading.value = false
  }
}

async function createPlan() {
  loading.value = true
  try {
    await platformApi('/api/platform/plans', {
      method: 'POST',
      body: JSON.stringify({
        code: planForm.code,
        name: planForm.name,
        defaultActiveSessionLimit: Number(planForm.defaultActiveSessionLimit),
        defaultMaxSessionSeconds: Number(planForm.defaultMaxSessionSeconds),
        defaultRuntimeClassLimits: {},
        isActive: true,
        reason: planForm.reason,
      }),
    })
    planForm.code = ''
    planForm.name = ''
    await refreshPlans()
    await refreshAudit()
    setNotice('plan 已创建')
  } catch (err: any) {
    setError(err?.message || '创建 plan 失败')
  } finally {
    loading.value = false
  }
}

async function createRuntimeImage() {
  loading.value = true
  try {
    await platformApi('/api/platform/runtime-images', {
      method: 'POST',
      body: JSON.stringify(imageForm),
    })
    imageForm.imageRef = ''
    imageForm.imageDigest = ''
    await refreshImages()
    await refreshAudit()
    setNotice('runtime image 已登记')
  } catch (err: any) {
    setError(err?.message || '登记 runtime image 失败')
  } finally {
    loading.value = false
  }
}

async function createRuntimePool() {
  loading.value = true
  try {
    await platformApi('/api/platform/runtime-pools', {
      method: 'POST',
      body: JSON.stringify({
        name: poolForm.name,
        runtimeClasses: poolForm.runtimeClasses.split(',').map(item => item.trim()).filter(Boolean),
        activeSessionCapacity: Number(poolForm.activeSessionCapacity),
        isEnabled: true,
        isDraining: false,
        reason: poolForm.reason,
      }),
    })
    await refreshCapacity()
    await refreshAudit()
    setNotice('runtime pool 已创建')
  } catch (err: any) {
    setError(err?.message || '创建 runtime pool 失败')
  } finally {
    loading.value = false
  }
}

async function runtimePoolAction(pool: RuntimePool, action: 'drain' | 'resume' | 'disable' | 'enable') {
  const reason = window.prompt('原因', `runtime_pool_${action}`)
  if (reason === null) return
  loading.value = true
  try {
    await platformApi(`/api/platform/runtime-pools/${pool.id}`, {
      method: 'PATCH',
      body: JSON.stringify({
        isDraining: action === 'drain' ? true : action === 'resume' ? false : undefined,
        isEnabled: action === 'disable' ? false : action === 'enable' ? true : undefined,
        reason,
      }),
    })
    await refreshCapacity()
    await refreshAudit()
    setNotice(`runtime pool 已${action}`)
  } catch (err: any) {
    setError(err?.message || 'runtime pool 操作失败')
  } finally {
    loading.value = false
  }
}

async function registerRuntimeNode() {
  loading.value = true
  try {
    await platformApi('/api/platform/runtime-nodes', {
      method: 'POST',
      body: JSON.stringify(nodeForm),
    })
    nodeForm.providerNodeName = ''
    await refreshCapacity()
    await refreshAudit()
    setNotice('runtime node 已登记')
  } catch (err: any) {
    setError(err?.message || '登记 runtime node 失败')
  } finally {
    loading.value = false
  }
}

async function runtimeNodeAction(node: RuntimeNode, status: 'active' | 'draining' | 'disabled') {
  const reason = window.prompt('原因', `runtime_node_${status}`)
  if (reason === null) return
  loading.value = true
  try {
    await platformApi(`/api/platform/runtime-nodes/${node.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ status, reason }),
    })
    await refreshCapacity()
    await refreshAudit()
    setNotice(`runtime node 已切换为 ${status}`)
  } catch (err: any) {
    setError(err?.message || 'runtime node 操作失败')
  } finally {
    loading.value = false
  }
}

function statusVariant(status: string) {
  if (status === 'active' || status === 'approved' || status === 'ready' || status === 'success') return 'default'
  if (status === 'suspended' || status === 'pending' || status === 'provisioning' || status === 'starting' || status === 'draining') return 'secondary'
  return 'destructive'
}

onMounted(async () => {
  if (!platformToken.value) return
  try {
    await fetchMe()
    await refreshAll()
  } catch (err: any) {
    setError(err?.message || '平台登录已失效')
  }
})
</script>

<template>
  <div class="min-h-screen bg-background text-foreground">
    <header class="h-14 border-b bg-background px-4 flex items-center justify-between">
      <div class="flex items-center gap-3 min-w-0">
        <img src="/brand/browser-pilot.svg" alt="" class="size-8 object-contain mix-blend-multiply dark:invert dark:mix-blend-screen" />
        <div class="min-w-0">
          <h1 class="text-base font-semibold truncate">Browser Pilot SaaS 平台控制台</h1>
          <p class="text-xs text-muted-foreground truncate">platform auth 与 tenant auth 独立</p>
        </div>
      </div>
      <div v-if="isLoggedIn" class="flex items-center gap-2">
        <Badge variant="outline">{{ user?.role }}</Badge>
        <span class="hidden sm:inline text-xs text-muted-foreground">{{ user?.email }}</span>
        <Button variant="ghost" size="sm" @click="logout">
          <LogOut class="size-4 mr-2" />
          退出
        </Button>
      </div>
    </header>

    <main v-if="!isLoggedIn" class="mx-auto w-full max-w-5xl p-4 sm:p-8">
      <div class="grid gap-6 md:grid-cols-2">
        <section class="rounded-lg border bg-card p-5">
          <div class="mb-5 flex items-center gap-2">
            <KeyRound class="size-5 text-muted-foreground" />
            <h2 class="text-base font-semibold">平台登录</h2>
          </div>
          <form class="space-y-4" @submit.prevent="handleLogin">
            <div class="space-y-2">
              <Label>邮箱</Label>
              <Input v-model="loginForm.email" autocomplete="username" />
            </div>
            <div class="space-y-2">
              <Label>密码</Label>
              <Input v-model="loginForm.password" type="password" autocomplete="current-password" />
            </div>
            <Button type="submit" class="w-full" :disabled="loading">
              <Loader2 v-if="loading" class="size-4 mr-2 animate-spin" />
              登录
            </Button>
          </form>
        </section>

        <section class="rounded-lg border bg-card p-5">
          <div class="mb-5 flex items-center gap-2">
            <UserPlus class="size-5 text-muted-foreground" />
            <h2 class="text-base font-semibold">首次初始化</h2>
          </div>
          <form class="space-y-4" @submit.prevent="handleSetup">
            <div class="space-y-2">
              <Label>姓名</Label>
              <Input v-model="setupForm.name" />
            </div>
            <div class="space-y-2">
              <Label>邮箱</Label>
              <Input v-model="setupForm.email" autocomplete="username" />
            </div>
            <div class="space-y-2">
              <Label>密码</Label>
              <Input v-model="setupForm.password" type="password" autocomplete="new-password" />
            </div>
            <Button type="submit" class="w-full" :disabled="bootstrapping">
              <Loader2 v-if="bootstrapping" class="size-4 mr-2 animate-spin" />
              创建 platform admin
            </Button>
          </form>
        </section>
      </div>
      <p v-if="error" class="mt-4 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">{{ error }}</p>
    </main>

    <main v-else class="p-3 sm:p-4 space-y-4">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <nav class="flex flex-wrap gap-1 rounded-md border bg-muted/30 p-1">
          <button
            v-for="tab in [
              ['tenants', 'Tenants'],
              ['plans', 'Plans'],
              ['images', 'Runtime Images'],
              ['capacity', 'Capacity'],
              ['placements', 'Placements'],
              ['audit', 'Audit'],
            ]"
            :key="tab[0]"
            class="rounded px-3 py-1.5 text-sm"
            :class="activeTab === tab[0] ? 'bg-background shadow-sm' : 'text-muted-foreground hover:text-foreground'"
            @click="activeTab = tab[0] as any"
          >
            {{ tab[1] }}
          </button>
        </nav>
        <Button variant="outline" size="sm" :disabled="loading" @click="refreshAll">
          <RefreshCw class="size-4 mr-2" :class="{ 'animate-spin': loading }" />
          刷新
        </Button>
      </div>

      <p v-if="notice" class="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-300">
        <CheckCircle2 class="inline size-4 mr-1 align-[-2px]" />
        {{ notice }}
      </p>
      <p v-if="error" class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
        <AlertTriangle class="inline size-4 mr-1 align-[-2px]" />
        {{ error }}
      </p>

      <section v-if="activeTab === 'tenants'" class="grid gap-4 xl:grid-cols-[1fr_360px]">
        <div class="rounded-lg border bg-card overflow-hidden">
          <div class="border-b px-4 py-3 flex items-center justify-between">
            <h2 class="text-sm font-semibold">Tenant 管理</h2>
            <Badge variant="outline">{{ tenants.length }}</Badge>
          </div>
          <div class="overflow-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>名称</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead>Quota</TableHead>
                  <TableHead>Namespace</TableHead>
                  <TableHead>Plan</TableHead>
                  <TableHead>保留</TableHead>
                  <TableHead class="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <TableRow v-for="tenant in tenants" :key="tenant.id" class="cursor-pointer" @click="pickTenant(tenant)">
                  <TableCell>
                    <div class="font-medium">{{ tenant.name }}</div>
                    <div class="text-xs text-muted-foreground">{{ tenant.slug }}</div>
                  </TableCell>
                  <TableCell><Badge :variant="statusVariant(tenant.status) as any">{{ tenant.status }}</Badge></TableCell>
                  <TableCell>{{ tenant.activeSessionLimit }} / {{ tenant.maxSessionSeconds }}s</TableCell>
                  <TableCell class="font-mono text-xs">{{ tenant.runtimeNamespace }}</TableCell>
                  <TableCell>{{ tenant.planCode || tenant.planId || '-' }}</TableCell>
                  <TableCell class="text-xs">
                    <div v-if="tenant.status === 'deleted'">
                      <div>{{ tenant.retentionUntil || '-' }}</div>
                      <div v-if="tenant.purgeRequestedAt" class="text-muted-foreground">{{ tenant.purgeRequestedAt }}</div>
                    </div>
                    <span v-else>-</span>
                  </TableCell>
                  <TableCell class="text-right whitespace-nowrap">
                    <Button v-if="tenant.status !== 'deleted'" variant="outline" size="sm" @click.stop="tenantAction(tenant, 'runtime-revoke')">Revoke</Button>
                    <Button v-if="tenant.status === 'active'" variant="outline" size="sm" @click.stop="tenantAction(tenant, 'suspend')">停用</Button>
                    <Button v-if="tenant.status === 'suspended'" variant="outline" size="sm" @click.stop="tenantAction(tenant, 'resume')">恢复</Button>
                    <Button v-if="tenant.status === 'deleted'" variant="outline" size="sm" class="ml-2" @click.stop="tenantAction(tenant, 'purge-request')">Purge</Button>
                    <Button v-if="tenant.status === 'deleted' && tenant.purgeRequestedAt" variant="destructive" size="sm" class="ml-2" @click.stop="tenantAction(tenant, 'purge')">Purge DB</Button>
                    <Button v-if="tenant.status !== 'deleted'" variant="destructive" size="sm" class="ml-2" @click.stop="tenantAction(tenant, 'delete')">删除</Button>
                  </TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </div>
        </div>

        <div class="space-y-4">
          <section class="rounded-lg border bg-card p-4">
            <h3 class="mb-3 text-sm font-semibold">创建 tenant</h3>
            <form class="space-y-3" @submit.prevent="createTenant">
              <div class="grid grid-cols-2 gap-3">
                <div class="space-y-1.5">
                  <Label>名称</Label>
                  <Input v-model="tenantForm.name" required />
                </div>
                <div class="space-y-1.5">
                  <Label>Slug</Label>
                  <Input v-model="tenantForm.slug" />
                </div>
              </div>
              <div class="grid grid-cols-2 gap-3">
                <div class="space-y-1.5">
                  <Label>Active quota</Label>
                  <Input v-model.number="tenantForm.activeSessionLimit" type="number" min="0" />
                </div>
                <div class="space-y-1.5">
                  <Label>最长秒数</Label>
                  <Input v-model.number="tenantForm.maxSessionSeconds" type="number" min="1" />
                </div>
              </div>
              <div class="space-y-1.5">
                <Label>初始 superadmin 邮箱</Label>
                <Input v-model="tenantForm.initialAdminEmail" />
              </div>
              <div class="grid grid-cols-2 gap-3">
                <Input v-model="tenantForm.initialAdminName" placeholder="姓名" />
                <Input v-model="tenantForm.initialAdminPassword" type="password" placeholder="密码" />
              </div>
              <Input v-model="tenantForm.reason" />
              <Button type="submit" class="w-full" :disabled="!canAdmin || loading">创建</Button>
            </form>
          </section>

          <section class="rounded-lg border bg-card p-4">
            <h3 class="mb-3 text-sm font-semibold">调整 quota</h3>
            <form class="space-y-3" @submit.prevent="updateQuota">
              <select v-model="quotaForm.tenantId" class="h-9 w-full rounded-md border bg-background px-3 text-sm">
                <option v-for="tenant in tenants" :key="tenant.id" :value="tenant.id">{{ tenant.name }}</option>
              </select>
              <div class="grid grid-cols-2 gap-3">
                <Input v-model.number="quotaForm.activeSessionLimit" type="number" min="0" />
                <Input v-model.number="quotaForm.maxSessionSeconds" type="number" min="1" />
              </div>
              <Input v-model="quotaForm.reason" />
              <p v-if="selectedTenant" class="text-xs text-muted-foreground">当前 tenant：{{ selectedTenant.runtimeNamespace }}</p>
              <Button type="submit" class="w-full" :disabled="!canAdmin || !quotaForm.tenantId || loading">保存 quota</Button>
            </form>
          </section>
        </div>
      </section>

      <section v-else-if="activeTab === 'plans'" class="grid gap-4 xl:grid-cols-[1fr_360px]">
        <div class="rounded-lg border bg-card overflow-hidden">
          <div class="border-b px-4 py-3 flex items-center gap-2">
            <Layers class="size-4 text-muted-foreground" />
            <h2 class="text-sm font-semibold">付费档位 / entitlement 模板</h2>
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Code</TableHead>
                <TableHead>名称</TableHead>
                <TableHead>默认 quota</TableHead>
                <TableHead>状态</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow v-for="plan in plans" :key="plan.id">
                <TableCell class="font-mono text-xs">{{ plan.code }}</TableCell>
                <TableCell>{{ plan.name }}</TableCell>
                <TableCell>{{ plan.defaultActiveSessionLimit }} / {{ plan.defaultMaxSessionSeconds }}s</TableCell>
                <TableCell><Badge :variant="plan.isActive ? 'default' : 'secondary'">{{ plan.isActive ? 'active' : 'inactive' }}</Badge></TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </div>
        <section class="rounded-lg border bg-card p-4">
          <h3 class="mb-3 text-sm font-semibold">创建 plan</h3>
          <form class="space-y-3" @submit.prevent="createPlan">
            <Input v-model="planForm.code" placeholder="code" required />
            <Input v-model="planForm.name" placeholder="名称" required />
            <div class="grid grid-cols-2 gap-3">
              <Input v-model.number="planForm.defaultActiveSessionLimit" type="number" min="0" />
              <Input v-model.number="planForm.defaultMaxSessionSeconds" type="number" min="1" />
            </div>
            <Input v-model="planForm.reason" />
            <Button type="submit" class="w-full" :disabled="!canAdmin || loading">创建</Button>
          </form>
        </section>
      </section>

      <section v-else-if="activeTab === 'images'" class="grid gap-4 xl:grid-cols-[1fr_420px]">
        <div class="rounded-lg border bg-card overflow-hidden">
          <div class="border-b px-4 py-3 flex items-center gap-2">
            <ShieldCheck class="size-4 text-muted-foreground" />
            <h2 class="text-sm font-semibold">Approved Runtime Images</h2>
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Runtime class</TableHead>
                <TableHead>Image</TableHead>
                <TableHead>Digest</TableHead>
                <TableHead>状态</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow v-for="image in images" :key="image.id">
                <TableCell>{{ image.runtimeClass }}</TableCell>
                <TableCell class="font-mono text-xs">{{ image.imageRef }}</TableCell>
                <TableCell class="font-mono text-xs">{{ image.imageDigest }}</TableCell>
                <TableCell>
                  <Badge :variant="statusVariant(image.scanStatus) as any">{{ image.scanStatus }}</Badge>
                  <Badge class="ml-1" :variant="statusVariant(image.approvalStatus) as any">{{ image.approvalStatus }}</Badge>
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </div>
        <section class="rounded-lg border bg-card p-4">
          <h3 class="mb-3 text-sm font-semibold">登记 approved digest</h3>
          <form class="space-y-3" @submit.prevent="createRuntimeImage">
            <select v-model="imageForm.runtimeClass" class="h-9 w-full rounded-md border bg-background px-3 text-sm">
              <option value="standard_chrome">standard_chrome</option>
              <option value="cloak_chromium">cloak_chromium</option>
            </select>
            <Input v-model="imageForm.imageRef" placeholder="registry.example/browser-runtime" required />
            <Input v-model="imageForm.imageDigest" placeholder="sha256:..." required />
            <div class="grid grid-cols-2 gap-3">
              <select v-model="imageForm.scanStatus" class="h-9 rounded-md border bg-background px-3 text-sm">
                <option value="pending">pending</option>
                <option value="passed">passed</option>
                <option value="failed">failed</option>
              </select>
              <select v-model="imageForm.approvalStatus" class="h-9 rounded-md border bg-background px-3 text-sm">
                <option value="pending">pending</option>
                <option value="approved">approved</option>
                <option value="rejected">rejected</option>
                <option value="revoked">revoked</option>
              </select>
            </div>
            <Input v-model="imageForm.reason" />
            <Button type="submit" class="w-full" :disabled="!canAdmin || loading">登记</Button>
          </form>
        </section>
      </section>

      <section v-else-if="activeTab === 'capacity'" class="grid gap-4 xl:grid-cols-[1fr_420px]">
        <div class="space-y-4">
          <section class="rounded-lg border bg-card overflow-hidden">
            <div class="border-b px-4 py-3 flex items-center justify-between">
              <h2 class="text-sm font-semibold">Runtime pool 容量</h2>
              <Badge variant="outline">{{ runtimePools.length }}</Badge>
            </div>
            <div class="overflow-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Pool</TableHead>
                    <TableHead>Runtime classes</TableHead>
                    <TableHead>Capacity</TableHead>
                    <TableHead>状态</TableHead>
                    <TableHead class="text-right">操作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  <TableRow v-for="pool in runtimePools" :key="pool.id">
                    <TableCell>
                      <div class="font-medium">{{ pool.name }}</div>
                      <div class="font-mono text-xs text-muted-foreground">{{ pool.id }}</div>
                    </TableCell>
                    <TableCell class="text-xs">{{ pool.runtimeClasses.join(', ') || '-' }}</TableCell>
                    <TableCell>{{ pool.activeReservationCount }} / {{ pool.activeSessionCapacity }}</TableCell>
                    <TableCell>
                      <Badge :variant="statusVariant(pool.isEnabled ? (pool.isDraining ? 'draining' : 'active') : 'disabled') as any">
                        {{ pool.isEnabled ? (pool.isDraining ? 'draining' : 'active') : 'disabled' }}
                      </Badge>
                    </TableCell>
                    <TableCell class="text-right whitespace-nowrap">
                      <Button v-if="!pool.isDraining" variant="outline" size="sm" @click="runtimePoolAction(pool, 'drain')">Drain</Button>
                      <Button v-else variant="outline" size="sm" @click="runtimePoolAction(pool, 'resume')">Resume</Button>
                      <Button v-if="pool.isEnabled" variant="destructive" size="sm" class="ml-2" @click="runtimePoolAction(pool, 'disable')">Disable</Button>
                      <Button v-else variant="outline" size="sm" class="ml-2" @click="runtimePoolAction(pool, 'enable')">Enable</Button>
                    </TableCell>
                  </TableRow>
                </TableBody>
              </Table>
            </div>
          </section>

          <section class="rounded-lg border bg-card overflow-hidden">
            <div class="border-b px-4 py-3 flex items-center justify-between">
              <h2 class="text-sm font-semibold">Runtime node 状态</h2>
              <Badge variant="outline">{{ runtimeNodes.length }}</Badge>
            </div>
            <div class="overflow-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Node</TableHead>
                    <TableHead>Pool</TableHead>
                    <TableHead>状态</TableHead>
                    <TableHead>Allocatable</TableHead>
                    <TableHead class="text-right">操作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  <TableRow v-for="node in runtimeNodes" :key="node.id">
                    <TableCell class="font-mono text-xs">{{ node.providerNodeName }}</TableCell>
                    <TableCell class="font-mono text-xs">{{ node.runtimePoolId }}</TableCell>
                    <TableCell><Badge :variant="statusVariant(node.status) as any">{{ node.status }}</Badge></TableCell>
                    <TableCell class="text-xs">{{ JSON.stringify(node.allocatable || {}) }}</TableCell>
                    <TableCell class="text-right whitespace-nowrap">
                      <Button variant="outline" size="sm" @click="runtimeNodeAction(node, 'draining')">Drain</Button>
                      <Button variant="outline" size="sm" class="ml-2" @click="runtimeNodeAction(node, 'active')">Active</Button>
                      <Button variant="destructive" size="sm" class="ml-2" @click="runtimeNodeAction(node, 'disabled')">Disable</Button>
                    </TableCell>
                  </TableRow>
                </TableBody>
              </Table>
            </div>
          </section>
        </div>

        <div class="space-y-4">
          <section class="rounded-lg border bg-card p-4">
            <h3 class="mb-3 text-sm font-semibold">创建 runtime pool</h3>
            <form class="space-y-3" @submit.prevent="createRuntimePool">
              <Input v-model="poolForm.name" required />
              <Input v-model="poolForm.runtimeClasses" />
              <Input v-model.number="poolForm.activeSessionCapacity" type="number" min="0" />
              <Input v-model="poolForm.reason" />
              <Button type="submit" class="w-full" :disabled="!canAdmin || loading">创建</Button>
            </form>
          </section>

          <section class="rounded-lg border bg-card p-4">
            <h3 class="mb-3 text-sm font-semibold">登记 runtime node</h3>
            <form class="space-y-3" @submit.prevent="registerRuntimeNode">
              <select v-model="nodeForm.runtimePoolId" class="h-9 w-full rounded-md border bg-background px-3 text-sm">
                <option v-for="pool in runtimePools" :key="pool.id" :value="pool.id">{{ pool.name }}</option>
              </select>
              <Input v-model="nodeForm.providerNodeName" placeholder="k8s-node-name" required />
              <select v-model="nodeForm.status" class="h-9 w-full rounded-md border bg-background px-3 text-sm">
                <option value="active">active</option>
                <option value="draining">draining</option>
                <option value="disabled">disabled</option>
              </select>
              <Input v-model="nodeForm.reason" />
              <Button type="submit" class="w-full" :disabled="!canAdmin || loading">登记</Button>
            </form>
          </section>
        </div>
      </section>

      <section v-else-if="activeTab === 'placements'" class="rounded-lg border bg-card overflow-hidden">
        <div class="border-b px-4 py-3 flex items-center gap-2">
          <Activity class="size-4 text-muted-foreground" />
          <h2 class="text-sm font-semibold">Runtime placement 账本</h2>
        </div>
        <div class="overflow-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Session</TableHead>
                <TableHead>Tenant</TableHead>
                <TableHead>Phase</TableHead>
                <TableHead>Namespace</TableHead>
                <TableHead>Pod / Gateway</TableHead>
                <TableHead>Failure</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow v-for="placement in placements" :key="placement.id">
                <TableCell class="font-mono text-xs">{{ placement.sessionId }}</TableCell>
                <TableCell class="font-mono text-xs">{{ placement.tenantId }}</TableCell>
                <TableCell><Badge :variant="statusVariant(placement.runtimePhase) as any">{{ placement.runtimePhase }}</Badge></TableCell>
                <TableCell class="font-mono text-xs">{{ placement.runtimeNamespace }}</TableCell>
                <TableCell class="font-mono text-xs">{{ placement.runtimePodName || '-' }} / {{ placement.egressGatewayPodName || '-' }}</TableCell>
                <TableCell class="text-xs text-destructive">{{ placement.failureReason || placement.lastError || '-' }}</TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </div>
      </section>

      <section v-else class="rounded-lg border bg-card overflow-hidden">
        <div class="border-b px-4 py-3 flex items-center justify-between">
          <h2 class="text-sm font-semibold">Platform audit</h2>
          <Badge variant="outline">{{ auditEvents.length }}</Badge>
        </div>
        <div class="overflow-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>时间</TableHead>
                <TableHead>动作</TableHead>
                <TableHead>结果</TableHead>
                <TableHead>Tenant</TableHead>
                <TableHead>原因 / 错误</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow v-for="event in auditEvents" :key="event.id">
                <TableCell class="whitespace-nowrap text-xs">{{ event.createdAt || '-' }}</TableCell>
                <TableCell>{{ event.action }}</TableCell>
                <TableCell><Badge :variant="statusVariant(event.outcome) as any">{{ event.outcome }}</Badge></TableCell>
                <TableCell class="font-mono text-xs">{{ event.tenantId || '-' }}</TableCell>
                <TableCell class="text-xs">{{ event.error || event.reason || '-' }}</TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </div>
      </section>
    </main>
  </div>
</template>
