// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest'

interface Resolved {
  base?: string
  define?: Record<string, string>
}

async function loadConfig(electron: string | undefined, mode = 'production'): Promise<Resolved> {
  vi.resetModules()
  if (electron === undefined) vi.stubEnv('ELECTRON', '')
  else vi.stubEnv('ELECTRON', electron)
  // A computed specifier keeps `tsc` from pulling vite.config.ts (which belongs to
  // tsconfig.node.json) into the app project; Vitest still resolves it at runtime.
  const specifier = '../vite.config.ts'
  const { default: config } = await import(/* @vite-ignore */ specifier)
  const resolved = typeof config === 'function' ? await config({ command: 'build', mode }) : config
  return resolved as Resolved
}

const SAME_ORIGIN = 'import.meta.env.VITE_API_SAME_ORIGIN'

describe('vite config', () => {
  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it('uses absolute asset URLs for every build', async () => {
    // With './' a deep link such as /evidence/1 requests /evidence/assets/... and renders blank.
    // The desktop build is served from its local server's root, like the web build.
    expect((await loadConfig(undefined)).base).toBe('/')
    expect((await loadConfig('1')).base).toBe('/')
    expect((await loadConfig(undefined, 'electron')).base).toBe('/')
  })

  it('makes only the desktop build call the API on its own origin', async () => {
    expect((await loadConfig(undefined)).define?.[SAME_ORIGIN]).toBeUndefined()
    // `npm run electron:build` selects it with `--mode electron`; ELECTRON=1 still works.
    expect((await loadConfig(undefined, 'electron')).define?.[SAME_ORIGIN]).toBe('"true"')
    expect((await loadConfig('1')).define?.[SAME_ORIGIN]).toBe('"true"')
  })
})
