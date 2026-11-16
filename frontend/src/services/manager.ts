/**
 * The Manager agent (issue #331): what it suggested and decided alone under
 * the company's policy, the client's one-click override, and the brief.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type Inbox, type InboxBlockedTask } from './api'

/** One inbox item the Manager agent looked at. A suggestion is a claim. */
export interface ManagerDecision {
  id: number
  /** task, procedure, run, task_branch, integration, publication, delivery, specification, architecture, …_questions or sprint. */
  item_kind: string
  item_id: number
  category: string
  /** Empty: its answer was unusable and the item is left to the client. */
  suggested_decision: string | null
  suggested_reason: string | null
  suggested_by: string
  suggested_at: string | null
  /** Set only when the company's policy let the agent decide alone. */
  decided_by: string | null
  rule: string | null
  outcome: string | null
  decided_at: string | null
  overridden_by: string | null
  overridden_at: string | null
  /** pending, done, not_allowed, stale, unusable or failed. */
  action_state?: string | null
  /** Why the policy did not let the agent decide alone: the platform's sentence, never the model's claim. */
  refusal_reason?: string | null
}

/** A suggestion the policy did not let the agent take alone, in the brief. */
export interface ClientBriefRefusal {
  id: number
  item_kind: string
  item_id: number
  category: string
  /** The model's suggestion and reason: claims. */
  decision: string | null
  reason: string | null
  action_state: string | null
  /** The platform's sentence. */
  refusal_reason: string
}

/** A blocked task with the Manager agent's suggestion, and its carry-over mark (the policy's carry_over rule). */
export type InboxTaskWithManager = InboxBlockedTask & {
  suggestion?: ManagerDecision | null
  marked_for_carry_over?: boolean
}

/** The inbox with the Manager agent's suggestions and decisions, when it is on. */
export type InboxWithManager = Omit<Inbox, 'blocked_tasks'> & {
  blocked_tasks: InboxTaskWithManager[]
  agent_decisions?: ManagerDecision[]
}

/** The client's brief: what got done, what the agent decided, what waits, the cost. */
export interface ClientBrief {
  since: string
  done: { count: number }
  agent: {
    suggested: number
    taken_alone: number
    overridden: number
    refused?: number
    refusals?: ClientBriefRefusal[]
  }
  waiting: { total: number }
  cost: { tokens_since: number; tokens_total: number; over_budget: boolean }
}

/** One category's counts in the Manager agent's record (counted, never reported). */
export interface ManagerRecordCounts {
  suggested: number
  decided: number
  agreed: number
  unusable: number
  taken_alone: number
  overridden: number
  override_rate: number | null
}

/** `GET /v1/manager_record`: agreement with the client and overrides, by category. */
export interface ManagerRecord {
  categories: Record<string, ManagerRecordCounts>
  total: ManagerRecordCounts
  flagged_rules: string[]
}

/** The rule names a policy may carry; each is off until a Manager names it. */
export const MANAGER_RULES = [
  'accept_delivery',
  'adopt_procedure',
  'approve_run',
  'merge_local',
  'undo_delivery',
  'send_back_blocked',
  'carry_over',
  'answer_question',
  'publish',
  'resume_after_credit',
  'approve_specification',
  'approve_architecture',
  'accept_high_risk',
] as const

/** `GET /v1/companies/{id}/manager_policy`: what the agent may decide alone. */
export interface ManagerPolicy {
  company_id: number
  rules: string[]
  max_files: number
  max_criteria: number
  max_files_high_risk: number
  publish_enabled: boolean
  updated_by: string | null
  updated_at: string | null
  /** Every rule the backend knows, with what it needs to hold. */
  catalogue: Record<string, string>
  /** Decisions no policy can hand to the agent. */
  always_escalate: string[]
}

export type ManagerPolicyUpdate = Pick<
  ManagerPolicy,
  'rules' | 'max_files' | 'max_criteria' | 'max_files_high_risk' | 'publish_enabled'
>

export const managerApi = {
  policy: (companyId: number) => api.get<ManagerPolicy>(`/v1/companies/${companyId}/manager_policy`),
  savePolicy: (companyId: number, data: ManagerPolicyUpdate) =>
    api.put<ManagerPolicy>(`/v1/companies/${companyId}/manager_policy`, data),
  record: (companyId: number) => api.get<ManagerRecord>(`/v1/manager_record?company_id=${companyId}`),
  override: (decisionId: number) =>
    api.post<{ id: number; outcome: string }>(`/v1/manager_decisions/${decisionId}/override`, {}),
  brief: (companyId: number) => api.get<ClientBrief>(`/v1/companies/${companyId}/brief`),
}

export function useManagerRecord(companyId: number | null) {
  return useQuery({
    queryKey: ['manager-record', companyId ?? 0] as const,
    queryFn: () => managerApi.record(companyId!),
    enabled: companyId !== null,
  })
}

export function useManagerPolicy(companyId: number | null) {
  return useQuery({
    queryKey: ['company', companyId ?? 0, 'manager-policy'] as const,
    queryFn: () => managerApi.policy(companyId!),
    enabled: companyId !== null,
  })
}

export function useSaveManagerPolicy(companyId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: ManagerPolicyUpdate) => managerApi.savePolicy(companyId, data),
    onSuccess: saved => {
      // The PUT answers with the stored policy: the panel shows it without waiting for a GET.
      queryClient.setQueryData(['company', companyId, 'manager-policy'], saved)
      queryClient.invalidateQueries({ queryKey: ['company', companyId, 'manager-policy'] })
      queryClient.invalidateQueries({ queryKey: ['manager-record'] })
    },
  })
}

export function useClientBrief(companyId: number | null) {
  return useQuery({
    queryKey: ['company', companyId ?? 0, 'brief'] as const,
    queryFn: () => managerApi.brief(companyId!),
    enabled: companyId !== null,
    refetchInterval: 60_000,
  })
}

/** The query families an override can change. */
export const OVERRIDE_INVALIDATES = [
  'inbox',
  'company',
  'sprint',
  'sprints',
  'procedures',
  'agent-runs',
  'agent-run',
  'audit-verification',
  'manager-record',
] as const

export function useOverrideManagerDecision() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: managerApi.override,
    // An override changes a task (the boards), a procedure or a run's
    // approval, so every view of those is read again, with the record.
    onSuccess: () => {
      for (const key of OVERRIDE_INVALIDATES) {
        queryClient.invalidateQueries({ queryKey: [key] })
      }
    },
  })
}
