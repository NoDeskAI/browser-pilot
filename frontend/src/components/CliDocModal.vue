<script setup lang="ts">
import { ref, computed } from 'vue'
import { X, Copy, Check, SquareTerminal } from 'lucide-vue-next'

defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const apiUrl = computed(() => location.origin)

const copiedIdx = ref<number | null>(null)
let copyTimer: ReturnType<typeof setTimeout> | null = null

function copyBlock(text: string, idx: number) {
  navigator.clipboard.writeText(text).then(() => {
    if (copyTimer) clearTimeout(copyTimer)
    copiedIdx.value = idx
    copyTimer = setTimeout(() => { copiedIdx.value = null }, 1500)
  })
}

const blocks = computed(() => [
  {
    label: '安装',
    code: 'pip install nwb-cli',
  },
  {
    label: '连接到此服务',
    code: `nwb config set api-url ${apiUrl.value}`,
  },
  {
    label: '基本用法',
    code: `nwb session list
nwb session create --name "My Task"
nwb session use <session-id>
nwb navigate https://example.com
nwb observe
nwb screenshot -o page.png`,
  },
])
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-[9998] bg-black/50 flex items-center justify-center"
      @click.self="emit('close')"
      @keydown.escape.window="emit('close')"
    >
      <div class="max-w-md w-full mx-4 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl shadow-2xl">
        <!-- Header -->
        <div class="flex items-center gap-2 px-4 py-3 border-b border-[var(--color-border)]">
          <SquareTerminal class="w-4 h-4 text-[var(--color-accent)]" />
          <span class="text-sm font-semibold text-[var(--color-text)]">CLI 接入指南</span>
          <button
            @click="emit('close')"
            class="ml-auto w-6 h-6 flex items-center justify-center rounded-md hover:bg-[var(--color-surface-hover)] text-[var(--color-text-dim)] hover:text-[var(--color-text)] transition-colors"
          >
            <X class="w-4 h-4" />
          </button>
        </div>

        <!-- Body -->
        <div class="p-4 space-y-4 max-h-[70vh] overflow-y-auto">
          <div v-for="(block, i) in blocks" :key="i">
            <div class="text-[10px] text-[var(--color-text-dim)] uppercase tracking-wider mb-1.5">{{ block.label }}</div>
            <div class="relative group/code">
              <pre class="px-3 py-2.5 rounded-lg bg-[var(--color-bg)] border border-[var(--color-border)] text-[11px] font-mono text-[var(--color-text)] leading-relaxed overflow-x-auto whitespace-pre">{{ block.code }}</pre>
              <button
                @click="copyBlock(block.code, i)"
                class="absolute top-1.5 right-1.5 w-6 h-6 flex items-center justify-center rounded-md transition-colors"
                :class="copiedIdx === i
                  ? 'text-green-400 bg-green-400/10'
                  : 'text-[var(--color-text-dim)] opacity-0 group-hover/code:opacity-100 hover:bg-[var(--color-surface-hover)]'"
                :title="copiedIdx === i ? '已复制' : '复制'"
              >
                <Check v-if="copiedIdx === i" class="w-3.5 h-3.5" />
                <Copy v-else class="w-3.5 h-3.5" />
              </button>
            </div>
          </div>

          <p class="text-[11px] text-[var(--color-text-dim)] leading-relaxed">
            添加 <code class="px-1 py-0.5 rounded bg-[var(--color-bg)] text-[10px] font-mono">--json</code> 可获得机器可读输出，适合与 AI Agent 框架集成。
          </p>
        </div>
      </div>
    </div>
  </Teleport>
</template>
