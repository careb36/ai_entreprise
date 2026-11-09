import { render, screen } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CostLine, RunCostCard, TaskCostCard } from './CostSummary'
import { costApi, formatCostCents, formatTokens, type CostSummary } from '../services/cost'
import { queryClient } from '../lib/queryClient'

const base: CostSummary = {
  turns: 1, reported_turns: 1, estimated_turns: 0, unavailable_turns: 0,
  reported_tokens: 1500, input_tokens: 1000, output_tokens: 500, estimated_tokens: 0,
  cost_cents: 42, reason: null, unpriced_models: [], models: ['coder'], price_source: 'models.dev',
}

describe('formatting', () => {
  it('formats cents as dollars, small amounts with more decimals, and keeps unknown unknown', () => {
    expect(formatCostCents(42)).toBe('$0.42')
    expect(formatCostCents(12345)).toBe('$123.45')
    expect(formatCostCents(0)).toBe('$0.00')
    expect(formatCostCents(0.12)).toBe('$0.0012')
    expect(formatCostCents(null)).toBeNull()
    expect(formatTokens(228700)).toBe('228.7k')
    expect(formatTokens(2_500_000)).toBe('2.5M')
    expect(formatTokens(12)).toBe('12')
  })
})

describe('CostLine', () => {
  it('shows the cost and the reported tokens in and out', () => {
    render(<CostLine cost={base} />)
    expect(screen.getByTestId('cost-line')).toHaveTextContent('$0.42')
    expect(screen.getByTestId('cost-line')).toHaveTextContent('1.0k in · 500 out tokens')
  })

  it('says price unknown with the reason, never $0.00', () => {
    render(<CostLine cost={{ ...base, cost_cents: null, reason: 'price_ambiguous', unpriced_models: ['split'] }} />)
    const line = screen.getByTestId('cost-line')
    expect(line).toHaveTextContent('price unknown (model listed at several prices)')
    expect(line).not.toHaveTextContent('$')
    expect(screen.getByTitle('split')).toBeInTheDocument()
  })

  it('labels estimated tokens apart and shows a total when the split was not recorded', () => {
    render(<CostLine cost={{
      ...base, cost_cents: null, reason: 'token_split_unknown', input_tokens: 0, output_tokens: 0,
      estimated_tokens: 900, estimated_turns: 1,
    }} />)
    const line = screen.getByTestId('cost-line')
    expect(line).toHaveTextContent('price unknown (input/output split not recorded)')
    expect(line).toHaveTextContent('1.5k reported tokens')
    expect(line).toHaveTextContent('900 estimated tokens, not priced')
  })

  it('says when no model call was recorded', () => {
    render(<CostLine cost={{ ...base, turns: 0, reported_turns: 0, reported_tokens: 0, input_tokens: 0, output_tokens: 0, cost_cents: null, reason: 'no_turns', models: [] }} />)
    expect(screen.getByTestId('cost-line')).toHaveTextContent('no model calls recorded')
  })
})

describe('cost cards', () => {
  afterEach(() => {
    queryClient.clear()
    vi.restoreAllMocks()
  })

  it("loads a run's cost for its company and a task's cost", async () => {
    const run = vi.spyOn(costApi, 'run').mockResolvedValue({ ...base, run_id: 3, version: 2 })
    const task = vi.spyOn(costApi, 'task').mockResolvedValue({ ...base, task_id: 5, cost_cents: 150, runs: [] })
    render(
      <QueryClientProvider client={queryClient}>
        <RunCostCard companyId={42} runId={3} />
        <TaskCostCard taskId={5} />
      </QueryClientProvider>,
    )
    expect(await screen.findByRole('region', { name: 'Cost of this run' })).toHaveTextContent('$0.42')
    expect(await screen.findByRole('region', { name: 'Cost of this task' })).toHaveTextContent('$1.50')
    expect(run).toHaveBeenCalledWith(42, 3)
    expect(task).toHaveBeenCalledWith(5)
  })
})
