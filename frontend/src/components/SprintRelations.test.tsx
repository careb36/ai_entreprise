import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SprintRelations } from './SprintRelations'
import { queryClient } from '../lib/queryClient'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const relation = (id: number, extra: Record<string, unknown>) => ({
  id, relation: 'blocked_by', stored_kind: 'blocked_by', task_id: 10, related_task_id: 11,
  other_task: { id: 11, title: 'Schema', status: 'todo', sprint_id: 3 }, state: 'confirmed',
  proposed_by: 'Architect', proposed_role: 'Architect', confirmed_by: 'person', confirmed_at: null, drives_dispatch: true, created_at: null, ...extra,
})

const body = {
  sprint_id: 3,
  relations: [
    relation(1, {}),
    relation(2, { relation: 'duplicate', stored_kind: 'duplicate', task_id: 20, related_task_id: 12, other_task: { id: 20, title: null, status: 'done', sprint_id: 1, sprint_name: 'Sprint 1' }, state: 'proposed', drives_dispatch: false }),
  ],
  metrics: { total: 2, proposed: 1, confirmed: 1, by_kind: { blocked_by: 1, duplicate: 1, relates_to: 0 }, duplicates_delivered: 1 },
}

function renderPanel() {
  return render(<QueryClientProvider client={queryClient}><MemoryRouter><SprintRelations sprintId={3} /></MemoryRouter></QueryClientProvider>)
}

describe('SprintRelations', () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => {
    vi.unstubAllGlobals()
    queryClient.clear()
  })

  it('reads nothing until opened, then lists each relation from its sprint task with its state', async () => {
    const fetch = vi.fn(async (_url: string) => response(200, body))
    vi.stubGlobal('fetch', fetch)
    renderPanel()
    expect(fetch).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Show' }))
    const table = await screen.findByRole('table', { name: 'Task relations' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(within(rows[0]).getByRole('link', { name: 'task #10' })).toHaveAttribute('href', '/sprints/3/tasks/10')
    expect(within(rows[0]).getByText('Blocked by')).toBeInTheDocument()
    expect(within(rows[0]).getByRole('link', { name: 'task #11: Schema' })).toHaveAttribute('href', '/sprints/3/tasks/11')
    expect(within(rows[0]).getByText('Confirmed')).toBeInTheDocument()
    // The second is read from task 12, its sprint task; the other is in another sprint, and the relation is a claim.
    expect(within(rows[1]).getByRole('link', { name: 'task #12' })).toBeInTheDocument()
    expect(within(rows[1]).getByText('in Sprint 1')).toBeInTheDocument()
    expect(within(rows[1]).getByText('Proposed')).toBeInTheDocument()
    expect(screen.getByText('1 confirmed duplicate was delivered twice.')).toBeInTheDocument()
    expect(String(fetch.mock.calls[0][0])).toContain('/v1/sprints/3/relations')
  })
})
