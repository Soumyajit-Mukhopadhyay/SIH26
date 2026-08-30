import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': path.resolve(__dirname, 'src') } },
  server: {
    port: 5173,
    proxy: {
      // The console talks to a same-origin /api, so there is no CORS story and
      // no base-URL configuration to get wrong between dev and a built bundle.
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, '') },
    },
  },
  build: { target: 'es2022', chunkSizeWarningLimit: 2400 },
})
