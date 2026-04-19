const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000'
const TIMEOUT_MS = 90_000
const MAX_RETRIES = 2

// ── Error types ───────────────────────────────────────────────────────────────

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }
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

// ── Interfaces ───────────────────────────────────────────────────────────────

export interface CompanyInfo {
  id: number
  name: string
  autonomy_level: number
  token_quota: number
  token_used: number
}

export interface BacklogItem {
  id: number
  sprint_id: number
  title: string
  description?: string
  status: 'todo' | 'in_progress' | 'in_review' | 'done' | 'blocked'
  priority: 'low' | 'medium' | 'high' | 'critical'
  assignee?: string
  comments: { author: string; text: string; created_at: string }[]
  created_at: string
  updated_at: string
}

export interface SprintBoard {
  sprint: {
    id: number
    sprint_name: string
    status: string
    goal?: string
    start_date: string
    scope_size: string
    item_limit: number
  }
  columns: Record<string, BacklogItem[]>
  total: number
}

// ── Core fetch ───────────────────────────────────────────────────────────────

interface FetchOptions extends RequestInit {
  params?: Record<string, string | number>
  _attempt?: number
}

async function fetchApi<T>(endpoint: string, options: FetchOptions = {}): Promise<T> {
  const { params, _attempt = 0, ...fetchOptions } = options

  let url = `${API_BASE}${endpoint}`
  if (params) {
    const searchParams = new URLSearchParams(
      Object.fromEntries(Object.entries(params).map(([k, v]) => [k, String(v)]))
    )
    url += `?${searchParams.toString()}`
  }

  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS)

  try {
    const response = await fetch(url, {
      ...fetchOptions,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...fetchOptions.headers,
      },
    })

    clearTimeout(timer)

    if (!response.ok) {
      if (response.status >= 500 && _attempt < MAX_RETRIES) {
        await delay(600 * (_attempt + 1))
        return fetchApi<T>(endpoint, { ...options, _attempt: _attempt + 1 })
      }
      throw new ApiError(response.status, friendlyMessage(response.status))
    }

    if (response.status === 204) return undefined as unknown as T
    return response.json() as Promise<T>

  } catch (err) {
    clearTimeout(timer)
    if (err instanceof ApiError) throw err
    if ((err as Error).name === 'AbortError') {
      if (_attempt < MAX_RETRIES) return fetchApi<T>(endpoint, { ...options, _attempt: _attempt + 1 })
      throw new ApiError(0, 'Request timed out. Please check your connection.')
    }
    if (_attempt < MAX_RETRIES) {
      await delay(600 * (_attempt + 1))
      return fetchApi<T>(endpoint, { ...options, _attempt: _attempt + 1 })
    }
    throw new ApiError(0, 'Network error. Please check your connection.')
  }
}

function delay(ms: number) {
  return new Promise(resolve => setTimeout(resolve, ms))
}

// ── Public API client ─────────────────────────────────────────────────────────

export const api = {
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

  // ── Domain Endpoints ───────────────────────────────────────────────────────

  // Core & Enterprise
  health: () => api.get<{ status: string; timestamp: string }>('/health'),
  
  initCompany: (data: { company_name: string; default_sprint_length_days?: number; autonomy_level?: number }) =>
    api.post<CompanyInfo>('/v1/init_company', data),

  discoverTeam: (companyId: number | null, projectPath: string) =>
    api.post<any>('/v1/discover_team', { company_id: companyId, project_path: projectPath }),

  approveTask: (taskId: number, approverName: string, decision: 'approved' | 'rejected', feedback?: string) =>
    api.post<any>('/v1/approve_task', { task_id: taskId, approver_name: approverName, decision, feedback }),

  getQuotas: (companyId: number) =>
    api.get<any[]>('/v1/enterprise/quotas', { company_id: companyId }),

  getAnalyticsSummary: (sprintId: number) =>
    api.get<any>('/v1/analytics/summary', { sprint_id: sprintId }),

  // Sprints & Backlog
  getSprintBoard: (sprintId: number) =>
    api.get<SprintBoard>(`/v1/sprints/${sprintId}/board`),

  updateBacklogItem: (itemId: number, data: { status?: string; priority?: string; assignee?: string; comment?: string }) =>
    api.patch<BacklogItem>(`/v1/backlog/${itemId}`, data),

  startSprint: (data: { company_id: number; sprint_name: string; start_date: string; scope_size: string; backlog_items: any[] }) =>
    api.post<any>('/v1/start_sprint', data),

  // Agents & Memory
  generate: (data: { company_id: number; story: string; constraints?: string; interaction_id?: string }) =>
    api.post<any>('/v1/generate', data),

  searchMemory: (query: string, companyId?: number) =>
    api.get<any[]>('/v1/memory/search', { q: query, ...(companyId && { company_id: companyId }) }),

  // Support
  getTickets: () => api.get<any[]>('/v1/tickets'),
  reportBug: (data: any) => api.post<any>('/v1/report_bug', data),
}

