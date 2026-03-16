<script setup lang="ts">
import { computed } from 'vue'
import { useDocker } from '../composables/useDocker'
import type { Solution } from '../solutions'

const props = defineProps<{
  solution: Solution
  active: boolean
}>()

const emit = defineEmits<{ select: [] }>()
const { state: docker, startService, stopService } = useDocker()

const isLoading = computed(() => !!docker.loading[props.solution.id])

const containerState = computed(() => {
  const svcs = props.solution.services
  const states = svcs.map(s => docker.statuses[s])
  if (states.every(s => s === 'running')) return 'running'
  if (states.some(s => s === 'running')) return 'partial'
  if (states.some(s => s === 'exited' || s === 'created')) return 'stopped'
  return 'unknown'
})

const statusLabel = computed(() => {
  if (isLoading.value) return '操作中...'
  switch (containerState.value) {
    case 'running': return '运行中'
    case 'partial': return '部分运行'
    case 'stopped': return '已停止'
    default: return '未部署'
  }
})

function toggleService(e: Event) {
  e.stopPropagation()
  if (isLoading.value) return
  if (containerState.value === 'running') {
    stopService(props.solution.id)
  } else {
    startService(props.solution.id)
  }
}
</script>

<template>
  <div
    class="group relative rounded-lg border p-3 cursor-pointer transition-all duration-150"
    :class="active
      ? 'border-[color:var(--accent)] bg-[color:var(--accent)]/8 shadow-sm'
      : 'border-[var(--color-border)] bg-[var(--color-bg)] hover:border-[color:var(--accent)]/50 hover:bg-[var(--color-surface-hover)]'"
    :style="{ '--accent': solution.color } as any"
    @click="emit('select')"
  >
    <!-- Top row: name + controls -->
    <div class="flex items-center justify-between mb-1.5">
      <h3 class="text-sm font-semibold truncate">{{ solution.name }}</h3>

      <div class="flex items-center gap-1.5">
        <!-- Status dot -->
        <span
          class="inline-block w-2 h-2 rounded-full shrink-0"
          :class="{
            'bg-green-500': containerState === 'running',
            'bg-yellow-500': containerState === 'partial' || isLoading,
            'bg-gray-500': containerState === 'stopped',
            'bg-red-500': containerState === 'unknown',
            'animate-pulse': isLoading,
          }"
        />

        <!-- Start / Stop button -->
        <button
          @click="toggleService"
          :disabled="isLoading"
          class="shrink-0 w-6 h-6 flex items-center justify-center rounded transition-colors disabled:opacity-40"
          :class="containerState === 'running'
            ? 'text-red-400 hover:bg-red-500/20'
            : 'text-green-400 hover:bg-green-500/20'"
          :title="containerState === 'running' ? '停止' : '启动'"
        >
          <!-- Spinner -->
          <svg v-if="isLoading" class="w-3.5 h-3.5 animate-spin" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
            <path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.49-8.49l2.83-2.83M2 12h4m12 0h4m-3.93 7.07l-2.83-2.83M7.76 7.76L4.93 4.93" />
          </svg>
          <!-- Stop icon -->
          <svg v-else-if="containerState === 'running'" class="w-3 h-3" fill="currentColor" viewBox="0 0 16 16">
            <rect x="3" y="3" width="10" height="10" rx="1" />
          </svg>
          <!-- Play icon -->
          <svg v-else class="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 20 20">
            <path d="M6.3 2.841A1.5 1.5 0 004 4.11V15.89a1.5 1.5 0 002.3 1.269l9.344-5.89a1.5 1.5 0 000-2.538L6.3 2.84z"/>
          </svg>
        </button>
      </div>
    </div>

    <!-- Tech + latency + status label -->
    <div class="flex items-center gap-2 mb-2 text-xs text-[var(--color-text-dim)]">
      <span>{{ solution.tech }}</span>
      <span class="text-[var(--color-border)]">|</span>
      <span>{{ solution.latency }}</span>
      <span
        class="ml-auto shrink-0 whitespace-nowrap text-[10px] px-1.5 py-0.5 rounded-full"
        :class="{
          'bg-green-500/15 text-green-400': containerState === 'running',
          'bg-yellow-500/15 text-yellow-400': containerState === 'partial' || isLoading,
          'bg-gray-500/15 text-gray-400': containerState === 'stopped' || containerState === 'unknown',
        }"
      >
        {{ statusLabel }}
      </span>
    </div>

    <!-- Tags -->
    <div class="flex flex-wrap gap-1">
      <span
        v-for="tag in solution.tags.slice(0, 3)"
        :key="tag"
        class="text-[10px] px-1.5 py-0.5 rounded border border-[var(--color-border)] text-[var(--color-text-dim)]"
      >{{ tag }}</span>
      <span
        v-if="solution.tags.length > 3"
        class="text-[10px] px-1.5 py-0.5 text-[var(--color-text-dim)]"
      >+{{ solution.tags.length - 3 }}</span>
    </div>

    <!-- Active indicator bar -->
    <div
      v-if="active"
      class="absolute left-0 top-2 bottom-2 w-0.5 rounded-r"
      :style="{ backgroundColor: solution.color }"
    />
  </div>
</template>
