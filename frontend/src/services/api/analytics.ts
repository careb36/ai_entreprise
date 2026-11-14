import type { Http } from './client'

// ── Orchestration Health types ────────────────────────────────────────────────

export interface OrchestrationHealthWeek {
  week: string
  qa_needs_work_rate: number
  avg_developer_tokens: number
  architect_consult_count: number
  task_count: number
  post_batch1: boolean
  /** Developer turns whose token usage was not reported: left out of the average, not counted as zero. */
  developer_turns_without_tokens?: number
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

export const analyticsEndpoints = (api: Http) => ({
  getQuotas: (companyId: number) =>
    api.get<any[]>('/v1/enterprise/quotas', { company_id: companyId }),
  getQuotaUsageHistory: (companyId: number, days = 7) =>
    api.get<{ days: { date: string; tokens: number }[]; total: number }>('/v1/enterprise/quota_usage_history', { company_id: companyId, days }),

  resetQuota: (buId: number) =>
    api.post<any>(`/v1/enterprise/quotas/${buId}/reset`, {}),

  getAnalyticsSummary: (sprintId: number) =>
    api.get<any>('/v1/analytics/summary', { sprint_id: sprintId }),

  getOrchestrationHealth: (companyId: number, weeks = 8) =>
    api.get<OrchestrationHealthResponse>('/v1/analytics/orchestration_health', { company_id: companyId, weeks }),
})
