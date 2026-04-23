<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Marked } from 'marked'
import { createHighlighter, type Highlighter } from 'shiki'
import { Copy, Check } from 'lucide-vue-next'
import { useSessions } from '../composables/useSessions'
import { Button } from '@/components/ui/button'
import { Loader2 } from 'lucide-vue-next'

const { t, locale } = useI18n()

const { brand } = useSessions()
const apiUrl = computed(() => location.origin)
const cmd = computed(() => brand.cliCommandName)

function buildDocEn(c: string, url: string, installShell: string, installPython: string, title: string) {
  return `# ${c} CLI — ${title}

## Install

### Quick Install (zero dependencies)
${installShell}

### Python CLI (optional, richer output)
${installPython}
${c} config set api-url ${url}

## Session Management

${c} session list                    # List all sessions
${c} session create --name "Task"    # Create session
${c} session use <session-id>        # Activate session
${c} session start [session-id]      # Start browser container
${c} session stop [session-id]       # Stop browser container
${c} session delete <session-id>     # Delete session

## Browser Commands (require active session)

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

## Example Workflow

${c} session create --name "My Task" --json
# → {"id": "abc-123-...", "name": "My Task"}
${c} session use abc-123-...
${c} session start
${c} navigate https://example.com
${c} observe --json
# → {"url": "...", "title": "...", "elements": [{"tag": "A", "text": "Link", "x": 320, "y": 200}, ...]}
${c} click 320 200
${c} type "search query"
${c} key Enter
${c} screenshot -o result.png`
}

function buildDocZh(c: string, url: string, installShell: string, installPython: string, title: string) {
  return `# ${c} CLI — ${title}

## 安装

### 快速安装（零依赖）
${installShell}

### Python CLI（可选，更丰富的输出）
${installPython}
${c} config set api-url ${url}

## 会话管理

${c} session list                    # 列出所有会话
${c} session create --name "任务"    # 创建会话
${c} session use <session-id>        # 激活会话
${c} session start [session-id]      # 启动浏览器容器
${c} session stop [session-id]       # 停止浏览器容器
${c} session delete <session-id>     # 删除会话

## 浏览器命令（需要先激活会话）

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

## 使用示例

${c} session create --name "我的任务" --json
# → {"id": "abc-123-...", "name": "我的任务"}
${c} session use abc-123-...
${c} session start
${c} navigate https://example.com
${c} observe --json
# → {"url": "...", "title": "...", "elements": [{"tag": "A", "text": "Link", "x": 320, "y": 200}, ...]}
${c} click 320 200
${c} type "搜索内容"
${c} key Enter
${c} screenshot -o result.png`
}

const fullDoc = computed(() => {
  const c = cmd.value
  const url = apiUrl.value
  const installShell = brand.cliInstallCommand
  const installPython = brand.cliPythonInstallCommand
  const title = brand.appTitle
  return locale.value === 'zh'
    ? buildDocZh(c, url, installShell, installPython, title)
    : buildDocEn(c, url, installShell, installPython, title)
})

function toRenderable(plain: string): string {
  const lines = plain.split('\n')
  const result: string[] = []
  let buf: string[] = []

  function flush() {
    while (buf.length && buf[0].trim() === '') buf.shift()
    while (buf.length && buf[buf.length - 1].trim() === '') buf.pop()
    if (buf.length) {
      result.push('', '```bash', ...buf, '```')
    }
    buf = []
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    const isHeading = /^#{1,3} /.test(line) && (i === 0 || lines[i - 1].trim() === '')
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
    result.push({ id: slugify(m[1]), text: m[1] })
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
  const plain = fullDoc.value
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
let docCopyTimer: ReturnType<typeof setTimeout> | null = null

function copyDoc() {
  navigator.clipboard.writeText(fullDoc.value).then(() => {
    if (docCopyTimer) clearTimeout(docCopyTimer)
    docCopied.value = true
    docCopyTimer = setTimeout(() => { docCopied.value = false }, 2000)
  })
}

onMounted(render)
watch([locale, fullDoc], render)

onUnmounted(() => {
  if (intersectionObserver) intersectionObserver.disconnect()
})
</script>

<template>
  <div class="max-w-5xl mx-auto px-6 py-8">
    <div v-if="loading" class="flex items-center justify-center py-20">
      <Loader2 class="size-5 animate-spin text-muted-foreground" />
    </div>

    <div v-else class="flex gap-10">
      <div class="flex-1 min-w-0 max-w-3xl relative">
        <Button
          @click="copyDoc"
          :variant="docCopied ? 'outline' : 'default'"
          size="sm"
          class="absolute right-0 top-0.5 shrink-0 gap-1.5"
          :class="docCopied ? 'text-green-500 border-green-500/30 bg-background' : ''"
        >
          <Check v-if="docCopied" class="size-3.5" />
          <Copy v-else class="size-3.5" />
          {{ docCopied ? t('cliDoc.copiedBtn') : t('cliDoc.copyBtn') }}
        </Button>
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
</template>
