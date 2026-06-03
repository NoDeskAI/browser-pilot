import type { Component } from 'vue'

export type EeAccountMenuItem = {
  path: string
  labelKey: string
  icon?: Component
}

export const eeAccountMenuItems: EeAccountMenuItem[] = []
