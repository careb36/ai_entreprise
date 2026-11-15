// @vitest-environment node
import * as fs from 'fs'
import * as http from 'http'
import type { AddressInfo } from 'net'
import * as os from 'os'
import * as path from 'path'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { checkBackendUrl, refusal, resolveStatic, rewriteLocation, startLocalServer, stripCookieDomain, type LocalServer } from './local-server'

// The test setup replaces `fetch`, so requests go through node:http.
interface Answer {
  status: number
  headers: http.IncomingHttpHeaders
  body: string
}

function request(
  origin: string,
  target: string,
  options: { method?: string; headers?: Record<string, string>; body?: string } = {},
): Promise<Answer> {
  const url = new URL(origin)
  return new Promise((resolve, reject) => {
    const req = http.request(
      { host: url.hostname, port: url.port, method: options.method ?? 'GET', path: target, headers: options.headers },
      res => {
        let body = ''
        res.setEncoding('utf8')
        res.on('data', chunk => (body += chunk))
        res.on('end', () => resolve({ status: res.statusCode ?? 0, headers: res.headers, body }))
      },
    )
    req.on('error', reject)
    req.end(options.body)
  })
}

interface Seen {
  method: string
  url: string
  headers: http.IncomingHttpHeaders
  body: string
}

let dist: string
let backend: http.Server
let backendOrigin: string
let local: LocalServer
const seen: Seen[] = []
let releaseStream: (() => void) | null = null

beforeAll(async () => {
  dist = fs.mkdtempSync(path.join(os.tmpdir(), 'fabrique-dist-'))
  fs.mkdirSync(path.join(dist, 'assets'))
  fs.writeFileSync(path.join(dist, 'index.html'), '<!doctype html><div id="root"></div>')
  fs.writeFileSync(path.join(dist, 'assets', 'app-1234.js'), 'console.log(1)')
  fs.writeFileSync(path.join(path.dirname(dist), 'fabrique-secret.txt'), 'outside')

  backend = http.createServer((req, res) => {
    let body = ''
    req.on('data', chunk => (body += chunk))
    req.on('end', () => {
      seen.push({ method: req.method ?? '', url: req.url ?? '', headers: req.headers, body })
      if (req.url === '/v1/auth/login') {
        res.writeHead(200, {
          'Content-Type': 'application/json',
          'Access-Control-Allow-Origin': 'http://localhost:5173',
          'Set-Cookie': [
            'fabrique_session=abc; Domain=localhost; HttpOnly; Max-Age=900; Path=/; SameSite=strict; Secure',
            'fabrique_refresh=def; domain=api.example.com; HttpOnly; Path=/v1/auth; SameSite=strict; Secure',
            'fabrique_csrf=ghi; Path=/; SameSite=strict; Secure',
          ],
        })
        return res.end('{"token_type":"cookie"}')
      }
      if (req.url === '/v1/auth/sso/callback?state=s&code=c') {
        res.writeHead(302, { Location: 'http://localhost:5173/login/sso?code=handoff' })
        return res.end()
      }
      if (req.url === '/v1/auth/sso/acme/start') {
        res.writeHead(302, { Location: 'https://idp.example.com/authorize?state=s' })
        return res.end()
      }
      if (req.url === '/v1/glassroom/stream?sprint_id=1') {
        res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' })
        res.write('data: first\n\n')
        releaseStream = () => res.end('data: second\n\n')
        return
      }
      res.writeHead(200, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify({ path: req.url }))
    })
  })
  await new Promise<void>(done => backend.listen(0, '127.0.0.1', done))
  backendOrigin = `http://127.0.0.1:${(backend.address() as AddressInfo).port}`
  local = await startLocalServer({ distDir: dist, backendUrl: backendOrigin })
})

afterAll(async () => {
  await local.close()
  backend.closeAllConnections()
  await new Promise<void>(done => backend.close(() => done()))
  fs.rmSync(dist, { recursive: true, force: true })
  fs.rmSync(path.join(path.dirname(dist), 'fabrique-secret.txt'), { force: true })
})

const host = () => new URL(local.origin).host

describe('local server: static files', () => {
  it('listens on 127.0.0.1 only, on a port the system picked', () => {
    expect(local.origin).toMatch(/^http:\/\/127\.0\.0\.1:\d+$/)
    expect(local.origin).not.toBe('http://127.0.0.1:0')
  })

  it('serves the built page with its type and the security headers', async () => {
    const answer = await request(local.origin, '/')
    expect(answer.status).toBe(200)
    expect(answer.headers['content-type']).toBe('text/html; charset=utf-8')
    expect(answer.headers['content-security-policy']).toContain("script-src 'self'")
    expect(answer.headers['content-security-policy']).toContain("frame-ancestors 'none'")
    expect(answer.headers['x-content-type-options']).toBe('nosniff')
    expect(answer.headers['x-frame-options']).toBe('DENY')
    expect(answer.headers['cross-origin-opener-policy']).toBe('same-origin')
    expect(answer.headers['cache-control']).toBe('no-cache')
    const asset = await request(local.origin, '/assets/app-1234.js')
    expect(asset.headers['content-type']).toBe('text/javascript; charset=utf-8')
    expect(asset.body).toBe('console.log(1)')
  })

  it('answers the app routes with index.html and a missing asset with 404', async () => {
    const route = await request(local.origin, '/evidence/1?tab=runs')
    expect(route.status).toBe(200)
    expect(route.body).toContain('id="root"')
    expect((await request(local.origin, '/assets/missing.js')).status).toBe(404)
  })

  it('refuses to leave the build folder', async () => {
    for (const target of ['/%2e%2e/fabrique-secret.txt', '/assets/..%2f..%2ffabrique-secret.txt', '/..%5cfabrique-secret.txt']) {
      const answer = await request(local.origin, target)
      expect(answer.status).not.toBe(200)
      expect(answer.body).not.toContain('outside')
    }
    expect(resolveStatic(dist, '/../fabrique-secret.txt')).toBeNull()
    expect(resolveStatic(dist, '/a%00b')).toBeNull()
    expect(resolveStatic(dist, '/assets/app-1234.js')).toBe(path.join(dist, 'assets', 'app-1234.js'))
  })

  it('serves only GET and HEAD outside /v1', async () => {
    const answer = await request(local.origin, '/index.html', { method: 'POST', headers: { Origin: local.origin } })
    expect(answer.status).toBe(405)
  })
})

describe('local server: the /v1 proxy', () => {
  it('forwards /v1 to the backend with the backend as Host and without Origin', async () => {
    seen.length = 0
    const answer = await request(local.origin, '/v1/companies?limit=5', { headers: { Cookie: 'fabrique_session=abc', 'X-Forwarded-For': '10.0.0.1' } })
    expect(answer.status).toBe(200)
    expect(JSON.parse(answer.body)).toEqual({ path: '/v1/companies?limit=5' })
    expect(seen[0].headers.host).toBe(new URL(backendOrigin).host)
    expect(seen[0].headers.cookie).toBe('fabrique_session=abc')
    expect(seen[0].headers['x-forwarded-for']).toBeUndefined()
  })

  it('forwards an unsafe request with its body, cookie and CSRF header', async () => {
    seen.length = 0
    const answer = await request(local.origin, '/v1/tasks', {
      method: 'POST',
      headers: { Origin: local.origin, 'Content-Type': 'application/json', 'X-Fabrique-CSRF': 'ghi', Cookie: 'fabrique_csrf=ghi' },
      body: '{"title":"x"}',
    })
    expect(answer.status).toBe(200)
    expect(seen[0]).toMatchObject({ method: 'POST', body: '{"title":"x"}' })
    expect(seen[0].headers['x-fabrique-csrf']).toBe('ghi')
    expect(seen[0].headers.origin).toBeUndefined()
  })

  it('drops the cookies\' Domain and keeps every other attribute, and grants no other origin', async () => {
    const answer = await request(local.origin, '/v1/auth/login', { method: 'POST', headers: { Origin: local.origin }, body: '{}' })
    expect(answer.headers['set-cookie']).toEqual([
      'fabrique_session=abc; HttpOnly; Max-Age=900; Path=/; SameSite=strict; Secure',
      'fabrique_refresh=def; HttpOnly; Path=/v1/auth; SameSite=strict; Secure',
      'fabrique_csrf=ghi; Path=/; SameSite=strict; Secure',
    ])
    expect(answer.headers['access-control-allow-origin']).toBeUndefined()
  })

  it('sends the SSO callback\'s redirect to the local /login/sso and leaves the provider\'s alone', async () => {
    const back = await request(local.origin, '/v1/auth/sso/callback?state=s&code=c', { headers: { 'Sec-Fetch-Site': 'none' } })
    expect(back.status).toBe(302)
    expect(back.headers.location).toBe(`${local.origin}/login/sso?code=handoff`)
    const out = await request(local.origin, '/v1/auth/sso/acme/start')
    expect(out.headers.location).toBe('https://idp.example.com/authorize?state=s')
  })

  it('streams Server-Sent Events as they come', async () => {
    const url = new URL(local.origin)
    const chunks: string[] = []
    await new Promise<void>((resolve, reject) => {
      const req = http.get({ host: url.hostname, port: url.port, path: '/v1/glassroom/stream?sprint_id=1' }, res => {
        expect(res.headers['content-type']).toBe('text/event-stream')
        res.setEncoding('utf8')
        res.on('data', chunk => {
          chunks.push(chunk)
          // The first event arrived while the backend still holds the stream open.
          if (chunks.length === 1) releaseStream?.()
        })
        res.on('end', () => resolve())
      })
      req.on('error', reject)
    })
    expect(chunks[0]).toBe('data: first\n\n')
    expect(chunks.join('')).toBe('data: first\n\ndata: second\n\n')
  })

  it('answers 502 when the backend is down', async () => {
    const dead = await startLocalServer({ distDir: dist, backendUrl: 'http://127.0.0.1:1' })
    try {
      expect((await request(dead.origin, '/v1/health')).status).toBe(502)
    } finally {
      await dead.close()
    }
  })

  it('never forwards anything but /v1, nor an absolute or scheme-relative target', async () => {
    seen.length = 0
    expect((await request(local.origin, '/v1x/secret')).body).toContain('id="root"')
    expect((await request(local.origin, '/docs')).body).toContain('id="root"')
    expect((await request(local.origin, 'http://evil.example/v1/companies')).status).toBe(400)
    expect((await request(local.origin, '//evil.example/v1/companies')).status).toBe(400)
    expect(seen).toHaveLength(0)
  })
})

describe('local server: who may call it', () => {
  it('refuses a Host that is not its own (DNS rebinding)', async () => {
    seen.length = 0
    for (const forged of ['evil.example', 'localhost:' + new URL(local.origin).port, '127.0.0.1']) {
      const answer = await request(local.origin, '/v1/companies', { headers: { Host: forged } })
      expect(answer.status).toBe(403)
    }
    expect((await request(local.origin, '/', { headers: { Host: 'evil.example' } })).status).toBe(403)
    expect(seen).toHaveLength(0)
  })

  it('refuses an unsafe method without its own Origin', async () => {
    seen.length = 0
    for (const headers of [{}, { Origin: 'http://evil.example' }, { Origin: 'http://127.0.0.1:5173' }, { Origin: 'null' }]) {
      const answer = await request(local.origin, '/v1/tasks', { method: 'POST', headers, body: '{}' })
      expect(answer.status).toBe(403)
    }
    expect(seen).toHaveLength(0)
  })

  it('refuses a read sent by another origin or site', async () => {
    seen.length = 0
    expect((await request(local.origin, '/v1/companies', { headers: { Origin: 'http://localhost:5173' } })).status).toBe(403)
    expect((await request(local.origin, '/v1/companies', { headers: { 'Sec-Fetch-Site': 'same-site' } })).status).toBe(403)
    expect((await request(local.origin, '/v1/companies', { headers: { 'Sec-Fetch-Site': 'cross-site' } })).status).toBe(403)
    expect(seen).toHaveLength(0)
    expect((await request(local.origin, '/v1/companies', { headers: { 'Sec-Fetch-Site': 'same-origin' } })).status).toBe(200)
  })

  it('decides from Host, Origin, Sec-Fetch-Site and the method', () => {
    const origin = 'http://127.0.0.1:4000'
    const ok = { host: '127.0.0.1:4000' }
    expect(refusal({ method: 'GET', headers: ok }, '127.0.0.1:4000', origin)).toBeNull()
    expect(refusal({ method: 'DELETE', headers: { ...ok, origin } }, '127.0.0.1:4000', origin)).toBeNull()
    expect(refusal({ method: 'DELETE', headers: ok }, '127.0.0.1:4000', origin)).toBe('unsafe method without Origin')
    expect(refusal({ method: 'GET', headers: { ...ok, 'sec-fetch-site': 'none' } }, '127.0.0.1:4000', origin)).toBeNull()
  })
})

describe('the backend it forwards to', () => {
  it('takes https anywhere and plain http only on this machine', async () => {
    expect(checkBackendUrl('https://api.example.com').host).toBe('api.example.com')
    expect(checkBackendUrl('http://localhost:8000').port).toBe('8000')
    expect(checkBackendUrl('http://127.0.0.1:8000').hostname).toBe('127.0.0.1')
    expect(checkBackendUrl('http://[::1]:8000').hostname).toBe('[::1]')
    // The session cookies would cross the network in clear.
    expect(() => checkBackendUrl('http://192.168.1.10:8000')).toThrow(/https/)
    expect(() => checkBackendUrl('http://localhost.evil.example')).toThrow(/https/)
    expect(() => checkBackendUrl('file:///etc')).toThrow()
    expect(() => checkBackendUrl('not a url')).toThrow(/not a URL/)
    await expect(startLocalServer({ distDir: dist, backendUrl: 'http://api.example.com' })).rejects.toThrow(/https/)
  })
})

describe('header rewriting', () => {
  it('strips Domain in any case and position', () => {
    expect(stripCookieDomain('a=b; Path=/; DOMAIN=.example.com; Secure')).toBe('a=b; Path=/; Secure')
    // A cookie whose value mentions domain= is untouched.
    expect(stripCookieDomain('domain=x; Path=/')).toBe('domain=x; Path=/')
  })

  it('moves the backend\'s own redirects to the local origin, and only those', () => {
    const backendUrl = new URL('https://api.example.com')
    const localOrigin = 'http://127.0.0.1:4000'
    expect(rewriteLocation('https://api.example.com/v1/x?y=1', '/v1/a', backendUrl, localOrigin)).toBe(`${localOrigin}/v1/x?y=1`)
    expect(rewriteLocation('/v1/x', '/v1/a', backendUrl, localOrigin)).toBe(`${localOrigin}/v1/x`)
    expect(rewriteLocation('https://app.example.com/login/sso?error=denied', '/v1/auth/sso/acme/start', backendUrl, localOrigin))
      .toBe(`${localOrigin}/login/sso?error=denied`)
    // Outside the SSO routes a redirect to another host is not touched.
    expect(rewriteLocation('https://app.example.com/login/sso?code=c', '/v1/other', backendUrl, localOrigin))
      .toBe('https://app.example.com/login/sso?code=c')
    expect(rewriteLocation('https://idp.example.com/authorize', '/v1/auth/sso/acme/start', backendUrl, localOrigin))
      .toBe('https://idp.example.com/authorize')
  })
})
