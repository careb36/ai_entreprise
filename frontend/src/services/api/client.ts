import { useAuthStore } from '../../store/useAuthStore'

export const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000'
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
    401: 'Session expired. Please refresh the page.',
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
  auth?: boolean
  params?: Record<string, string | number>
  /** Retry 5xx responses (default true). A 503 that means "not connected" should not. */
  retryServerErrors?: boolean
  once?: boolean; timeoutMs?: number; _attempt?: number // once: never resent (not idempotent)
}

let onUnauthorized: (() => void) | null = null

export function setOnUnauthorized(handler: (() => void) | null) {
  onUnauthorized = handler
}

export async function fetchApi<T>(endpoint: string, options: FetchOptions = {}): Promise<T> {
  const { params, _attempt = 0, auth = true, once = false, timeoutMs = TIMEOUT_MS, ...rest } = options
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
    const token = auth ? useAuthStore.getState().getValidToken() : null
    const headers = new Headers(fetchOptions.headers)
    if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
    if (token) headers.set('Authorization', ['Bearer', token].join(' '))

    const response = await fetch(url, {
      ...fetchOptions,
      signal: controller.signal,
      headers,
    })

    clearTimeout(timer)

    if (!response.ok) {
      if (
        response.status === 401 &&
        endpoint.split('?')[0] !== '/v1/auth/login' &&
        token &&
        useAuthStore.getState().token === token
      ) {
        useAuthStore.getState().logout()
        onUnauthorized?.()
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

/** GET a file with the user's token; the caller saves it. */
export async function fetchBlob(endpoint: string): Promise<{ blob: Blob; filename: string | null }> {
  const token = useAuthStore.getState().getValidToken()
  const headers = new Headers()
  if (token) headers.set('Authorization', ['Bearer', token].join(' '))
  const response = await fetch(`${API_BASE}${endpoint}`, { headers })
  if (!response.ok) {
    throw new ApiError(response.status, friendlyMessage(response.status), ...(await readDetail(response)))
  }
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const match = /filename="?([^";]+)"?/.exec(disposition)
  return { blob: await response.blob(), filename: match ? match[1] : null }
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

  delete: <T>(endpoint: string) =>
    fetchApi<T>(endpoint, { method: 'DELETE' }),
  }
}

export type Http = ReturnType<typeof createHttp>
