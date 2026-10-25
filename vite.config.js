import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
  test: { environment: 'node', include: ['src/**/*.test.js'] },
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.svg', 'pwa-192.png', 'pwa-512.png'],
      manifest: {
        name: 'Clarity Chat AI Workspace',
        short_name: 'Clarity Chat',
        description: 'A local-first AI chat workspace powered by Ollama.',
        theme_color: '#10131d',
        background_color: '#10131d',
        display: 'standalone',
        start_url: '/',
        icons: [
          { src: '/pwa-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
          { src: '/pwa-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
          { src: '/pwa-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
          { src: '/pwa-512.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'any' },
        ],
      },
      // Never let the service worker cache or answer API calls (chat must always hit the server).
      workbox: { navigateFallback: '/index.html', navigateFallbackDenylist: [/^\/api\//] },
    }),
  ],
})
