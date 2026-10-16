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

export interface InitCompanyRequest {
  company_name: string
  description?: string
  default_scope_size?: 'S' | 'M' | 'L'
  default_sprint_length_days?: number
  autonomy_level?: number
  timezone?: string
  agents?: DiscoverTeamAgent[]
  permissions?: Record<string, string>
  governance?: Record<string, boolean>
  project_path?: string
  detected_stack?: DetectedStack
}

export interface InitCompanyResponse {
  company_id: number
  sprint_id: number
  task_count: number
  message: string
}

export interface DetectedStack {
  name: string
  language: string
  all_languages?: Record<string, number>
  frameworks: string[]
  databases: string[]
  infra: string[]
  complexity: string
  file_count: number
  team_source?: 'ai' | 'rules'
}

export interface DiscoverTeamAgent {
  name?: string
  role: string
  seniority: string
  specialty: string
  persona?: string
  reasoning?: string
  model?: string
  system_prompt?: string
  token_budget?: number
}

export interface DiscoverTeamResponse {
  discovery_id: number | null
  suggested_team: DiscoverTeamAgent[]
  detected_stack: DetectedStack
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

export interface PlannedSprintTask {
  title: string
  description: string
  priority: 'low' | 'medium' | 'high' | 'critical'
  assignee: string
}

export interface SprintPlanResponse {
  sprint_name: string
  goal: string
  scope_size: 'S' | 'M' | 'L'
  tasks: PlannedSprintTask[]
}

// ── Orchestration Health types ────────────────────────────────────────────────

export interface OrchestrationHealthWeek {
  week: string
  qa_needs_work_rate: number
  avg_developer_tokens: number
  architect_consult_count: number
  task_count: number
  post_batch1: boolean
}

export interface OrchestrationHealthSummary {
  qa_needs_work_rate: number
  avg_developer_tokens: number
}

export interface OrchestrationHealthResponse {
  anchor_commit: string
  anchor_date: string
  weeks: OrchestrationHealthWeek[]
  summary: {
    pre_batch1: OrchestrationHealthSummary
    post_batch1: OrchestrationHealthSummary
  }
}

export type AgentRunStatus = 'ingested' | 'verified' | 'refuted' | 'unverifiable'

export interface AgentRunApproval {
  id: number
  company_id: number
  agent_run_id: number
  approver_name: string
  decision: 'approved' | 'rejected'
  feedback: string | null
  rejection_category: 'scope' | 'quality' | 'security' | 'other' | null
  decided_at: string
  audit_log_id: number | null
}

export interface AgentRunListItem {
  id: number
  company_id: number
  external_ref: string
  version: number
  producer: string
  model: string | null
  agent_role: string | null
  subject: Record<string, unknown> | null
  claims: Record<string, unknown> | null
  artifact_digest: string
  artifact_size: number
  status: AgentRunStatus
  started_at: string | null
  finished_at: string | null
  ingested_at: string
  audit_log_id: number | null
  latest_approval: Pick<AgentRunApproval, 'decision' | 'feedback' | 'approver_name' | 'decided_at'> | null
}

export interface AgentRun extends Omit<AgentRunListItem, 'latest_approval'> {
  artifact_text: string | null
}

export interface VerificationCheck {
  name: string
  outcome: 'pass' | 'refuted' | 'unverifiable'
  observation: string
}

export interface AgentRunVerification {
  id: number
  verdict: Exclude<AgentRunStatus, 'ingested'>
  checks: VerificationCheck[] | null
  evidence: Record<string, unknown> | null
  observed_at: string
  audit_log_id: number | null
}

export interface AuditVerification {
  interaction_id: string
  rows: { id: number; action: string; status: 'valid' | 'invalid' | 'unsigned' | 'legacy'; mode: string }[]
  summary: { valid: number; invalid: number; unsigned: number; legacy: number }
}

export interface AgentRunApprovalRequest {
  company_id: number
  approver_name: string
  decision: 'approved' | 'rejected'
  feedback?: string
  rejection_category?: 'scope' | 'quality' | 'security' | 'other'
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
  
  initCompany: (data: InitCompanyRequest) =>
    api.post<InitCompanyResponse>('/v1/init_company', data),

  discoverTeam: (companyId: number | null, projectPath: string) =>
    api.post<DiscoverTeamResponse>('/v1/discover_team', { company_id: companyId, project_path: projectPath }),

  getAgentRuns: (companyId: number, filters: { producer?: string; status?: AgentRunStatus; limit?: number } = {}) =>
    api.get<{ agent_runs: AgentRunListItem[] }>('/v1/agent_runs', { company_id: companyId, ...filters }),

  getAgentRun: (runId: number, companyId: number) =>
    api.get<AgentRun>(`/v1/agent_runs/${runId}`, { company_id: companyId }),

  getAgentRunVerifications: (runId: number, companyId: number) =>
    api.get<{ verifications: AgentRunVerification[] }>(`/v1/agent_runs/${runId}/verifications`, { company_id: companyId }),

  verifyAgentRun: (runId: number, companyId: number) =>
    api.post<AgentRunVerification>(`/v1/agent_runs/${runId}/verify`, { company_id: companyId }),

  getAgentRunApprovals: (runId: number, companyId: number) =>
    api.get<{ approvals: AgentRunApproval[] }>(`/v1/agent_runs/${runId}/approvals`, { company_id: companyId }),

  approveAgentRun: (runId: number, request: AgentRunApprovalRequest) =>
    api.post<AgentRunApproval>(`/v1/agent_runs/${runId}/approve`, request),

  getAuditVerification: (externalRef: string) =>
    api.get<AuditVerification>('/v1/audit/verify', { interaction_id: `agent-run:${externalRef}` }),

  getQuotas: (companyId: number) =>
    api.get<any[]>('/v1/enterprise/quotas', { company_id: companyId }),

  resetQuota: (buId: number) =>
    api.post<any>(`/v1/enterprise/quotas/${buId}/reset`, {}),

  getAnalyticsSummary: (sprintId: number) =>
    api.get<any>('/v1/analytics/summary', { sprint_id: sprintId }),

  getOrchestrationHealth: (companyId: number, weeks = 8) =>
    api.get<OrchestrationHealthResponse>('/v1/analytics/orchestration_health', { company_id: companyId, weeks }),

  // Sprints & Backlog
  getSprintBoard: (sprintId: number) =>
    api.get<SprintBoard>(`/v1/sprints/${sprintId}/board`),

  updateBacklogItem: (itemId: number, data: {
    status?: string
    priority?: string
    assignee?: string
    comment?: string
  }) => api.patch<BacklogItem>(`/v1/backlog/${itemId}`, data),

  startSprint: (data: { company_id: number; sprint_name: string; start_date: string; scope_size: string; goal?: string; backlog_items: any[] }) =>
    api.post<any>('/v1/start_sprint', data),

  planSprint: (data: { company_id: number; prompt: string; scope_size: 'S' | 'M' | 'L' }) =>
    api.post<SprintPlanResponse>('/v1/plan_sprint', data),

  planSelfImprovementSprint: (data: { company_id: number; scope_size: 'S' | 'M' | 'L' }) =>
    api.post<SprintPlanResponse>('/v1/plan_self_improvement_sprint', data),

  // Agents & Memory
  generate: (data: { company_id: number; story: string; constraints?: string; interaction_id?: string }) =>
    api.post<any>('/v1/generate', data),

  searchMemory: (query: string, companyId?: number) =>
    api.get<any[]>('/v1/memory/search', { q: query, ...(companyId && { company_id: companyId }) }),

  // Support
  getTickets: () => api.get<any[]>('/v1/tickets'),
  reportBug: (data: any) => api.post<any>('/v1/report_bug', data),
}
