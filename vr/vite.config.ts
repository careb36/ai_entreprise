/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The room is a static site: judges open a link (GitHub Pages or any host),
// so VR_BASE is the path it is served from there ("/la-fabrique/vr/").
export default defineConfig({
  plugins: [react()],
  base: process.env.VR_BASE ?? '/',
  build: {
    chunkSizeWarningLimit: 1500,
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
