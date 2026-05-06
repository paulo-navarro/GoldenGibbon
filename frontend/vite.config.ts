import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    watch: {
      usePolling: true,
    },
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET || 'http://localhost:8000',
      },
      '/ws': {
        target: process.env.VITE_WS_TARGET || 'ws://localhost:8000',
        ws: true,
      },
    },
  },
})
