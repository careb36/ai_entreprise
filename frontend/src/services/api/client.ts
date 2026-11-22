// The packaged desktop app serves the page and forwards /v1 from one local
// origin (frontend/electron/local-server.ts), so its build calls the API on
// the page's own origin; `vite build --mode electron` sets the flag.
export const API_BASE = import.meta.env.VITE_API_SAME_ORIGIN === 'true' ? '' : import.meta.env.VITE_API_URL || 'http://localhost:8000'
const TIMEOUT_MS = 90_000
const MAX_RETRIES = 2

// ── Error types ───────────────────────────────────────────────────────────────

export class ApiError extends Error {
  /** The server's own `detail` message, when the error body carried one. */
  public detail?: string

  constructor(public status: number, message: string, detail?: string, public data?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.detail = detail
  }
}

/** The server's `detail` when present, else the generic message. */
export function errorDetail(error: unknown): string {
  if (error instanceof ApiError) return error.detail || error.message
  return error instanceof Error ? error.message : String(error)
}

export function isClientError(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status >= 400 && error.status < 500
}

function friendlyMessage(status: number): string {
  const map: Record<number, string> = {
    400: 'Invalid request. Please check the form and try again.',
    401: 'Session expired. Please sign in again.',
    403: 'You do not have permission for this action.',
    404: 'Resource not found.',
    422: 'Invalid data submitted. Please review your inputs.',
    429: 'Too many requests. Please wait a moment and retry.',
    500: 'Server error. Our team has been notified.',
    502: 'Service temporarily unavailable.',
    503: 'Service is starting up. Try again in a few seconds.',
  }
  return map[status] ?? `Unexpected error (${status}). Please try again.`
}

// ── Core fetch ───────────────────────────────────────────────────────────────

interface FetchOptions extends RequestInit {
  /** Part of the signed-in session: a 401 tries one refresh (default true). */
  auth?: boolean
  params?: Record<string, string | number>
  /** Retry 5xx responses (default true). A 503 that means "not connected" should not. */
  retryServerErrors?: boolean
  once?: boolean; timeoutMs?: number; _attempt?: number // once: never resent (not idempotent)
  _refreshed?: boolean; _csrfReloaded?: boolean
}

let onUnauthorized: (() => void) | null = null

export function setOnUnauthorized(handler: (() => void) | null) {
  onUnauthorized = handler
}

// ── Browser session (issue #524, docs/archive/design/SESSIONS.md) ───────────────────
// The access and refresh tokens are HttpOnly cookies the backend sets: this
// module never sees them. It sends cookies (`credentials: 'include'`), the
// CSRF header on unsafe methods, and on a 401 tries one refresh.

/** What the auth store tells the client; the client imports no store. */
export interface SessionHooks {
  /** Changes on every sign-in and sign-out; null while signed out. */
  current: () => number | null
  /** The refresh was refused: end session *epoch* if it is still the current
   * one, and say whether it was. */
  expired: (epoch: number) => boolean
}

let sessionHooks: SessionHooks | null = null

export function setSessionHooks(hooks: SessionHooks | null) {
  sessionHooks = hooks
}

export const CSRF_COOKIE = 'fabrique_csrf'
export const CSRF_HEADER = 'X-Fabrique-CSRF'
const UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])
/** Routes whose 401 means "wrong credentials", never "refresh and retry". */
const NO_REFRESH = new Set(['/v1/auth/login', '/v1/auth/refresh', '/v1/auth/logout', '/v1/auth/sso/exchange'])

// The CSRF value is not a credential (it is useless without the HttpOnly
// cookie); it is kept in memory only, for a page that cannot read the API's
// cookie because it is served from another host.
let csrfMemory: string | null = null

export function setCsrfToken(token: string | null) {
  csrfMemory = token
}

function readCookie(name: string): string | null {
  if (typeof document === 'undefined') return null
  for (const part of document.cookie.split(';')) {
    const [key, ...rest] = part.trim().split('=')
    if (key === name) return decodeURIComponent(rest.join('='))
  }
  return null
}

function csrfToken(): string | null {
  return readCookie(CSRF_COOKIE) || csrfMemory
}

/**
 * A 403 that says the CSRF header was missing (the page could not read the
 * API host's cookie and lost its memory on reload): ask the API for the value
 * once. True when there is one to resend with; nothing was done the first time.
 */
async function reloadCsrf(response: Response): Promise<boolean> {
  if (response.status !== 403 || response.headers?.get(CSRF_HEADER) !== 'required') return false
  try {
    const answer = await fetch(`${API_BASE}/v1/auth/csrf`, { credentials: 'include' })
    csrfMemory = answer.ok ? ((await answer.json())?.csrf_token ?? null) : null
  } catch {
    csrfMemory = null
  }
  return csrfMemory !== null
}

type RefreshOutcome = 'refreshed' | 'raced' | 'expired'
let refreshing: Promise<RefreshOutcome> | null = null

/** One refresh at a time per tab; a 409 means another tab just did it. */
function refreshSession(): Promise<RefreshOutcome> {
  refreshing ??= (async (): Promise<RefreshOutcome> => {
    try {
      const send = () => {
        const headers = new Headers()
        const csrf = csrfToken()
        if (csrf) headers.set(CSRF_HEADER, csrf)
        return fetch(`${API_BASE}/v1/auth/refresh`, { method: 'POST', credentials: 'include', headers })
      }
      let response = await send()
      if (await reloadCsrf(response)) response = await send()
      if (response.ok) return 'refreshed'
      return response.status === 409 ? 'raced' : 'expired'
    } catch {
      return 'expired'
    } finally {
      refreshing = null
    }
  })()
  return refreshing
}

/**
 * After a 401 on a request sent with session *epoch*: whether to resend it.
 * A refresh that fails, or a resent request refused again, ends the session.
 */
async function recoverFrom401(endpoint: string, epoch: number | null, retried: boolean): Promise<boolean> {
  if (epoch === null || NO_REFRESH.has(endpoint.split('?')[0])) return false
  if (sessionHooks?.current() !== epoch) return false // a newer session: not ours to end
  if (!retried && (await refreshSession()) !== 'expired') return true
  if (sessionHooks?.expired(epoch)) onUnauthorized?.()
  return false
}

export async function fetchApi<T>(endpoint: string, options: FetchOptions = {}): Promise<T> {
  const { params, _attempt = 0, _refreshed = false, _csrfReloaded = false, auth = true, once = false, timeoutMs = TIMEOUT_MS, ...rest } = options
  const { retryServerErrors = !once, ...fetchOptions } = rest

  let url = `${API_BASE}${endpoint}`
  if (params) {
    const searchParams = new URLSearchParams(
      Object.fromEntries(Object.entries(params).map(([k, v]) => [k, String(v)]))
    )
    url += `?${searchParams.toString()}`
  }

  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)

  try {
    const epoch = auth ? (sessionHooks?.current() ?? null) : null
    const headers = new Headers(fetchOptions.headers)
    if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
    if (UNSAFE.has((fetchOptions.method ?? 'GET').toUpperCase())) {
      const csrf = csrfToken()
      if (csrf) headers.set(CSRF_HEADER, csrf)
    }

    const response = await fetch(url, {
      ...fetchOptions,
      credentials: 'include',
      signal: controller.signal,
      headers,
    })

    clearTimeout(timer)

    if (!response.ok) {
      if (response.status === 401 && (await recoverFrom401(endpoint, epoch, _refreshed))) {
        return fetchApi<T>(endpoint, { ...options, _refreshed: true })
      }
      if (!_csrfReloaded && (await reloadCsrf(response))) {
        return fetchApi<T>(endpoint, { ...options, _csrfReloaded: true })
      }
      if (response.status >= 500 && retryServerErrors && _attempt < MAX_RETRIES) {
        await delay(600 * (_attempt + 1))
        return fetchApi<T>(endpoint, { ...options, _attempt: _attempt + 1 })
      }
      throw new ApiError(response.status, friendlyMessage(response.status), ...(await readDetail(response)))
    }

    if (response.status === 204) return undefined as unknown as T
    return response.json() as Promise<T>

  } catch (err) {
    clearTimeout(timer)
    if (err instanceof ApiError) throw err
    if ((err as Error).name === 'AbortError') {
      if (!once && _attempt < MAX_RETRIES) return fetchApi<T>(endpoint, { ...options, _attempt: _attempt + 1 })
      throw new ApiError(0, 'Request timed out. Please check your connection.')
    }
    if (!once && _attempt < MAX_RETRIES) {
      await delay(600 * (_attempt + 1))
      return fetchApi<T>(endpoint, { ...options, _attempt: _attempt + 1 })
    }
    throw new ApiError(0, 'Network error. Please check your connection.')
  }
}

/** A GET with the session's cookies, refreshed once on a 401. */
async function getWithSession(endpoint: string): Promise<Response> {
  const epoch = sessionHooks?.current() ?? null
  const send = () => fetch(`${API_BASE}${endpoint}`, { credentials: 'include' })
  const response = await send()
  if (response.status === 401 && (await recoverFrom401(endpoint, epoch, false))) return send()
  return response
}

/** GET a file with the user's session; the caller saves it. */
export async function fetchBlob(endpoint: string): Promise<{ blob: Blob; filename: string | null }> {
  const response = await getWithSession(endpoint)
  if (!response.ok) {
    throw new ApiError(response.status, friendlyMessage(response.status), ...(await readDetail(response)))
  }
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const match = /filename="?([^";]+)"?/.exec(disposition)
  return { blob: await response.blob(), filename: match ? match[1] : null }
}

/**
 * GET a body with its response headers and the user's session (a run's full
 * log, whose `X-Log-*` headers say what the backend observed). A refusal is an
 * `ApiError` whose `data` is the body's `detail` as sent.
 */
export async function fetchWithHeaders(endpoint: string): Promise<{ blob: Blob; headers: Headers }> {
  const response = await getWithSession(endpoint)
  if (!response.ok) {
    throw new ApiError(response.status, friendlyMessage(response.status), ...(await readDetail(response)))
  }
  return { blob: await response.blob(), headers: response.headers }
}

/**
 * GET a text body with the user's session (a Markdown document). A refusal is
 * an `ApiError` whose message is the body's `detail` when it has one.
 */
export async function fetchText(endpoint: string): Promise<string> {
  const response = await getWithSession(endpoint)
  if (!response.ok) {
    let detail: string | undefined
    try {
      const body = await response.json()
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      detail = undefined
    }
    throw new ApiError(response.status, detail ?? `HTTP ${response.status}`, detail)
  }
  return response.text()
}

/** The body's `detail` as a message and as sent (an object carries more, e.g. a 409's findings). */
async function readDetail(response: Response): Promise<[string | undefined, unknown]> {
  try {
    const detail = (await response.json())?.detail
    if (typeof detail === 'string' && detail) return [detail, detail]
    // Some endpoints answer with {message, ...} (e.g. GitHub rate limits).
    if (detail && typeof detail === 'object' && typeof detail.message === 'string') return [detail.message, detail]
    return [undefined, detail]
  } catch {
    return [undefined, undefined]
  }
}

function delay(ms: number) {
  return new Promise(resolve => setTimeout(resolve, ms))
}

// ── Base methods ──────────────────────────────────────────────────────────────

/** The generic verbs every domain's endpoints call through (`api.get`, `api.post`, ...). */
export function createHttp() {
  return {
  get: <T>(endpoint: string, params?: Record<string, string | number>) =>
    fetchApi<T>(endpoint, { method: 'GET', params }),

  post: <T>(endpoint: string, data?: unknown) =>
    fetchApi<T>(endpoint, { method: 'POST', body: data ? JSON.stringify(data) : undefined }),

  put: <T>(endpoint: string, data: unknown) =>
    fetchApi<T>(endpoint, { method: 'PUT', body: JSON.stringify(data) }),

  patch: <T>(endpoint: string, data: unknown) =>
    fetchApi<T>(endpoint, { method: 'PATCH', body: JSON.stringify(data) }),

  delete: <T>(endpoint: string, data?: unknown) =>
    fetchApi<T>(endpoint, { method: 'DELETE', body: data ? JSON.stringify(data) : undefined }),
  }
}

export type Http = ReturnType<typeof createHttp>
