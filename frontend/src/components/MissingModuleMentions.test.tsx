import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MissingModuleMentions } from './MissingModuleMentions'
import { missingModuleMentionsApi, type MissingModuleMentions as Data } from '../services/missingModuleMentions'
import { queryClient } from '../lib/queryClient'

const data = (overrides: Partial<Data> = {}): Data => ({
  company_id: 42,
  sprint_id: null,
  counts_what: 'what the reviewers wrote, not whether a module was in fact missing.',
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

const TITLE = "Rework requests naming a missing module (reviewers' words)"

function renderTile(sprintId?: number) {
  return render(
    <QueryClientProvider client={queryClient}>
      <MissingModuleMentions companyId={42} sprintId={sprintId} />
    </QueryClientProvider>,
  )
}

describe('MissingModuleMentions', () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => {
    queryClient.clear()
    vi.restoreAllMocks()
  })

  it('is labelled as a claim, dashed, and never as observed', async () => {
    const get = vi.spyOn(missingModuleMentionsApi, 'get').mockResolvedValue(data())
    renderTile()

    const panel = await screen.findByRole('region', { name: TITLE })
    expect(get).toHaveBeenCalledWith(42, { sprintId: undefined })
    expect(panel).toHaveClass('border-dashed')
    expect(within(panel).getByText(/^Claimed: this counts what reviewers wrote/)).toBeInTheDocument()
    expect(within(panel).queryByText(/^Observed/)).not.toBeInTheDocument()
    expect(within(panel).getByRole('img', { name: '5 rework requests naming a missing module' })).toBeInTheDocument()
    expect(within(panel).getByText('Before and after 2026-11-12 12:33 UTC, when the grounded check (#503) merged')).toBeInTheDocument()
  })

  it('shows the share before and after the cutoff', async () => {
    vi.spyOn(missingModuleMentionsApi, 'get').mockResolvedValue(data())
    renderTile()

    const figures = await screen.findByLabelText('Share of rework requests naming a missing module, before and after the cutoff')
    const sides = Array.from(figures.children).map(side => side.textContent)
    expect(sides).toEqual([
      'Before40%4 of 10 rework requestsover 14.52 days',
      'After25%1 of 4 rework requestsover 6.48 days',
    ])
  })

  it('splits the counts by who wrote them, the gate apart', async () => {
    vi.spyOn(missingModuleMentionsApi, 'get').mockResolvedValue(data())
    renderTile()

    const table = await screen.findByRole('table', { name: /by who wrote them/ })
    const rows = within(table).getAllByRole('row').slice(1).map(row => Array.from(row.children).map(cell => cell.textContent))
    expect(rows).toEqual([
      ['The gate', '1 / 2', '0 / 0'],
      ['A person', '0 / 2', '0 / 0'],
      ['An agent reviewer', '3 / 6', '1 / 4'],
    ])
  })

  it('draws a dashed column per week of each side, with the cutoff between them', async () => {
    vi.spyOn(missingModuleMentionsApi, 'get').mockResolvedValue(data())
    renderTile()

    const chart = await screen.findByRole('img', { name: /per ISO week, before and after the cutoff/ })
    const columns = Array.from(chart.querySelectorAll('path'))
    expect(columns.map(column => column.querySelector('title')?.textContent)).toEqual([
      '2026-W44 (Before): 2 of 4 rework requests name a missing module',
      '2026-W45 (Before): 0 of 0 rework requests name a missing module',
      '2026-W46 (Before): 2 of 6 rework requests name a missing module',
      '2026-W46 (After): 1 of 3 rework requests names a missing module',
      '2026-W47 (After): 0 of 1 rework requests name a missing module',
    ])
    expect(columns[0].getAttribute('stroke-dasharray')).toBe('3 2')
    expect(chart.querySelector('line > title')?.textContent).toBe('Cutoff')
  })

  it('gives the weeks as a table for readers without the chart', async () => {
    vi.spyOn(missingModuleMentionsApi, 'get').mockResolvedValue(data())
    renderTile()

    fireEvent.click(await screen.findByText('Show the weeks as a table'))
    const table = screen.getByRole('table', { name: 'Rework requests naming a missing module per ISO week and side of the cutoff' })
    const last = within(table).getAllByRole('row').at(-1)!
    expect(Array.from(last.children).map(cell => cell.textContent)).toEqual(['2026-W47', 'After', '0', '1'])
  })

  it('says when there was no rework request, and names a cutoff that was chosen', async () => {
    const empty = { rework_requests: {}, requests_whose_text_names_missing_module: {} }
    vi.spyOn(missingModuleMentionsApi, 'get').mockResolvedValue(data({
      cutoff: '2026-11-01T00:00:00', cutoff_is_default: false,
      before: { ...data().before, ...empty }, after: { ...data().after, ...empty }, weeks: [],
    }))
    renderTile(7)

    expect(await screen.findByText('No rework request in this period.')).toBeInTheDocument()
    expect(screen.getByText('Before and after 2026-11-01 00:00 UTC')).toBeInTheDocument()
    expect(screen.queryByText('Show the weeks as a table')).not.toBeInTheDocument()
    expect(missingModuleMentionsApi.get).toHaveBeenLastCalledWith(42, { sprintId: 7 })
  })
})
