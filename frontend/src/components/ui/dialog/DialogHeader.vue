<script setup lang="ts">
import type { HTMLAttributes } from 'vue'
import { cn } from '@/lib/utils'

const props = defineProps<{
  class?: HTMLAttributes['class']
}>()
</script>

<template>
  <div
    data-slot="dialog-header"
    :class="cn('gap-2 flex flex-col sticky top-0 bg-popover z-10 -mx-4 -mt-4 px-4 pt-4 pb-2 rounded-t-xl', props.class)"
  >
    <slot />
  </div>
</template>
