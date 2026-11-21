import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TaskEditForm } from './TaskEditForm'
import { queryClient } from '../lib/queryClient'
import type { BacklogItem } from '../services/api'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body, headers: new Headers() } as unknown as Response
}

const task = { id: 12, title: 'Reject API tokens and add an audit view', description: 'Too broad.', status: 'blocked' } as BacklogItem

function renderForm(item: BacklogItem = task) {
  return render(<QueryClientProvider client={queryClient}><TaskEditForm task={item} sprintId={3} /></QueryClientProvider>)
}

describe('TaskEditForm', () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => vi.unstubAllGlobals())

  it('sends the changed fields, the reason and the send-back choice', async () => {
    const fetch = vi.fn(async () => response(200, { ...task, title: 'Only the SSO check', changed: ['title'], event_id: 9 }))
    vi.stubGlobal('fetch', fetch)
    renderForm()
    fireEvent.click(screen.getByRole('button', { name: /edit task/i }))
    const save = screen.getByRole('button', { name: /save/i })
    expect(save).toBeDisabled()
    fireEvent.change(screen.getByLabelText(/title/i), { target: { value: 'Only the SSO check' } })
    expect(save).toBeDisabled() // no reason yet
    fireEvent.change(screen.getByLabelText(/why/i), { target: { value: 'Narrowed after three attempts' } })
    expect(screen.getByRole('checkbox')).toBeChecked() // a blocked task goes back to work by default
    fireEvent.click(save)
    await waitFor(() => expect(fetch).toHaveBeenCalled())
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toContain('/v1/backlog/12/content')
    expect(init.method).toBe('PUT')
    expect(JSON.parse(String(init.body))).toEqual({
      title: 'Only the SSO check', reason: 'Narrowed after three attempts', send_back: true,
    })
    await waitFor(() => expect(screen.getByRole('button', { name: /edit task/i })).toBeInTheDocument())
  })

  it('shows why the backend refused', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response(409, { detail: 'A task in in_progress cannot be edited' })))
    renderForm()
    fireEvent.click(screen.getByRole('button', { name: /edit task/i }))
    fireEvent.change(screen.getByLabelText(/description/i), { target: { value: 'Smaller.' } })
    fireEvent.change(screen.getByLabelText(/why/i), { target: { value: 'Narrowed after three attempts' } })
    fireEvent.click(screen.getByRole('button', { name: /save/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent('cannot be edited')
  })

  it('offers no edit while an agent works on the task', () => {
    renderForm({ ...task, status: 'in_progress' } as BacklogItem)
    expect(screen.queryByRole('button', { name: /edit task/i })).toBeNull()
    expect(screen.getByText(/only in to do or blocked/i)).toBeInTheDocument()
  })
})
