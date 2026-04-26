import { existsSync } from 'fs'
import path from 'node:path'
import { resolve } from 'path'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

const eeDir = resolve(__dirname, '../ee/frontend')
const edition = process.env.EDITION
const hasEE = edition ? edition === 'ee' : existsSync(resolve(eeDir, 'index.ts'))

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
      'lucide-vue-next': path.resolve(__dirname, './node_modules/lucide-vue-next'),
      ...(hasEE ? { '@ee': eeDir } : {}),
    },
    dedupe: ['vue', 'vue-router', 'vue-i18n'],
  },
  define: {
    __EE__: hasEE,
  },
  server: {
    port: 9874,
    host: true,
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
