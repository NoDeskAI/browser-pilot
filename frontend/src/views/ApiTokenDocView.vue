<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { marked, Marked } from 'marked'
import { createHighlighter, type Highlighter } from 'shiki'
import { Loader2 } from 'lucide-vue-next'

const { locale } = useI18n()

const html = ref('')
const loading = ref(true)
const activeId = ref('')
const headings = ref<{ id: string; text: string }[]>([])
let observer: IntersectionObserver | null = null

const zhContent = `
# API Token 使用文档

## 认证方式

所有 API 请求通过 HTTP Header 携带 Token 进行认证：

\`\`\`bash
curl -H "Authorization: Bearer bp_xxxxxxxxxxxx" http://localhost:9222/api/...
\`\`\`

Token 以 \`bp_\` 开头，例如 \`bp_a1b2c3d4e5f6...\`

## Token 类型

### 用户级 Token

- 在 **账号设置** 页面创建
- 拥有当前用户的完整 API 权限
- 适合个人使用的自动化工具、CLI 脚本

### 会话级 Token

- 在 **会话详情** 页面点击钥匙图标创建
- 仅能操作绑定的那一个会话
- 适合分发给外部服务（如 AutoTesting），实现最小权限控制

## 常用 API 示例

> 以下示例中 \`SESSION_ID\` 为会话 ID，\`TOKEN\` 为你的 API Token。

### 打开网页

\`\`\`bash
curl -X POST http://localhost:9222/api/browser/navigate \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"sessionId": "'$SESSION_ID'", "url": "https://example.com"}'
\`\`\`

### 获取页面内容

\`\`\`bash
curl -X POST http://localhost:9222/api/browser/observe \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"sessionId": "'$SESSION_ID'"}'
\`\`\`

### 截图

\`\`\`bash
curl http://localhost:9222/api/browser/screenshot?sessionId=$SESSION_ID \\
  -H "Authorization: Bearer $TOKEN" \\
  --output screenshot.png
\`\`\`

### 点击元素

\`\`\`bash
curl -X POST http://localhost:9222/api/browser/click \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"sessionId": "'$SESSION_ID'", "x": 100, "y": 200}'
\`\`\`

### 输入文本

\`\`\`bash
curl -X POST http://localhost:9222/api/browser/type \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"sessionId": "'$SESSION_ID'", "text": "Hello World"}'
\`\`\`

## 会话级 Token 可用的 API

会话级 Token 只能调用以下接口，且 \`sessionId\` 必须匹配绑定的会话：

| 分类 | 接口 | 说明 |
|------|------|------|
| 浏览器操作 | \`POST /api/browser/navigate\` | 打开网页 |
| | \`GET /api/browser/current\` | 获取当前页面信息 |
| | \`POST /api/browser/observe\` | 获取页面内容 |
| | \`POST /api/browser/click\` | 坐标点击 |
| | \`POST /api/browser/click-element\` | 元素点击 |
| | \`POST /api/browser/type\` | 输入文本 |
| | \`POST /api/browser/key\` | 模拟按键 |
| | \`POST /api/browser/scroll\` | 滚动页面 |
| | \`GET /api/browser/tabs\` | 获取标签页列表 |
| | \`POST /api/browser/switch-tab\` | 切换标签页 |
| | \`GET /api/browser/screenshot\` | 截图 |
| Docker | \`POST /api/docker/navigate\` | 容器级导航 |
| | \`POST /api/docker/clipboard\` | 剪贴板操作 |
| | \`POST /api/docker/browser-lang\` | 切换浏览器语言 |
| 会话 | \`GET /api/sessions/{id}\` | 查看会话详情 |
| | \`POST /api/sessions/{id}/container/start\` | 启动容器 |
| | \`POST /api/sessions/{id}/container/stop\` | 停止容器 |
| | \`POST /api/sessions/{id}/container/pause\` | 休眠容器 |
| | \`POST /api/sessions/{id}/container/unpause\` | 恢复容器 |
| | \`GET /api/sessions/{id}/logs\` | 查看运行日志 |

其余接口（会话列表、创建/删除会话、用户管理等）需要使用 **用户级 Token**。

## 创建 Token

- **用户级 Token**：进入 [账号设置](/account) → API Token 区域 → 点击「创建 Token」
- **会话级 Token**：进入会话详情页 → 顶部工具栏点击钥匙图标

Token 仅在创建时显示一次，请立即复制保存。

## 错误码

| 状态码 | 含义 | 处理方式 |
|--------|------|----------|
| \`401 Unauthorized\` | Token 无效或已删除 | 检查 Token 是否正确、是否已被删除 |
| \`403 Forbidden\` | 会话级 Token 越权访问 | 确认 \`sessionId\` 与 Token 绑定的会话一致 |
`

const enContent = `
# API Token Documentation

## Authentication

All API requests are authenticated via an HTTP header:

\`\`\`bash
curl -H "Authorization: Bearer bp_xxxxxxxxxxxx" http://localhost:9222/api/...
\`\`\`

Tokens are prefixed with \`bp_\`, e.g. \`bp_a1b2c3d4e5f6...\`

## Token Types

### User-level Token

- Created in **Account Settings**
- Has full API access for the current user
- Ideal for personal automation tools and CLI scripts

### Session-scoped Token

- Created via the key icon in the **session detail** page
- Can only operate on the bound session
- Ideal for distributing to external services (e.g. AutoTesting) with least-privilege access

## Common API Examples

> In the examples below, \`SESSION_ID\` is the session ID and \`TOKEN\` is your API token.

### Navigate to a URL

\`\`\`bash
curl -X POST http://localhost:9222/api/browser/navigate \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"sessionId": "'$SESSION_ID'", "url": "https://example.com"}'
\`\`\`

### Observe Page Content

\`\`\`bash
curl -X POST http://localhost:9222/api/browser/observe \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"sessionId": "'$SESSION_ID'"}'
\`\`\`

### Take a Screenshot

\`\`\`bash
curl http://localhost:9222/api/browser/screenshot?sessionId=$SESSION_ID \\
  -H "Authorization: Bearer $TOKEN" \\
  --output screenshot.png
\`\`\`

### Click at Coordinates

\`\`\`bash
curl -X POST http://localhost:9222/api/browser/click \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"sessionId": "'$SESSION_ID'", "x": 100, "y": 200}'
\`\`\`

### Type Text

\`\`\`bash
curl -X POST http://localhost:9222/api/browser/type \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"sessionId": "'$SESSION_ID'", "text": "Hello World"}'
\`\`\`

## APIs Available to Session-scoped Tokens

Session-scoped tokens can only call the following endpoints, and the \`sessionId\` must match the bound session:

| Category | Endpoint | Description |
|----------|----------|-------------|
| Browser | \`POST /api/browser/navigate\` | Navigate to URL |
| | \`GET /api/browser/current\` | Get current page info |
| | \`POST /api/browser/observe\` | Get page content |
| | \`POST /api/browser/click\` | Click at coordinates |
| | \`POST /api/browser/click-element\` | Click element |
| | \`POST /api/browser/type\` | Type text |
| | \`POST /api/browser/key\` | Simulate keypress |
| | \`POST /api/browser/scroll\` | Scroll page |
| | \`GET /api/browser/tabs\` | List tabs |
| | \`POST /api/browser/switch-tab\` | Switch tab |
| | \`GET /api/browser/screenshot\` | Take screenshot |
| Docker | \`POST /api/docker/navigate\` | Container-level navigation |
| | \`POST /api/docker/clipboard\` | Clipboard operations |
| | \`POST /api/docker/browser-lang\` | Switch browser language |
| Session | \`GET /api/sessions/{id}\` | View session details |
| | \`POST /api/sessions/{id}/container/start\` | Start container |
| | \`POST /api/sessions/{id}/container/stop\` | Stop container |
| | \`POST /api/sessions/{id}/container/pause\` | Hibernate container |
| | \`POST /api/sessions/{id}/container/unpause\` | Resume container |
| | \`GET /api/sessions/{id}/logs\` | View runtime logs |

All other endpoints (session list, create/delete sessions, user management, etc.) require a **user-level token**.

## Creating Tokens

- **User-level Token**: Go to [Account Settings](/account) → API Tokens section → Click "Create Token"
- **Session-scoped Token**: Open a session detail page → Click the key icon in the toolbar

Tokens are only displayed once at creation time. Copy and save immediately.

## Error Codes

| Status | Meaning | Resolution |
|--------|---------|------------|
| \`401 Unauthorized\` | Token is invalid or deleted | Verify the token is correct and has not been revoked |
| \`403 Forbidden\` | Session-scoped token used on wrong session | Ensure \`sessionId\` matches the token's bound session |
`

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
  if (observer) observer.disconnect()
  const sections = headings.value
    .map(h => document.getElementById(h.id))
    .filter((el): el is HTMLElement => !!el)
  if (!sections.length) return

  observer = new IntersectionObserver(
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
  sections.forEach(el => observer!.observe(el))
}

async function render() {
  loading.value = true
  const md = locale.value === 'zh' ? zhContent : enContent
  headings.value = extractHeadings(md)

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

onMounted(render)
watch(locale, render)

onUnmounted(() => {
  if (observer) observer.disconnect()
})
</script>

<template>
  <div class="max-w-5xl mx-auto px-6 py-8">
    <div v-if="loading" class="flex items-center justify-center py-20">
        <Loader2 class="size-5 animate-spin text-muted-foreground" />
      </div>

      <div v-else class="flex gap-10">
        <article class="flex-1 min-w-0 max-w-3xl markdown-body" v-html="html" />

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
