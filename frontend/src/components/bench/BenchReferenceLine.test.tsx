import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { LearningTrend } from '../LearningTrend'
import { BenchReferenceLine } from './BenchReferenceLine'
import { benchReference, latestValidCard } from '../../lib/benchReference'
import type { BenchCard, BenchTotals, BenchValidCard, SprintSummary } from '../../services/api'

const noAdvice = { advised: { total: 0, done: 0, rework: 0 }, unadvised: { total: 0, done: 0, rework: 0 } }
const sprint = (id: number, name: string, rework: number, blocked: number): SprintSummary => ({
  id, sprint_name: name, status: 'completed', goal: null, start_date: null, scope_size: 'S',
  total: 4, done: 4 - blocked, blocked, rework,
  second_attempts: { total: 0, done: 0, briefed: 0, briefed_done: 0 }, console_advice: noAdvice, architect_advice: noAdvice,
})
// Rework per task 0.50 then 0.25; blocked share 25% then 0%.
const SPRINTS = [sprint(1, 'Sprint 1', 2, 1), sprint(2, 'Sprint 2', 1, 0)]

const TOTALS: BenchTotals = {
  specs: 2, specs_completed: 2, tasks: 8, done_first_attempt: 6, first_attempt_rate: 0.75,
  rework_per_task: 0.375, blocked_share: 0.125, wall_clock_seconds: 140,
  criteria: { measured: true, value: { passed: 6, total: 8, rate: 0.75 } },
  tokens: { measured: true, value: 16000, turns_reported: 8, turns_unreported: 0 },
  prompt: { measured: false, reason: 'not_in_card' },
}

function card(stamp: string, totals: Partial<BenchTotals> = {}): BenchValidCard {
  return {
    file: `${stamp}.json`, folder: '', stamp, problems: [], status: 'valid', schema_version: 3,
    commit: 'c'.repeat(40), llm: 'fake', policy: null, run: null, graders_sha: null, sections_off: [],
    has_ablation: false, totals: { ...TOTALS, ...totals }, specs: [], prompt_sections: null, humaneval: null, swebench: null,
  }
}
const INVALID: BenchCard = { file: '20261118T090000Z.json', folder: '', stamp: '20261118T090000Z', problems: ['schema_version'], status: 'invalid' }

function renderTrend(benchCards?: BenchCard[]) {
  return render(<MemoryRouter><LearningTrend sprints={SPRINTS} benchCards={benchCards} /></MemoryRouter>)
}

const tile = (label: string) => screen.getByText(label, { selector: 'p' }).closest('.card') as HTMLElement

describe('benchReference', () => {
  it('reads each metric from the newest valid card, skipping a newer invalid one', () => {
    const cards = [INVALID, card('20261114T201804Z'), card('20261108T140751Z', { rework_per_task: 9 })]
    expect(latestValidCard(cards)?.stamp).toBe('20261114T201804Z')
    expect(benchReference(cards, 'reworkPerTask')).toEqual({ value: 0.375, stamp: '20261114T201804Z' })
    expect(benchReference(cards, 'blockedShare')?.value).toBe(0.125)
    expect(benchReference(cards, 'passRate')?.value).toBe(0.75)
    expect(benchReference(cards, 'tokensPerTask')?.value).toBe(2000)
  })

  it('has no reference for a metric the card did not measure, never a 0', () => {
    const cards = [card('20261114T201804Z', {
      criteria: { measured: false, reason: 'grade_unmeasured' },
      tokens: { measured: false, reason: 'no_reported_turns', turns_reported: 0, turns_unreported: 3 },
    })]
    expect(benchReference(cards, 'passRate')).toBeNull()
    expect(benchReference(cards, 'tokensPerTask')).toBeNull()
    expect(benchReference(cards, 'reworkPerTask')?.value).toBe(0.375)
  })

  it('has no per-task reference from a card that ran no task', () => {
    const cards = [card('20261114T201804Z', { tasks: 0, rework_per_task: 0, blocked_share: 0 })]
    expect(benchReference(cards, 'reworkPerTask')).toBeNull()
    expect(benchReference(cards, 'blockedShare')).toBeNull()
    expect(benchReference(cards, 'tokensPerTask')).toBeNull()
  })

  it('has no reference without a valid card', () => {
    expect(benchReference(undefined, 'reworkPerTask')).toBeNull()
    expect(benchReference([], 'reworkPerTask')).toBeNull()
    expect(benchReference([INVALID], 'reworkPerTask')).toBeNull()
  })
})

describe('the bench reference on the Sprints tiles', () => {
  it('draws the measured value under each tile, named as the bench reference, dated and linked to /bench', () => {
    renderTrend([INVALID, card('20261114T201804Z')])

    const rework = within(tile('Rework requests per task'))
    expect(rework.getByText('Bench reference: 0.38')).toBeInTheDocument()
    expect(rework.getByText(/scorecard of Nov 14, 2026/)).toBeInTheDocument()
    expect(rework.getByRole('link', { name: 'See the bench' })).toHaveAttribute('href', '/bench')
    expect(rework.getByTestId('bench-reference-rule')).toBeInTheDocument()

    const blocked = within(tile('Share of blocked tasks'))
    expect(blocked.getByText('Bench reference: 13%')).toBeInTheDocument()
  })

  it('draws no line on a tile whose number the card did not measure', () => {
    renderTrend([card('20261114T201804Z', { tasks: 0, rework_per_task: 0, blocked_share: 0 })])
    expect(screen.queryByTestId('bench-reference')).not.toBeInTheDocument()
    expect(screen.queryByTestId('bench-reference-rule')).not.toBeInTheDocument()
    expect(screen.queryByText(/Bench reference/)).not.toBeInTheDocument()
  })

  it('draws nothing when no valid card exists', () => {
    renderTrend([INVALID])
    expect(screen.queryByTestId('bench-reference')).not.toBeInTheDocument()
    expect(screen.queryByTestId('bench-reference-rule')).not.toBeInTheDocument()
    renderTrend(undefined)
    expect(screen.queryByTestId('bench-reference')).not.toBeInTheDocument()
  })

  it('renders nothing for a null reference', () => {
    const { container } = render(<MemoryRouter><BenchReferenceLine reference={null} format={String} /></MemoryRouter>)
    expect(container).toBeEmptyDOMElement()
  })
})
