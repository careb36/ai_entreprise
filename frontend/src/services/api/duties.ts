import type { Http } from './client'

/** Segregation of duties, the access review and the service accounts (issue #543). */
export type SodMode = 'off' | 'high_risk' | 'all'

export interface SodPolicy {
  company_id: number
  mode: SodMode
  modes: SodMode[]
  high_risk_actions: string[]
  all_actions: string[]
  solo_exception: boolean
  solo_reason: string | null
  solo_granted_by: string | null
  solo_granted_at: string | null
  /** Exactly one person can approve: the exception may apply. */
  solo_eligible: boolean
  solo_in_force: boolean
  approvers: number
  review_interval_days: number
  inactive_days: number
  updated_by: string | null
  updated_at: string | null
}

export interface SodPolicyInput {
  mode?: SodMode
  review_interval_days?: number
  inactive_days?: number
}

export interface SodPersonRef {
  user_id: number | null
  label: string | null
}

export type SodStatus = 'separated' | 'exception' | 'violation' | 'unattributed' | 'not_a_person'

export interface SodApproval {
  action: string
  subject: { kind: string; id: number; label: string }
  approver: SodPersonRef
  requesters: (SodPersonRef & { why: string })[]
  status: SodStatus
  conflicts: string[]
  at: string | null
}

export type SodDutyCounts = Record<'request' | 'plan' | 'deliver' | 'verify' | 'approve' | 'release', number>

export interface SodReport {
  company_id: number
  mode: SodMode
  summary: Record<SodStatus, number>
  approvals: SodApproval[]
  violations: SodApproval[]
  refusals: { audit_log_id: number; at: string | null; by: string | null; action: string | null; conflict: string | null }[]
  matrix: {
    duties: (keyof SodDutyCounts)[]
    members: (SodDutyCounts & {
      user_id: number
      label: string
      effective_role: string | null
      member: boolean
      holds_request_and_approval: boolean
    })[]
    roles: (SodDutyCounts & { role: string })[]
    agents: (SodDutyCounts & { role: string })[]
  }
}

export type ReviewState = 'scheduled' | 'due' | 'missed'

export interface AccessReviewMember {
  kind: 'member'
  id: number
  label: string
  email: string
  own_role: string
  teams: { name: string; role: string }[]
  effective_role: string | null
  sso_linked: boolean
  chat_links: string[]
  last_activity: string | null
  inactive: boolean
}

export interface AccessReviewToken {
  kind: 'token'
  id: number
  label: string
  role: string
  created_by: string | null
  created_at: string | null
  expires_at: string | null
  last_activity: string | null
  inactive: boolean
}

export interface AccessReviewListing {
  company_id: number
  schedule: {
    due_at: string | null
    state: ReviewState
    grace_days: number
    interval_days: number
    inactive_days: number
    last_completed_at: string | null
    last_on_time: boolean | null
  }
  members: AccessReviewMember[]
  tokens: AccessReviewToken[]
  history: {
    id: number
    due_at: string | null
    completed_at: string
    completed_by: string
    on_time: boolean
    summary: Record<string, number>
    note: string | null
  }[]
}

export interface AccessDecision {
  kind: 'member' | 'token'
  id: number
  decision: 'confirm' | 'remove'
}

export interface ServiceAccount {
  kind: string
  name: string | null
  owner?: string | null
  scope: string
  created_at?: string | null
  age_days?: number | null
  status?: string
  method?: string
  present?: boolean
}

export interface ServiceAccountsInventory {
  company_id: number
  generated_at: string
  company: ServiceAccount[]
  deployment: ServiceAccount[]
}

// The policy, the exception, the review and the inventory are a signed-in
// Owner's (never an API token's); the report is a Manager's. All audited.
export const dutiesEndpoints = (http: Http) => ({
  getSodPolicy: (companyId: number) => http.get<SodPolicy>(`/v1/companies/${companyId}/sod/policy`),

  updateSodPolicy: (companyId: number, data: SodPolicyInput) =>
    http.put<SodPolicy>(`/v1/companies/${companyId}/sod/policy`, data),

  grantSodException: (companyId: number, reason: string) =>
    http.post<SodPolicy>(`/v1/companies/${companyId}/sod/exception`, { reason }),

  withdrawSodException: (companyId: number) =>
    http.delete<SodPolicy>(`/v1/companies/${companyId}/sod/exception`),

  getSodReport: (companyId: number) => http.get<SodReport>(`/v1/companies/${companyId}/sod/report`),

  getAccessReview: (companyId: number) =>
    http.get<AccessReviewListing>(`/v1/companies/${companyId}/access-review`),

  completeAccessReview: (companyId: number, decisions: AccessDecision[], note: string | null) =>
    http.post<{ id: number; on_time: boolean }>(`/v1/companies/${companyId}/access-review`, { decisions, note }),

  getServiceAccounts: (companyId: number) =>
    http.get<ServiceAccountsInventory>(`/v1/companies/${companyId}/service-accounts`),
})
