import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { waitFor } from '@testing-library/react'
import { useAgentStore } from './agentStore'
import { useAuthStore } from './useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

class FakeEventSource {
  static opened: FakeEventSource[] = []
  url: string
  onopen: (() => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()
  constructor(url: string) {
    this.url = url
    FakeEventSource.opened.push(this)
  }
}

describe('Glassroom stream connection', () => {
  beforeEach(() => {
    FakeEventSource.opened = []
    vi.stubGlobal('EventSource', FakeEventSource)
    useAuthStore.setState({ token: 'valid-token', expiresAt: Date.now() + 60_000 })
  })

  afterEach(() => {
    useAgentStore.getState().disconnect()
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
  })

  it('opens the stream with a ticket for the sprint, asked for on every connection', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(response(200, { ticket: 'ticket-1', expires_in: 300 }))
      .mockResolvedValueOnce(response(200, { ticket: 'ticket-2', expires_in: 300 }))
    vi.stubGlobal('fetch', fetch)

    useAgentStore.getState().connect('7')
    await waitFor(() => expect(FakeEventSource.opened).toHaveLength(1))
    const first = new URL(FakeEventSource.opened[0].url)
    expect(new URL(fetch.mock.calls[0][0]).pathname).toBe('/v1/sprints/7/stream_ticket')
    expect(fetch.mock.calls[0][1].method).toBe('POST')
    expect(Object.fromEntries(first.searchParams)).toEqual({ sprint_id: '7', ticket: 'ticket-1' })

    // A ticket is short-lived: reconnecting asks for a new one.
    useAgentStore.getState().connect('7')
    await waitFor(() => expect(FakeEventSource.opened).toHaveLength(2))
    expect(new URL(FakeEventSource.opened[1].url).searchParams.get('ticket')).toBe('ticket-2')
  })

  it('opens it without a ticket when none is needed or none could be had', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(response(200, { ticket: null, expires_in: 0 }))
      .mockResolvedValueOnce(response(403, { detail: 'Not a member of this company' }))
    vi.stubGlobal('fetch', fetch)

    useAgentStore.getState().connect('7')
    await waitFor(() => expect(FakeEventSource.opened).toHaveLength(1))
    expect(new URL(FakeEventSource.opened[0].url).searchParams.has('ticket')).toBe(false)

    useAgentStore.getState().connect('7')
    await waitFor(() => expect(FakeEventSource.opened).toHaveLength(2))
    expect(new URL(FakeEventSource.opened[1].url).searchParams.has('ticket')).toBe(false)
  })

  it('does not open a stream once disconnected while the ticket was on its way', async () => {
    let answer: (value: Response) => void = () => {}
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => { answer = resolve })))

    useAgentStore.getState().connect('7')
    useAgentStore.getState().disconnect()
    answer(response(200, { ticket: 'late', expires_in: 300 }))
    await new Promise(resolve => setTimeout(resolve, 0))

    expect(FakeEventSource.opened).toHaveLength(0)
  })
})
