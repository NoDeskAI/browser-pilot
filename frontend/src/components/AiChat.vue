<script setup lang="ts">
import { ref, nextTick, onMounted, onBeforeUnmount, computed, watch } from 'vue'
import { marked } from 'marked'
import { Sparkles, Trash2, Settings, X, Loader, ChevronDown, Check, ChevronRight, Square, Send, Crosshair } from 'lucide-vue-next'
import type { ChatBlock, ChatMessage } from '../types'
import { useSessions } from '../composables/useSessions'

marked.setOptions({ breaks: true })

const { createSession, loadMessages, saveMessages, renameSession, getAppState, saveAppState } = useSessions()

function renderMarkdown(content: string): string {
  return marked.parse(content, { async: false }) as string
}

const props = defineProps<{
  sessionId: string | null
}>()

const TOOL_LABELS: Record<string, string> = {
  browser_navigate: '导航',
  browser_observe: '观察页面',
  browser_click: '点击坐标',
  browser_click_element: '点击元素',
  browser_type: '输入文本',
  browser_key: '按键',
  browser_scroll: '滚动',
  browser_get_page_info: '页面信息',
  browser_list_tabs: '标签页列表',
  browser_switch_tab: '切换标签页',
  docker_status: '容器状态',
  docker_start: '启动服务',
  docker_stop: '停止服务',
  bash: '执行命令',
  file_read: '读取文件',
  file_write: '写入文件',
  file_edit: '编辑文件',
  grep: '搜索内容',
  glob: '搜索文件',
}

const TOOL_ICONS: Record<string, string> = {
  browser_navigate: '🌐', browser_observe: '👁', browser_click: '🖱',
  browser_click_element: '🖱', browser_type: '⌨', browser_key: '⌨',
  browser_scroll: '📜', browser_get_page_info: 'ℹ',
  browser_list_tabs: '📑', browser_switch_tab: '↔',
  docker_status: '🐳', docker_start: '▶', docker_stop: '⏹',
  bash: '💻', file_read: '📄', file_write: '📝',
  file_edit: '✏', grep: '🔍', glob: '📂',
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

const emit = defineEmits<{
  (e: 'browser-active'): void
  (e: 'session-created', id: string): void
  (e: 'highlight-click', coords: { x: number; y: number }): void
}>()

let currentSessionId: string | null = null

watch(() => props.sessionId, async (newId) => {
  if (newId === currentSessionId) return
  currentSessionId = newId
  if (currentAbort) {
    currentAbort.abort()
    currentAbort = null
    loading.value = false
  }
  if (newId) {
    const loaded = await loadMessages(newId)
    messages.value = loaded
    await scrollToBottom()
  } else {
    messages.value = []
  }
}, { immediate: true })

const apiKey = ref('')
const baseUrl = ref('https://api.openai.com/v1')
const model = ref('gpt-4o-mini')
const apiType = ref<'openai' | 'anthropic'>('openai')

async function loadConfig() {
  const [key, url, m, t] = await Promise.all([
    getAppState('ai_api_key'),
    getAppState('ai_base_url'),
    getAppState('ai_model'),
    getAppState('ai_api_type'),
  ])

  if (key) {
    apiKey.value = key
    if (url) baseUrl.value = url
    if (m) model.value = m
    if (t) apiType.value = t as 'openai' | 'anthropic'
  } else {
    const lsKey = localStorage.getItem('ai_api_key')
    if (lsKey) {
      apiKey.value = lsKey
      baseUrl.value = localStorage.getItem('ai_base_url') || baseUrl.value
      model.value = localStorage.getItem('ai_model') || model.value
      apiType.value = (localStorage.getItem('ai_api_type') as 'openai' | 'anthropic') || apiType.value
      await Promise.all([
        saveAppState('ai_api_key', apiKey.value),
        saveAppState('ai_base_url', baseUrl.value),
        saveAppState('ai_model', model.value),
        saveAppState('ai_api_type', apiType.value),
      ])
      localStorage.removeItem('ai_api_key')
      localStorage.removeItem('ai_base_url')
      localStorage.removeItem('ai_model')
      localStorage.removeItem('ai_api_type')
    }
  }
}

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
const modelFilterActive = ref(false)
const comboboxRef = ref<HTMLElement>()
let fetchDebounceTimer: ReturnType<typeof setTimeout> | null = null

function getPresetModels(): string[] {
  const match = urlPresets.find(p => p.url === baseUrl.value)
  return match ? match.presetModels : []
}

const filteredModels = computed(() => {
  const list = modelOptions.value.length ? modelOptions.value : getPresetModels()
  if (!modelFilterActive.value) return list
  const q = model.value.trim().toLowerCase()
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
  if (fetchDebounceTimer) clearTimeout(fetchDebounceTimer)
  fetchModels()
}

function closeModelDropdown() {
  modelFilterActive.value = false
  modelDropdownOpen.value = false
}

function onModelFocus(e: FocusEvent) {
  modelFilterActive.value = false
  modelDropdownOpen.value = true
  ;(e.target as HTMLInputElement).select()
}

function onModelBlur() {
  closeModelDropdown()
}

function onModelInput() {
  modelFilterActive.value = true
  if (!modelDropdownOpen.value) modelDropdownOpen.value = true
}

function onModelEnter(e: KeyboardEvent) {
  closeModelDropdown()
  ;(e.target as HTMLInputElement).blur()
}

function selectModel(m: string) {
  model.value = m
  closeModelDropdown()
}

function handleComboboxClickOutside(e: MouseEvent) {
  if (comboboxRef.value && !comboboxRef.value.contains(e.target as Node)) {
    closeModelDropdown()
  }
}

watch([apiKey, baseUrl], () => {
  if (fetchDebounceTimer) clearTimeout(fetchDebounceTimer)
  fetchDebounceTimer = setTimeout(() => fetchModels(), 600)
})

watch(configOpen, (open) => {
  if (!open) closeModelDropdown()
})

async function saveConfig() {
  await Promise.all([
    saveAppState('ai_api_key', apiKey.value),
    saveAppState('ai_base_url', baseUrl.value),
    saveAppState('ai_model', model.value),
    saveAppState('ai_api_type', apiType.value),
  ])
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

function toolResultSummary(result: any, toolName?: string): string {
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
  if (typeof result.exitCode === 'number') {
    const status = result.exitCode === 0 ? '成功' : `退出码 ${result.exitCode}`
    const preview = (result.stdout || result.stderr || '').slice(0, 120)
    return `${status}${preview ? ': ' + preview : ''}`
  }
  if (result.path && result.totalLines) {
    return `${result.path} (${result.totalLines} 行)`
  }
  if (result.path && typeof result.replacements === 'number') {
    return `${result.path}: ${result.replacements} 处替换`
  }
  if (result.path && result.lines) {
    return `${result.path} (${result.lines} 行, ${result.bytes} bytes)`
  }
  if (typeof result.matches === 'number') {
    return `${result.matches} 个匹配`
  }
  if (result.files) {
    return `${result.count || result.files.length} 个文件`
  }
  if (result.ok === true) return '成功'
  return JSON.stringify(result).slice(0, 80)
}

function isCodeTool(name?: string): boolean {
  return !!name && ['bash', 'file_read', 'file_write', 'file_edit', 'grep', 'glob'].includes(name)
}

function getToolResultContent(result: any, toolName?: string): string | null {
  if (!result || !toolName) return null
  if (toolName === 'bash') return result.stdout || result.stderr || null
  if (toolName === 'file_read') return result.content || null
  if (toolName === 'grep') return result.output || null
  if (toolName === 'glob' && result.files?.length) return result.files.join('\n')
  return null
}

function hasClickCoords(block: ChatBlock): boolean {
  return block.toolName === 'browser_click' && block.args?.x != null && block.args?.y != null
}

function handleToolCallClick(block: ChatBlock) {
  if (hasClickCoords(block)) {
    emit('highlight-click', { x: block.args!.x, y: block.args!.y })
  }
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

function buildApiMessages() {
  const result: { role: 'user' | 'assistant'; content: string }[] = []

  for (const m of messages.value) {
    if (m.role === 'user') {
      const text = m.blocks.filter(b => b.type === 'text' && b.content).map(b => b.content!).join('\n')
      if (text) result.push({ role: 'user', content: text })
    } else {
      const text = m.blocks.filter(b => b.type === 'text' && b.content).map(b => b.content!).join('\n')
      if (!text) continue
      const hasError = m.blocks.some(b => b.type === 'error')
      const content = hasError
        ? text + '\n\n[系统提示：本轮回复生成过程中连接中断，上述工具调用可能未成功执行。不要信任上文中关于操作结果的描述，必须重新调用工具执行操作。]'
        : text
      result.push({ role: 'assistant', content })
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

  if (!currentSessionId) {
    const session = await createSession(text.slice(0, 20) || '新会话')
    currentSessionId = session.id
    emit('session-created', session.id)
  } else {
    const { state } = useSessions()
    const s = state.sessions.find(s => s.id === currentSessionId)
    if (s && s.name === '新会话' && messages.value.length === 0) {
      await renameSession(currentSessionId, text.slice(0, 20))
    }
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
        sessionId: currentSessionId,
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
    if (currentSessionId) {
      saveMessages(currentSessionId, messages.value)
    }
  }
}

function handleEnter(e: KeyboardEvent) {
  if (e.isComposing) return
  e.preventDefault()
  send()
}

function clearChat() {
  messages.value = []
  if (currentSessionId) {
    saveMessages(currentSessionId, [])
  }
}

onMounted(async () => {
  await loadConfig()
  if (fetchDebounceTimer) clearTimeout(fetchDebounceTimer)
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
        <Sparkles class="w-4 h-4 text-[var(--color-accent)]" />
        <span class="text-sm font-semibold">NoDeskPane Agent</span>
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
                  @focus="onModelFocus"
                  @blur="onModelBlur"
                  @input="onModelInput"
                  @keydown.enter.prevent="onModelEnter"
                  @keydown.escape="closeModelDropdown"
                  class="w-full px-2.5 py-1.5 pr-7 rounded-md bg-[var(--color-bg)] border border-[var(--color-border)] text-xs text-[var(--color-text)] outline-none focus:border-[var(--color-accent)] transition-colors"
                  placeholder="gpt-4o-mini" />
                <button @mousedown.prevent @click.stop="modelDropdownOpen = !modelDropdownOpen" class="absolute right-1 top-1/2 -translate-y-1/2 w-5 h-5 flex items-center justify-center rounded hover:bg-[var(--color-surface-hover)] text-[var(--color-text-dim)] transition-colors">
                  <Loader v-if="modelLoading" class="w-3 h-3 animate-spin" />
                  <ChevronDown v-else class="w-3 h-3 transition-transform" :class="modelDropdownOpen ? 'rotate-180' : ''" />
                </button>
              </div>
              <div v-if="modelDropdownOpen" @mousedown.prevent class="absolute z-10 left-0 right-0 mt-1 max-h-48 overflow-y-auto rounded-md bg-[var(--color-bg)] border border-[var(--color-border)] shadow-lg">
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
    <div ref="chatContainer" class="flex-1 overflow-y-auto overflow-x-hidden p-3 space-y-2">
      <!-- Empty state -->
      <div v-if="messages.length === 0" class="h-full flex flex-col items-center justify-center text-[var(--color-text-dim)]">
        <Sparkles class="w-10 h-10 mb-3 opacity-20" :stroke-width="1" />
        <p class="text-xs text-center leading-relaxed">
          NoDeskPane Agent 已就绪<br />
          <span class="text-[10px] opacity-60">浏览器操控 · Docker 管理 · 代码读写 · 命令执行</span>
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
              <div class="max-w-[85%] px-3 py-2 rounded-lg text-xs leading-relaxed break-words bg-[var(--color-bg)] text-[var(--color-text)] border border-[var(--color-border)] rounded-bl-sm markdown-body overflow-hidden" v-html="renderMarkdown(seg.content)"></div>
            </div>
          </template>
        </template>

        <!-- Tool call -->
        <div v-else-if="item.block.type === 'tool_call'" class="flex justify-start">
          <div
            class="max-w-[90%] rounded-md border bg-[var(--color-bg)] overflow-hidden transition-colors"
            :class="hasClickCoords(item.block)
              ? 'border-[var(--color-border)] hover:border-red-500/50 cursor-pointer'
              : 'border-[var(--color-border)]'"
            @click="handleToolCallClick(item.block)"
          >
            <div class="flex items-center gap-1.5 px-2.5 py-1.5 border-b border-[var(--color-border)] bg-[var(--color-surface)]">
              <Loader v-if="item.block.loading" class="w-3 h-3 animate-spin text-[var(--color-accent)]" />
              <Check v-else class="w-3 h-3 text-green-400" />
              <span class="text-[11px] leading-none">{{ TOOL_ICONS[item.block.toolName || ''] || '🔧' }}</span>
              <span class="text-[10px] font-medium text-[var(--color-text)]">{{ TOOL_LABELS[item.block.toolName || ''] || item.block.toolName }}</span>
              <span class="text-[9px] text-[var(--color-text-dim)] ml-auto font-mono">{{ item.block.toolName }}</span>
              <Crosshair v-if="hasClickCoords(item.block)" class="w-3 h-3 shrink-0 text-[var(--color-text-dim)]" title="点击定位到浏览器视窗" />
            </div>
            <div v-if="item.block.args && Object.keys(item.block.args).length" class="px-2.5 py-1.5 text-[10px] text-[var(--color-text-dim)] font-mono truncate">
              {{ toolArgsSummary(item.block.args) }}
            </div>
          </div>
        </div>

        <!-- Tool result -->
        <div v-else-if="item.block.type === 'tool_result'" class="flex justify-start">
          <div class="max-w-[90%]">
            <!-- Code tool result with expandable output -->
            <div v-if="isCodeTool(item.block.toolName) && getToolResultContent(item.block.result, item.block.toolName)" class="rounded-md border border-[var(--color-border)] overflow-hidden">
              <div class="px-2.5 py-1 bg-[var(--color-surface)] text-[10px] text-[var(--color-text-dim)] font-mono border-b border-[var(--color-border)] break-all">
                {{ toolResultSummary(item.block.result, item.block.toolName) }}
              </div>
              <pre class="px-2.5 py-1.5 text-[10px] leading-relaxed text-[var(--color-text)] bg-[#0d1117] overflow-x-auto max-h-48 font-mono whitespace-pre">{{ getToolResultContent(item.block.result, item.block.toolName) }}</pre>
            </div>
            <!-- Default result summary -->
            <div v-else-if="item.block.result" class="px-2.5 py-1.5 rounded-md bg-[var(--color-bg)] border border-[var(--color-border)] text-[10px] text-[var(--color-text-dim)] font-mono break-all">
              {{ toolResultSummary(item.block.result, item.block.toolName) }}
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
        <textarea v-model="input" @keydown.enter.exact="handleEnter" rows="1"
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

<style scoped>
.markdown-body :deep(pre) {
  overflow-x: auto;
  max-width: 100%;
}
.markdown-body :deep(code) {
  word-break: break-all;
}
.markdown-body :deep(a) {
  word-break: break-all;
}
.markdown-body :deep(p) {
  overflow-wrap: break-word;
  word-break: break-word;
}
.markdown-body :deep(img) {
  max-width: 100%;
}
.markdown-body :deep(table) {
  display: block;
  overflow-x: auto;
  max-width: 100%;
}
</style>
