import type { Http } from './client'

/** The Verifier's verdicts a delivery can carry, and `none` before any (issue #531). */
export type ProgressVerdict = 'verified' | 'refuted' | 'unverifiable' | 'none'

/** Counts at one moment of a sprint, built from its task events. */
export interface ProgressCounts {
  status: Record<string, number>
  verification: Record<ProgressVerdict, number>
  /** Tasks with a `delivered` event: the producer's claim. */
  claimed: number
  /** Tasks whose latest verdict is `verified`: the observation. */
  verified: number
  /** Tasks claimed and not verified. */
  gap: number
}

export interface ProgressPoint extends ProgressCounts {
  /** A day (`YYYY-MM-DD`) or, by event, the event's time. */
  at: string
  events: number
  event_id?: number
}

export interface ProgressSeries {
  v: number
  sprint_id: number | null
  granularity: 'day' | 'event'
  tasks: number
  events: number
  points: ProgressPoint[]
  final: ProgressCounts
  by_task: { task_id: number; status: string; claimed: boolean; verification: Exclude<ProgressVerdict, 'none'> | null }[]
}

export interface ProgressSnapshotCheck {
  digest: 'match' | 'mismatch'
  audit: 'match' | 'mismatch' | 'missing'
  chain: string
  intact: boolean
}

export interface ProgressSnapshot {
  id: number
  sprint_id: number
  close_number: number
  digest: string
  audit_log_id: number | null
  created_at: string | null
  retrospective: Record<string, number>
  final: ProgressCounts | null
  verification?: ProgressSnapshotCheck
}

export interface SprintProgress {
  sprint_id: number
  status: string
  /** `live` for an open sprint, `snapshot` for a closed one. */
  source: 'live' | 'snapshot'
  series: ProgressSeries
  retrospective: Record<string, number> | null
  snapshot: ProgressSnapshot | null
  snapshots: ProgressSnapshot[]
}

export type CoverageState = 'verified' | 'done' | 'claimed' | 'open' | 'uncovered'

export interface SpecificationProgress {
  specification_id: number
  sprint_ids: number[]
  requirements: { requirement: string; tasks: number[]; claimed: number; done: number; verified: number; state: CoverageState }[]
  summary: Record<CoverageState, number>
}

export const progressEndpoints = (api: Http) => ({
  getSprintProgress: (sprintId: number) =>
    api.get<SprintProgress>(`/v1/sprints/${sprintId}/progress`),

  getSpecificationProgress: (specId: number) =>
    api.get<SpecificationProgress>(`/v1/specifications/${specId}/progress`),
})
