/**
 * Done, rework and cost by difficulty and model (issue #319):
 * `GET /v1/companies/{id}/routing_record`, counted from the company's
 * completed sprints. The difficulty is the deterministic estimate the task was
 * routed by (stored on the task before its first attempt); the model is the one
 * the proxy reported for its last delivery. The splits by band, served model and
 * policy carry the same numbers, so a policy is kept or reverted on them.
 */
import { useQuery } from '@tanstack/react-query'
import { api } from './api'

export interface RoutingTokens {
  /** Tokens of the turns whose usage the provider reported. */
  reported: number
  turns_reported: number
  /** Turns with an estimated or no count: counted apart, never added. */
  turns_unreported: number
}

export type DifficultyBand = 'low' | 'medium' | 'high'

/** The numbers of a group of tasks, whatever the split. */
export interface RoutingNumbers {
  tasks: number
  done: number
  done_first_attempt: number
  blocked: number
  rework: number
  rework_per_task: number
  blocked_share: number
  first_attempt_rate: number
  tokens: RoutingTokens
  /** US cents of the tasks whose cost is known; null when none is. */
  cost_cents?: number | null
  /** Tasks whose cost is unknown (a turn without a price): counted apart, never 0. */
  cost_unknown?: number
}

export interface RoutingRow extends RoutingNumbers {
  difficulty: number
  /** The estimate's own band (1-2 low, 3 medium, 4-5 high). */
  difficulty_band?: DifficultyBand
  /** The routing policy's band holding the difficulty, when one is configured. */
  band: string | null
  /** Null: the task had no delivery. */
  model: string | null
}

export interface RoutingRecord {
  rows: RoutingRow[]
  tasks: number
  policy: Record<string, string> | null
  policy_label?: string | null
  policy_source?: 'company' | 'deployment' | null
  /** The same numbers by the estimate's band, by served model and by the policy each task ran under. */
  by_difficulty_band?: (RoutingNumbers & { difficulty_band: DifficultyBand })[]
  by_model?: (RoutingNumbers & { model: string | null })[]
  by_policy?: (RoutingNumbers & { policy: string | null })[]
}

export const routingApi = {
  record: (companyId: number) => api.get<RoutingRecord>(`/v1/companies/${companyId}/routing_record`),
}

export function useRoutingRecord(companyId: number | null) {
  return useQuery({
    queryKey: ['company', companyId ?? 0, 'routing-record'] as const,
    queryFn: () => routingApi.record(companyId!),
    enabled: companyId !== null,
  })
}
