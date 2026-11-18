import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { GateRefutations } from './GateRefutations'
import { GATE_UNCONFIRMED, gateRefutationsApi, type GateRefutations as Data } from '../services/gateRefutations'
import { queryClient } from '../lib/queryClient'

const data = (overrides: Partial<Data> = {}): Data => ({
  company_id: 42,
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

function renderTile(sprintId?: number) {
  return render(
    <QueryClientProvider client={queryClient}>
      <GateRefutations companyId={42} sprintId={sprintId} />
    </QueryClientProvider>,
  )
}

describe('GateRefutations', () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => {
    queryClient.clear()
    vi.restoreAllMocks()
  })

  it('shows the refutations per problem, most frequent first, labelled as observed', async () => {
    const get = vi.spyOn(gateRefutationsApi, 'get').mockResolvedValue(data())
    renderTile()

    const panel = await screen.findByRole('region', { name: 'Deliveries the gate refuted' })
    expect(get).toHaveBeenCalledWith(42, { sprintId: undefined })
    expect(within(panel).getByRole('img', { name: '5 refuted deliveries' })).toBeInTheDocument()
    expect(within(panel).getByText(/^Observed: the Verifier's send-backs/)).toBeInTheDocument()
    expect(within(panel).getByText('From 2026-11-02 to 2026-11-22')).toBeInTheDocument()
    const rows = within(within(panel).getByRole('list', { name: 'Refutations by problem' })).getAllByRole('listitem')
    expect(rows.map(row => row.textContent)).toEqual(['Tests failed3', 'Invented dependency or import2'])
  })

  it('draws one column per ISO week, a week without a send-back included', async () => {
    vi.spyOn(gateRefutationsApi, 'get').mockResolvedValue(data())
    renderTile()

    const chart = await screen.findByRole('img', { name: 'Refuted deliveries per ISO week' })
    const titles = Array.from(chart.querySelectorAll('path > title')).map(title => title.textContent)
    expect(titles).toEqual(['2026-W45: 3 refuted deliveries', '2026-W46: 0 refuted deliveries', '2026-W47: 2 refuted deliveries'])
  })

  it('says the unconfirmed deliveries apart and keeps them out of the refutations', async () => {
    vi.spyOn(gateRefutationsApi, 'get').mockResolvedValue(data())
    renderTile()

    expect(await screen.findByText(/^1 delivery sent back because the daemon never confirmed the write/)).toBeInTheDocument()
    expect(screen.queryByText('Delivery unconfirmed', { selector: 'li span' })).not.toBeInTheDocument()
  })

  it('gives the weeks as a table for readers without the chart', async () => {
    vi.spyOn(gateRefutationsApi, 'get').mockResolvedValue(data())
    renderTile()

    fireEvent.click(await screen.findByText('Show the weeks as a table'))
    const table = screen.getByRole('table', { name: 'Send-backs by the gate per ISO week and problem' })
    const header = within(table).getAllByRole('columnheader').map(cell => cell.textContent)
    expect(header).toEqual(['Week', 'Tests failed', 'Invented dependency or import', 'Delivery unconfirmed', 'Refuted'])
    const last = within(table).getAllByRole('row').at(-1)!
    expect(Array.from(last.children).map(cell => cell.textContent)).toEqual(['2026-W47', '0', '2', '1', '2'])
  })

  it('says when the gate refuted nothing, and shows a problem it does not know by its name', async () => {
    vi.spyOn(gateRefutationsApi, 'get').mockResolvedValueOnce(data({ by_problem: {}, refuted: 0, unconfirmed: 0, weeks: [] }))
    const { unmount } = renderTile()
    expect(await screen.findByText('The gate refuted no delivery in this period.')).toBeInTheDocument()
    expect(screen.queryByText('Show the weeks as a table')).not.toBeInTheDocument()
    unmount()
    queryClient.clear()

    vi.spyOn(gateRefutationsApi, 'get').mockResolvedValueOnce(data({ by_problem: { 'a new problem': 1 }, refuted: 1, unconfirmed: 0 }))
    renderTile(7)
    expect(await screen.findByText('a new problem', { selector: 'li span' })).toBeInTheDocument()
    expect(gateRefutationsApi.get).toHaveBeenLastCalledWith(42, { sprintId: 7 })
  })
})
