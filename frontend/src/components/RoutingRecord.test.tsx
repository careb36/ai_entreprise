import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { RoutingRecord } from './RoutingRecord'
import { routingApi, type RoutingRow } from '../services/routing'
import { queryClient } from '../lib/queryClient'

const row = (overrides: Partial<RoutingRow>): RoutingRow => ({
  difficulty: 2, band: null, model: 'cheap-model', tasks: 4, done: 3, done_first_attempt: 2, blocked: 1, rework: 3,
  rework_per_task: 0.75, blocked_share: 0.25, first_attempt_rate: 0.5,
  tokens: { reported: 1200, turns_reported: 6, turns_unreported: 0 }, ...overrides,
})

function renderCard() {
  return render(
    <QueryClientProvider client={queryClient}>
      <RoutingRecord companyId={42} />
    </QueryClientProvider>,
  )
}

describe('RoutingRecord', () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => {
    queryClient.clear()
    vi.restoreAllMocks()
  })

  it('shows each difficulty and model with first attempts, rework, blocked share and reported tokens', async () => {
    const record = vi.spyOn(routingApi, 'record').mockResolvedValue({
      tasks: 6,
      policy: { '1-3': 'cheap-model', '4-5': 'strong-model' },
      rows: [
        row({ band: '1-3' }),
        row({ difficulty: 4, band: '4-5', model: 'strong-model', tasks: 1, done: 1, done_first_attempt: 1, blocked: 0, rework: 0, rework_per_task: 0, blocked_share: 0, first_attempt_rate: 1, tokens: { reported: 900, turns_reported: 2, turns_unreported: 1 } }),
        row({ difficulty: 5, band: '4-5', model: null, tasks: 1, done: 0, done_first_attempt: 0, blocked: 1, rework: 0, rework_per_task: 0, blocked_share: 1, first_attempt_rate: 0, tokens: { reported: 0, turns_reported: 0, turns_unreported: 0 } }),
      ],
    })
    renderCard()

    const section = await screen.findByRole('region', { name: 'Difficulty and model' })
    expect(record).toHaveBeenCalledWith(42)
    const table = within(section).getByRole('table', { name: 'Tasks by difficulty and model' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows.map(r => within(r).getByRole('rowheader').textContent)).toEqual(['2 (band 1-3)', '4 (band 4-5)', '5 (band 4-5)'])
    expect(rows[0]).toHaveTextContent('cheap-model')
    expect(rows[0]).toHaveTextContent('50% (2; 3 done)')
    expect(rows[0]).toHaveTextContent('0.75')
    expect(rows[0]).toHaveTextContent('25%')
    expect(rows[0]).toHaveTextContent('1200')
    expect(rows[2]).toHaveTextContent('no delivery')
    // Estimated or missing counts are said apart, never added to the tokens.
    expect(within(section).getByText('1 turn without a reported token count, left out of the tokens.')).toBeInTheDocument()
  })

  it('shows nothing before a sprint has closed', async () => {
    vi.spyOn(routingApi, 'record').mockResolvedValue({ tasks: 0, policy: null, rows: [] })
    const { container } = renderCard()
    await vi.waitFor(() => expect(routingApi.record).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('splits done, rework and cost by band, by served model and by policy', async () => {
    const numbers = (overrides: Partial<RoutingRow>) => {
      const { difficulty: _d, band: _b, model: _m, ...rest } = row(overrides)
      return rest
    }
    vi.spyOn(routingApi, 'record').mockResolvedValue({
      tasks: 6,
      policy: { low: 'cheap-model', high: 'strong-model' },
      policy_label: 'cheap-easy',
      policy_source: 'company',
      rows: [row({ difficulty_band: 'low' })],
      by_difficulty_band: [
        { difficulty_band: 'low', ...numbers({ tasks: 4, done: 3, cost_cents: 42, cost_unknown: 1 }) },
        { difficulty_band: 'high', ...numbers({ tasks: 2, done: 1, rework_per_task: 1.5, cost_cents: null, cost_unknown: 2 }) },
      ],
      by_model: [
        { model: 'cheap-model', ...numbers({ tasks: 4, done: 3, cost_cents: 42, cost_unknown: 0 }) },
        { model: null, ...numbers({ tasks: 2, done: 0, cost_cents: null, cost_unknown: 2 }) },
      ],
      by_policy: [
        { policy: 'cheap-easy', ...numbers({ tasks: 5 }) },
        { policy: null, ...numbers({ tasks: 1 }) },
      ],
    })
    renderCard()
    const section = await screen.findByRole('region', { name: 'Difficulty and model' })

    const byBand = within(section).getByRole('table', { name: 'Tasks by difficulty band' })
    const bandRows = within(byBand).getAllByRole('row').slice(1)
    expect(bandRows.map(r => within(r).getByRole('rowheader').textContent)).toEqual(['Low', 'High'])
    expect(bandRows[0]).toHaveTextContent('$0.42')
    expect(bandRows[0]).toHaveTextContent('(+1 task unpriced)')
    // A cost that is not known is said, never shown as zero.
    expect(bandRows[1]).toHaveTextContent('unknown')
    expect(bandRows[1]).toHaveTextContent('1.50')

    fireEvent.click(within(section).getByRole('button', { name: 'By served model' }))
    const byModel = within(section).getByRole('table', { name: 'Tasks by served model' })
    const modelRows = within(byModel).getAllByRole('row').slice(1)
    expect(modelRows.map(r => within(r).getByRole('rowheader').textContent)).toEqual(['cheap-model', 'no delivery'])
    expect(within(section).getByRole('button', { name: 'By served model' })).toHaveAttribute('aria-pressed', 'true')

    fireEvent.click(within(section).getByRole('button', { name: 'By policy' }))
    const byPolicy = within(section).getByRole('table', { name: 'Tasks by routing policy' })
    expect(within(byPolicy).getAllByRole('rowheader').map(h => h.textContent)).toEqual(['cheap-easy', 'no policy'])
  })

  it('offers no policy split when no task ran under one', async () => {
    const { difficulty: _d, band: _b, model: _m, ...rest } = row({})
    vi.spyOn(routingApi, 'record').mockResolvedValue({
      tasks: 4, policy: null, rows: [row({})],
      by_difficulty_band: [{ difficulty_band: 'low', ...rest }],
      by_model: [{ model: 'cheap-model', ...rest }],
      by_policy: [{ policy: null, ...rest }],
    })
    renderCard()
    const section = await screen.findByRole('region', { name: 'Difficulty and model' })
    expect(within(section).getByRole('button', { name: 'By band' })).toBeInTheDocument()
    expect(within(section).queryByRole('button', { name: 'By policy' })).not.toBeInTheDocument()
  })

  it('names the difficulty alone without a policy', async () => {
    vi.spyOn(routingApi, 'record').mockResolvedValue({ tasks: 4, policy: null, rows: [row({})] })
    renderCard()
    const section = await screen.findByRole('region', { name: 'Difficulty and model' })
    expect(within(section).getByRole('rowheader')).toHaveTextContent(/^2$/)
    expect(within(section).queryByText(/without a reported token count/)).not.toBeInTheDocument()
  })
})
