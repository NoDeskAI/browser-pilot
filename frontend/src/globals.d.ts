declare const __EE__: boolean

declare module '@ee/routes' {
  export const eeRoutes: Array<import('vue-router').RouteRecordRaw>
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
