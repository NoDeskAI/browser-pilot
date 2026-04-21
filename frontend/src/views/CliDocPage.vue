<script setup lang="ts">
import { ref, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Copy, Check, SquareTerminal } from 'lucide-vue-next'
import { useSessions } from '../composables/useSessions'
import { Button } from '@/components/ui/button'

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

const docLines = computed(() => {
  return fullDoc.value.split('\n')
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
  <div class="max-w-4xl mx-auto px-6 py-8">
    <div class="mb-6">
      <h2 class="text-lg font-semibold flex items-center gap-2 mb-2">
        <SquareTerminal class="size-5 text-primary" />
        {{ t('cliDoc.title') }}
      </h2>
      <p class="text-sm text-muted-foreground">{{ t('cliDoc.description') }}</p>
    </div>

    <Button
      @click="copyDoc"
      :variant="docCopied ? 'outline' : 'default'"
      class="w-full gap-2 mb-6"
      :class="docCopied ? 'text-green-500 border-green-500/30 bg-background' : ''"
    >
      <Check v-if="docCopied" class="size-4" />
      <Copy v-else class="size-4" />
      {{ docCopied ? t('cliDoc.copiedBtn') : t('cliDoc.copyBtn') }}
    </Button>

    <div class="rounded-md border border-border bg-muted/30 overflow-hidden flex flex-col">
      <div class="overflow-auto min-w-0">
        <div class="p-4 text-[11px] font-mono text-muted-foreground leading-relaxed w-fit min-w-full">
          <div v-for="(line, index) in docLines" :key="index" class="flex">
            <span class="select-none opacity-50 w-6 shrink-0 text-right mr-3 pr-3 border-r border-border">{{ index + 1 }}</span>
            <span class="whitespace-pre">{{ line }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
