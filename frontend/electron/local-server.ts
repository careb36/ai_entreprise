/**
 * The packaged desktop app's local origin (issue #524, docs/design/SESSIONS.md,
 * "Desktop app").
 *
 * The session cookies are `SameSite=Strict`, so a page loaded from `file://`
 * or a custom scheme never sends them. This server, bound to 127.0.0.1 on a
 * random port, serves the built frontend and forwards `/v1/*` to the
 * configured backend, so the page and the API share one origin and the
 * cookies are first-party.
 *
 * It adds no authority of its own: it holds no credential, forwards only
 * `/v1/*` and only to the configured backend, and refuses a request whose
 * `Host` is not its own (DNS rebinding), whose `Origin` is another origin,
 * whose `Sec-Fetch-Site` says another site sent it, or which uses an unsafe
 * method without its own `Origin`.
 */
import * as fs from 'fs'
import * as http from 'http'
import * as https from 'https'
import type { AddressInfo } from 'net'
import * as path from 'path'

export interface LocalServerOptions {
  /** The built frontend (`dist/`). */
  distDir: string
  /** The backend `/v1/*` is forwarded to, e.g. `http://localhost:8000`. */
  backendUrl: string
  /** Defaults to 0: the system picks a free port. */
  port?: number
}

export interface LocalServer {
  /** `http://127.0.0.1:<port>`, the page's origin. */
  origin: string
  close(): Promise<void>
}

const HOST = '127.0.0.1'
const UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])
const SSO_PREFIX = '/v1/auth/sso/'

const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.txt': 'text/plain; charset=utf-8',
  '.wasm': 'application/wasm',
}

/**
 * The headers the page is served with. The script policy is stricter than
 * the `<meta>` policy in `index.html` (which the Vite dev server needs);
 * both apply, so the page gets the intersection.
 */
export const PAGE_HEADERS: Readonly<Record<string, string>> = {
  'Content-Security-Policy':
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; " +
    "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
  'X-Content-Type-Options': 'nosniff',
  'X-Frame-Options': 'DENY',
  'Referrer-Policy': 'no-referrer',
  'Permissions-Policy': 'geolocation=(), microphone=(), camera=()',
  'Cross-Origin-Opener-Policy': 'same-origin',
  'Cross-Origin-Resource-Policy': 'same-origin',
}

// Not forwarded in either direction: hop-by-hop headers (RFC 9110 §7.6.1).
const HOP_BY_HOP = new Set([
  'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'proxy-connection',
  'te', 'trailer', 'transfer-encoding', 'upgrade',
])
// Request headers that describe the local hop, not the page's request to the API.
const REQUEST_DROP = new Set(['host', 'origin', 'referer', 'forwarded'])
// The proxy is same-origin with the page: it never grants another origin a read.
const RESPONSE_DROP = new Set(['strict-transport-security'])

/** `Set-Cookie` without its `Domain`, so the cookie binds to the local origin;
 * every other attribute (`Path`, `HttpOnly`, `Secure`, `SameSite`, `Max-Age`) is kept. */
export function stripCookieDomain(setCookie: string): string {
  return setCookie
    .split(';')
    .filter((part, index) => index === 0 || !/^\s*domain\s*=/i.test(part))
    .join(';')
}

/**
 * A redirect's `Location` as the page should follow it: one on the backend's
 * own origin is moved to the local origin; the SSO routes' redirect to the
 * frontend's `/login/sso` (wherever `SSO_FRONTEND_URL` points) lands on the
 * local origin too. Anything else, such as the identity provider, is unchanged.
 */
export function rewriteLocation(location: string, requestPath: string, backend: URL, localOrigin: string): string {
  let target: URL
  try {
    target = new URL(location, backend)
  } catch {
    return location
  }
  const prefix = backendPrefix(backend)
  if (target.origin === backend.origin && (prefix === '' || target.pathname.startsWith(`${prefix}/`))) {
    return `${localOrigin}${target.pathname.slice(prefix.length)}${target.search}${target.hash}`
  }
  if (requestPath.startsWith(SSO_PREFIX) && target.pathname === '/login/sso') {
    return `${localOrigin}${target.pathname}${target.search}`
  }
  return location
}

function backendPrefix(backend: URL): string {
  return backend.pathname.replace(/\/+$/, '')
}

/** Why a request is refused, or null when it may go on. */
export function refusal(
  req: Pick<http.IncomingMessage, 'method' | 'headers'>,
  localHost: string,
  localOrigin: string,
): string | null {
  if (req.headers.host !== localHost) return 'unexpected Host'
  const origin = req.headers.origin
  if (origin !== undefined && origin !== localOrigin) return 'cross-origin request'
  const site = req.headers['sec-fetch-site']
  if (site !== undefined && site !== 'same-origin' && site !== 'none') return 'cross-site request'
  if (UNSAFE.has((req.method ?? 'GET').toUpperCase()) && origin !== localOrigin) return 'unsafe method without Origin'
  return null
}

function isApiPath(pathname: string): boolean {
  return pathname === '/v1' || pathname.startsWith('/v1/')
}

function send(res: http.ServerResponse, status: number, message: string) {
  res.writeHead(status, { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store', ...PAGE_HEADERS })
  res.end(message)
}

/** The file under *distDir* for a request path, or null when it would leave it. */
export function resolveStatic(distDir: string, pathname: string): string | null {
  let decoded: string
  try {
    decoded = decodeURIComponent(pathname)
  } catch {
    return null
  }
  if (decoded.includes('\0') || decoded.split(/[\\/]/).includes('..')) return null
  const root = path.resolve(distDir)
  const file = path.resolve(root, `.${path.sep}${decoded}`)
  if (file !== root && !file.startsWith(root + path.sep)) return null
  return file
}

function serveFile(res: http.ServerResponse, file: string, method: string, isIndex: boolean) {
  fs.stat(file, (error, stat) => {
    if (error || !stat.isFile()) return send(res, 404, 'Not found')
    const type = MIME[path.extname(file).toLowerCase()] ?? 'application/octet-stream'
    res.writeHead(200, {
      'Content-Type': type,
      'Content-Length': stat.size,
      // Hashed assets never change; the page itself is always revalidated.
      'Cache-Control': isIndex ? 'no-cache' : 'public, max-age=31536000, immutable',
      ...PAGE_HEADERS,
    })
    if (method === 'HEAD') return res.end()
    fs.createReadStream(file).on('error', () => res.destroy()).pipe(res)
  })
}

function serveStatic(distDir: string, req: http.IncomingMessage, res: http.ServerResponse, pathname: string) {
  const method = (req.method ?? 'GET').toUpperCase()
  if (method !== 'GET' && method !== 'HEAD') return send(res, 405, 'Method not allowed')
  const file = resolveStatic(distDir, pathname)
  if (file === null) return send(res, 400, 'Bad path')
  const index = path.join(path.resolve(distDir), 'index.html')
  fs.stat(file, (error, stat) => {
    if (!error && stat.isFile()) return serveFile(res, file, method, file === index)
    // The single-page app's routes (/evidence/1, /login/sso) are index.html;
    // a missing asset (it has an extension) stays a 404.
    if (path.extname(pathname) === '') return serveFile(res, index, method, true)
    send(res, 404, 'Not found')
  })
}

function forwardHeaders(req: http.IncomingMessage, backend: URL): http.OutgoingHttpHeaders {
  const headers: http.OutgoingHttpHeaders = {}
  for (const [name, value] of Object.entries(req.headers)) {
    const key = name.toLowerCase()
    if (value === undefined || HOP_BY_HOP.has(key) || REQUEST_DROP.has(key)) continue
    if (key.startsWith('x-forwarded-') || key.startsWith('sec-fetch-')) continue
    headers[key] = value
  }
  headers.host = backend.host
  return headers
}

function proxy(backend: URL, localOrigin: string, requestPath: string, req: http.IncomingMessage, res: http.ServerResponse) {
  const client = backend.protocol === 'https:' ? https : http
  const upstream = client.request(
    {
      protocol: backend.protocol,
      hostname: backend.hostname,
      port: backend.port || undefined,
      method: req.method,
      path: `${backendPrefix(backend)}${requestPath}`,
      headers: forwardHeaders(req, backend),
    },
    answer => {
      const headers: http.OutgoingHttpHeaders = {}
      for (const [name, value] of Object.entries(answer.headers)) {
        const key = name.toLowerCase()
        if (value === undefined || HOP_BY_HOP.has(key) || RESPONSE_DROP.has(key)) continue
        if (key.startsWith('access-control-')) continue
        if (key === 'set-cookie') headers[key] = (value as string[]).map(stripCookieDomain)
        else if (key === 'location') headers[key] = rewriteLocation(String(value), requestPath, backend, localOrigin)
        else headers[key] = value
      }
      res.writeHead(answer.statusCode ?? 502, answer.statusMessage, headers)
      // Server-Sent Events: send the headers now and every chunk as it comes.
      res.flushHeaders()
      answer.pipe(res)
      answer.on('error', () => res.destroy())
    },
  )
  upstream.on('error', () => {
    if (!res.headersSent) send(res, 502, 'Backend unavailable')
    else res.destroy()
  })
  // The page went away (a closed EventSource): stop the backend's stream too.
  res.on('close', () => {
    if (!res.writableFinished) upstream.destroy()
  })
  req.pipe(upstream)
}

function isLoopback(hostname: string): boolean {
  return hostname === 'localhost' || hostname === '[::1]' || /^127(\.\d{1,3}){3}$/.test(hostname)
}

/**
 * The backend as a URL, or an error: https anywhere, plain http only on this
 * machine, since the session cookies travel on that hop.
 */
export function checkBackendUrl(value: string): URL {
  let backend: URL
  try {
    backend = new URL(value)
  } catch {
    throw new Error(`BACKEND_URL is not a URL: ${value}`)
  }
  if (backend.protocol === 'https:') return backend
  if (backend.protocol === 'http:' && isLoopback(backend.hostname)) return backend
  throw new Error('BACKEND_URL must be https, or http on localhost: the session cookies travel on that connection')
}

/** Start the server on 127.0.0.1; resolves once it listens. */
export function startLocalServer(options: LocalServerOptions): Promise<LocalServer> {
  let backend: URL
  try {
    backend = checkBackendUrl(options.backendUrl)
  } catch (error) {
    return Promise.reject(error)
  }
  let localHost = ''
  let localOrigin = ''

  const server = http.createServer((req, res) => {
    const why = refusal(req, localHost, localOrigin)
    if (why !== null) return send(res, 403, `Forbidden: ${why}`)
    // Only origin-form targets ("/path?query"): never "http://other/…" or "//other/…".
    let target: URL | null = null
    try {
      if ((req.url ?? '').startsWith('/')) target = new URL(req.url ?? '/', localOrigin)
    } catch {
      target = null
    }
    if (target === null || target.origin !== localOrigin) return send(res, 400, 'Bad request')
    if (isApiPath(target.pathname)) return proxy(backend, localOrigin, `${target.pathname}${target.search}`, req, res)
    serveStatic(options.distDir, req, res, target.pathname)
  })
  // The page's EventSource stays open as long as the sprint runs.
  server.requestTimeout = 0
  server.headersTimeout = 60_000

  return new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(options.port ?? 0, HOST, () => {
      const { port } = server.address() as AddressInfo
      localHost = `${HOST}:${port}`
      localOrigin = `http://${localHost}`
      resolve({
        origin: localOrigin,
        close: () =>
          new Promise<void>(done => {
            server.closeAllConnections()
            server.close(() => done())
          }),
      })
    })
  })
}
