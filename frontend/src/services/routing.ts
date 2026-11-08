/**
 * Done, rework and cost by difficulty and model (issue #319):
 * `GET /v1/companies/{id}/routing_record`, counted from the company's
 * completed sprints. The difficulty is the deterministic estimate the task was
 * routed by; the model is the one the proxy reported for its last delivery.
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

export interface RoutingRow {
  difficulty: number
  /** The routing policy's band holding the difficulty, when one is configured. */
  band: string | null
  /** Null: the task had no delivery. */
  model: string | null
  tasks: number
  done: number
  done_first_attempt: number
  blocked: number
  rework: number
  rework_per_task: number
  blocked_share: number
  first_attempt_rate: number
  tokens: RoutingTokens
}

export interface RoutingRecord {
  rows: RoutingRow[]
  tasks: number
  policy: Record<string, string> | null
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
