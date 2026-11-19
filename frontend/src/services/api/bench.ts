import type { Http } from './client'

/**
 * The bench's committed scorecards (`GET /v1/bench/scorecards`,
 * `backend/app/services/models/bench_scorecards.py`; issue #685). Every number
 * is what the bench's scorer observed; a metric the card did not measure is
 * `{ measured: false, reason }`, never a 0.
 */
export type BenchUnmeasuredReason =
  | 'grade_unmeasured'
  | 'no_criteria'
  | 'no_reported_turns'
  | 'cost_unknown'
  | 'not_in_card'
  | 'no_prompt_measure'
  | 'no_problems_graded'
  | 'no_instances_measured'
  | 'tests_not_run'

export type BenchMeasure<T> =
  | { measured: true; value: T }
  | { measured: false; reason: BenchUnmeasuredReason; detail?: unknown }

export interface BenchCriteria { passed: number; total: number; rate: number }

export type BenchTokens = BenchMeasure<number> & { turns_reported: number; turns_unreported: number }

export interface BenchPrompt {
  prompts: number
  prompt_sections_missing: number
  tokens_estimated: number
  tokens_mean: number
  system_tokens_estimated: number
  cut_prompts: number
  related_code_prompts: number
}

export type BenchSpecStatus = 'completed' | 'timeout' | 'error' | 'budget_exceeded'

export interface BenchSpec {
  name: string
  status: BenchSpecStatus
  error: string | null
  tasks: { total: number; done: number; blocked: number; done_first_attempt: number }
  rework_requests: number
  rework_per_task: number
  blocked_share: number
  first_attempt_rate: number
  wall_clock_seconds: number
  models: Record<string, string[]>
  criteria: BenchMeasure<BenchCriteria>
  results: Record<string, 'pass' | 'fail' | 'missing'>
  isolation: string | null
  tokens: BenchTokens
  cost_cents: BenchMeasure<number>
  prompt: BenchMeasure<BenchPrompt>
}

export interface BenchTotals {
  specs: number
  specs_completed: number
  tasks: number
  done_first_attempt: number
  first_attempt_rate: number
  rework_per_task: number
  blocked_share: number
  wall_clock_seconds: number
  criteria: BenchMeasure<BenchCriteria>
  tokens: BenchTokens
  prompt: BenchMeasure<BenchPrompt>
}

export interface BenchPromptSection { section: string; tokens: number; prompts: number; share: number; cut: number }

/** A HumanEval problem (schema v5): its outcome from HumanEval's own tests, or unmeasured with the card's reason. */
export interface BenchHumanEvalProblem {
  task_id: string
  entry_point: string
  status: BenchSpecStatus
  outcome: BenchMeasure<'pass' | 'fail'>
  wall_clock_seconds: number | null
  tokens: BenchTokens
}

/** The card's `humaneval` block (schema v5); pass@1 is unmeasured while any problem is. */
export interface BenchHumanEval {
  dataset: { name: string; source: string; sha256: string; license: string }
  graded: number
  passed: number
  failed: number
  unmeasured: number
  pass_at_1: BenchMeasure<number>
  tokens: BenchTokens
  problems: BenchHumanEvalProblem[]
  skipped: string[]
}

/** A SWE-bench Lite instance (schema v6): its outcome from its own tests run by the scorer, or unmeasured with the card's reason. */
export interface BenchSweBenchInstance {
  instance_id: string
  repo: string
  status: BenchSpecStatus
  outcome: BenchMeasure<'pass' | 'fail'>
  reason: string | null
  fail_to_pass: BenchMeasure<{ passed: number; total: number }>
  pass_to_pass: BenchMeasure<{ passed: number; total: number }>
  wall_clock_seconds: number | null
  tokens: BenchTokens
}

/** The card's `swebench` block (schema v6); the resolve rate leaves unmeasured instances out and is unmeasured when none was measured. */
export interface BenchSweBench {
  dataset: { name: string; revision: string; split: string; source: string; license: string; pinned: number }
  graded: number
  measured: number
  resolved: number
  failed: number
  unmeasured: number
  resolved_rate: BenchMeasure<number>
  tokens: BenchTokens
  instances: BenchSweBenchInstance[]
  skipped: string[]
}

interface BenchCardBase {
  file: string
  folder: '' | 'real' | 'ablation'
  stamp: string
  problems: string[]
}

export interface BenchValidCard extends BenchCardBase {
  status: 'valid'
  schema_version: number
  commit: string
  llm: string
  policy: string | null
  run: { status: 'complete' | 'budget_exceeded'; budget: number | null; tokens_spent: number; skipped: string[] } | null
  graders_sha: string | null
  sections_off: string[]
  has_ablation: boolean
  totals: BenchTotals
  specs: BenchSpec[]
  prompt_sections: BenchPromptSection[] | null
  humaneval: BenchHumanEval | null
  swebench: BenchSweBench | null
}

export interface BenchRejectedCard extends BenchCardBase { status: 'invalid' | 'unreadable' }

export type BenchCard = BenchValidCard | BenchRejectedCard

export interface BenchHistory { available: boolean; cards: BenchCard[] }

// Any signed-in caller reads them; nothing writes.
export const benchEndpoints = (http: Http) => ({
  getBenchScorecards: () => http.get<BenchHistory>('/v1/bench/scorecards'),
})
