/**
 * The Manager agent (issue #331): what it suggested and decided alone under
 * the company's policy, the client's one-click override, and the brief.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type Inbox, type InboxBlockedTask } from './api'

/** One inbox item the Manager agent looked at. A suggestion is a claim. */
export interface ManagerDecision {
  id: number
  item_kind: 'task' | 'procedure' | 'run'
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
}

/** The inbox with the Manager agent's suggestions and decisions, when it is on. */
export type InboxWithManager = Omit<Inbox, 'blocked_tasks'> & {
  blocked_tasks: (InboxBlockedTask & { suggestion?: ManagerDecision | null })[]
  agent_decisions?: ManagerDecision[]
}

/** The client's brief: what got done, what the agent decided, what waits, the cost. */
export interface ClientBrief {
  since: string
  done: { count: number }
  agent: { suggested: number; taken_alone: number; overridden: number }
  waiting: { total: number }
  cost: { tokens_since: number; tokens_total: number; over_budget: boolean }
}

export const managerApi = {
  override: (decisionId: number) =>
    api.post<{ id: number; outcome: string }>(`/v1/manager_decisions/${decisionId}/override`, {}),
  brief: (companyId: number) => api.get<ClientBrief>(`/v1/companies/${companyId}/brief`),
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
