declare const __EE__: boolean

declare module '@ee/routes' {
  export const eeRoutes: Array<import('vue-router').RouteRecordRaw>
  export const eePublicRoutePrefixes: string[]
}

declare module '@ee/locales' {
  export const eeMessages: {
    zh: Record<string, unknown>
    en: Record<string, unknown>
  }
}

declare module '@ee/nav' {
  import { Component } from 'vue'

  export type EeAccountMenuItem = {
    path: string
    labelKey: string
    icon?: Component
  }

  export const eeAccountMenuItems: EeAccountMenuItem[]
}

declare module '@ee/components/SsoLoginButton.vue' {
  import { DefineComponent } from 'vue'
  const component: DefineComponent
  export default component
}

declare module '@ee/components/SsoConfigPanel.vue' {
  import { DefineComponent } from 'vue'
  const component: DefineComponent
  export default component
}

declare module '@ee/components/TenantManager.vue' {
  import { DefineComponent } from 'vue'
  const component: DefineComponent
  export default component
}
