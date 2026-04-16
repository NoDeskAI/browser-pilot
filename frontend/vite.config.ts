import { existsSync } from 'fs'
import { resolve } from 'path'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

const eeDir = resolve(__dirname, '../ee/frontend')
const hasEE = existsSync(eeDir)

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  resolve: {
    alias: hasEE ? { '@ee': eeDir } : {},
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
