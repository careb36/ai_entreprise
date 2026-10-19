/**
 * The numbers that say whether the company is learning, per closed sprint.
 *
 * Rework requests per task and the share of blocked tasks are what keeps or
 * reverts a procedure (backend `services/procedures.py`); shown sprint by
 * sprint, oldest to newest, they are the trend a person reads without opening
 * anything. Only completed sprints with at least one task count: an active
 * sprint's numbers are still moving and an empty one measures nothing.
 */
import type { SprintSummary } from '../services/api'

export interface SprintPoint {
  id: number
  name: string
  reworkPerTask: number
  blockedShare: number
}

/** Closed sprints with tasks, oldest first. Empty when nothing closed yet. */
export function learningTrend(sprints: SprintSummary[]): SprintPoint[] {
  return sprints
    .filter(sprint => sprint.status === 'completed' && sprint.total > 0)
    .sort((a, b) => a.id - b.id)
    .map(sprint => ({
      id: sprint.id,
      name: sprint.sprint_name,
      reworkPerTask: sprint.rework / sprint.total,
      blockedShare: sprint.blocked / sprint.total,
    }))
}

export const formatRework = (value: number) => value.toFixed(2)
export const formatShare = (value: number) => `${Math.round(value * 100)}%`
