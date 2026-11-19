import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TaskEventLog } from './TaskEventLog'
import { queryClient } from '../lib/queryClient'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const event = {
  id: 1, task_id: 12, sprint_id: 3, kind: 'claimed', event_type: 'claimed', actor: 'Developer', run_id: 40, verification_id: null,
  task_status: 'in_progress', legacy: false, payload: { note: '<b>agent text</b>' }, seq: 1, prev_hash: null, chain_hash: 'h1',
  created_at: '2026-11-19T10:00:00+00:00',
}

function log(chain: Record<string, unknown>, events = [event]) {
  return { task_id: 12, source: 'events', chain: { task_id: 12, events: events.length, ...chain }, events }
}

function renderLog() {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter><TaskEventLog taskId={12} /></MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('TaskEventLog', () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => {
    vi.unstubAllGlobals()
    queryClient.clear()
  })

  it('reads nothing until opened, then shows the events and the chain the backend verified', async () => {
    const fetch = vi.fn(async (_url: string) => response(200, log({ status: 'intact', breaks: [] })))
    vi.stubGlobal('fetch', fetch)
    renderLog()
    expect(fetch).not.toHaveBeenCalled()
    const toggle = screen.getByRole('button', { name: 'Show' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(toggle)
    expect(await screen.findByText('Chain intact')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Hide' })).toHaveAttribute('aria-expanded', 'true')
    const list = screen.getByRole('list', { name: 'Events of this task, oldest first' })
    expect(within(list).getByText('claimed')).toBeInTheDocument()
    expect(within(list).getByText('by Developer')).toBeInTheDocument()
    expect(within(list).getByRole('link', { name: 'run 40' })).toHaveAttribute('href', '/evidence/40')
    // Agent text in a payload is shown as text, never as markup.
    expect(within(list).getByText(/<b>agent text<\/b>/)).toBeInTheDocument()
    expect(String(fetch.mock.calls[0]?.[0])).toContain('/v1/tasks/12/events')
  })

  it('reports a broken chain as an alert', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response(200, log({ status: 'broken', breaks: [{ seq: 1, reason: 'hash_mismatch' }] }))))
    renderLog()
    fireEvent.click(screen.getByRole('button', { name: 'Show' }))
    expect(await screen.findByText('Chain broken')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Event 1: The row or its place was altered')
  })
})
