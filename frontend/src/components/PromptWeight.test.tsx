import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PromptWeightBreakdown, SprintPromptWeight } from './PromptWeight'
import { CostCard } from './CostSummary'
import { costApi, type CostSummary, type PromptWeight } from '../services/cost'
import { queryClient } from '../lib/queryClient'

const weight: PromptWeight = {
  calls: 3,
  tokens: 1600,
  system_tokens: 300,
  estimate: 'chars/4',
  sections: [
    { name: 'repository', chars: 4800, tokens: 1200, share: 0.75, calls: 3, cut: 0 },
    { name: 'lessons', chars: 1600, tokens: 400, share: 0.25, calls: 2, cut: 1 },
  ],
}

const cost: CostSummary = {
  turns: 1, reported_turns: 1, estimated_turns: 0, unavailable_turns: 0,
  reported_tokens: 1500, input_tokens: 1000, output_tokens: 500, estimated_tokens: 0,
  cost_cents: 42, reason: null, unpriced_models: [], models: ['coder'], price_source: 'models.dev',
}

afterEach(() => {
  vi.restoreAllMocks()
  queryClient.clear()
})

describe('PromptWeightBreakdown', () => {
  it('lists the sections heaviest first with their tokens, share and cuts, as an estimate', () => {
    render(<PromptWeightBreakdown weight={weight} />)
    const block = screen.getByTestId('prompt-weight')
    expect(block).toHaveTextContent('3 prompts · 1.6k estimated tokens, plus 300 in the system prompt')
    const items = screen.getAllByRole('listitem')
    expect(items[0]).toHaveTextContent('Repository')
    expect(items[0]).toHaveTextContent('1.2k · 75%')
    expect(items[1]).toHaveTextContent('Lessons')
    expect(items[1]).toHaveTextContent('cut 1×')
    expect(block).toHaveTextContent('characters / 4')
  })

  it('says so when no prompt was measured', () => {
    render(<PromptWeightBreakdown weight={{ ...weight, calls: 0, sections: [] }} />)
    expect(screen.getByText('No prompt measure recorded for these runs.')).toBeInTheDocument()
  })

  it('shows an unknown section by its name', () => {
    render(<PromptWeightBreakdown weight={{ ...weight, sections: [{ ...weight.sections[0], name: 'new_section' }] }} />)
    expect(screen.getByText('new_section')).toBeInTheDocument()
  })
})

describe('CostCard', () => {
  it('folds the prompt weight under the cost when it was measured', () => {
    render(<CostCard cost={cost} title="Cost of this task" weight={weight} />)
    expect(screen.getByText('Prompt weight by section')).toBeInTheDocument()
    expect(screen.getByTestId('prompt-weight')).toBeInTheDocument()
  })

  it('shows no weight without a measure', () => {
    render(<CostCard cost={cost} title="Cost of this task" weight={{ ...weight, calls: 0, sections: [] }} />)
    expect(screen.queryByText('Prompt weight by section')).not.toBeInTheDocument()
  })
})

describe('SprintPromptWeight', () => {
  it('loads the sprint weight only once opened', async () => {
    const sprint = vi.spyOn(costApi, 'sprint').mockResolvedValue({ ...cost, sprint_id: 7, prompt_weight: weight })
    render(
      <QueryClientProvider client={queryClient}>
        <SprintPromptWeight sprintId={7} />
      </QueryClientProvider>,
    )
    expect(sprint).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Show prompt weight' }))
    expect(await screen.findByTestId('prompt-weight')).toBeInTheDocument()
    expect(sprint).toHaveBeenCalledWith(7)
    expect(screen.getByRole('button', { name: 'Hide prompt weight' })).toHaveAttribute('aria-expanded', 'true')
  })
})
