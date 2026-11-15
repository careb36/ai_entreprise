/**
 * What a run, a task and a sprint cost, in tokens and US cents
 * (`GET /v1/agent_runs/{id}/cost`, `GET /v1/tasks/{id}/cost`,
 * `GET /v1/sprints/{id}/cost`, and `cost` on each sprint of `GET /v1/sprints`;
 * backend `services/run_cost.py`). The first three also carry `prompt_weight`,
 * the estimated tokens of each prompt section (#443).
 *
 * Only usage the provider reported is priced. Estimated tokens come apart and
 * are never in the cents; a model with no price is `cost_cents: null` with
 * the reason, never 0. The formatting follows Aixle Flow's single module for
 * cost and tokens (`shared/lib/sessionFormat.ts`), except that an unknown
 * cost is never shown like a zero one.
 */
import { useQuery } from '@tanstack/react-query'
import { api } from './api'

export type CostReason =
  | 'no_turns'
  | 'no_reported_usage'
  | 'token_split_unknown'
  | 'price_unknown'
  | 'price_ambiguous'
  | 'model_unknown'

export interface CostSummary {
  turns: number
  reported_turns: number
  estimated_turns: number
  unavailable_turns: number
  reported_tokens: number
  input_tokens: number
  output_tokens: number
  estimated_tokens: number
  /** Unrounded US cents; `null` when not every reported turn could be priced. */
  cost_cents: number | null
  reason: CostReason | null
  unpriced_models: string[]
  models: string[]
  price_source: string
}

/** One prompt section, added up over the prompts the task worker sent (#443). */
export interface PromptSectionWeight {
  name: string
  chars: number
  /** Estimated tokens (characters / 4), never the provider's count. */
  tokens: number
  /** Share of the sections' tokens, 0–1. */
  share: number
  /** Prompts that carried it. */
  calls: number
  /** Prompts whose budget left it out. */
  cut: number
}

/** The backend's own measure of the prompts it sent (`services/models/prompt_weight.py`). */
export interface PromptWeight {
  calls: number
  tokens: number
  system_tokens: number
  sections: PromptSectionWeight[]
  estimate: string
}

export interface RunCost extends CostSummary {
  run_id: number
  version: number
  prompt_weight?: PromptWeight
}

export interface TaskCost extends CostSummary {
  task_id: number
  runs: RunCost[]
  prompt_weight?: PromptWeight
}

export interface SprintCost extends CostSummary {
  sprint_id: number
  prompt_weight?: PromptWeight
}

export const costApi = {
  run: (companyId: number, runId: number) =>
    api.get<RunCost>(`/v1/agent_runs/${runId}/cost`, { company_id: companyId }),
  task: (taskId: number) => api.get<TaskCost>(`/v1/tasks/${taskId}/cost`),
  sprint: (sprintId: number) => api.get<SprintCost>(`/v1/sprints/${sprintId}/cost`),
}

export function useRunCost(companyId: number, runId: number) {
  return useQuery({
    queryKey: ['agent-run', companyId, runId, 'cost'],
    queryFn: () => costApi.run(companyId, runId),
  })
}

export function useTaskCost(taskId: number) {
  return useQuery({
    queryKey: ['task-cost', taskId],
    queryFn: () => costApi.task(taskId),
  })
}

/** A sprint's cost and prompt weight, fetched only once asked for (`enabled`). */
export function useSprintCost(sprintId: number, enabled: boolean) {
  return useQuery({
    queryKey: ['sprint-cost', sprintId],
    queryFn: () => costApi.sprint(sprintId),
    enabled,
  })
}

/** Cents → currency (`42` → `$0.42`); below a cent, up to four decimals. `null` stays `null`. */
export function formatCostCents(cents: number | null, locale = 'en'): string | null {
  if (cents === null || !Number.isFinite(cents)) return null
  const dollars = cents / 100
  const small = dollars > 0 && dollars < 0.01
  return new Intl.NumberFormat(locale, {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: small ? 4 : 2,
  }).format(dollars)
}

/** `228700` → `228.7k`. */
export function formatTokens(count: number): string {
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}k`
  return String(count)
}
