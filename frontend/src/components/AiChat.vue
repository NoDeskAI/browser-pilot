<script setup lang="ts">
import { ref, nextTick, onMounted, onUnmounted, computed } from 'vue'

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
  browser_click_text: '点击文字',
  browser_click_element: '点击元素',
  browser_triple_like: '一键三连',
  browser_type: '输入文本',
  browser_key: '按键',
  browser_scroll: '滚动',
  browser_get_page_info: '页面信息',
  browser_list_tabs: '标签页列表',
  browser_switch_tab: '切换标签页',
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
const textareaRef = ref<HTMLTextAreaElement>()
const configOpen = ref(false)
const wsConnected = ref(false)
let ws: WebSocket | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let reconnectDelay = 1000

const emit = defineEmits<{
  (e: 'browser-active'): void
}>()

const apiKey = ref(localStorage.getItem('ai_api_key') || '')
const baseUrl = ref(localStorage.getItem('ai_base_url') || 'https://api.openai.com/v1')
const model = ref(localStorage.getItem('ai_model') || 'gpt-4o-mini')
const apiType = ref<'openai' | 'anthropic'>(
  (localStorage.getItem('ai_api_type') as 'openai' | 'anthropic') || 'openai'
)

const presets = [
  { label: 'OpenAI', url: 'https://api.openai.com/v1', model: 'gpt-4o-mini', type: 'openai' as const },
  { label: 'Anthropic', url: 'https://api.anthropic.com', model: 'claude-sonnet-4-20250514', type: 'anthropic' as const },
  { label: 'DeepSeek', url: 'https://api.deepseek.com/v1', model: 'deepseek-chat', type: 'openai' as const },
  { label: 'SiliconFlow', url: 'https://api.siliconflow.cn/v1', model: 'Qwen/Qwen2.5-7B-Instruct', type: 'openai' as const },
]

function applyPreset(p: typeof presets[number]) {
  baseUrl.value = p.url
  model.value = p.model
  apiType.value = p.type
}

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

function autoResizeTextarea() {
  const el = textareaRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 120) + 'px'
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
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ action: 'abort' }))
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

const MAX_RECENT_TURNS = 5

function buildApiMessages() {
  const all = messages.value.filter(m => m.blocks.some(b => (b.type === 'text' && b.content) || b.type === 'tool_call'))

  const firstUserMsg = all.find(m => m.role === 'user')
  const firstUserText = firstUserMsg?.blocks.filter(b => b.type === 'text' && b.content).map(b => b.content!).join('\n') || ''

  const recentStart = Math.max(0, all.length - MAX_RECENT_TURNS * 2)
  const oldMsgs = all.slice(0, recentStart)
  const recentMsgs = all.slice(recentStart)

  const result: { role: 'user' | 'assistant'; content: string }[] = []

  const prevSession = localStorage.getItem('ai_prev_session')
  if (prevSession && all.length <= MAX_RECENT_TURNS * 2) {
    const prefix = `[上一轮对话摘要]\n${prevSession}\n[摘要结束]\n\n以上是之前对话的记录，仅供参考。请根据用户当前指令执行操作。`
    if (oldMsgs.length === 0 && recentMsgs.length > 0 && recentMsgs[0].role === 'user') {
      const firstContent = recentMsgs[0].blocks.filter(b => b.type === 'text').map(b => b.content).join('\n')
      result.push({ role: 'user', content: `${prefix}\n\n${firstContent}` })
      for (const m of recentMsgs.slice(1)) {
        if (m.role === 'user') {
          result.push({ role: 'user', content: m.blocks.filter(b => b.type === 'text').map(b => b.content).join('\n') })
        } else {
          result.push({ role: 'assistant', content: summarizeAssistantMsg(m) })
        }
      }
      return result
    }
  }

  if (oldMsgs.length > 0) {
    const lines: string[] = []
    for (const m of oldMsgs) {
      const text = m.blocks.filter(b => b.type === 'text' && b.content).map(b => b.content!).join(' ').slice(0, 100)
      if (m.role === 'user') {
        lines.push(`用户: ${text}`)
      } else {
        const tools = m.blocks.filter(b => b.type === 'tool_call').map(b => (b.toolName || '').replace('browser_', '')).join(',')
        lines.push(`助手: ${tools ? `(did: ${tools}) ` : ''}${text.slice(0, 60)}`)
      }
    }
    const summaryWithTask = firstUserText
      ? `[原始任务] ${firstUserText}\n\n[之前的对话摘要]\n${lines.join('\n')}\n[摘要结束]\n\n请继续执行原始任务中尚未完成的步骤。必须调用工具来操作，不要只用文字描述。`
      : `[之前的对话摘要]\n${lines.join('\n')}\n[摘要结束]`
    result.push({ role: 'user', content: summaryWithTask })
    result.push({ role: 'assistant', content: '好的，我了解之前的对话和原始任务。我会继续执行，现在调用工具操作。' })
  }

  for (const m of recentMsgs) {
    if (m.role === 'user') {
      result.push({ role: 'user', content: m.blocks.filter(b => b.type === 'text').map(b => b.content).join('\n') })
    } else {
      result.push({ role: 'assistant', content: summarizeAssistantMsg(m) })
    }
  }

  return result
}

function summarizeAssistantMsg(m: ChatMessage): string {
  const textParts = m.blocks.filter(b => b.type === 'text' && b.content).map(b => b.content!)
  const toolCalls = m.blocks.filter(b => b.type === 'tool_call' && b.toolName)
  const toolResults = m.blocks.filter(b => b.type === 'tool_result' && b.id)
  if (toolCalls.length > 0) {
    const ops = toolCalls.map(tc => {
      const tr = toolResults.find(r => r.id === tc.id)
      const ok = tr?.result?.ok !== false
      const name = (tc.toolName || '').replace('browser_', '')
      return ok ? name : `${name}(failed)`
    }).join(',')
    const text = textParts.join(' ').slice(0, 80)
    return `(did: ${ops})${text ? ' ' + text : ''}`
  }
  return textParts.join('\n').slice(0, 120) || '(no output)'
}

async function send() {
  const text = input.value.trim()
  if (!text) return

  if (loading.value) {
    stopGeneration()
    await nextTick()
  }

  if (!ws || ws.readyState !== WebSocket.OPEN) {
    messages.value.push({ role: 'user', blocks: [{ type: 'text', content: text }] })
    messages.value.push({ role: 'assistant', blocks: [{ type: 'error', content: '后端未连接，请稍候重试' }] })
    input.value = ''
    return
  }

  messages.value.push({ role: 'user', blocks: [{ type: 'text', content: text }] })
  input.value = ''
  loading.value = true
  await nextTick()
  autoResizeTextarea()
  await scrollToBottom()

  const fullMessages = buildApiMessages()

  messages.value.push({ role: 'assistant', blocks: [] })

  ws.send(JSON.stringify({
    action: 'chat',
    messages: fullMessages,
    apiKey: apiKey.value,
    baseUrl: baseUrl.value,
    model: model.value,
    apiType: apiType.value,
  }))
}

function handleEnter(e: KeyboardEvent) {
  if (e.isComposing) return
  e.preventDefault()
  send()
}

function compressMessages(): string {
  const all = messages.value
  if (all.length === 0) return ''
  const lines: string[] = []
  for (const m of all) {
    const text = m.blocks.filter(b => b.type === 'text' && b.content).map(b => b.content!).join(' ').slice(0, 120)
    const tools = m.blocks.filter(b => b.type === 'tool_call').map(b => b.toolName).join(', ')
    if (m.role === 'user') {
      lines.push(`用户: ${text}`)
    } else {
      lines.push(`助手: ${tools ? `[${tools}] ` : ''}${text.slice(0, 80)}`)
    }
  }
  return lines.join('\n').slice(0, 3000)
}

function clearChat() {
  const summary = compressMessages()
  if (summary) {
    localStorage.setItem('ai_prev_session', summary)
  }
  messages.value = []
}

function connectWs() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return

  const url = `ws://${location.hostname}:8180/ws/agent`
  ws = new WebSocket(url)

  ws.onopen = () => {
    wsConnected.value = true
    reconnectDelay = 1000
  }

  ws.onmessage = async (event) => {
    try {
      const evt = JSON.parse(event.data)
      const last = messages.value[messages.value.length - 1]
      if (!last || last.role !== 'assistant') return
      const blocks = last.blocks

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
          loading.value = false
          break
      }
      await scrollToBottom()
    } catch {}
  }

  ws.onclose = () => {
    wsConnected.value = false
    ws = null
    scheduleReconnect()
  }

  ws.onerror = () => {
    wsConnected.value = false
  }
}

function scheduleReconnect() {
  if (reconnectTimer) clearTimeout(reconnectTimer)
  reconnectTimer = setTimeout(() => {
    connectWs()
    reconnectDelay = Math.min(reconnectDelay * 2, 16000)
  }, reconnectDelay)
}

onMounted(() => {
  if (!apiKey.value) configOpen.value = true
  connectWs()
})

onUnmounted(() => {
  if (reconnectTimer) clearTimeout(reconnectTimer)
  if (ws) ws.close()
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
        <span class="w-1.5 h-1.5 rounded-full" :class="wsConnected ? 'bg-green-400' : 'bg-red-400 animate-pulse'" :title="wsConnected ? '后端已连接' : '后端断开'"></span>
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

    <!-- Config panel -->
    <div v-if="configOpen" class="shrink-0 p-3 border-b border-[var(--color-border)] space-y-2">
      <div>
        <label class="text-[10px] text-[var(--color-text-dim)] uppercase tracking-wider">快速选择</label>
        <div class="flex flex-wrap gap-1 mt-1">
          <button v-for="p in presets" :key="p.label" @click="applyPreset(p)"
            class="px-2 py-0.5 rounded text-[10px] border transition-colors"
            :class="baseUrl === p.url ? 'border-[var(--color-accent)] text-[var(--color-accent)] bg-[var(--color-accent)]/10' : 'border-[var(--color-border)] text-[var(--color-text-dim)] hover:border-[var(--color-text-dim)]'"
          >{{ p.label }}</button>
        </div>
      </div>
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
      </div>
      <div>
        <label class="text-[10px] text-[var(--color-text-dim)] uppercase tracking-wider">API Key</label>
        <input v-model="apiKey" type="password" class="w-full mt-0.5 px-2.5 py-1.5 rounded-md bg-[var(--color-bg)] border border-[var(--color-border)] text-xs text-[var(--color-text)] outline-none focus:border-[var(--color-accent)] transition-colors" placeholder="sk-..." />
      </div>
      <div>
        <label class="text-[10px] text-[var(--color-text-dim)] uppercase tracking-wider">Model</label>
        <input v-model="model" class="w-full mt-0.5 px-2.5 py-1.5 rounded-md bg-[var(--color-bg)] border border-[var(--color-border)] text-xs text-[var(--color-text)] outline-none focus:border-[var(--color-accent)] transition-colors" placeholder="gpt-4o-mini" />
      </div>
      <button @click="saveConfig" class="w-full py-1.5 rounded-md text-xs font-medium bg-[var(--color-accent)] text-white hover:bg-[var(--color-accent-dim)] transition-colors">保存设置</button>
    </div>

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
        <textarea ref="textareaRef" v-model="input" @keydown.enter.exact="handleEnter" @input="autoResizeTextarea" rows="1"
          class="flex-1 px-3 py-2 rounded-lg bg-[var(--color-bg)] border border-[var(--color-border)] text-xs text-[var(--color-text)] outline-none focus:border-[var(--color-accent)] transition-colors resize-none leading-relaxed"
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

  </div>
</template>
