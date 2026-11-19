import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SprintEventLog } from './SprintEventLog'
import { queryClient } from '../lib/queryClient'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const events = [
  { id: 1, task_id: 10, event_type: 'claimed', payload: { by: '<script>x</script>' }, created_at: '2026-11-19T10:00:00+00:00' },
  { id: 2, task_id: 11, event_type: 'done', payload: {}, created_at: '2026-11-19T11:00:00+00:00' },
  { id: 3, task_id: null, event_type: 'claimed', payload: null, created_at: null },
]

function renderLog() {
  return render(<QueryClientProvider client={queryClient}><MemoryRouter><SprintEventLog sprintId={3} /></MemoryRouter></QueryClientProvider>)
}

describe('SprintEventLog', () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => {
    vi.unstubAllGlobals()
    queryClient.clear()
  })

  it('reads nothing until opened, then lists the events with their task and a type filter', async () => {
    const fetch = vi.fn(async (_url: string) => response(200, { sprint_id: 3, events }))
    vi.stubGlobal('fetch', fetch)
    renderLog()
    expect(fetch).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Show' }))
    const list = await screen.findByRole('list', { name: "The sprint's events, oldest first" })
    expect(within(list).getAllByRole('listitem')).toHaveLength(3)
    expect(within(list).getByRole('link', { name: 'task #10' })).toHaveAttribute('href', '/sprints/3/tasks/10')
    // A payload is text, never markup.
    expect(within(list).getByText(/<script>x<\/script>/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Type'), { target: { value: 'done' } })
    expect(within(list).getAllByRole('listitem')).toHaveLength(1)
    expect(String(fetch.mock.calls[0][0])).toContain('/v1/sprints/3/events')
  })

  it('reports a failed read with a retry', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response(403, { detail: 'nope' })))
    renderLog()
    fireEvent.click(screen.getByRole('button', { name: 'Show' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('nope')
  })
})
