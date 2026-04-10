import { createRouter, createWebHistory } from 'vue-router'
import MainView from '../views/MainView.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: MainView },
    { path: '/settings', component: () => import('../views/SettingsView.vue') },
  ],
})
