// @vitest-environment node
import { describe, expect, it, vi } from 'vitest'

vi.mock('electron', () => ({ app: {}, BrowserWindow: {}, dialog: {}, ipcMain: {}, shell: {} }))

const { fromOrigin, isWebUrl } = await import('./ipc-handlers')

const frame = (url: string | undefined) => ({ senderFrame: url === undefined ? null : { url } }) as Parameters<typeof fromOrigin>[0]

describe('IPC sender check', () => {
  it('answers only the page on the app\'s own origin', () => {
    const origin = 'http://127.0.0.1:4000'
    expect(fromOrigin(frame(`${origin}/companies/new`), origin)).toBe(true)
    expect(fromOrigin(frame('https://idp.example.com/login'), origin)).toBe(false)
    expect(fromOrigin(frame('http://127.0.0.1:4001/'), origin)).toBe(false)
    expect(fromOrigin(frame(undefined), origin)).toBe(false)
    // Before the local server is up there is no origin to match.
    expect(fromOrigin(frame(`${origin}/`), '')).toBe(false)
  })

  it('opens only web links externally', () => {
    expect(isWebUrl('https://github.com')).toBe(true)
    expect(isWebUrl('file:///etc/passwd')).toBe(false)
  })
})
