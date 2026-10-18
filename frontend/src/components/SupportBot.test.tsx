import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SupportBot } from './SupportBot'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

function requestTo(fetch: ReturnType<typeof vi.fn>, pathname: string) {
  const call = fetch.mock.calls.find(([url]) => new URL(url).pathname === pathname)
  expect(call, `no request to ${pathname}`).toBeDefined()
  return { url: new URL(call![0]), init: call![1] as RequestInit }
}

describe('SupportBot', () => {
  beforeEach(() => {
    useAuthStore.setState({ token: 'valid-token', expiresAt: Date.now() + 60_000 })
    // jsdom has no layout: the chat scrolls its last message into view.
    Element.prototype.scrollIntoView = vi.fn()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
  })

  it('sends the conversation as `messages`, the body the support chat endpoint reads', async () => {
    const fetch = vi.fn().mockResolvedValue(response(200, { reply: 'Hola, ¿en qué te ayudo?' }))
    vi.stubGlobal('fetch', fetch)
    render(<SupportBot />)

    fireEvent.click(screen.getByTitle('Open Support'))
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'How do I start a sprint?' } })
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' })

    expect(await screen.findByText('Hola, ¿en qué te ayudo?')).toBeInTheDocument()
    const { init } = requestTo(fetch, '/v1/support/chat')
    const body = JSON.parse(init.body as string)
    expect(body).not.toHaveProperty('message')
    expect(body.messages.at(-1)).toEqual({ role: 'user', content: 'How do I start a sprint?' })
    expect(body.messages[0].role).toBe('assistant')
  })

  it('looks tickets up on the support endpoints', async () => {
    const fetch = vi.fn().mockResolvedValue(response(200, { tickets: [], count: 0 }))
    vi.stubGlobal('fetch', fetch)
    render(<SupportBot />)

    fireEvent.click(screen.getByTitle('Open Support'))
    fireEvent.click(screen.getByTitle('My Tickets'))
    const email = screen.getByPlaceholderText(/email/i)
    fireEvent.change(email, { target: { value: 'ana@example.com' } })
    fireEvent.keyDown(email, { key: 'Enter' })

    await waitFor(() => expect(fetch).toHaveBeenCalled())
    const { url } = requestTo(fetch, '/v1/support/tickets')
    expect(url.searchParams.get('reporter_email')).toBe('ana@example.com')
  })
})
