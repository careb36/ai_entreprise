import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // Electron loads dist/index.html from file://, so it needs relative asset
  // URLs. The web build is served from the site root and must use absolute
  // ones: with './' a deep link such as /evidence/1 would request
  // /evidence/assets/... and render a blank page.
  base: process.env.ELECTRON ? './' : '/',
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/v1': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
    watch: {
      usePolling: true,
      interval: 1000,
    },
  }
})
