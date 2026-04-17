import { existsSync } from 'fs'
import path from 'node:path'
import { resolve } from 'path'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

const eeDir = resolve(__dirname, '../ee/frontend')
const hasEE = existsSync(eeDir)

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
      ...(hasEE ? { '@ee': eeDir } : {}),
    },
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
