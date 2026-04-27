<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Marked } from 'marked'
import { createHighlighter, type Highlighter } from 'shiki'
import { Copy, Check } from 'lucide-vue-next'
import { useSessions } from '../composables/useSessions'
import { api } from '../lib/api'
import { Button } from '@/components/ui/button'
import { Loader2 } from 'lucide-vue-next'
import { Switch } from '@/components/ui/switch'
import { toast } from 'vue-sonner'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import {
  AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog'

const { t, locale } = useI18n()
const props = withDefaults(defineProps<{ mode?: 'manual' | 'agent' }>(), {
  mode: 'manual',
})

const { brand } = useSessions()
const apiUrl = computed(() => location.origin)
const cmd = computed(() => brand.cliCommandName)
const isAgentDoc = computed(() => props.mode === 'agent')
const pageTitle = computed(() => {
  if (isAgentDoc.value) {
    return locale.value === 'zh' ? 'Agent 自动接入' : 'Agent CLI Access'
  }
  return locale.value === 'zh' ? 'CLI 手动接入' : 'Manual CLI Access'
})
const includeToken = ref(false)
const cliAccessToken = ref('')
const tokenLoading = ref(false)
const tokenConfirmOpen = ref(false)

function tokenConfigLine(c: string): string {
  return includeToken.value && cliAccessToken.value
    ? `\n${c} config set api-token ${cliAccessToken.value}`
    : ''
}

function setupBlockEn(c: string, url: string, installShell: string): string {
  const tokenConfig = includeToken.value && cliAccessToken.value
    ? `\n${c} config set api-token "${cliAccessToken.value}"`
    : ''
  const verify = includeToken.value && cliAccessToken.value
    ? `\n# Verify API reachability and token auth.\n${c} session list`
    : ''
  return `# Cursor/Agent: run this whole block before any ${c} command in every fresh workspace, container, or shell session.
command -v ${c} >/dev/null 2>&1 || ${installShell}
${c} config set api-url "${url}"${tokenConfig}
${c} config show${verify}`
}

function setupBlockZh(c: string, url: string, installShell: string): string {
  const tokenConfig = includeToken.value && cliAccessToken.value
    ? `\n${c} config set api-token "${cliAccessToken.value}"`
    : ''
  const verify = includeToken.value && cliAccessToken.value
    ? `\n# 验证 API 连通性和 Token 认证。\n${c} session list`
    : ''
  return `# Cursor/Agent：在每个新的工作区、容器或 shell session 开始时，先完整运行本段，再执行任何 ${c} 命令。
command -v ${c} >/dev/null 2>&1 || ${installShell}
${c} config set api-url "${url}"${tokenConfig}
${c} config show${verify}`
}

function manualCommandReferenceEn(c: string): string {
  return `## Session Management

${c} session list                    # List all sessions
${c} session create --name "Task"    # Create session
${c} session use <session-id>        # Activate session
${c} session start <session-id>      # Start browser container
${c} session stop <session-id>       # Stop browser container
${c} session delete <session-id>     # Delete session

## Browser Commands (use active session or --session)

${c} navigate <url>                  # Go to URL
${c} observe                         # Get page elements with coordinates
${c} click <x> <y>                   # Click at coordinates
${c} click-element <css-selector>    # Click element by selector
${c} type <text>                     # Type into focused input
${c} key <key>                       # Press key (Enter, Tab, Escape …)
${c} scroll <delta_y>                # Scroll page (positive = down)
${c} tabs                            # List browser tabs
${c} switch-tab --index <n>          # Switch tab
${c} page-info                       # Current URL and title
${c} screenshot -o page.png          # Save screenshot
${c} logs                            # View CDP event logs

## Flags

--json / -j                         Add to any command for JSON output
--session <id> / -s <id>            Target a session without \`session use\`
--api-url <url>                     Override API URL per-command
BPILOT_API_URL                      Override API URL for the current shell
BPILOT_API_TOKEN                    Use this token for the current shell

\`${c} session use <session-id>\` is a terminal convenience shortcut. Commands that need a session target can omit \`<session-id>\` only after a session is active.

## Example Workflow

${c} session create --name "My Task" --json
# → {"id": "abc-123-...", "name": "My Task"}
${c} session use abc-123-...
${c} session start                   # Uses active session abc-123-...
${c} navigate https://example.com
${c} observe --json
# → {"url": "...", "title": "...", "elements": [{"tag": "A", "text": "Link", "x": 320, "y": 200}, ...]}
${c} click 320 200
${c} type "search query"
${c} key Enter
${c} screenshot -o result.png`
}

function manualCommandReferenceZh(c: string): string {
  return `## 会话管理

${c} session list                    # 列出所有会话
${c} session create --name "任务"    # 创建会话
${c} session use <session-id>        # 激活会话
${c} session start <session-id>      # 启动浏览器容器
${c} session stop <session-id>       # 停止浏览器容器
${c} session delete <session-id>     # 删除会话

## 浏览器命令（使用 active session 或 --session）

${c} navigate <url>                  # 导航到指定 URL
${c} observe                         # 获取页面元素及坐标
${c} click <x> <y>                   # 点击指定坐标
${c} click-element <css-selector>    # 通过 CSS 选择器点击元素
${c} type <text>                     # 向当前聚焦的输入框输入文本
${c} key <key>                       # 按键（Enter、Tab、Escape …）
${c} scroll <delta_y>                # 滚动页面（正数 = 向下）
${c} tabs                            # 列出浏览器标签页
${c} switch-tab --index <n>          # 切换标签页
${c} page-info                       # 获取当前页面 URL 和标题
${c} screenshot -o page.png          # 保存截图
${c} logs                            # 查看 CDP 事件日志

## 通用参数

--json / -j                         任意命令后加此参数以 JSON 格式输出
--session <id> / -s <id>            直接指定会话 ID（无需先 session use）
--api-url <url>                     覆盖 API 地址（仅当次生效）
BPILOT_API_URL                      当前 shell 的 API 地址覆盖
BPILOT_API_TOKEN                    当前 shell 使用的 API Token

\`${c} session use <session-id>\` 是本机终端便捷方式。只有先激活会话后，需要会话目标的命令才可以省略 \`<session-id>\`。

## 使用示例

${c} session create --name "我的任务" --json
# → {"id": "abc-123-...", "name": "我的任务"}
${c} session use abc-123-...
${c} session start                   # 使用已激活的 abc-123-...
${c} navigate https://example.com
${c} observe --json
# → {"url": "...", "title": "...", "elements": [{"tag": "A", "text": "Link", "x": 320, "y": 200}, ...]}
${c} click 320 200
${c} type "搜索内容"
${c} key Enter
${c} screenshot -o result.png`
}

function agentSessionTargetEn(c: string): string {
  return `## Session Target (stateless)

# Choose exactly one path, then copy the session id into each --session argument.

# Path A: reuse an existing session.
${c} session list --json

# Path B: create a new session, then copy the returned "id".
${c} session create --name "Agent Task" --json

# Use the copied id directly:
${c} --session "<session-id>" session start`
}

function agentSessionTargetZh(c: string): string {
  return `## 会话目标（无状态）

# 只选择一种方案，然后把 session id 直接填进每条命令的 --session 参数。

# 方案 A：复用现有会话。
${c} session list --json

# 方案 B：创建新会话，然后复制返回的 "id"。
${c} session create --name "Agent Task" --json

# 直接使用复制出来的 id：
${c} --session "<session-id>" session start`
}

function agentCommandReferenceEn(c: string): string {
  return `## Session Management

${c} session list --json                         # List all sessions
${c} session create --name "Task" --json         # Create session and read returned id
${c} --session "<session-id>" session start      # Start browser container
${c} --session "<session-id>" session stop       # Stop browser container
${c} --session "<session-id>" session delete     # Delete session

## Browser Commands (always pass --session)

${c} --session "<session-id>" navigate <url>                  # Go to URL
${c} --session "<session-id>" observe --json                  # Get page elements with coordinates
${c} --session "<session-id>" click <x> <y>                   # Click at coordinates
${c} --session "<session-id>" click-element <css-selector>    # Click element by selector
${c} --session "<session-id>" type <text>                     # Type into focused input
${c} --session "<session-id>" key <key>                       # Press key (Enter, Tab, Escape …)
${c} --session "<session-id>" scroll <delta_y>                # Scroll page (positive = down)
${c} --session "<session-id>" tabs --json                     # List browser tabs
${c} --session "<session-id>" switch-tab --index <n>          # Switch tab
${c} --session "<session-id>" page-info --json                # Current URL and title
${c} --session "<session-id>" screenshot -o page.png          # Save screenshot
${c} --session "<session-id>" logs                            # View CDP event logs

## Flags

--json / -j                         Add to any command for JSON output
--session <id> / -s <id>            Required for stateless Agent calls that target a session
--api-url <url>                     Override API URL per-command

## Example Workflow

${c} session create --name "Agent Task" --json
# Read the returned "id", then:
${c} --session "abc-123-..." session start
${c} --session "abc-123-..." navigate https://example.com
${c} --session "abc-123-..." observe --json
# → {"url": "...", "title": "...", "elements": [{"tag": "A", "text": "Link", "x": 320, "y": 200}, ...]}
${c} --session "abc-123-..." click 320 200
${c} --session "abc-123-..." type "search query"
${c} --session "abc-123-..." key Enter
${c} --session "abc-123-..." screenshot -o result.png`
}

function agentCommandReferenceZh(c: string): string {
  return `## 会话管理

${c} session list --json                         # 列出所有会话
${c} session create --name "任务" --json         # 创建会话并读取返回的 id
${c} --session "<session-id>" session start      # 启动浏览器容器
${c} --session "<session-id>" session stop       # 停止浏览器容器
${c} --session "<session-id>" session delete     # 删除会话

## 浏览器命令（始终传 --session）

${c} --session "<session-id>" navigate <url>                  # 导航到指定 URL
${c} --session "<session-id>" observe --json                  # 获取页面元素及坐标
${c} --session "<session-id>" click <x> <y>                   # 点击指定坐标
${c} --session "<session-id>" click-element <css-selector>    # 通过 CSS 选择器点击元素
${c} --session "<session-id>" type <text>                     # 向当前聚焦的输入框输入文本
${c} --session "<session-id>" key <key>                       # 按键（Enter、Tab、Escape …）
${c} --session "<session-id>" scroll <delta_y>                # 滚动页面（正数 = 向下）
${c} --session "<session-id>" tabs --json                     # 列出浏览器标签页
${c} --session "<session-id>" switch-tab --index <n>          # 切换标签页
${c} --session "<session-id>" page-info --json                # 获取当前页面 URL 和标题
${c} --session "<session-id>" screenshot -o page.png          # 保存截图
${c} --session "<session-id>" logs                            # 查看 CDP 事件日志

## 通用参数

--json / -j                         任意命令后加此参数以 JSON 格式输出
--session <id> / -s <id>            Agent 无状态调用会话命令时必须显式传入
--api-url <url>                     覆盖 API 地址（仅当次生效）

## 使用示例

${c} session create --name "Agent 任务" --json
# 读取返回的 "id"，然后：
${c} --session "abc-123-..." session start
${c} --session "abc-123-..." navigate https://example.com
${c} --session "abc-123-..." observe --json
# → {"url": "...", "title": "...", "elements": [{"tag": "A", "text": "Link", "x": 320, "y": 200}, ...]}
${c} --session "abc-123-..." click 320 200
${c} --session "abc-123-..." type "搜索内容"
${c} --session "abc-123-..." key Enter
${c} --session "abc-123-..." screenshot -o result.png`
}

function buildManualDocEn(c: string, url: string, installShell: string, title: string) {
  return `# ${c} Manual CLI Access — ${title}

## Install

${installShell}
${c} config set api-url ${url}${tokenConfigLine(c)}

${manualCommandReferenceEn(c)}`
}

function buildManualDocZh(c: string, url: string, installShell: string, title: string) {
  return `# ${c} CLI 手动接入 — ${title}

## 安装

${installShell}
${c} config set api-url ${url}${tokenConfigLine(c)}

${manualCommandReferenceZh(c)}`
}

function buildAgentDocEn(c: string, url: string, installShell: string, title: string) {
  return `# ${c} Agent CLI Access — ${title}

## Bootstrap (run first)

${setupBlockEn(c, url, installShell)}

${agentSessionTargetEn(c)}

## Agent Rules

# Run the bootstrap block before any ${c} command in every fresh workspace, container, or shell session.
# Do not only save this document into a skill or memory; execute the bootstrap block.
# Do not use ${c} session use, shell variables, active_session, or BPILOT_ACTIVE_SESSION for session targeting.
# Copy the actual session id into every --session "<session-id>" argument.
# Prefer --json for state-reading commands so the result is easy to parse.
# If no session id is known, list sessions or create one before browser actions.
# Do not create another API token. Use the generated api-token config line or saved config.

${agentCommandReferenceEn(c)}`
}

function buildAgentDocZh(c: string, url: string, installShell: string, title: string) {
  return `# ${c} Agent 自动接入 — ${title}

## 启动配置（先运行）

${setupBlockZh(c, url, installShell)}

${agentSessionTargetZh(c)}

## Agent 规则

# 每个新的工作区、容器或 shell session 开始时，先完整运行启动配置段，再执行任何 ${c} 命令。
# 不要只把本文档写进 skill 或记忆；必须实际执行启动配置段。
# 不要使用 ${c} session use、shell 变量、active_session 或 BPILOT_ACTIVE_SESSION 来指定会话。
# 把真实 session id 直接填进每条命令的 --session "<session-id>" 参数。
# 读取状态时优先使用 --json，方便解析结果。
# 如果还不知道 session id，先列出现有会话或创建新会话。
# 不要再创建新的 API Token，使用生成的 api-token 配置行或已保存的配置。

${agentCommandReferenceZh(c)}`
}

const fullDoc = computed(() => {
  const c = cmd.value
  const url = apiUrl.value
  const installShell = brand.cliInstallCommand
  const title = brand.appTitle
  if (isAgentDoc.value) {
    return locale.value === 'zh'
      ? buildAgentDocZh(c, url, installShell, title)
      : buildAgentDocEn(c, url, installShell, title)
  }
  return locale.value === 'zh'
    ? buildManualDocZh(c, url, installShell, title)
    : buildManualDocEn(c, url, installShell, title)
})
const renderedDoc = computed(() => fullDoc.value.replace(/^# .+(?:\r?\n)+/, ''))

function toRenderable(plain: string): string {
  const lines = plain.split('\n')
  const result: string[] = []
  let buf: string[] = []

  function flush() {
    while (buf.length && (buf[0] ?? '').trim() === '') buf.shift()
    while (buf.length && (buf[buf.length - 1] ?? '').trim() === '') buf.pop()
    if (buf.length) {
      result.push('', '```bash', ...buf, '```')
    }
    buf = []
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i] ?? ''
    const prevLine = lines[i - 1] ?? ''
    const isHeading = /^#{2,3} /.test(line) && (i === 0 || prevLine.trim() === '')
    if (isHeading) {
      flush()
      result.push('', line)
    } else {
      buf.push(line)
    }
  }
  flush()
  return result.join('\n')
}

const html = ref('')
const loading = ref(true)
const activeId = ref('')
const headings = ref<{ id: string; text: string }[]>([])
let intersectionObserver: IntersectionObserver | null = null

function slugify(text: string): string {
  return text.toLowerCase().replace(/[^\w\u4e00-\u9fff]+/g, '-').replace(/^-+|-+$/g, '')
}

function extractHeadings(md: string): { id: string; text: string }[] {
  const result: { id: string; text: string }[] = []
  for (const m of md.matchAll(/^## (.+)$/gm)) {
    const text = m[1] ?? ''
    result.push({ id: slugify(text), text })
  }
  return result
}

function setupScrollSpy() {
  if (intersectionObserver) intersectionObserver.disconnect()
  const sections = headings.value
    .map(h => document.getElementById(h.id))
    .filter((el): el is HTMLElement => !!el)
  if (!sections.length) return

  intersectionObserver = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          activeId.value = entry.target.id
          break
        }
      }
    },
    { rootMargin: '-80px 0px -70% 0px', threshold: 0 }
  )
  sections.forEach(el => intersectionObserver!.observe(el))
}

async function render() {
  loading.value = true
  const plain = renderedDoc.value
  const md = toRenderable(plain)
  headings.value = extractHeadings(plain)

  let highlighter: Highlighter | null = null
  try {
    highlighter = await createHighlighter({
      themes: ['vitesse-dark'],
      langs: ['bash'],
    })
  } catch { /* fallback to plain rendering */ }

  const instance = new Marked()
  instance.use({
    renderer: {
      heading({ tokens, depth }) {
        const text = this.parser.parseInline(tokens)
        if (depth === 2) {
          const raw = tokens.map((t: any) => t.raw ?? t.text ?? '').join('')
          return `<h2 id="${slugify(raw)}">${text}</h2>\n`
        }
        return `<h${depth}>${text}</h${depth}>\n`
      },
      code({ text, lang }) {
        if (highlighter) {
          try {
            return highlighter.codeToHtml(text, { lang: lang || 'text', theme: 'vitesse-dark' })
          } catch { /* fall through */ }
        }
        const escaped = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        return `<pre><code>${escaped}</code></pre>\n`
      },
    },
  })

  try {
    html.value = await instance.parse(md) as string
  } catch {
    const { marked } = await import('marked')
    html.value = await marked(md) as string
  }
  loading.value = false

  await nextTick()
  setupScrollSpy()
}

function scrollTo(id: string) {
  const el = document.getElementById(id)
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

const docCopied = ref(false)
const manualCopyOpen = ref(false)
const manualCopyText = ref('')
const manualCopyRef = ref<HTMLTextAreaElement | null>(null)
let docCopyTimer: ReturnType<typeof setTimeout> | null = null

function selectManualCopyText() {
  window.setTimeout(() => {
    manualCopyRef.value?.focus()
    manualCopyRef.value?.select()
  }, 0)
}

function handleManualCopyFocus(event: FocusEvent) {
  if (event.target instanceof HTMLTextAreaElement) event.target.select()
}

function writeClipboardTextWithSelection(text: string): boolean {
  const activeElement = document.activeElement instanceof HTMLElement ? document.activeElement : null
  const selection = window.getSelection()
  const ranges = selection
    ? Array.from({ length: selection.rangeCount }, (_, i) => selection.getRangeAt(i))
    : []
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.inset = '0 auto auto 0'
  textarea.style.opacity = '0'
  textarea.style.pointerEvents = 'none'
  document.body.appendChild(textarea)
  textarea.focus()
  textarea.select()
  textarea.setSelectionRange(0, textarea.value.length)

  try {
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    document.body.removeChild(textarea)
    if (selection) {
      selection.removeAllRanges()
      ranges.forEach(range => selection.addRange(range))
    }
    activeElement?.focus()
  }
}

async function writeClipboardText(text: string): Promise<boolean> {
  if (writeClipboardTextWithSelection(text)) return true

  try {
    await navigator.clipboard?.writeText(text)
    return true
  } catch {
    return false
  }
}

async function copyDoc() {
  const copied = await writeClipboardText(fullDoc.value)
  if (!copied) {
    manualCopyText.value = fullDoc.value
    manualCopyOpen.value = true
    selectManualCopyText()
    return
  }

  if (docCopyTimer) clearTimeout(docCopyTimer)
  docCopied.value = true
  toast.success(t('cliDoc.copySuccess'))
  docCopyTimer = setTimeout(() => { docCopied.value = false }, 2000)
}

async function createCliAccessToken(): Promise<string | null> {
  tokenLoading.value = true
  try {
    const res = await api('/api/auth/tokens', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'CLI Access Doc' }),
    })
    if (!res.ok) {
      toast.error(t('cliDoc.tokenCreateError'))
      return null
    }
    const data = await res.json()
    return data.token || null
  } catch {
    toast.error(t('cliDoc.tokenCreateError'))
    return null
  } finally {
    tokenLoading.value = false
  }
}

async function handleTokenToggle(checked: boolean) {
  if (!checked) {
    includeToken.value = false
    return
  }

  if (!cliAccessToken.value) {
    tokenConfirmOpen.value = true
    return
  }

  includeToken.value = true
}

async function confirmIncludeToken() {
  if (cliAccessToken.value) {
    includeToken.value = true
    tokenConfirmOpen.value = false
    return
  }

  const token = await createCliAccessToken()
  if (!token) {
    includeToken.value = false
    tokenConfirmOpen.value = false
    return
  }

  cliAccessToken.value = token
  includeToken.value = true
  tokenConfirmOpen.value = false
}

onMounted(render)
watch([locale, fullDoc], render)

onUnmounted(() => {
  if (intersectionObserver) intersectionObserver.disconnect()
})
</script>

<template>
  <div class="w-full max-w-5xl mx-auto overflow-x-hidden px-6 py-8">
    <div v-if="loading" class="flex items-center justify-center py-20">
      <Loader2 class="size-5 animate-spin text-muted-foreground" />
    </div>

    <div v-else class="flex min-w-0 gap-10">
      <div class="flex-1 min-w-0 max-w-3xl">
        <div class="mb-6 flex min-w-0 flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
          <h1 class="min-w-0 flex-1 break-words text-[1.75rem] font-bold leading-[1.3]">
            {{ pageTitle }}
          </h1>
          <div class="flex max-w-full flex-wrap items-center gap-2 sm:ml-auto sm:justify-end">
            <label class="flex items-center gap-2 whitespace-nowrap text-xs text-muted-foreground select-none">
              <Switch
                :model-value="includeToken"
                :disabled="tokenLoading"
                @update:model-value="handleTokenToggle"
              />
              <span>{{ tokenLoading ? t('cliDoc.tokenCreating') : t('cliDoc.includeToken') }}</span>
            </label>
            <Button
              @click="copyDoc"
              :variant="docCopied ? 'outline' : 'default'"
              :disabled="tokenLoading"
              size="sm"
              class="shrink-0 gap-1.5 whitespace-nowrap"
              :class="docCopied ? 'text-green-500 border-green-500/30 bg-background' : ''"
            >
              <Check v-if="docCopied" class="size-3.5" />
              <Copy v-else class="size-3.5" />
              {{ t('cliDoc.copyBtn') }}
            </Button>
          </div>
        </div>
        <article class="markdown-body" v-html="html" />
      </div>

      <nav class="hidden lg:block w-48 shrink-0">
        <div class="sticky top-8 space-y-1.5">
          <p class="font-medium text-xs text-muted-foreground uppercase tracking-wider mb-3">
            {{ locale === 'zh' ? '目录' : 'On this page' }}
          </p>
          <a
            v-for="h in headings"
            :key="h.id"
            href="javascript:void(0)"
            class="block py-1 text-[13px] leading-snug transition-colors border-l-2 pl-3"
            :class="activeId === h.id
              ? 'text-foreground font-medium border-foreground'
              : 'text-muted-foreground hover:text-foreground border-transparent'"
            @click="scrollTo(h.id)"
          >
            {{ h.text }}
          </a>
        </div>
      </nav>
    </div>
  </div>

  <AlertDialog
    :open="tokenConfirmOpen"
    @update:open="(v: boolean) => { if (!v && !tokenLoading) tokenConfirmOpen = false }"
  >
    <AlertDialogContent>
      <AlertDialogHeader>
        <AlertDialogTitle>{{ t('cliDoc.tokenConfirmTitle') }}</AlertDialogTitle>
        <AlertDialogDescription>{{ t('cliDoc.tokenConfirm') }}</AlertDialogDescription>
      </AlertDialogHeader>
      <AlertDialogFooter>
        <AlertDialogCancel :disabled="tokenLoading">
          {{ t('cliDoc.tokenConfirmCancel') }}
        </AlertDialogCancel>
        <Button :disabled="tokenLoading" @click="confirmIncludeToken">
          <Loader2 v-if="tokenLoading" class="size-4 animate-spin" />
          {{ tokenLoading ? t('cliDoc.tokenCreating') : t('cliDoc.tokenConfirmAction') }}
        </Button>
      </AlertDialogFooter>
    </AlertDialogContent>
  </AlertDialog>

  <Dialog :open="manualCopyOpen" @update:open="(v: boolean) => { manualCopyOpen = v; if (v) selectManualCopyText() }">
    <DialogContent class="sm:max-w-2xl">
      <DialogHeader>
        <DialogTitle>{{ t('cliDoc.manualCopyTitle') }}</DialogTitle>
        <DialogDescription>{{ t('cliDoc.manualCopyDescription') }}</DialogDescription>
      </DialogHeader>
      <textarea
        ref="manualCopyRef"
        :value="manualCopyText"
        readonly
        class="h-80 w-full resize-none rounded-md border border-input bg-background p-3 font-mono text-xs leading-relaxed outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
        @focus="handleManualCopyFocus"
      />
      <DialogFooter>
        <Button type="button" variant="outline" @click="manualCopyOpen = false">
          {{ t('cliDoc.manualCopyClose') }}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
</template>
