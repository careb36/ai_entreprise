/**
 * Deliveries the gate before QA sent back, per problem and per ISO week
 * (issue #549): `GET /v1/analytics/gate_refutations`. The backend reads the
 * Verifier's own send-backs from the task-event chain, never what an agent
 * said, so every count here is an observation. `delivery unconfirmed` (the
 * daemon never confirmed the write) is counted apart and is not in `refuted`.
 */
import { useQuery } from '@tanstack/react-query'
import { api } from './api'

/** The name the backend gives a send-back whose write the daemon never confirmed. */
export const GATE_UNCONFIRMED = 'delivery unconfirmed'

export interface GateRefutationWeek {
  /** ISO week, e.g. `2026-W46`. */
  week: string
  by_problem: Record<string, number>
}

export interface GateRefutations {
  company_id: number
  sprint_id: number | null
  /** ISO dates, both inclusive. */
  since: string
  until: string
  /** Every problem the gate named, `delivery unconfirmed` included. */
  by_problem: Record<string, number>
  /** Every send-back but `delivery unconfirmed`. */
  refuted: number
  unconfirmed: number
  weeks: GateRefutationWeek[]
}

export interface GateRefutationsQuery {
  sprintId?: number
  /** ISO dates; the backend defaults to the eight weeks up to today. */
  since?: string
  until?: string
}

export const gateRefutationsApi = {
  get: (companyId: number, query: GateRefutationsQuery = {}) => {
    const params: Record<string, string | number> = { company_id: companyId }
    if (query.sprintId !== undefined) params.sprint_id = query.sprintId
    if (query.since) params.since = query.since
    if (query.until) params.until = query.until
    return api.get<GateRefutations>('/v1/analytics/gate_refutations', params)
  },
}

/** The refutations of a week, `delivery unconfirmed` left out as the backend's total leaves it. */
export function weekRefuted(week: GateRefutationWeek): number {
  return Object.entries(week.by_problem)
    .filter(([problem]) => problem !== GATE_UNCONFIRMED)
    .reduce((sum, [, count]) => sum + count, 0)
}

/** The refuting problems, most frequent first (ties by name), `delivery unconfirmed` left out. */
export function refutingProblems(data: GateRefutations): [string, number][] {
  return Object.entries(data.by_problem)
    .filter(([problem, count]) => problem !== GATE_UNCONFIRMED && count > 0)
    .sort(([a, x], [b, y]) => y - x || a.localeCompare(b))
}

const DAY_MS = 86_400_000

/** The ISO week of an ISO date (`2026-11-18` → `2026-W47`), as the backend keys its weeks. */
export function isoWeek(isoDate: string): string {
  const [y, m, d] = isoDate.split('-').map(Number)
  const day = Date.UTC(y, m - 1, d)
  const weekday = (new Date(day).getUTCDay() + 6) % 7
  const thursday = new Date(day + (3 - weekday) * DAY_MS)
  const year = thursday.getUTCFullYear()
  const week = 1 + Math.floor((thursday.getTime() - Date.UTC(year, 0, 1)) / DAY_MS / 7)
  return `${year}-W${String(week).padStart(2, '0')}`
}

/**
 * Every ISO week from `since` to `until`, each with the backend's counts or
 * none: a week without a send-back is a zero the reader should see, not a gap.
 */
export function allWeeks(data: GateRefutations): GateRefutationWeek[] {
  const known = new Map(data.weeks.map(week => [week.week, week]))
  const [sy, sm, sd] = data.since.split('-').map(Number)
  const [uy, um, ud] = data.until.split('-').map(Number)
  const end = Date.UTC(uy, um - 1, ud)
  const keys: string[] = []
  for (let day = Date.UTC(sy, sm - 1, sd); day <= end; day += DAY_MS) {
    const key = isoWeek(new Date(day).toISOString().slice(0, 10))
    if (keys[keys.length - 1] !== key) keys.push(key)
  }
  // A week the backend returned outside the range is kept rather than dropped.
  for (const week of data.weeks) if (!keys.includes(week.week)) keys.push(week.week)
  return keys.sort().map(key => known.get(key) ?? { week: key, by_problem: {} })
}

export function useGateRefutations(companyId: number | null, query: GateRefutationsQuery = {}) {
  return useQuery({
    queryKey: ['company', companyId ?? 0, 'gate-refutations', query.sprintId ?? null, query.since ?? null, query.until ?? null] as const,
    queryFn: () => gateRefutationsApi.get(companyId!, query),
    enabled: companyId !== null,
  })
}
