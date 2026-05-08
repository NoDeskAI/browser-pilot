<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useAttrs, watch } from 'vue'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

defineOptions({
  inheritAttrs: false,
})

const props = withDefaults(defineProps<{
  display?: string | number | boolean | null
  content?: unknown
  json?: boolean
  contentClass?: string
  side?: 'top' | 'right' | 'bottom' | 'left'
  align?: 'start' | 'center' | 'end'
}>(), {
  json: false,
  contentClass: '',
  side: 'left',
  align: 'start',
})

const attrs = useAttrs()

const triggerAttrs = computed(() => {
  const rest = { ...attrs }
  delete rest.class
  return rest
})

const triggerEl = ref<HTMLElement | null>(null)
const isTruncated = ref(false)
let resizeObserver: ResizeObserver | undefined

function updateTruncation() {
  const el = triggerEl.value
  if (!el) {
    isTruncated.value = false
    return
  }
  isTruncated.value = el.scrollWidth > el.clientWidth + 1
}

function observeTrigger() {
  resizeObserver?.disconnect()
  resizeObserver = undefined
  updateTruncation()
  if (typeof ResizeObserver !== 'undefined' && triggerEl.value) {
    resizeObserver = new ResizeObserver(updateTruncation)
    resizeObserver.observe(triggerEl.value)
  }
}

const triggerClass = computed(() => cn(
  'inline-block min-w-0 max-w-[160px] truncate font-mono align-bottom',
  isTruncated.value && 'cursor-help border-b border-dashed border-muted-foreground/50',
  attrs.class,
))

const displayText = computed(() => {
  if (props.display === null || props.display === undefined || props.display === '') return '-'
  return String(props.display)
})

const fullValue = computed(() => {
  if (props.content === null || props.content === undefined || props.content === '') return displayText.value
  return props.content
})

const tooltipText = computed(() => {
  if (props.json || typeof fullValue.value === 'object') {
    try {
      return JSON.stringify(fullValue.value, null, 2)
    } catch {
      return String(fullValue.value)
    }
  }
  return String(fullValue.value || '-')
})

const hasTooltip = computed(() => isTruncated.value && tooltipText.value !== '-')

const tooltipContentClass = computed(() => cn(
  props.json
    ? 'w-[320px] max-w-[calc(100vw-2rem)] text-[10px] font-mono leading-snug'
    : 'max-w-[360px] whitespace-pre-wrap break-words text-[10px] font-mono leading-snug',
  props.contentClass,
))

watch(displayText, async () => {
  await nextTick()
  updateTruncation()
})

watch(triggerEl, async () => {
  await nextTick()
  observeTrigger()
})

onMounted(async () => {
  await nextTick()
  observeTrigger()
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
})
</script>

<template>
  <Tooltip v-if="hasTooltip">
    <TooltipTrigger as-child>
      <span ref="triggerEl" v-bind="triggerAttrs" :class="triggerClass">{{ displayText }}</span>
    </TooltipTrigger>
    <TooltipContent
      :side="side"
      :align="align"
      :class="tooltipContentClass"
    >
      <pre v-if="json" class="w-full min-w-0 max-h-64 overflow-y-auto overflow-x-hidden whitespace-pre-wrap break-words [scrollbar-gutter:stable] font-mono">{{ tooltipText }}</pre>
      <span v-else>{{ tooltipText }}</span>
    </TooltipContent>
  </Tooltip>
  <span v-else ref="triggerEl" v-bind="triggerAttrs" :class="triggerClass">{{ displayText }}</span>
</template>
