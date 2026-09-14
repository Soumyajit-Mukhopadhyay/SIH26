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
      // Target is overridable so a second backend can be run alongside the one
      // serving the demo. Two checkouts of this repo on one machine otherwise
      // fight over port 8000, and the loser is whichever you started second.
      '/api': {
        target: process.env.ORCA_API_TARGET ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
    },
  },
  build: { target: 'es2022', chunkSizeWarningLimit: 2400 },
})
