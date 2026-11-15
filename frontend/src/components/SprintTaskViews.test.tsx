import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SprintTaskViews } from './SprintTaskViews'
import { queryClient } from '../lib/queryClient'
import { useAuthStore } from '../store/useAuthStore'

vi.mock('./KanbanBoard', () => ({
  default: ({ sprintId }: { sprintId: string }) => <div>Kanban for sprint {sprintId}</div>,
}))

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const VIEWS = {
  views: [{
    id: 4, company_id: 1, name: 'Refuted', filters: { and: [{ field: 'verdict', op: 'eq', value: 'refuted' }] },
    group_by: null, order_by: null, layout: 'list', visible_columns: ['title', 'verdict'], access: 'company',
    locked: true, owner_name: 'lead', mine: false, can_edit: false, valid: true, invalid_reason: null,
    updated_by: null, created_at: null, updated_at: null,
  }],
  fields: {}, group_fields: [], order_fields: [], columns: [], can_share: false,
}
const TASKS = {
  count: 1,
  tasks: [{
    id: 31, sprint_id: 8, title: 'Fix the login', status: 'blocked', priority: 'high', assignee: 'Developer',
    assignee_role: 'Developer', kind: null, covers: [], updated_at: null, verdict: 'refuted', risk_tier: 'high',
    producer: 'le-fabrique-task-worker', rework_count: 2, waits_for_person: true,
  }],
}

function renderViews() {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter><SprintTaskViews sprintId={8} companyId={1} /></MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('SprintTaskViews', () => {
  beforeEach(() => {
    queryClient.clear()
    useAuthStore.setState({ expiresAt: Date.now() + 60_000 })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('keeps the kanban until a filter or the list layout is chosen', async () => {
    const fetch = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url.includes('/views') && init?.method === 'POST') return Promise.resolve(response(201, { ...VIEWS.views[0], id: 5, name: 'Mine', access: 'private', locked: false, mine: true, can_edit: true }))
      if (url.includes('/views')) return Promise.resolve(response(200, VIEWS))
      return Promise.resolve(response(200, TASKS))
    })
    vi.stubGlobal('fetch', fetch)
    renderViews()
    expect(screen.getByText('Kanban for sprint 8')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'List' }))
    expect(await screen.findByRole('link', { name: 'Fix the login' })).toHaveAttribute('href', '/sprints/8/tasks/31')
    expect(screen.getByText('1 task matches')).toBeInTheDocument()
    const taskCall = fetch.mock.calls.map(([u]) => new URL(u)).find(u => u.pathname === '/v1/tasks')!
    expect(taskCall.searchParams.get('company_id')).toBe('1')
    expect(JSON.parse(taskCall.searchParams.get('filter')!)).toEqual({ and: [{ field: 'sprint_id', op: 'eq', value: 8 }] })

    // A saved company view loads its filter; a Viewer cannot share a new one.
    await screen.findByRole('option', { name: 'Refuted · Company · locked' })
    fireEvent.change(screen.getByRole('combobox', { name: 'Saved view' }), { target: { value: '4' } })
    await waitFor(() => expect(fetch.mock.calls.some(([u]) => String(u).includes('refuted'))).toBe(true))
    expect(screen.getByRole('option', { name: 'Company' })).toBeDisabled()

    fireEvent.change(screen.getByRole('textbox', { name: 'View name' }), { target: { value: 'Mine' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Shared with' }), { target: { value: 'private' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save view' }))
    await screen.findByText('View "Mine" saved.')
    const [saveUrl, saveInit] = fetch.mock.calls.find(([, init]) => init?.method === 'POST')!
    expect(new URL(saveUrl).pathname).toBe('/v1/companies/1/views')
    expect(JSON.parse(saveInit.body)).toMatchObject({
      name: 'Mine', access: 'private', layout: 'list',
      filters: { and: [{ field: 'verdict', op: 'eq', value: 'refuted' }] },
    })
  })
})
