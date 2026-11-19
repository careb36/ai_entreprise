/**
 * The company's self-improvement loop (`routers/rsi_cycles.py`,
 * docs/design/rsi-loop.md): its switches, limits and counts, and its cycles.
 * Every member reads them. Only a signed-in Owner acts, and only on what a
 * person decides: resuming the loop after its stop, recording the merge a
 * person made of a kept cycle (pinned to C), importing a cycle runner's
 * record. No route starts a cycle or records a decision.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './api'

export type RsiStatus =
  | 'running' | 'awaiting_merge' | 'awaiting_person' | 'merged' | 'reverted' | 'unmeasured' | 'budget_exceeded' | 'stopped'

export interface RsiState {
  company_id: number
  rsi_enabled: boolean
  self_hosting: boolean
  stopped: boolean
  consecutive_without_keep: number
  stop_after: number
  cycles_today: number
  cycles_per_day: number
  token_budget: number | null
  budget_reason: string | null
  max_diff_files: number
  max_diff_lines: number
  kept_merged_by_person: number
  person_merges_required: number
  may_start: boolean
  why_not_start: string | null
}

export interface RsiCycle {
  id: number
  cycle_key: string
  finding_id: number | null
  source: string | null
  status: RsiStatus | string
  baseline_sha: string | null
  candidate_sha: string | null
  sprint_id: number | null
  integration_id: number | null
  pull_request: string | null
  decision: string | null
  reasons: unknown[]
  diff_files: number | null
  diff_lines: number | null
  token_budget: number | null
  tokens_reported: number | null
  merged_sha: string | null
  merged_by: string | null
  started_at: string | null
  ended_at: string | null
  created_at: string | null
}

const base = (companyId: number) => `/v1/companies/${companyId}/rsi`

export const rsiApi = {
  list: (companyId: number) => api.get<{ state: RsiState; cycles: RsiCycle[] }>(`${base(companyId)}/cycles`),
  resume: (companyId: number) => api.post<RsiState>(`${base(companyId)}/resume`, {}),
  merge: (companyId: number, cycleId: number, mergedSha: string) =>
    api.post<RsiCycle>(`${base(companyId)}/cycles/${cycleId}/merge`, { merged_sha: mergedSha }),
  importRecord: (companyId: number, record: Record<string, unknown>) =>
    api.post<RsiCycle>(`${base(companyId)}/cycles/import`, { record }),
}

/** A full git commit sha, as the backend requires for a merge. */
export const FULL_SHA = /^[0-9a-f]{40}$/

const cyclesKey = (companyId: number | null) => ['rsi-cycles', companyId] as const

export function useRsiCycles(companyId: number | null) {
  return useQuery({ queryKey: cyclesKey(companyId), queryFn: () => rsiApi.list(companyId as number), enabled: companyId !== null })
}

type RsiAction =
  | { kind: 'resume' }
  | { kind: 'merge'; cycleId: number; sha: string }
  | { kind: 'import'; record: Record<string, unknown> }

export function useRsiAction(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (action: RsiAction): Promise<unknown> => {
      const id = companyId as number
      switch (action.kind) {
        case 'resume': return rsiApi.resume(id)
        case 'merge': return rsiApi.merge(id, action.cycleId, action.sha)
        case 'import': return rsiApi.importRecord(id, action.record)
      }
    },
    onSuccess: () => client.invalidateQueries({ queryKey: cyclesKey(companyId) }),
  })
}
