<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { CheckCircle2, ExternalLink, Loader2, TriangleAlert } from 'lucide-vue-next'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'

const route = useRoute()
const status = ref<'authorizing' | 'opening' | 'error'>('authorizing')
const error = ref('')
const deepLink = ref('')

const title = computed(() => {
  if (status.value === 'opening') return '已连接 Browser Lite'
  if (status.value === 'error') return '无法连接 Browser Lite'
  return '正在连接 Browser Lite'
})

const description = computed(() => {
  if (status.value === 'opening') return '正在返回 Browser Lite。如果应用没有自动打开，请点击下面的按钮。'
  if (status.value === 'error') return error.value
  return '正在确认你的 Browser Pilot 账号并授权这台 Mac。'
})

async function authorize() {
  const requestToken = typeof route.query.request === 'string' ? route.query.request : ''
  if (!requestToken) {
    status.value = 'error'
    error.value = '登录请求缺少必要参数，请返回 Browser Lite 后重试。'
    return
  }
  try {
    const response = await api('/api/browser-lite/auth/authorize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ requestToken }),
    })
    const body = await response.json().catch(() => ({}))
    if (!response.ok || typeof body.deepLink !== 'string') {
      throw new Error(typeof body.detail === 'string' ? body.detail : 'Browser Lite 授权失败')
    }
    deepLink.value = body.deepLink
    status.value = 'opening'
    window.location.assign(deepLink.value)
  } catch (reason: any) {
    status.value = 'error'
    error.value = reason?.message || 'Browser Lite 授权失败，请重新发起登录。'
  }
}

onMounted(authorize)
</script>

<template>
  <div class="min-h-screen flex items-center justify-center px-4">
    <div class="w-full max-w-[420px] rounded-xl border bg-card p-7 text-center shadow-sm">
      <img src="/brand/browser-pilot.svg" alt="" class="mx-auto mb-5 size-12 object-contain mix-blend-multiply dark:invert dark:mix-blend-screen" />
      <div class="mx-auto mb-4 flex size-11 items-center justify-center rounded-full bg-muted">
        <Loader2 v-if="status === 'authorizing'" class="size-5 animate-spin text-muted-foreground" />
        <CheckCircle2 v-else-if="status === 'opening'" class="size-5 text-emerald-600" />
        <TriangleAlert v-else class="size-5 text-destructive" />
      </div>
      <h1 class="text-lg font-semibold">{{ title }}</h1>
      <p class="mt-2 text-sm leading-6 text-muted-foreground">{{ description }}</p>
      <Button v-if="status === 'opening' && deepLink" class="mt-6 w-full" as-child>
        <a :href="deepLink"><ExternalLink class="mr-2 size-4" />打开 Browser Lite</a>
      </Button>
    </div>
  </div>
</template>
