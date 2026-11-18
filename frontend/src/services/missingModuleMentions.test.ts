import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  allWeeks,
  mentionShare,
  missingModuleMentionsApi,
  sourcesOf,
  totals,
  type MissingModuleMentions,
} from './missingModuleMentions'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const mentionsData = (overrides: Partial<MissingModuleMentions> = {}): MissingModuleMentions => ({
  company_id: 4,
  sprint_id: null,
  counts_what: 'Rework requests whose text names a missing module, package or import: what the reviewers wrote, not whether a module was in fact missing.',
  cutoff: '2026-11-12T12:33:51',
  cutoff_is_default: true,
  before: {
    since: '2026-10-29T00:00:00', until: '2026-11-12T12:33:51', days: 14.52,
    rework_requests: { agent_reviewer: 6, gate: 2, person: 2 },
    requests_whose_text_names_missing_module: { agent_reviewer: 3, gate: 1 },
  },
  after: {
    since: '2026-11-12T12:33:51', until: '2026-11-19T00:00:00', days: 6.48,
    rework_requests: { agent_reviewer: 4 },
    requests_whose_text_names_missing_module: { agent_reviewer: 1 },
  },
  weeks: [
    { week: '2026-W44', period: 'before', rework_requests: { agent_reviewer: 4 }, requests_whose_text_names_missing_module: { agent_reviewer: 2 } },
    { week: '2026-W46', period: 'before', rework_requests: { agent_reviewer: 2, gate: 2, person: 2 }, requests_whose_text_names_missing_module: { agent_reviewer: 1, gate: 1 } },
    { week: '2026-W46', period: 'after', rework_requests: { agent_reviewer: 3 }, requests_whose_text_names_missing_module: { agent_reviewer: 1 } },
    { week: '2026-W47', period: 'after', rework_requests: { agent_reviewer: 1 }, requests_whose_text_names_missing_module: {} },
  ],
  ...overrides,
})

describe('missing module mentions API', () => {
  beforeEach(() => {
    useAuthStore.setState({ expiresAt: Date.now() + 60_000 })
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
  })

  it('reads a company with the default cutoff and period', async () => {
    const fetch = vi.fn().mockResolvedValue(response(200, mentionsData()))
    vi.stubGlobal('fetch', fetch)

    const result = await missingModuleMentionsApi.get(4)

    expect(result.cutoff_is_default).toBe(true)
    const [url, init] = fetch.mock.calls[0] as [string, RequestInit]
    const sent = new URL(url)
    expect(sent.pathname).toBe('/v1/analytics/missing_module_mentions')
    expect(init.method ?? 'GET').toBe('GET')
    expect(Object.fromEntries(sent.searchParams)).toEqual({ company_id: '4' })
  })

  it('sends the sprint, the cutoff and the period when given', async () => {
    const fetch = vi.fn().mockResolvedValue(response(200, mentionsData({ sprint_id: 7 })))
    vi.stubGlobal('fetch', fetch)

    await missingModuleMentionsApi.get(4, { sprintId: 7, cutoff: '2026-11-01T00:00:00', since: '2026-10-01', until: '2026-11-30' })

    const sent = new URL((fetch.mock.calls[0] as [string])[0])
    expect(Object.fromEntries(sent.searchParams)).toEqual({
      company_id: '4', sprint_id: '7', cutoff: '2026-11-01T00:00:00', since: '2026-10-01', until: '2026-11-30',
    })
  })
})

describe('missing module mentions helpers', () => {
  it('adds up every source, and gives no share when there was no request', () => {
    const data = mentionsData()
    expect(totals(data.before)).toEqual({ requests: 10, mentions: 4 })
    expect(mentionShare(data.after)).toBe(0.25)
    expect(mentionShare({ rework_requests: {}, requests_whose_text_names_missing_module: {} })).toBeNull()
  })

  it('lists the known sources in their fixed order, then any other by name', () => {
    expect(sourcesOf(mentionsData())).toEqual(['gate', 'person', 'agent_reviewer'])
    const data = mentionsData()
    data.after.rework_requests = { zeta: 1, alpha: 2, person: 1 }
    expect(sourcesOf(data)).toEqual(['gate', 'person', 'agent_reviewer', 'alpha', 'zeta'])
  })

  it('fills each side with its weeks, a week without a request as a zero, the cutoff week on both sides', () => {
    const weeks = allWeeks(mentionsData())
    expect(weeks.map(week => `${week.period} ${week.week}`)).toEqual([
      'before 2026-W44', 'before 2026-W45', 'before 2026-W46', 'after 2026-W46', 'after 2026-W47',
    ])
    expect(weeks[1].rework_requests).toEqual({})
  })

  it('draws no week for an empty side, and keeps a week the backend returned outside the range', () => {
    const data = mentionsData()
    data.after = { ...data.after, since: '2026-11-12T12:33:51', until: '2026-11-12T12:33:51', days: 0 }
    data.weeks = [{ week: '2026-W50', period: 'after', rework_requests: { person: 1 }, requests_whose_text_names_missing_module: {} }]
    const after = allWeeks(data).filter(week => week.period === 'after')
    expect(after.map(week => week.week)).toEqual(['2026-W50'])
  })
})
