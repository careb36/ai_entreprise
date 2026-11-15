import { fetchText, type Http } from './client'

export type UsagePeriod = 'day' | 'week' | 'month'
export type UsageDimension = 'period' | 'person' | 'team' | 'role' | 'producer' | 'model'

export const USAGE_DIMENSIONS: UsageDimension[] = ['period', 'person', 'team', 'role', 'producer', 'model']

/** One row's numbers, read back from the records (never an agent's claim). */
export interface UsageNumbers {
  tasks: number
  rework: number
  runs: number
  verified: number
  refuted: number
  unverified: number
  approvals: number
  changes_requested: number
  turns: number
  reported_turns: number
  estimated_turns: number
  unavailable_turns: number
  input_tokens: number
  output_tokens: number
  reported_tokens: number
  estimated_tokens: number
  /** The priced turns only; null when none was priced (never shown as 0). */
  cost_cents: number | null
  /** Turns whose cost is unknown, counted apart. */
  cost_unknown_turns: number
}

export interface UsageRow extends UsageNumbers {
  /** null: unattributed (no person, no team, no role...). */
  key: string | number | null
  label: string | null
}

export interface UsageReport {
  company_id: number
  since: string
  until: string
  period: UsagePeriod
  timezone: 'UTC'
  generated_at: string
  columns: string[]
  totals: UsageNumbers
  by_period: UsageRow[]
  by_person: UsageRow[]
  by_team: UsageRow[]
  by_role: UsageRow[]
  by_producer: UsageRow[]
  by_model: UsageRow[]
}

export interface UsageQuery {
  since: string
  until: string
  period: UsagePeriod
}

function usageQuery(query: UsageQuery): string {
  return new URLSearchParams({ since: query.since, until: query.until, period: query.period }).toString()
}

// Manager+; the CSV export is audited.
export const usageEndpoints = (http: Http) => ({
  getUsage: (companyId: number, query: UsageQuery) =>
    http.get<UsageReport>(`/v1/companies/${companyId}/usage`, { ...query }),

  exportUsageCsv: (companyId: number, query: UsageQuery) =>
    fetchText(`/v1/companies/${companyId}/usage.csv?${usageQuery(query)}`),
})
