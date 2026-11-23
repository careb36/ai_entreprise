import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TaskComments } from './TaskComments'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'
import type { BacklogItem } from '../services/api'

function task(status: BacklogItem['status'], comments: BacklogItem['comments'] = []): BacklogItem {
  return {
    id: 12, sprint_id: 3, title: 'Login', status, priority: 'medium', comments,
    created_at: '2026-11-20T00:00:00', updated_at: '2026-11-20T00:00:00',
  }
}

function patched() {
  return vi.fn(async (_url: string, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body ?? '{}'))
    return { ok: true, status: 200, json: async () => ({ id: 12, sprint_id: 3, status: body.status ?? 'blocked', comments: [] }) } as Response
  })
}

function renderAs(role: 'Owner' | 'Manager' | 'Viewer', item: BacklogItem) {
  useAuthStore.setState({ expiresAt: Date.now() + 60_000, memberships: [{ company_id: 4, human_role: role }] })
  return render(
    <QueryClientProvider client={queryClient}>
      <TaskComments task={item} />
    </QueryClientProvider>,
  )
}

function sent(fetch: ReturnType<typeof patched>) {
  const call = fetch.mock.calls.find(([, init]) => init?.method === 'PATCH')
  return JSON.parse(String(call?.[1]?.body))
}

describe('TaskComments', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 4 })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it("shows a viewer the people's comments only, with no form", () => {
    renderAs('Viewer', task('blocked', [
      { author: 'Developer', text: 'a deliverable', created_at: '2026-11-20T01:00:00' },
      { author: 'user', text: 'Look at the login first', created_at: '2026-11-20T02:00:00' },
    ]))
    expect(screen.getByText('Look at the login first')).toBeInTheDocument()
    expect(screen.queryByText('a deliverable')).not.toBeInTheDocument()
    expect(screen.queryByRole('form', { name: 'Add a comment' })).not.toBeInTheDocument()
  })

  it('names who wrote each comment, and an unidentified one as such', () => {
    renderAs('Viewer', task('blocked', [
      { author: 'user', text: 'Signed in', created_at: '2026-11-20T02:00:00', by: 'Ada Owner', identified: true },
      { author: 'user', text: 'From a script', created_at: '2026-11-20T03:00:00', by: null, identified: false },
    ]))
    expect(screen.getByText(/Ada Owner · 2026-11-20 02:00/)).toBeInTheDocument()
    expect(screen.getByText(/Unidentified \(no signed-in person\) · 2026-11-20 03:00/)).toBeInTheDocument()
  })

  it('adds a comment', async () => {
    const fetch = patched()
    vi.stubGlobal('fetch', fetch)
    renderAs('Manager', task('todo'))
    fireEvent.change(screen.getByLabelText('Comment'), { target: { value: '  Ship it small  ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add comment' }))
    await waitFor(() => expect(screen.getByLabelText('Comment')).toHaveValue(''))
    expect(sent(fetch)).toEqual({ comment: 'Ship it small' })
  })

  it('discards a task only with a reason, sent as the comment', async () => {
    const fetch = patched()
    vi.stubGlobal('fetch', fetch)
    renderAs('Owner', task('blocked'))
    const discard = screen.getByRole('button', { name: 'Discard task' })
    expect(discard).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Comment'), { target: { value: 'Overlaps another feature' } })
    fireEvent.click(discard)
    await waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(sent(fetch)).toEqual({ status: 'discarded', comment: 'Overlaps another feature' })
  })

  it('offers no discard for a task a worker may hold', () => {
    renderAs('Manager', task('in_progress'))
    expect(screen.queryByRole('button', { name: 'Discard task' })).not.toBeInTheDocument()
  })

  it('says a discarded task is not run and reopens it to To do', async () => {
    const fetch = patched()
    vi.stubGlobal('fetch', fetch)
    renderAs('Manager', task('discarded', [{ author: 'user', text: 'Not needed', created_at: '2026-11-20T02:00:00' }]))
    expect(screen.getByText(/Discarded by a person/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Discard task' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Reopen (move to To do)' }))
    await waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(sent(fetch)).toEqual({ status: 'todo' })
  })
})
