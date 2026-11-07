import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  // Electron loads dist/index.html from file://, so it needs relative asset
  // URLs. The web build is served from the site root and must use absolute
  // ones: with './' a deep link such as /evidence/1 would request
  // /evidence/assets/... and render a blank page.
  // `npm run electron:build` selects it with `--mode electron` (an env var
  // prefix such as ELECTRON=1 does not work in Windows' cmd.exe); ELECTRON=1
  // is still honoured.
  base: process.env.ELECTRON || mode === 'electron' ? './' : '/',
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    // Hooks run in the order they were registered, so the setup file's
    // afterEach unmounts what a test rendered before the test file's own
    // afterEach clears the shared query client; otherwise the still-mounted
    // components refetch into the next test's fetch stub.
    sequence: { hooks: 'list' },
    // The self-hosted runner shares its machine with the e2e job and a second
    // runner: a form test that takes 0.6 s here took over 5 s there and timed
    // out. A real hang still fails, 20 s later.
    testTimeout: 20000,
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
}))
