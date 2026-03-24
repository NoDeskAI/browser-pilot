<script setup lang="ts">
import { ref, nextTick, onMounted, onBeforeUnmount, computed, watch } from 'vue'
import { marked } from 'marked'
import { Sparkles, Trash2, Settings, X, Loader, ChevronDown, Check, ChevronRight, Square, Send } from 'lucide-vue-next'

marked.setOptions({ breaks: true })

function renderMarkdown(content: string): string {
  return marked.parse(content, { async: false }) as string
}

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
const expandedThinks = ref(new Set<string>())
let currentAbort: AbortController | null = null
let textBuffer = ''
let bufferTarget: ChatBlock | null = null
let bufferTimer: ReturnType<typeof setInterval> | null = null

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

function startBufferDrain() {
  if (bufferTimer) return
  bufferTimer = setInterval(() => {
    if (!bufferTarget || !textBuffer) {
      stopBufferDrain()
      return
    }
    const charsPerTick = Math.max(1, Math.ceil(textBuffer.length / 8))
    const chunk = textBuffer.slice(0, charsPerTick)
    textBuffer = textBuffer.slice(charsPerTick)
    bufferTarget.content = (bufferTarget.content || '') + chunk
    scrollToBottom()
  }, 40)
}

function stopBufferDrain() {
  if (bufferTimer) {
    clearInterval(bufferTimer)
    bufferTimer = null
  }
}

function flushBuffer() {
  stopBufferDrain()
  if (bufferTarget && textBuffer) {
    bufferTarget.content = (bufferTarget.content || '') + textBuffer
  }
  textBuffer = ''
  bufferTarget = null
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

function parseThinkSegments(content: string): { type: 'think' | 'text'; content: string; unclosed?: boolean }[] {
  const segments: { type: 'think' | 'text'; content: string; unclosed?: boolean }[] = []
  const regex = /<think>([\s\S]*?)<\/think>/g
  let lastIndex = 0
  let match
  while ((match = regex.exec(content)) !== null) {
    if (match.index > lastIndex) {
      const text = content.slice(lastIndex, match.index).trim()
      if (text) segments.push({ type: 'text', content: text })
    }
    const think = match[1].trim()
    if (think) segments.push({ type: 'think', content: think })
    lastIndex = regex.lastIndex
  }
  const remaining = content.slice(lastIndex)
  const unclosedMatch = remaining.match(/^(\s*)<think>([\s\S]*)$/)
  if (unclosedMatch) {
    const thinkContent = unclosedMatch[2].trim()
    if (thinkContent) segments.push({ type: 'think', content: thinkContent, unclosed: true })
  } else {
    const text = remaining.trim()
    if (text) segments.push({ type: 'text', content: text })
  }
  return segments
}

function toggleThink(key: string) {
  if (expandedThinks.value.has(key)) {
    expandedThinks.value.delete(key)
  } else {
    expandedThinks.value.add(key)
  }
}

// ---------------------------------------------------------------------------
// Send message
// ---------------------------------------------------------------------------

function stopGeneration() {
  flushBuffer()
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
                bufferTarget = lastBlock
              } else {
                const newBlock: ChatBlock = { type: 'text', content: '' }
                blocks.push(newBlock)
                bufferTarget = newBlock
              }
              textBuffer += evt.content
              startBufferDrain()
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
    flushBuffer()
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
  flushBuffer()
  document.removeEventListener('click', handleComboboxClickOutside)
  if (fetchDebounceTimer) clearTimeout(fetchDebounceTimer)
})
</script>

<template>
  <div class="h-full flex flex-col bg-[var(--color-surface)]">
    <!-- Header -->
    <div class="shrink-0 flex items-center justify-between px-3 py-2.5 border-b border-[var(--color-border)]">
      <div class="flex items-center gap-2">
        <Sparkles class="w-4 h-4 text-[var(--color-accent)]" />
        <span class="text-sm font-semibold">AI Agent</span>
        <span class="text-[9px] px-1.5 py-0.5 rounded-full bg-[var(--color-accent)]/15 text-[var(--color-accent)]">ReAct</span>
      </div>
      <div class="flex items-center gap-1">
        <button @click="clearChat" class="w-7 h-7 flex items-center justify-center rounded-md hover:bg-[var(--color-surface-hover)] text-[var(--color-text-dim)] transition-colors" title="清除对话">
          <Trash2 class="w-3.5 h-3.5" />
        </button>
        <button @click="configOpen = !configOpen" class="w-7 h-7 flex items-center justify-center rounded-md hover:bg-[var(--color-surface-hover)] text-[var(--color-text-dim)] transition-colors" :class="configOpen ? 'bg-[var(--color-surface-hover)] text-[var(--color-accent)]' : ''" title="API 设置">
          <Settings class="w-3.5 h-3.5" />
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
              <X class="w-4 h-4" />
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
                  <Loader v-if="modelLoading" class="w-3 h-3 animate-spin" />
                  <ChevronDown v-else class="w-3 h-3 transition-transform" :class="modelDropdownOpen ? 'rotate-180' : ''" />
                </button>
              </div>
              <div v-if="modelDropdownOpen" class="absolute z-10 left-0 right-0 mt-1 max-h-48 overflow-y-auto rounded-md bg-[var(--color-bg)] border border-[var(--color-border)] shadow-lg">
                <div v-if="modelLoading" class="px-2.5 py-2 text-[10px] text-[var(--color-text-dim)] text-center">加载模型列表...</div>
                <template v-else-if="filteredModels.length">
                  <button v-for="m in filteredModels" :key="m" @click="selectModel(m)"
                    class="w-full text-left px-2.5 py-1.5 text-xs hover:bg-[var(--color-surface-hover)] transition-colors flex items-center justify-between"
                    :class="m === model ? 'text-[var(--color-accent)]' : 'text-[var(--color-text)]'">
                    <span class="truncate">{{ m }}</span>
                    <Check v-if="m === model" class="w-3 h-3 shrink-0 ml-1" />
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
        <Sparkles class="w-10 h-10 mb-3 opacity-20" :stroke-width="1" />
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

        <!-- Assistant text (with think block support) -->
        <template v-else-if="item.role === 'assistant' && item.block.type === 'text'">
          <template v-for="(seg, si) in parseThinkSegments(item.block.content || '')" :key="`${i}-${si}`">
            <div v-if="seg.type === 'think'" class="flex justify-start">
              <div class="max-w-[85%] rounded-lg border border-dashed border-[var(--color-border)] bg-[var(--color-surface)] overflow-hidden rounded-bl-sm">
                <button
                  @click="toggleThink(`${i}-${si}`)"
                  class="w-full flex items-center gap-1.5 px-2.5 py-1.5 text-[10px] text-[var(--color-text-dim)] hover:text-[var(--color-text)] transition-colors select-none"
                >
                  <ChevronRight class="w-2.5 h-2.5 shrink-0 transition-transform" :class="(seg.unclosed || expandedThinks.has(`${i}-${si}`)) ? 'rotate-90' : ''" />
                  <span class="font-medium">思考过程</span>
                  <Loader v-if="seg.unclosed" class="w-2.5 h-2.5 ml-auto animate-spin text-[var(--color-text-dim)]" />
                </button>
                <div v-if="seg.unclosed || expandedThinks.has(`${i}-${si}`)" class="px-2.5 pb-2 border-t border-dashed border-[var(--color-border)]">
                  <p class="pt-1.5 text-[10px] leading-relaxed text-[var(--color-text-dim)] italic whitespace-pre-wrap break-words">{{ seg.content }}</p>
                </div>
              </div>
            </div>
            <div v-else class="flex justify-start">
              <div class="max-w-[85%] px-3 py-2 rounded-lg text-xs leading-relaxed break-words bg-[var(--color-bg)] text-[var(--color-text)] border border-[var(--color-border)] rounded-bl-sm markdown-body" v-html="renderMarkdown(seg.content)"></div>
            </div>
          </template>
        </template>

        <!-- Tool call -->
        <div v-else-if="item.block.type === 'tool_call'" class="flex justify-start">
          <div class="max-w-[90%] rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] overflow-hidden">
            <div class="flex items-center gap-1.5 px-2.5 py-1.5 border-b border-[var(--color-border)] bg-[var(--color-surface)]">
              <Loader v-if="item.block.loading" class="w-3 h-3 animate-spin text-[var(--color-accent)]" />
              <Check v-else class="w-3 h-3 text-green-400" />
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
          <div class="max-w-[90%]">
            <div v-if="item.block.result" class="px-2.5 py-1.5 rounded-md bg-[var(--color-bg)] border border-[var(--color-border)] text-[10px] text-[var(--color-text-dim)] font-mono">
              {{ toolResultSummary(item.block.result) }}
            </div>
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
          <Square class="w-3.5 h-3.5" fill="currentColor" :stroke-width="0" />
        </button>
        <button v-else @click="send" :disabled="!input.trim()"
          class="shrink-0 w-8 h-8 flex items-center justify-center rounded-lg bg-[var(--color-accent)] text-white hover:bg-[var(--color-accent-dim)] transition-colors disabled:opacity-40">
          <Send class="w-3.5 h-3.5" />
        </button>
      </div>
      <p v-if="!apiKey" class="mt-1.5 text-[10px] text-yellow-400/80">请先点击右上角齿轮配置 API Key</p>
    </div>

  </div>
</template>
