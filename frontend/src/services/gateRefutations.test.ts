import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  GATE_UNCONFIRMED,
  allWeeks,
  gateRefutationsApi,
  isoWeek,
  refutingProblems,
  weekRefuted,
  type GateRefutations,
} from './gateRefutations'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const data = (overrides: Partial<GateRefutations> = {}): GateRefutations => ({
  company_id: 4,
  sprint_id: null,
  since: '2026-11-02',
  until: '2026-11-22',
  by_problem: { 'invented dependency or import': 2, 'tests failed': 3, [GATE_UNCONFIRMED]: 1 },
  refuted: 5,
  unconfirmed: 1,
  weeks: [
    { week: '2026-W45', by_problem: { 'tests failed': 3 } },
    { week: '2026-W47', by_problem: { 'invented dependency or import': 2, [GATE_UNCONFIRMED]: 1 } },
  ],
  ...overrides,
})

describe('gate refutations API', () => {
  beforeEach(() => {
    useAuthStore.setState({ expiresAt: Date.now() + 60_000 })
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
  })

  it('reads a company over the default period', async () => {
    const fetch = vi.fn().mockResolvedValue(response(200, data()))
    vi.stubGlobal('fetch', fetch)

    const result = await gateRefutationsApi.get(4)

    expect(result.refuted).toBe(5)
    const [url, init] = fetch.mock.calls[0] as [string, RequestInit]
    const sent = new URL(url)
    expect(sent.pathname).toBe('/v1/analytics/gate_refutations')
    expect(init.method ?? 'GET').toBe('GET')
    expect(Object.fromEntries(sent.searchParams)).toEqual({ company_id: '4' })
  })

  it('sends the sprint and the period when given', async () => {
    const fetch = vi.fn().mockResolvedValue(response(200, data({ sprint_id: 7 })))
    vi.stubGlobal('fetch', fetch)

    await gateRefutationsApi.get(4, { sprintId: 7, since: '2026-10-01', until: '2026-10-31' })

    const sent = new URL((fetch.mock.calls[0] as [string])[0])
    expect(Object.fromEntries(sent.searchParams)).toEqual({
      company_id: '4', sprint_id: '7', since: '2026-10-01', until: '2026-10-31',
    })
  })
})

describe('gate refutations helpers', () => {
  it('keys a date by its ISO week, as the backend does', () => {
    expect(isoWeek('2026-11-18')).toBe('2026-W47')
    expect(isoWeek('2026-11-16')).toBe('2026-W47')
    expect(isoWeek('2026-11-15')).toBe('2026-W46')
    // Week 1 holds the year's first Thursday; 2027-01-01 is a Friday.
    expect(isoWeek('2027-01-01')).toBe('2026-W53')
    expect(isoWeek('2026-01-01')).toBe('2026-W01')
  })

  it('fills the weeks without a send-back with zeros, in order', () => {
    const weeks = allWeeks(data())
    expect(weeks.map(week => week.week)).toEqual(['2026-W45', '2026-W46', '2026-W47'])
    expect(weeks[1].by_problem).toEqual({})
  })

  it('leaves an unconfirmed delivery out of the refutations', () => {
    expect(weekRefuted(data().weeks[1])).toBe(2)
    expect(refutingProblems(data())).toEqual([['tests failed', 3], ['invented dependency or import', 2]])
  })
})
