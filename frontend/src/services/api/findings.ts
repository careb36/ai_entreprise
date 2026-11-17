import type { Http } from './client'

/**
 * The self-improvement loop's findings (`docs/design/rsi-loop.md`, §1 and §7;
 * `backend/app/routers/findings.py`): a defect or gap the platform observed,
 * with its target measure and the outcome of every cycle run for it. Read-only
 * here: the platform writes the outcomes, never a person or an agent.
 */
export type FindingStatus = 'open' | 'in_cycle' | 'kept' | 'reverted' | 'unmeasured' | 'closed_by_person'

/** The fitness decision of one cycle (`services/evidence/fitness.py`). */
export type OutcomeDecision = 'keep' | 'revert' | 'unmeasured'

/** One guard the fitness function judged (`fitness_checks._reason`). */
export interface FindingReason {
  guard?: string
  outcome?: string
  code?: string
  detail?: string
}

/** One cycle's outcome: an observation the platform recorded. */
export interface FindingOutcome {
  cycle: string | number | null
  decision: OutcomeDecision
  reasons: (FindingReason | string)[]
  baseline_sha: string
  candidate_sha: string
  date: string | null
  produced_by: string
}

export interface LoopFinding {
  id: number
  company_id: number
  source: string | null
  source_ref: string | null
  title: string
  specification_sha256: string
  specification_path: string | null
  target_measure: string | null
  status: FindingStatus
  outcomes: FindingOutcome[]
  closed_by: string | null
  created_at: string | null
  updated_at: string | null
}

export interface LoopFindingDetail extends LoopFinding {
  specification_text: string | null
}

// Any member of the company (a Viewer included) reads them; no route writes.
export const findingEndpoints = (http: Http) => ({
  getFindings: (companyId: number) =>
    http.get<{ findings: LoopFinding[] }>(`/v1/findings?company_id=${companyId}`),

  getFinding: (findingId: number) => http.get<LoopFindingDetail>(`/v1/findings/${findingId}`),
})
