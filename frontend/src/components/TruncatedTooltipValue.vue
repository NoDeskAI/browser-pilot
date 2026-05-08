<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useAttrs, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { toast } from 'vue-sonner'
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

const { t } = useI18n()
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
  'inline-block min-w-0 max-w-[160px] truncate font-mono cursor-pointer align-bottom',
  isTruncated.value && 'border-b border-dashed border-muted-foreground/50',
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
const copyText = computed(() => (hasTooltip.value ? tooltipText.value : displayText.value))

const tooltipContentClass = computed(() => cn(
  props.json
    ? 'w-[320px] max-w-[calc(100vw-2rem)] text-[10px] font-mono leading-snug'
    : 'max-w-[360px] whitespace-pre-wrap break-words text-[10px] font-mono leading-snug',
  props.contentClass,
))

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
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
  }
  return writeClipboardTextWithSelection(text)
}

async function copyValue() {
  const copied = await writeClipboardText(copyText.value)
  if (copied) {
    toast.success(t('session.copied'))
  } else {
    toast.error(t('vnc.clipboardError'))
  }
}

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
      <span
        ref="triggerEl"
        v-bind="triggerAttrs"
        :class="triggerClass"
        role="button"
        tabindex="0"
        @click.stop="copyValue"
        @keydown.enter.prevent.stop="copyValue"
        @keydown.space.prevent.stop="copyValue"
      >{{ displayText }}</span>
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
  <span
    v-else
    ref="triggerEl"
    v-bind="triggerAttrs"
    :class="triggerClass"
    role="button"
    tabindex="0"
    @click.stop="copyValue"
    @keydown.enter.prevent.stop="copyValue"
    @keydown.space.prevent.stop="copyValue"
  >{{ displayText }}</span>
</template>
