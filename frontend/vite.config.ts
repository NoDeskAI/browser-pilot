import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import { dockerApiPlugin } from './vite-plugin-docker-api'

export default defineConfig({
  plugins: [vue(), tailwindcss(), dockerApiPlugin()],
  server: {
    port: 9874,
    host: true,
  },
})
