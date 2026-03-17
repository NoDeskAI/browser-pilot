import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import { dockerApiPlugin } from './vite-plugin-docker-api'
import { aiChatPlugin } from './vite-plugin-ai-chat'

export default defineConfig({
  plugins: [vue(), tailwindcss(), dockerApiPlugin(), aiChatPlugin()],
  server: {
    port: 9874,
    host: true,
  },
})
