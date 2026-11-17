import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// The packaged desktop app's build: `npm run electron:build` selects it with
// `--mode electron` (an env var prefix such as ELECTRON=1 does not work in
// Windows' cmd.exe); ELECTRON=1 is still honoured.
const isElectronBuild = (mode: string) => Boolean(process.env.ELECTRON) || mode === 'electron'

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  // Both builds are served from a site root (the desktop app's from its
  // local server, frontend/electron/local-server.ts), so asset URLs are
  // absolute: with './' a deep link such as /evidence/1 would request
  // /evidence/assets/... and render a blank page.
  base: '/',
  // The desktop build calls the API on the page's own origin, which its
  // local server forwards to the backend (docs/design/SESSIONS.md, Desktop app).
  define: isElectronBuild(mode) ? { 'import.meta.env.VITE_API_SAME_ORIGIN': JSON.stringify('true') } : {},
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
