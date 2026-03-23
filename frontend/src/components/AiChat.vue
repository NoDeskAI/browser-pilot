<script setup lang="ts">
import { ref, nextTick, onMounted, onBeforeUnmount, computed, watch } from 'vue'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ChatBlock {
  type: 'text' | 'tool_call' | 'tool_result' | 'error'
  content?: string
  id?: string
  toolName?: string
  args?: Record<string, any>
  result?: any
  screenshot?: string
  loading?: boolean
}

interface ChatMessage {
  role: 'user' | 'assistant'
  blocks: ChatBlock[]
}

const TOOL_LABELS: Record<string, string> = {
  browser_navigate: '导航',
  browser_observe: '观察页面',
  browser_click: '点击坐标',
  browser_click_element: '点击元素',
  browser_type: '输入文本',
  browser_key: '按键',
  browser_scroll: '滚动',
  browser_get_page_info: '页面信息',
  docker_status: '容器状态',
  docker_start: '启动服务',
  docker_stop: '停止服务',
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const messages = ref<ChatMessage[]>([])
const input = ref('')
const loading = ref(false)
const chatContainer = ref<HTMLElement>()
const configOpen = ref(false)
const previewImage = ref<string | null>(null)
let currentAbort: AbortController | null = null

const emit = defineEmits<{
  (e: 'browser-active'): void
}>()

const apiKey = ref(localStorage.getItem('ai_api_key') || '')
const baseUrl = ref(localStorage.getItem('ai_base_url') || 'https://api.openai.com/v1')
const model = ref(localStorage.getItem('ai_model') || 'gpt-4o-mini')
const apiType = ref<'openai' | 'anthropic'>(
  (localStorage.getItem('ai_api_type') as 'openai' | 'anthropic') || 'openai'
)

const urlPresets = [
  { label: 'OpenAI', url: 'https://api.openai.com/v1', type: 'openai' as const, defaultModel: 'gpt-4o-mini',
    presetModels: ['gpt-4o', 'gpt-4o-mini', 'o1', 'o1-mini', 'o3-mini'] },
  { label: 'Anthropic', url: 'https://api.anthropic.com', type: 'anthropic' as const, defaultModel: 'claude-sonnet-4-20250514',
    presetModels: ['claude-sonnet-4-20250514', 'claude-3-5-haiku-20241022'] },
  { label: 'DeepSeek', url: 'https://api.deepseek.com/v1', type: 'openai' as const, defaultModel: 'deepseek-chat',
    presetModels: ['deepseek-chat', 'deepseek-reasoner'] },
  { label: 'SiliconFlow', url: 'https://api.siliconflow.cn/v1', type: 'openai' as const, defaultModel: 'Qwen/Qwen2.5-7B-Instruct',
    presetModels: ['Qwen/Qwen2.5-7B-Instruct'] },
  { label: 'MiniMax', url: 'https://api.minimaxi.com/v1', type: 'openai' as const, defaultModel: 'MiniMax-M2.5',
    presetModels: ['MiniMax-M2.7', 'MiniMax-M2.5', 'MiniMax-M2.1', 'MiniMax-M2'] },
]

const modelOptions = ref<string[]>([])
const modelLoading = ref(false)
const modelDropdownOpen = ref(false)
const comboboxRef = ref<HTMLElement>()
let fetchDebounceTimer: ReturnType<typeof setTimeout> | null = null

function getPresetModels(): string[] {
  const match = urlPresets.find(p => p.url === baseUrl.value)
  return match ? match.presetModels : []
}

const filteredModels = computed(() => {
  const q = model.value.trim().toLowerCase()
  const list = modelOptions.value.length ? modelOptions.value : getPresetModels()
  if (!q) return list
  return list.filter(m => m.toLowerCase().includes(q))
})

async function fetchModels() {
  if (!apiKey.value || !baseUrl.value) {
    modelOptions.value = getPresetModels()
    return
  }
  modelLoading.value = true
  try {
    const resp = await fetch('/api/ai/models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ baseUrl: baseUrl.value, apiKey: apiKey.value, apiType: apiType.value }),
    })
    const data = await resp.json()
    modelOptions.value = data.models?.length ? data.models : getPresetModels()
  } catch {
    modelOptions.value = getPresetModels()
  } finally {
    modelLoading.value = false
  }
}

function applyUrlPreset(p: typeof urlPresets[number]) {
  baseUrl.value = p.url
  apiType.value = p.type
  model.value = p.defaultModel
  fetchModels()
}

function selectModel(m: string) {
  model.value = m
  modelDropdownOpen.value = false
}

function handleComboboxClickOutside(e: MouseEvent) {
  if (comboboxRef.value && !comboboxRef.value.contains(e.target as Node)) {
    modelDropdownOpen.value = false
  }
}

watch([apiKey, baseUrl], () => {
  if (fetchDebounceTimer) clearTimeout(fetchDebounceTimer)
  fetchDebounceTimer = setTimeout(() => fetchModels(), 600)
})

function saveConfig() {
  localStorage.setItem('ai_api_key', apiKey.value)
  localStorage.setItem('ai_base_url', baseUrl.value)
  localStorage.setItem('ai_model', model.value)
  localStorage.setItem('ai_api_type', apiType.value)
  configOpen.value = false
}

// Flatten all blocks for rendering (preserving message boundaries)
const flatBlocks = computed(() => {
  const result: { role: 'user' | 'assistant'; block: ChatBlock; msgIdx: number }[] = []
  messages.value.forEach((msg, i) => {
    for (const block of msg.blocks) {
      result.push({ role: msg.role, block, msgIdx: i })
    }
  })
  return result
})

// ---------------------------------------------------------------------------
// Scroll & helpers
// ---------------------------------------------------------------------------

async function scrollToBottom() {
  await nextTick()
  if (chatContainer.value) {
    chatContainer.value.scrollTop = chatContainer.value.scrollHeight
  }
}

function toolArgsSummary(args: Record<string, any> | undefined): string {
  if (!args) return ''
  const entries = Object.entries(args)
  if (entries.length === 0) return ''
  return entries.map(([k, v]) => {
    const val = typeof v === 'string' ? (v.length > 40 ? v.slice(0, 40) + '...' : v) : JSON.stringify(v)
    return `${k}: ${val}`
  }).join(', ')
}

function toolResultSummary(result: any): string {
  if (!result) return ''
  if (result.error) return `错误: ${result.error}`
  if (result.currentPage?.url || result.currentPage?.title) {
    const title = result.currentPage.title || '(无标题)'
    const url = result.currentPage.url || ''
    const count = typeof result.currentPage.elementCount === 'number'
      ? ` · 元素 ${result.currentPage.elementCount}`
      : ''
    return `页面: ${title}${url ? ` · ${url}` : ''}${count}`
  }
  if (result.url || result.title || typeof result.elementCount === 'number') {
    const title = result.title || '(无标题)'
    const url = result.url || ''
    const count = typeof result.elementCount === 'number' ? ` · 元素 ${result.elementCount}` : ''
    return `页面: ${title}${url ? ` · ${url}` : ''}${count}`
  }
  if (result.statuses) {
    const entries = Object.entries(result.statuses as Record<string, string>)
    return entries.map(([k, v]) => `${k}: ${v}`).join(', ')
  }
  if (result.ok === true) return '成功'
  return JSON.stringify(result).slice(0, 80)
}

// ---------------------------------------------------------------------------
// Send message
// ---------------------------------------------------------------------------

function stopGeneration() {
  if (currentAbort) {
    currentAbort.abort()
    currentAbort = null
  }
  loading.value = false
  const last = messages.value[messages.value.length - 1]
  if (last?.role === 'assistant') {
    const pendingCalls = last.blocks.filter(b => b.type === 'tool_call' && b.loading)
    for (const b of pendingCalls) b.loading = false
    if (last.blocks.length === 0 || last.blocks.every(b => b.type === 'tool_call' || b.type === 'tool_result')) {
      last.blocks.push({ type: 'text', content: '(已中断)' })
    }
  }
}

const MAX_RECENT_TURNS = 3

function buildApiMessages() {
  const all = messages.value.filter(m => m.blocks.some(b => (b.type === 'text' && b.content) || b.type === 'tool_call'))

  const recentStart = Math.max(0, all.length - MAX_RECENT_TURNS * 2)
  const oldMsgs = all.slice(0, recentStart)
  const recentMsgs = all.slice(recentStart)

  const result: { role: 'user' | 'assistant'; content: string }[] = []

  if (oldMsgs.length > 0) {
    const lines: string[] = []
    for (const m of oldMsgs) {
      const text = m.blocks.filter(b => b.type === 'text' && b.content).map(b => b.content!).join(' ').slice(0, 100)
      if (m.role === 'user') {
        lines.push(`用户: ${text}`)
      } else {
        const tools = m.blocks.filter(b => b.type === 'tool_call').map(b => b.toolName).join(', ')
        lines.push(`助手: ${tools ? `[调用了 ${tools}] ` : ''}${text.slice(0, 60)}`)
      }
    }
    result.push({ role: 'user', content: `[之前的对话摘要]\n${lines.join('\n')}\n[摘要结束]` })
    result.push({ role: 'assistant', content: '好的，我了解之前的对话内容。请告诉我下一步需要做什么。' })
  }

  for (const m of recentMsgs) {
    if (m.role === 'user') {
      result.push({ role: 'user', content: m.blocks.filter(b => b.type === 'text').map(b => b.content).join('\n') })
    } else {
      const textParts = m.blocks.filter(b => b.type === 'text' && b.content).map(b => b.content!)
      const toolCalls = m.blocks.filter(b => b.type === 'tool_call' && b.toolName)
      const toolResults = m.blocks.filter(b => b.type === 'tool_result' && b.id)
      if (toolCalls.length > 0) {
        const summary = toolCalls.map((tc) => {
          const tr = toolResults.find(r => r.id === tc.id)
          const status = tr?.result?.ok === false ? `FAIL` : 'ok'
          const page = tr?.result?.currentPage ? ` ${tr.result.currentPage.url}` : ''
          return `${tc.toolName}=>${status}${page}`
        }).join('; ')
        result.push({ role: 'assistant', content: `[tools: ${summary}]\n${textParts.join('\n').slice(0, 200)}` })
      } else {
        result.push({ role: 'assistant', content: textParts.join('\n') || '(no content)' })
      }
    }
  }

  return result
}

async function send() {
  const text = input.value.trim()
  if (!text) return

  if (loading.value) {
    stopGeneration()
    await nextTick()
  }

  messages.value.push({ role: 'user', blocks: [{ type: 'text', content: text }] })
  input.value = ''
  loading.value = true
  await scrollToBottom()

  const abort = new AbortController()
  currentAbort = abort

  const fullMessages = buildApiMessages()

  const assistantMsg: ChatMessage = { role: 'assistant', blocks: [] }
  messages.value.push(assistantMsg)
  const aIdx = messages.value.length - 1
  const assistantEntry = messages.value[aIdx]!

  try {
    const resp = await fetch('/api/ai/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        messages: fullMessages,
        apiKey: apiKey.value,
        baseUrl: baseUrl.value,
        model: model.value,
        apiType: apiType.value,
      }),
      signal: abort.signal,
    })

    if (!resp.ok) {
      const err = await resp.json()
      assistantEntry.blocks.push({ type: 'error', content: err.error || resp.statusText })
      return
    }

    const reader = resp.body?.getReader()
    if (!reader) return

    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        const raw = line.slice(6).trim()
        if (!raw || raw === '[DONE]') continue

        try {
          const evt = JSON.parse(raw)
          const blocks = assistantEntry.blocks

          switch (evt.type) {
            case 'text': {
              const lastBlock = blocks[blocks.length - 1]
              if (lastBlock?.type === 'text') {
                lastBlock.content = (lastBlock.content || '') + evt.content
              } else {
                blocks.push({ type: 'text', content: evt.content })
              }
              break
            }
            case 'tool_call':
              blocks.push({
                type: 'tool_call',
                id: evt.id,
                toolName: evt.name,
                args: evt.args,
                loading: true,
              })
              if (evt.name?.startsWith('browser_')) {
                emit('browser-active')
              }
              break
            case 'tool_result': {
              const callBlock = blocks.find(b => b.type === 'tool_call' && b.id === evt.id)
              if (callBlock) callBlock.loading = false
              blocks.push({
                type: 'tool_result',
                id: evt.id,
                toolName: evt.name,
                result: evt.result,
                screenshot: evt.screenshot,
              })
              break
            }
            case 'error':
              blocks.push({ type: 'error', content: evt.message })
              break
            case 'done':
              break
          }
          await scrollToBottom()
        } catch {}
      }
    }
  } catch (e: any) {
    if (e.name === 'AbortError') return
    assistantEntry.blocks.push({ type: 'error', content: `请求失败: ${e.message}` })
  } finally {
    if (currentAbort === abort) {
      currentAbort = null
      loading.value = false
    }
    await scrollToBottom()
  }
}

function clearChat() {
  messages.value = []
}

onMounted(() => {
  if (!apiKey.value) configOpen.value = true
  document.addEventListener('click', handleComboboxClickOutside)
  if (apiKey.value && baseUrl.value) fetchModels()
})

onBeforeUnmount(() => {
  document.removeEventListener('click', handleComboboxClickOutside)
  if (fetchDebounceTimer) clearTimeout(fetchDebounceTimer)
})
</script>

<template>
  <div class="h-full flex flex-col bg-[var(--color-surface)]">
    <!-- Header -->
    <div class="shrink-0 flex items-center justify-between px-3 py-2.5 border-b border-[var(--color-border)]">
      <div class="flex items-center gap-2">
        <svg class="w-4 h-4 text-[var(--color-accent)]" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z" />
        </svg>
        <span class="text-sm font-semibold">AI Agent</span>
        <span class="text-[9px] px-1.5 py-0.5 rounded-full bg-[var(--color-accent)]/15 text-[var(--color-accent)]">ReAct</span>
      </div>
      <div class="flex items-center gap-1">
        <button @click="clearChat" class="w-7 h-7 flex items-center justify-center rounded-md hover:bg-[var(--color-surface-hover)] text-[var(--color-text-dim)] transition-colors" title="清除对话">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" /></svg>
        </button>
        <button @click="configOpen = !configOpen" class="w-7 h-7 flex items-center justify-center rounded-md hover:bg-[var(--color-surface-hover)] text-[var(--color-text-dim)] transition-colors" :class="configOpen ? 'bg-[var(--color-surface-hover)] text-[var(--color-accent)]' : ''" title="API 设置">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 011.37.49l1.296 2.247a1.125 1.125 0 01-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.723 7.723 0 010 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.955.26 1.43l-1.298 2.247a1.125 1.125 0 01-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 01-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.94-1.11.94h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 01-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 01-1.369-.49l-1.297-2.247a1.125 1.125 0 01.26-1.431l1.004-.827c.292-.24.437-.613.43-.991a6.932 6.932 0 010-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 01-.26-1.43l1.297-2.247a1.125 1.125 0 011.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28z" /><path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></svg>
        </button>
      </div>
    </div>

    <!-- Config dialog (teleported) -->
    <Teleport to="body">
      <div v-if="configOpen" class="fixed inset-0 z-[9998] bg-black/50 flex items-center justify-center" @click.self="configOpen = false" @keydown.escape.window="configOpen = false">
        <div class="max-w-sm w-full mx-4 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl shadow-2xl">
          <div class="flex items-center justify-between px-4 py-3 border-b border-[var(--color-border)]">
            <span class="text-sm font-semibold text-[var(--color-text)]">API 设置</span>
            <button @click="configOpen = false" class="w-6 h-6 flex items-center justify-center rounded-md hover:bg-[var(--color-surface-hover)] text-[var(--color-text-dim)] hover:text-[var(--color-text)] transition-colors">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>
            </button>
          </div>
          <div class="p-4 space-y-3">
            <div>
              <label class="text-[10px] text-[var(--color-text-dim)] uppercase tracking-wider">API 类型</label>
              <div class="flex gap-1 mt-1">
                <button @click="apiType = 'openai'" class="flex-1 py-1 rounded text-[10px] font-medium border transition-colors" :class="apiType === 'openai' ? 'border-[var(--color-accent)] text-[var(--color-accent)] bg-[var(--color-accent)]/10' : 'border-[var(--color-border)] text-[var(--color-text-dim)]'">OpenAI 兼容</button>
                <button @click="apiType = 'anthropic'" class="flex-1 py-1 rounded text-[10px] font-medium border transition-colors" :class="apiType === 'anthropic' ? 'border-[var(--color-accent)] text-[var(--color-accent)] bg-[var(--color-accent)]/10' : 'border-[var(--color-border)] text-[var(--color-text-dim)]'">Anthropic</button>
              </div>
            </div>
            <div>
              <label class="text-[10px] text-[var(--color-text-dim)] uppercase tracking-wider">API Base URL</label>
              <input v-model="baseUrl" class="w-full mt-0.5 px-2.5 py-1.5 rounded-md bg-[var(--color-bg)] border border-[var(--color-border)] text-xs text-[var(--color-text)] outline-none focus:border-[var(--color-accent)] transition-colors" :placeholder="apiType === 'anthropic' ? 'https://api.anthropic.com' : 'https://api.openai.com/v1'" />
              <div class="flex flex-wrap gap-1 mt-1.5">
                <button v-for="p in urlPresets" :key="p.label" @click="applyUrlPreset(p)"
                  class="px-2 py-0.5 rounded text-[10px] border transition-colors"
                  :class="baseUrl === p.url ? 'border-[var(--color-accent)] text-[var(--color-accent)] bg-[var(--color-accent)]/10' : 'border-[var(--color-border)] text-[var(--color-text-dim)] hover:border-[var(--color-text-dim)]'"
                >{{ p.label }}</button>
              </div>
            </div>
            <div>
              <label class="text-[10px] text-[var(--color-text-dim)] uppercase tracking-wider">API Key</label>
              <input v-model="apiKey" type="password" class="w-full mt-0.5 px-2.5 py-1.5 rounded-md bg-[var(--color-bg)] border border-[var(--color-border)] text-xs text-[var(--color-text)] outline-none focus:border-[var(--color-accent)] transition-colors" placeholder="sk-..." />
            </div>
            <div ref="comboboxRef" class="relative">
              <label class="text-[10px] text-[var(--color-text-dim)] uppercase tracking-wider">Model</label>
              <div class="relative mt-0.5">
                <input v-model="model"
                  @focus="modelDropdownOpen = true"
                  @keydown.escape="modelDropdownOpen = false"
                  class="w-full px-2.5 py-1.5 pr-7 rounded-md bg-[var(--color-bg)] border border-[var(--color-border)] text-xs text-[var(--color-text)] outline-none focus:border-[var(--color-accent)] transition-colors"
                  placeholder="gpt-4o-mini" />
                <button @click.stop="modelDropdownOpen = !modelDropdownOpen" class="absolute right-1 top-1/2 -translate-y-1/2 w-5 h-5 flex items-center justify-center rounded hover:bg-[var(--color-surface-hover)] text-[var(--color-text-dim)] transition-colors">
                  <svg v-if="modelLoading" class="w-3 h-3 animate-spin" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.49-8.49l2.83-2.83M2 12h4m12 0h4m-3.93 7.07l-2.83-2.83M7.76 7.76L4.93 4.93" /></svg>
                  <svg v-else class="w-3 h-3 transition-transform" :class="modelDropdownOpen ? 'rotate-180' : ''" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" /></svg>
                </button>
              </div>
              <div v-if="modelDropdownOpen" class="absolute z-10 left-0 right-0 mt-1 max-h-48 overflow-y-auto rounded-md bg-[var(--color-bg)] border border-[var(--color-border)] shadow-lg">
                <div v-if="modelLoading" class="px-2.5 py-2 text-[10px] text-[var(--color-text-dim)] text-center">加载模型列表...</div>
                <template v-else-if="filteredModels.length">
                  <button v-for="m in filteredModels" :key="m" @click="selectModel(m)"
                    class="w-full text-left px-2.5 py-1.5 text-xs hover:bg-[var(--color-surface-hover)] transition-colors flex items-center justify-between"
                    :class="m === model ? 'text-[var(--color-accent)]' : 'text-[var(--color-text)]'">
                    <span class="truncate">{{ m }}</span>
                    <svg v-if="m === model" class="w-3 h-3 shrink-0 ml-1" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4.5 12.75l6 6 9-13.5" /></svg>
                  </button>
                </template>
                <div v-else class="px-2.5 py-2 text-[10px] text-[var(--color-text-dim)] text-center">手动输入模型名称</div>
              </div>
            </div>
            <button @click="saveConfig" class="w-full py-1.5 rounded-md text-xs font-medium bg-[var(--color-accent)] text-white hover:bg-[var(--color-accent-dim)] transition-colors">保存设置</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Messages -->
    <div ref="chatContainer" class="flex-1 overflow-y-auto p-3 space-y-2">
      <!-- Empty state -->
      <div v-if="messages.length === 0" class="h-full flex flex-col items-center justify-center text-[var(--color-text-dim)]">
        <svg class="w-10 h-10 mb-3 opacity-20" fill="none" stroke="currentColor" stroke-width="1" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z" />
        </svg>
        <p class="text-xs text-center leading-relaxed">
          ReAct Agent 已就绪<br />
          <span class="text-[10px] opacity-60">可操控远程浏览器、管理 Docker 服务</span>
        </p>
      </div>

      <!-- Blocks -->
      <template v-for="(item, i) in flatBlocks" :key="i">
        <!-- User text -->
        <div v-if="item.role === 'user' && item.block.type === 'text'" class="flex justify-end">
          <div class="max-w-[85%] px-3 py-2 rounded-lg text-xs leading-relaxed whitespace-pre-wrap break-words bg-[var(--color-accent)] text-white rounded-br-sm">{{ item.block.content }}</div>
        </div>

        <!-- Assistant text -->
        <div v-else-if="item.role === 'assistant' && item.block.type === 'text'" class="flex justify-start">
          <div class="max-w-[85%] px-3 py-2 rounded-lg text-xs leading-relaxed whitespace-pre-wrap break-words bg-[var(--color-bg)] text-[var(--color-text)] border border-[var(--color-border)] rounded-bl-sm">{{ item.block.content }}</div>
        </div>

        <!-- Tool call -->
        <div v-else-if="item.block.type === 'tool_call'" class="flex justify-start">
          <div class="max-w-[90%] rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] overflow-hidden">
            <div class="flex items-center gap-1.5 px-2.5 py-1.5 border-b border-[var(--color-border)] bg-[var(--color-surface)]">
              <svg v-if="item.block.loading" class="w-3 h-3 animate-spin text-[var(--color-accent)]" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.49-8.49l2.83-2.83M2 12h4m12 0h4m-3.93 7.07l-2.83-2.83M7.76 7.76L4.93 4.93" /></svg>
              <svg v-else class="w-3 h-3 text-green-400" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4.5 12.75l6 6 9-13.5" /></svg>
              <span class="text-[10px] font-medium text-[var(--color-text)]">{{ TOOL_LABELS[item.block.toolName || ''] || item.block.toolName }}</span>
              <span class="text-[9px] text-[var(--color-text-dim)] ml-auto font-mono">{{ item.block.toolName }}</span>
            </div>
            <div v-if="item.block.args && Object.keys(item.block.args).length" class="px-2.5 py-1.5 text-[10px] text-[var(--color-text-dim)] font-mono truncate">
              {{ toolArgsSummary(item.block.args) }}
            </div>
          </div>
        </div>

        <!-- Tool result -->
        <div v-else-if="item.block.type === 'tool_result'" class="flex justify-start">
          <div class="max-w-[90%] space-y-1">
            <div v-if="item.block.result" class="px-2.5 py-1.5 rounded-md bg-[var(--color-bg)] border border-[var(--color-border)] text-[10px] text-[var(--color-text-dim)] font-mono">
              {{ toolResultSummary(item.block.result) }}
            </div>
            <button
              v-if="item.block.screenshot"
              @click="previewImage = item.block.screenshot"
              class="inline-flex items-center gap-1 px-2 py-1 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] text-[10px] text-[var(--color-text-dim)] hover:text-[var(--color-text)] hover:border-[var(--color-accent)]/50 transition-colors"
              type="button"
            >
              预览截图
            </button>
          </div>
        </div>

        <!-- Error -->
        <div v-else-if="item.block.type === 'error'" class="flex justify-start">
          <div class="max-w-[85%] px-3 py-2 rounded-lg text-xs bg-red-900/30 text-red-300 border border-red-800/40 rounded-bl-sm">{{ item.block.content }}</div>
        </div>
      </template>

      <!-- Loading indicator -->
      <div v-if="loading && (messages[messages.length - 1]?.blocks.length === 0)" class="flex justify-start">
        <div class="px-3 py-2 rounded-lg bg-[var(--color-bg)] border border-[var(--color-border)] rounded-bl-sm">
          <div class="flex gap-1">
            <span class="w-1.5 h-1.5 rounded-full bg-[var(--color-text-dim)] animate-bounce [animation-delay:0ms]" />
            <span class="w-1.5 h-1.5 rounded-full bg-[var(--color-text-dim)] animate-bounce [animation-delay:150ms]" />
            <span class="w-1.5 h-1.5 rounded-full bg-[var(--color-text-dim)] animate-bounce [animation-delay:300ms]" />
          </div>
        </div>
      </div>
    </div>

    <!-- Input -->
    <div class="shrink-0 p-3 border-t border-[var(--color-border)]">
      <div class="flex gap-2">
        <textarea v-model="input" @keydown.enter.exact.prevent="send" rows="1"
          class="flex-1 px-3 py-2 rounded-lg bg-[var(--color-bg)] border border-[var(--color-border)] text-xs text-[var(--color-text)] outline-none focus:border-[var(--color-accent)] transition-colors resize-none"
          :placeholder="loading ? '输入新指令可中断当前任务... (Enter 发送)' : '输入指令... (Enter 发送)'" />
        <button v-if="loading && !input.trim()" @click="stopGeneration"
          class="shrink-0 w-8 h-8 flex items-center justify-center rounded-lg bg-red-500/80 text-white hover:bg-red-500 transition-colors"
          title="停止生成">
          <svg class="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2" /></svg>
        </button>
        <button v-else @click="send" :disabled="!input.trim()"
          class="shrink-0 w-8 h-8 flex items-center justify-center rounded-lg bg-[var(--color-accent)] text-white hover:bg-[var(--color-accent-dim)] transition-colors disabled:opacity-40">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M6 12L3.269 3.126A59.768 59.768 0 0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5" /></svg>
        </button>
      </div>
      <p v-if="!apiKey" class="mt-1.5 text-[10px] text-yellow-400/80">请先点击右上角齿轮配置 API Key</p>
    </div>

    <!-- Screenshot preview overlay -->
    <Teleport to="body">
      <div v-if="previewImage" class="fixed inset-0 z-[9999] bg-black/80 flex items-center justify-center cursor-pointer" @click="previewImage = null">
        <img :src="'data:image/png;base64,' + previewImage" class="max-w-[90vw] max-h-[90vh] rounded-lg shadow-2xl" />
      </div>
    </Teleport>
  </div>
</template>
