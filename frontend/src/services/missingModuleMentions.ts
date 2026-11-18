/**
 * Rework requests whose text names a missing module, package or import,
 * before and after a cutoff (issue #549, the second measure):
 * `GET /v1/analytics/missing_module_mentions`. The events come from the
 * task-event chain, but the text counted is what a reviewer wrote: every
 * count here is a claim, not an observation (`counts_what` says so in the
 * backend's own words). The cutoff defaults to the grounded check's merge
 * (#503). The deliveries the gate refuted are `services/gateRefutations.ts`.
 */
import { useQuery } from '@tanstack/react-query'
import { api } from './api'
import { isoWeek } from './gateRefutations'

/** The sources the backend keys its counts by, in the order the view shows them. */
export const MENTION_SOURCES = ['gate', 'person', 'agent_reviewer'] as const
export type MentionSource = typeof MENTION_SOURCES[number]

export type Side = 'before' | 'after'

export interface MentionCounts {
  /** Per source; a source with nothing is left out. */
  rework_requests: Record<string, number>
  requests_whose_text_names_missing_module: Record<string, number>
}

export interface MentionPeriod extends MentionCounts {
  /** ISO-8601 datetimes, naive UTC; `since` inclusive, `until` exclusive. */
  since: string
  until: string
  days: number
}

export interface MentionWeek extends MentionCounts {
  /** ISO week, e.g. `2026-W46`. */
  week: string
  /** A week across the cutoff appears once per side. */
  period: Side
}

export interface MissingModuleMentions {
  company_id: number
  sprint_id: number | null
  /** What the counts mean and what they do not, in the backend's words. */
  counts_what: string
  /** ISO-8601 datetime, naive UTC. */
  cutoff: string
  cutoff_is_default: boolean
  before: MentionPeriod
  after: MentionPeriod
  weeks: MentionWeek[]
}

export interface MissingModuleMentionsQuery {
  sprintId?: number
  /** ISO datetime; the backend defaults to #503's merge. */
  cutoff?: string
  /** ISO dates; the backend defaults to eight weeks before the cutoff, and today. */
  since?: string
  until?: string
}

export const missingModuleMentionsApi = {
  get: (companyId: number, query: MissingModuleMentionsQuery = {}) => {
    const params: Record<string, string | number> = { company_id: companyId }
    if (query.sprintId !== undefined) params.sprint_id = query.sprintId
    if (query.cutoff) params.cutoff = query.cutoff
    if (query.since) params.since = query.since
    if (query.until) params.until = query.until
    return api.get<MissingModuleMentions>('/v1/analytics/missing_module_mentions', params)
  },
}

function sum(counts: Record<string, number>): number {
  return Object.values(counts).reduce((total, count) => total + count, 0)
}

/** Requests in total, and those whose text names a missing module, over every source. */
export function totals(counts: MentionCounts): { requests: number; mentions: number } {
  return { requests: sum(counts.rework_requests), mentions: sum(counts.requests_whose_text_names_missing_module) }
}

/** The mentions' share of the requests, 0..1, or null when there was no request. */
export function mentionShare(counts: MentionCounts): number | null {
  const { requests, mentions } = totals(counts)
  return requests > 0 ? mentions / requests : null
}

/** The known sources first in their fixed order, then any source the view does not know, by name. */
export function sourcesOf(data: MissingModuleMentions): string[] {
  const seen = new Set<string>()
  for (const period of [data.before, data.after]) {
    for (const key of Object.keys(period.rework_requests)) seen.add(key)
    for (const key of Object.keys(period.requests_whose_text_names_missing_module)) seen.add(key)
  }
  const known: string[] = MENTION_SOURCES.filter(source => seen.has(source))
  const other = [...seen].filter(source => !(MENTION_SOURCES as readonly string[]).includes(source)).sort()
  return [...known, ...other]
}

const DAY_MS = 86_400_000

/** The ISO weeks a period touches, from `since` (inclusive) to `until` (exclusive). */
function periodWeeks(period: MentionPeriod): string[] {
  const start = Date.parse(`${period.since}Z`)
  const end = Date.parse(`${period.until}Z`)
  if (!(end > start)) return []
  const keys: string[] = []
  const last = new Date(end - 1).toISOString().slice(0, 10)
  for (let day = Date.UTC(...dateParts(period.since)); ; day += DAY_MS) {
    const date = new Date(day).toISOString().slice(0, 10)
    const key = isoWeek(date)
    if (keys[keys.length - 1] !== key) keys.push(key)
    if (date >= last) break
  }
  return keys
}

function dateParts(iso: string): [number, number, number] {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number)
  return [y, m - 1, d]
}

/**
 * Every week of each side, in order, before first: a week without a request
 * is a zero the reader should see, not a gap. A week across the cutoff is
 * kept once per side, as the backend returns it.
 */
export function allWeeks(data: MissingModuleMentions): MentionWeek[] {
  const known = new Map(data.weeks.map(week => [`${week.period}|${week.week}`, week]))
  const result: MentionWeek[] = []
  for (const side of ['before', 'after'] as const) {
    const keys = periodWeeks(data[side])
    // A week the backend returned outside the range is kept rather than dropped.
    for (const week of data.weeks) if (week.period === side && !keys.includes(week.week)) keys.push(week.week)
    for (const key of keys.sort()) {
      result.push(known.get(`${side}|${key}`) ?? {
        week: key, period: side, rework_requests: {}, requests_whose_text_names_missing_module: {},
      })
    }
  }
  return result
}

export function useMissingModuleMentions(companyId: number | null, query: MissingModuleMentionsQuery = {}) {
  return useQuery({
    queryKey: ['company', companyId ?? 0, 'missing-module-mentions', query.sprintId ?? null,
      query.cutoff ?? null, query.since ?? null, query.until ?? null] as const,
    queryFn: () => missingModuleMentionsApi.get(companyId!, query),
    enabled: companyId !== null,
  })
}
