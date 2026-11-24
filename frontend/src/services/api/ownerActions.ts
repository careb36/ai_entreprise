import type { Http } from './client'

/**
 * Owner actions (issue #701, `docs/design/owner-actions.md`): steps only the
 * company's Owner takes, run by the platform from a catalogue, after a
 * confirmation that shows their cost and effect. A run's `observations` are
 * what the platform created or measured (ids its API returned, the daemon's
 * replies, its clock), never a producer's report.
 */
export type OwnerActionStatus = 'running' | 'succeeded' | 'failed' | 'cancelled'

export interface OwnerActionSandbox {
  asked: string | null
  observed: string
  observed_by: string | null
  container_mode: boolean
  modes: Record<string, number>
  images: string[]
  note?: string
}

export interface OwnerActionObservations {
  checkout?: { path: string; created: boolean; head: string; source_head: string; files: number | null } | null
  company_id?: number | null
  sprint_id?: number | null
  specification_id?: number | null
  architecture_id?: number | null
  issue_specification_id?: number | null
  issue_architecture_id?: number | null
  agent_run_ids?: number[]
  sandbox?: OwnerActionSandbox | null
  tokens_observed?: number
  exit_status?: number | null
  steps_done?: string[]
  started_at?: string
  finished_at?: string
  [key: string]: unknown
}

export interface OwnerActionRun {
  id: number
  company_id: number
  action: string
  status: OwnerActionStatus
  params: Record<string, unknown>
  started_by_user_id: number
  started_at: string | null
  finished_at: string | null
  cancel_requested_at: string | null
  exit_status: number | null
  reason: string | null
  observations: OwnerActionObservations
}

export interface OwnerActionParam {
  type?: string
  default?: unknown
  minimum?: number
  maximum?: number
  enum?: string[]
  pattern?: string
}

export interface OwnerAction {
  name: string
  issue: number
  params: Record<string, OwnerActionParam>
  required: string[]
  records: string[]
  available: boolean
  reason: string | null
  running_run_id: number | null
  last_runs: OwnerActionRun[]
}

export interface OwnerActionEffect { key: string; values: Record<string, string | number> }

export interface OwnerActionCommit { sha: string; subject: string; on_origin?: boolean }

export interface OwnerActionPreview {
  action: string
  params: Record<string, unknown>
  effect: OwnerActionEffect[]
  cost: { models: string[]; token_budget: number; max_seconds: number; budget_checked_between_calls?: boolean }
  /** What the platform observed before this confirmation (the daemon, GitHub); the digest covers it. */
  facts?: Record<string, unknown>
  /** Why it cannot start now (not covered by the digest). */
  blockers?: string[]
  /** A start needs a fresh re-authentication (a protected land). */
  step_up?: boolean
  /** The digest a start must echo: what the dialog showed is what runs. */
  confirmation: string
}

/** What the Owner picks from: the daemon's local branches, or the open issues. */
export interface OwnerActionOptions {
  main?: string | null
  branches?: { name: string; head: string; subject: string }[]
  repository?: string
  issues?: { number: number; title: string; url: string | null }[]
}

// The Owner's only: a Manager or an API token gets 403.
export const ownerActionEndpoints = (http: Http) => ({
  getOwnerActions: (companyId: number) =>
    http.get<{ actions: OwnerAction[] }>('/v1/owner_actions', { company_id: companyId }),
  previewOwnerAction: (companyId: number, name: string, params: Record<string, unknown>) =>
    http.post<OwnerActionPreview>(`/v1/owner_actions/${encodeURIComponent(name)}/preview`, { company_id: companyId, params }),
  startOwnerAction: (companyId: number, name: string, params: Record<string, unknown>, confirmation: string, stepUpPassword?: string) =>
    http.post<OwnerActionRun>(`/v1/owner_actions/${encodeURIComponent(name)}/runs`, {
      company_id: companyId, params, confirmation, ...(stepUpPassword ? { step_up_password: stepUpPassword } : {}),
    }),
  getOwnerActionOptions: (companyId: number, name: string) =>
    http.get<OwnerActionOptions>(`/v1/owner_actions/${encodeURIComponent(name)}/options`, { company_id: companyId }),
  getOwnerActionRun: (companyId: number, name: string, runId: number) =>
    http.get<OwnerActionRun>(`/v1/owner_actions/${encodeURIComponent(name)}/runs/${runId}`, { company_id: companyId }),
  cancelOwnerActionRun: (companyId: number, name: string, runId: number) =>
    http.post<OwnerActionRun>(`/v1/owner_actions/${encodeURIComponent(name)}/runs/${runId}/cancel`, { company_id: companyId }),
})
