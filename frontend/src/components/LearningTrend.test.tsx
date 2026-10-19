import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { LearningTrend } from './LearningTrend'
import { learningTrend } from '../lib/learningTrend'
import type { SprintSummary } from '../services/api'

const sprint = (overrides: Partial<SprintSummary>): SprintSummary => ({
  id: 1, sprint_name: 'S', status: 'completed', goal: null, start_date: null, scope_size: 'S',
  total: 4, done: 4, blocked: 0, rework: 0, ...overrides,
})

const SPRINTS: SprintSummary[] = [
  sprint({ id: 9, sprint_name: 'Hardening', status: 'active', total: 8, done: 3, blocked: 1, rework: 7 }),
  sprint({ id: 7, sprint_name: 'Sprint 2', total: 5, done: 4, blocked: 1, rework: 2 }),
  sprint({ id: 4, sprint_name: 'Sprint 1', total: 4, done: 2, blocked: 2, rework: 4 }),
  sprint({ id: 2, sprint_name: 'Empty', total: 0, done: 0 }),
]

describe('learningTrend', () => {
  it('reads closed sprints with tasks, oldest first, as rework per task and blocked share', () => {
    const points = learningTrend(SPRINTS)
    expect(points.map(p => p.name)).toEqual(['Sprint 1', 'Sprint 2'])
    expect(points[0]).toMatchObject({ reworkPerTask: 1, blockedShare: 0.5 })
    expect(points[1]).toMatchObject({ name: 'Sprint 2', reworkPerTask: 0.4, blockedShare: 0.2 })
  })

  it('has nothing to say before a sprint closes', () => {
    expect(learningTrend([SPRINTS[0]])).toEqual([])
  })
})

describe('LearningTrend', () => {
  it('shows the latest closed sprint, the change since the one before and every sprint in a table', () => {
    render(<LearningTrend sprints={SPRINTS} />)

    const section = screen.getByRole('region', { name: 'Is the company learning?' })
    // The latest values head the tiles and close the table.
    expect(within(section).getAllByText('0.40')).toHaveLength(2)
    expect(within(section).getAllByText('20%')).toHaveLength(2)
    // Lower is better: both dropped since Sprint 1.
    expect(within(section).getByText('−0.60')).toBeInTheDocument()
    expect(within(section).getByText('−30%')).toBeInTheDocument()
    expect(within(section).getAllByText('vs previous sprint')).toHaveLength(2)

    const [rework, blocked] = within(section).getAllByRole('img')
    expect(rework).toHaveAccessibleName('Rework requests per task')
    // Each column names its sprint and value on hover and focus.
    const titles = (chart: HTMLElement) => Array.from(chart.querySelectorAll('title')).map(node => node.textContent)
    expect(titles(rework)).toEqual(['Sprint 1: 1.00', 'Sprint 2: 0.40'])
    expect(titles(blocked)).toEqual(['Sprint 1: 50%', 'Sprint 2: 20%'])

    const table = within(section).getByRole('table', { name: 'Closed sprints, oldest first' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows.map(row => within(row).getByRole('rowheader').textContent)).toEqual(['Sprint 1', 'Sprint 2'])
    expect(rows[1]).toHaveTextContent('0.40')
    expect(rows[1]).toHaveTextContent('20%')
  })

  it('shows one closed sprint without a comparison, and nothing when none closed', () => {
    const { rerender } = render(<LearningTrend sprints={[SPRINTS[0], SPRINTS[2]]} />)
    const section = screen.getByRole('region', { name: 'Is the company learning?' })
    expect(within(section).getAllByText('1.00')).toHaveLength(2)
    expect(within(section).queryByText('vs previous sprint')).not.toBeInTheDocument()
    expect(within(section).queryByRole('img')).not.toBeInTheDocument()

    rerender(<LearningTrend sprints={[SPRINTS[0], SPRINTS[3]]} />)
    expect(screen.queryByRole('region')).not.toBeInTheDocument()
  })
})
