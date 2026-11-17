// @vitest-environment node
/**
 * The web frontend's own headers (issue #541, WSTG-v42-CLNT-09 and CONF-14).
 * `docker compose up` serves the web app from the Vite dev server, and the
 * page is where a person approves, holds and undoes work, so no other site
 * may frame it. The checks run against a real dev server.
 */
import * as http from 'http'
import * as path from 'path'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { createServer, type ViteDevServer } from 'vite'
import config, { WEB_PAGE_HEADERS } from './vite.config'

// The test setup replaces `fetch`, so requests go through node:http.
function headersOf(origin: string, target: string): Promise<{ status: number; headers: http.IncomingHttpHeaders }> {
  const url = new URL(origin)
  return new Promise((resolve, reject) => {
    const req = http.request({ host: url.hostname, port: url.port, path: target }, res => {
      res.resume()
      res.on('end', () => resolve({ status: res.statusCode ?? 0, headers: res.headers }))
    })
    req.on('error', reject)
    req.end()
  })
}

let server: ViteDevServer
let origin = ''

beforeAll(async () => {
  server = await createServer({
    configFile: path.resolve(__dirname, 'vite.config.ts'),
    root: __dirname,
    logLevel: 'silent',
    server: { host: '127.0.0.1', port: 0, strictPort: false, watch: null },
  })
  await server.listen()
  origin = server.resolvedUrls?.local[0]?.replace(/\/$/, '') ?? ''
}, 60_000)

afterAll(async () => {
  await server?.close()
})

describe('the web frontend headers', () => {
  it('the page cannot be framed by another site', async () => {
    const answer = await headersOf(origin, '/')
    expect(answer.status).toBe(200)
    expect(answer.headers['content-security-policy']).toContain("frame-ancestors 'none'")
    expect(answer.headers['x-frame-options']).toBe('DENY')
  })

  it('carries the rest of the baseline, on an app route too', async () => {
    for (const target of ['/', '/evidence/1']) {
      const answer = await headersOf(origin, target)
      expect(answer.headers['x-content-type-options']).toBe('nosniff')
      expect(answer.headers['referrer-policy']).toBe('no-referrer')
      expect(answer.headers['cross-origin-opener-policy']).toBe('same-origin')
      expect(answer.headers['content-security-policy']).toContain("object-src 'none'")
    }
  })

  it('leaves the scripts to the page policy, so the dev server keeps working', () => {
    expect(WEB_PAGE_HEADERS['Content-Security-Policy']).not.toContain('script-src')
  })

  it('serves the same headers from `vite preview`', () => {
    const resolved = typeof config === 'function' ? config({ mode: 'production', command: 'serve' }) : config
    expect((resolved as { preview?: { headers?: unknown } }).preview?.headers).toEqual(WEB_PAGE_HEADERS)
  })
})
