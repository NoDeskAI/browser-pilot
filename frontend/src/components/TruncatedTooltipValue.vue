<script setup lang="ts">
import { computed, useAttrs } from 'vue'
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

const triggerClass = computed(() => cn(
  'inline-block min-w-0 max-w-[160px] truncate font-mono cursor-help border-b border-dashed border-muted-foreground/50 align-bottom',
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

const hasTooltip = computed(() => tooltipText.value !== '-')

const tooltipContentClass = computed(() => cn(
  props.json
    ? 'w-[320px] max-w-[calc(100vw-2rem)] text-[10px] font-mono leading-snug'
    : 'max-w-[360px] whitespace-pre-wrap break-words text-[10px] font-mono leading-snug',
  props.contentClass,
))
</script>

<template>
  <Tooltip v-if="hasTooltip">
    <TooltipTrigger as-child>
      <span v-bind="triggerAttrs" :class="triggerClass">{{ displayText }}</span>
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
  <span v-else v-bind="triggerAttrs" :class="cn('inline-block min-w-0 max-w-[160px] truncate font-mono align-bottom', attrs.class)">{{ displayText }}</span>
</template>
