/**
 * Predicted difficulty scored against observed effort (issue #534):
 * `GET /v1/companies/{id}/calibration`, over the company's completed sprints.
 * The prediction is the difficulty estimate stored before each task's first
 * attempt; the observation is what the backend read from the task's own
 * records at sprint close (tokens the proxy reported, wall time, rework,
 * review failures, files the daemon wrote), never an agent's account.
 * Tasks whose tokens are unknown are counted apart, never as 0.
 */
import { useQuery } from '@tanstack/react-query'
import { api } from './api'
import type { DifficultyBand } from './routing'

export interface TokenSpread {
  min: number
  q1: number
  median: number
  q3: number
  max: number
}

/** The calibration numbers of a group of tasks, whatever the split. */
export interface CalibrationNumbers {
  tasks: number
  done: number
  mean_predicted: number | null
  rework_cycles_per_task: number | null
  /** Tasks with more than one rework cycle. */
  over_one_rework: number
  over_one_rework_share: number | null
  review_failures: number
  tokens_known: number
  /** Tasks whose tokens are not known (no turn, or a turn not reported). */
  tokens_unknown: number
  tokens_spread: TokenSpread | null
  /** Rank correlation of predicted score and observed tokens; null when it says nothing. */
  spearman: number | null
  off_by_two_bands: number
  off_by_two_bands_share: number | null
  observed_band_unknown: number
  wall_seconds_median: number | null
  wall_unknown: number
  files_written_median: number | null
  files_unknown: number
}

export type CalibrationSignal = {
  signal: 'low_band_reworked' | 'high_band_not_costlier' | 'no_rank_agreement'
  band: DifficultyBand | null
  value: number
}

export interface CalibrationReport {
  tasks: number
  overall: CalibrationNumbers
  by_predicted_band: (CalibrationNumbers & { predicted_band: DifficultyBand })[]
  by_role: (CalibrationNumbers & { role: string | null })[]
  by_producer: (CalibrationNumbers & { producer: string | null })[]
  by_model: (CalibrationNumbers & { model: string | null })[]
  by_sprint_id: (CalibrationNumbers & { sprint_id: number | null })[]
  /** Tasks from before the estimate was stored: their prediction recomputed from the plan. */
  predicted_recomputed: number
  /** Tasks closed before the observation was stored: read from their records now. */
  observed_computed: number
  signals: CalibrationSignal[]
  /** The routing policy is never changed from these numbers. */
  information_only: boolean
}

export const calibrationApi = {
  report: (companyId: number) => api.get<CalibrationReport>(`/v1/companies/${companyId}/calibration`),
}

export function useCalibration(companyId: number | null) {
  return useQuery({
    queryKey: ['company', companyId ?? 0, 'calibration'] as const,
    queryFn: () => calibrationApi.report(companyId!),
    enabled: companyId !== null,
  })
}
