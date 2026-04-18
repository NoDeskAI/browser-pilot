import { existsSync } from 'fs'
import path from 'node:path'
import { resolve } from 'path'
import { defineConfig, type Plugin } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

const eeDir = resolve(__dirname, '../ee/frontend')
const hasEE = existsSync(resolve(eeDir, 'routes.ts')) || existsSync(resolve(eeDir, 'routes/index.ts'))

function eeStubPlugin(): Plugin {
  return {
    name: 'ee-stub',
    resolveId(id) {
      if (!hasEE && id.startsWith('@ee/')) return '\0' + id
    },
    load(id) {
      if (!hasEE && id.startsWith('\0@ee/')) return 'export default {}'
    },
  }
}

export default defineConfig({
  plugins: [eeStubPlugin(), vue(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
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
