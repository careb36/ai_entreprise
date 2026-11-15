import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TaskRelations } from './TaskRelations'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const RELATIONS = [
  {
    id: 3, relation: 'duplicate', stored_kind: 'duplicate', task_id: 12, related_task_id: 40,
    other_task: { id: 40, title: 'Sign-in page', status: 'done', sprint_id: 9, sprint_name: 'Sprint 9' },
    state: 'proposed', proposed_by: 'QA', proposed_role: 'QA', confirmed_by: null, confirmed_at: null,
    drives_dispatch: false, created_at: '2026-11-15T10:00:00Z',
  },
  {
    id: 4, relation: 'relates_to', stored_kind: 'relates_to', task_id: 12, related_task_id: 7,
    other_task: { id: 7, title: 'Login page', status: 'blocked', sprint_id: 5, sprint_name: 'Sprint 5' },
    state: 'confirmed', proposed_by: 'system', proposed_role: 'system', confirmed_by: 'rule:carry_over',
    confirmed_at: '2026-11-15T09:00:00Z', drives_dispatch: false, created_at: '2026-11-15T09:00:00Z',
  },
  {
    id: 5, relation: 'blocked_by', stored_kind: 'blocked_by', task_id: 12, related_task_id: 13,
    other_task: { id: 13, title: 'Store', status: 'todo', sprint_id: 8, sprint_name: 'Sprint 8' },
    state: 'confirmed', proposed_by: 'lead@example.com', proposed_role: 'person', confirmed_by: 'lead@example.com',
    confirmed_at: '2026-11-15T09:30:00Z', drives_dispatch: true, created_at: '2026-11-15T09:30:00Z',
  },
]
const BODY = { task_id: 12, relations: RELATIONS, names: ['blocked_by', 'blocking', 'duplicate', 'relates_to'] }

function renderRelations(role: 'Viewer' | 'Manager') {
  useAppStore.setState({ companyId: 1 })
  useAuthStore.setState({ memberships: [{ company_id: 1, human_role: role }] } as never)
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <TaskRelations taskId={12} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function item(text: string) {
  return screen.getAllByRole('listitem').find(li => li.textContent?.includes(text))!
}

describe('TaskRelations', () => {
  beforeEach(() => {
    queryClient.clear()
    useAuthStore.setState({ expiresAt: Date.now() + 60_000 })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('lists each relation from this task’s side with its state, read-only for a Viewer', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(200, BODY)))
    renderRelations('Viewer')

    await screen.findByRole('list', { name: 'Relations to other tasks' })
    const duplicate = item('Sign-in page')
    expect(duplicate).toHaveTextContent('Proposed')
    expect(duplicate).toHaveTextContent('Proposed by QA')
    expect(within(duplicate).getByRole('link')).toHaveAttribute('href', '/sprints/9/tasks/40')

    const carried = item('Login page')
    expect(carried).toHaveTextContent('Relates to')
    expect(carried).toHaveTextContent('Confirmed')
    expect(carried).toHaveTextContent('Proposed by the System · confirmed by the carry_over rule')

    expect(item('Store')).toHaveTextContent('Waits for it before it starts')
    expect(screen.queryByRole('button', { name: 'Confirm' })).not.toBeInTheDocument()
    expect(screen.queryByRole('form', { name: 'Relate to another task' })).not.toBeInTheDocument()
  })

  it('lets a Manager confirm, reject and remove', async () => {
    const fetch = vi.fn().mockImplementation(async (_url: string, init?: RequestInit) =>
      response(200, init?.method === 'POST' ? { removed: true } : BODY))
    vi.stubGlobal('fetch', fetch)
    renderRelations('Manager')

    await screen.findByRole('list', { name: 'Relations to other tasks' })
    fireEvent.click(within(item('Sign-in page')).getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(fetch.mock.calls.some(([url, init]) =>
      String(url).endsWith('/v1/task_relations/3/confirm') && init?.method === 'POST')).toBe(true))

    fireEvent.click(within(item('Sign-in page')).getByRole('button', { name: 'Reject' }))
    await waitFor(() => expect(fetch.mock.calls.some(([url]) =>
      String(url).endsWith('/v1/task_relations/3/reject'))).toBe(true))

    expect(within(item('Login page')).queryByRole('button', { name: 'Confirm' })).not.toBeInTheDocument()
    fireEvent.click(within(item('Login page')).getByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(fetch.mock.calls.some(([url]) =>
      String(url).endsWith('/v1/task_relations/4/remove'))).toBe(true))
  })

  it('adds a relation by task number, confirmed when asked', async () => {
    const fetch = vi.fn().mockResolvedValue(response(200, { ...BODY, relations: [] }))
    vi.stubGlobal('fetch', fetch)
    renderRelations('Manager')

    await screen.findByText('No related tasks.')
    const form = screen.getByRole('form', { name: 'Relate to another task' })
    const submit = within(form).getByRole('button', { name: 'Add relation' })
    expect(submit).toBeDisabled()
    fireEvent.change(within(form).getByLabelText('Relation'), { target: { value: 'blocking' } })
    fireEvent.change(within(form).getByLabelText('Task #'), { target: { value: '41' } })
    fireEvent.click(within(form).getByLabelText('Confirm now'))
    fireEvent.click(submit)

    await waitFor(() => {
      const call = fetch.mock.calls.find(([url, init]) =>
        String(url).endsWith('/v1/tasks/12/relations') && init?.method === 'POST')
      expect(call).toBeDefined()
      expect(JSON.parse(String(call![1].body))).toEqual({ related_task_id: 41, relation: 'blocking', confirm: true })
    })
  })

  it('shows an error with a retry', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(500, { detail: 'boom' })))
    renderRelations('Viewer')
    expect(await screen.findByRole('alert', {}, { timeout: 5000 })).toHaveTextContent('Could not load the related tasks.')
  })
})
