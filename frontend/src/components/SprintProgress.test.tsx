import { render, screen, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SprintProgress } from './SprintProgress'
import { SprintProgressChart } from './SprintProgressChart'
import { queryClient } from '../lib/queryClient'
import { useAuthStore } from '../store/useAuthStore'
import type { ProgressSeries, SprintProgress as Progress } from '../services/api'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const counts = (claimed: number, verified: number, done: number) => ({
  status: { todo: 3 - claimed, in_progress: 0, in_review: claimed - done, blocked: 0, done },
  verification: { verified, refuted: claimed - verified, unverifiable: 0, none: 3 - claimed },
  claimed,
  verified,
  gap: claimed - verified,
})

const SERIES: ProgressSeries = {
  v: 1,
  sprint_id: 8,
  granularity: 'day',
  tasks: 3,
  events: 6,
  points: [
    { at: '2026-11-10', events: 3, ...counts(1, 1, 0) },
    { at: '2026-11-11', events: 6, ...counts(3, 1, 1) },
  ],
  final: counts(3, 1, 1),
  by_task: [],
}

const SNAPSHOT = {
  id: 4, sprint_id: 8, close_number: 1, digest: 'sha256:abc', audit_log_id: 9, created_at: null,
  retrospective: { total_tasks: 3 }, final: SERIES.final,
  verification: { digest: 'match', audit: 'match', chain: 'intact', intact: true },
}

function progress(overrides: Partial<Progress> = {}): Progress {
  return {
    sprint_id: 8, status: 'active', source: 'live', series: SERIES, retrospective: null,
    snapshot: null, snapshots: [], ...overrides,
  } as Progress
}

function renderProgress(specificationId?: number) {
  return render(
    <QueryClientProvider client={queryClient}>
      <SprintProgress sprintId={8} specificationId={specificationId} />
    </QueryClientProvider>,
  )
}

describe('SprintProgressChart', () => {
  it('draws the claimed and verified lines apart, with a legend and a table', () => {
    render(<SprintProgressChart series={SERIES} />)
    const chart = screen.getByRole('img')
    expect(chart).toHaveAccessibleName('3 of 3 tasks claimed delivered, 1 verified')
    const lines = chart.querySelectorAll('polyline')
    expect(lines).toHaveLength(2)
    expect(lines[0].getAttribute('points')).not.toEqual(lines[1].getAttribute('points'))
    // Told apart by line style as well as color.
    expect(lines[1].getAttribute('stroke-dasharray')).toBe('6 4')
    expect(within(chart).getByText('Delivered (claimed) 3')).toBeInTheDocument()
    expect(within(chart).getByText('Verified 1')).toBeInTheDocument()
    const rows = screen.getAllByRole('row')
    expect(rows[2]).toHaveTextContent('2026-11-11')
    expect(rows[2]).toHaveTextContent('312')
  })

  it('says so when there are no events', () => {
    render(<SprintProgressChart series={{ ...SERIES, points: [] }} />)
    expect(screen.getByText('No task events yet.')).toBeInTheDocument()
  })
})

describe('SprintProgress', () => {
  beforeEach(() => {
    queryClient.clear()
    useAuthStore.setState({ expiresAt: Date.now() + 60_000 })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('shows the live series of an open sprint and the gap', async () => {
    const fetch = vi.fn().mockResolvedValue(response(200, progress()))
    vi.stubGlobal('fetch', fetch)
    renderProgress()

    expect(await screen.findByText('2 of 3 tasks claimed delivered are not verified.')).toBeInTheDocument()
    expect(screen.getByText('Live, from the task events')).toBeInTheDocument()
    expect(String(fetch.mock.calls[0][0])).toContain('/v1/sprints/8/progress')
    expect(screen.getByText('Refuted').nextSibling).toHaveTextContent('2')
  })

  it('shows a closed sprint as sealed, or says when the seal does not verify', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(200, progress({
      status: 'completed', source: 'snapshot', snapshot: SNAPSHOT as Progress['snapshot'],
    }))))
    renderProgress()
    expect(await screen.findByText('Sealed at close 1; digest and audit chain verified')).toBeInTheDocument()

    queryClient.clear()
    const broken = { ...SNAPSHOT, verification: { ...SNAPSHOT.verification, digest: 'mismatch', intact: false } }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(200, progress({
      status: 'completed', source: 'snapshot', snapshot: broken as Progress['snapshot'],
    }))))
    renderProgress()
    expect(await screen.findByText('Sealed at close 1; the digest or the audit chain does not verify')).toBeInTheDocument()
  })

  it('lists the coverage per requirement of the sprint’s specification', async () => {
    const coverage = {
      specification_id: 5, sprint_ids: [8],
      requirements: [
        { requirement: 'REQ-1', tasks: [1, 2], claimed: 2, done: 1, verified: 1, state: 'verified' },
        { requirement: 'REQ-2', tasks: [], claimed: 0, done: 0, verified: 0, state: 'uncovered' },
      ],
      summary: { verified: 1, done: 0, claimed: 0, open: 0, uncovered: 1 },
    }
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) =>
      response(200, String(url).includes('/specifications/') ? coverage : progress())))
    renderProgress(5)

    const row = (await screen.findByText('REQ-2')).closest('tr')!
    expect(row).toHaveTextContent('Uncovered')
    expect(screen.getByText('REQ-1').closest('tr')).toHaveTextContent('Verified')
  })
})
