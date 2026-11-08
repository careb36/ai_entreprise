import { render, screen, within } from '@testing-library/react'
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

  it('names the difficulty alone without a policy', async () => {
    vi.spyOn(routingApi, 'record').mockResolvedValue({ tasks: 4, policy: null, rows: [row({})] })
    renderCard()
    const section = await screen.findByRole('region', { name: 'Difficulty and model' })
    expect(within(section).getByRole('rowheader')).toHaveTextContent(/^2$/)
    expect(within(section).queryByText(/without a reported token count/)).not.toBeInTheDocument()
  })
})
