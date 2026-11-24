import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { DndContext } from '@dnd-kit/core'
import { SortableContext } from '@dnd-kit/sortable'
import { describe, expect, it, vi } from 'vitest'
import type { BacklogItem, ObservedFacts } from '../../services/api'
import { TaskCard } from './TaskCard'

const NOTHING_OBSERVED: ObservedFacts = {
  run_id: null, verdict: null, risk_tier: null, risk_areas: [], protected_paths: [], waits_for_person: false, hold: null,
}

function card(status: BacklogItem['status'], observed: Partial<ObservedFacts>, extra: Partial<BacklogItem> = {}) {
  const item: BacklogItem = {
    id: 31, sprint_id: 7, title: 'Align the README', status, priority: 'high', assignee: 'Developer',
    comments: [], created_at: '2026-11-24T09:56:26Z', updated_at: '2026-11-24T10:51:01Z',
    observed: { ...NOTHING_OBSERVED, ...observed }, ...extra,
  }
  return render(
    <MemoryRouter>
      <DndContext>
        <SortableContext items={[item.id]}>
          <TaskCard item={item} sprintId={7} density="comfortable" onStatusChange={vi.fn()} updating={false} keyboardHintId="k" />
        </SortableContext>
      </DndContext>
    </MemoryRouter>,
  )
}

describe('what holds a card back, from its record', () => {
  it('a task blocked by a dependency waits for that task, not for a person, and is not shown as stuck', () => {
    const { container } = card('blocked', { hold: { kind: 'dependency', waiting_for: [30] }, waits_for_person: false }, { waiting_for: [30] })
    expect(screen.getByText('Waits for #30')).toBeInTheDocument()
    expect(screen.queryByText('Needs a person')).not.toBeInTheDocument()
    expect(container.querySelector('article')?.className).toContain('border-l-amber-500')
    expect(container.querySelector('article')?.className).not.toContain('border-l-red-500')
  })

  it('a to-do task waiting for a dependency says so, and nothing says it is in progress', () => {
    card('todo', { hold: { kind: 'dependency', waiting_for: [34, 35] } }, { waiting_for: [34, 35] })
    expect(screen.getByText('Waits for #34, #35')).toBeInTheDocument()
    expect(screen.getByRole('combobox')).toHaveValue('todo')
  })

  it('a risk hold says a person accepts the risk', () => {
    const { container } = card('blocked', { hold: { kind: 'risk_hold', reason: 'high tier' }, waits_for_person: true })
    const chip = container.querySelector('[data-hold="risk_hold"]') as HTMLElement
    expect(chip).toHaveTextContent('Risk hold')
    expect(chip.getAttribute('title')).toContain('Approved, held for a person to accept the risk.')
    expect(chip.getAttribute('title')).toContain('high tier')
  })

  it('names a retry the worker runs on its own', () => {
    const { container } = card('blocked', { hold: { kind: 'retry_pending', reason: 'model failed' } })
    expect(screen.getByText('Retry pending')).toBeInTheDocument()
    expect(container.querySelector('article')?.className).toContain('border-l-sky-500')
  })

  it('names a freeze and the run window', () => {
    card('blocked', { hold: { kind: 'frozen' }, waits_for_person: true })
    expect(screen.getByText('Frozen')).toBeInTheDocument()
  })

  it('names the run window hold', () => {
    card('todo', { hold: { kind: 'run_window' } })
    expect(screen.getByText('Held for the run window')).toBeInTheDocument()
  })

  it('a task blocked for a person says so', () => {
    card('blocked', { hold: { kind: 'person', reason: 'Tests failed' }, waits_for_person: true })
    expect(screen.getByText('Waits for a person')).toBeInTheDocument()
  })
})
