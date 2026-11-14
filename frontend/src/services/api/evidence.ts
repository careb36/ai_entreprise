import type { Http } from './client'

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
  /** The Reviewer agent's suggestion: a claim beside the approval, never one. `reviewed_at` with no decision: its answer was unusable. */
  suggested_decision?: 'approved' | 'rejected' | null
  suggested_category?: 'scope' | 'quality' | 'security' | 'other' | null
  suggested_reason?: string | null
  reviewed_by?: string | null
  reviewed_at?: string | null
}

export interface AgentRunFilters {
  producer?: string
  status?: AgentRunStatus
  limit?: number
  /** Exact match: every version of one piece of work, newest first. */
  external_ref?: string
  /** Runs whose `subject.sprint_id` is this sprint. */
  sprint_id?: number
  /** Leave out the company's own task worker and kickoff runs (the review loop decides them). */
  exclude_in_house?: boolean
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
  rows: { id: number; action: string; status: 'valid' | 'invalid' | 'unsigned' | 'legacy'; mode: string; company_id?: number | null; legacy?: boolean }[]
  summary: { valid: number; invalid: number; unsigned: number; legacy: number }
}

export interface AgentRunApprovalRequest {
  company_id: number
  approver_name: string
  decision: 'approved' | 'rejected'
  feedback?: string
  rejection_category?: 'scope' | 'quality' | 'security' | 'other'
}

/** How the Reviewer agent's suggestions compared with the decisions people recorded on a company's runs. */
export interface ReviewerRecord {
  decided: number
  agent_agreed: number
  /** Decided runs the agent gave no usable answer for (misses too). */
  agent_unusable: number
  /** Decided runs the Verifier's verdict alone called (verified or refuted): its baseline. */
  verifier_called: number
  verifier_agreed: number
}

export const evidenceEndpoints = (api: Http) => ({
  getAgentRuns: (companyId: number, filters: AgentRunFilters = {}) => {
    // Omit unset filters rather than sending them as the string "undefined".
    const params: Record<string, string | number> = { company_id: companyId }
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined) params[key] = typeof value === 'boolean' ? String(value) : value
    }
    return api.get<{ agent_runs: AgentRunListItem[] }>('/v1/agent_runs', params)
  },

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

  getAuditVerification: (externalRef: string, companyId: number) =>
    api.get<AuditVerification>('/v1/audit/verify', {
      interaction_id: `agent-run:${externalRef}`,
      company_id: companyId,
    }),

  /** The Reviewer agent's record on a company's runs (Viewer). */
  getReviewerRecord: (companyId: number) =>
    api.get<ReviewerRecord>(`/v1/agent_runs/reviewer_record?company_id=${companyId}`),
})
