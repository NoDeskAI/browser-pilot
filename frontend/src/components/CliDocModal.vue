<script setup lang="ts">
import { ref, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { X, Copy, Check, SquareTerminal } from 'lucide-vue-next'
import { useSessions } from '../composables/useSessions'

const { t, locale } = useI18n()
const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

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

const docCopied = ref(false)
let docCopyTimer: ReturnType<typeof setTimeout> | null = null

function copyDoc() {
  navigator.clipboard.writeText(fullDoc.value).then(() => {
    if (docCopyTimer) clearTimeout(docCopyTimer)
    docCopied.value = true
    docCopyTimer = setTimeout(() => { docCopied.value = false }, 2000)
  })
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-[9998] bg-black/50 flex items-center justify-center"
      @click.self="emit('close')"
      @keydown.escape.window="emit('close')"
    >
      <div class="max-w-lg w-full mx-4 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl shadow-2xl">
        <!-- Header -->
        <div class="flex items-center gap-2 px-4 py-3 border-b border-[var(--color-border)]">
          <SquareTerminal class="w-4 h-4 text-[var(--color-accent)]" />
          <span class="text-sm font-semibold text-[var(--color-text)]">{{ t('cliDoc.title') }}</span>
          <button
            @click="emit('close')"
            class="ml-auto w-6 h-6 flex items-center justify-center rounded-md hover:bg-[var(--color-surface-hover)] text-[var(--color-text-dim)] hover:text-[var(--color-text)] transition-colors"
          >
            <X class="w-4 h-4" />
          </button>
        </div>

        <!-- Body -->
        <div class="p-4 space-y-3">
          <!-- Primary CTA -->
          <button
            @click="copyDoc"
            class="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-xs font-medium transition-colors"
            :class="docCopied
              ? 'bg-green-500/20 text-green-400 border border-green-500/30'
              : 'bg-[var(--color-accent)] text-white hover:bg-[var(--color-accent-dim)]'"
          >
            <Check v-if="docCopied" class="w-3.5 h-3.5" />
            <Copy v-else class="w-3.5 h-3.5" />
            {{ docCopied ? t('cliDoc.copiedBtn') : t('cliDoc.copyBtn') }}
          </button>
          <p class="text-[11px] text-[var(--color-text-dim)] text-center leading-relaxed">
            {{ t('cliDoc.description') }}
          </p>

          <!-- Preview -->
          <div class="relative group/doc">
            <pre class="px-3 py-2.5 rounded-lg bg-[var(--color-bg)] border border-[var(--color-border)] text-[10px] font-mono text-[var(--color-text-dim)] leading-relaxed overflow-y-auto max-h-[45vh] whitespace-pre">{{ fullDoc }}</pre>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>
