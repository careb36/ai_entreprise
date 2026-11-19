import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Notifications, { urlProblem } from './Notifications'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const EVENTS = ['task_done', 'task_blocked', 'sprint_closed', 'run_refuted']
const hook = { url: 'https://hooks.example.com/f', events: ['task_done'], enabled: true, has_secret: true, last_delivery_at: null, last_status: null, last_error: 'timeout' }

function routed(webhook: unknown = hook) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const { pathname } = new URL(url)
    if (pathname !== '/v1/companies/4/webhook' && pathname !== '/v1/companies/4/webhook/ping') return response(404)
    if (init?.method === 'PUT') return response(200, { company_id: 4, webhook: { ...hook, ...JSON.parse(String(init.body)) } })
    if (init?.method === 'DELETE') return response(200, { company_id: 4, webhook: null })
    if (init?.method === 'POST') return response(200, { queued: 'ping' })
    return response(200, { company_id: 4, events: EVENTS, webhook })
  })
}

function renderAs(role: 'Owner' | 'Manager' | 'Viewer') {
  useAuthStore.setState({ expiresAt: Date.now() + 60_000, memberships: [{ company_id: 4, human_role: role }] })
  return render(<QueryClientProvider client={queryClient}><Notifications /></QueryClientProvider>)
}

const bodies = (fetch: ReturnType<typeof routed>, method: string) =>
  fetch.mock.calls.filter(([, init]) => init?.method === method).map(([, init]) => (init?.body ? JSON.parse(String(init.body)) : null))

describe('Notifications', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 4 })
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('shows a viewer where it sends and the last error, with no action', async () => {
    vi.stubGlobal('fetch', routed())
    renderAs('Viewer')
    expect(await screen.findByText('https://hooks.example.com/f')).toBeInTheDocument()
    expect(screen.getByText(/Events: A task is done/)).toBeInTheDocument()
    expect(screen.getByRole('note')).toHaveTextContent('timeout')
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('URL')).not.toBeInTheDocument()
  })

  it('creates a webhook only with an https URL and a long enough secret, then clears the secret', async () => {
    const fetch = routed(null)
    vi.stubGlobal('fetch', fetch)
    renderAs('Manager')
    const form = await screen.findByRole('form', { name: 'Create the webhook' })
    const url = within(form).getByLabelText('URL')
    fireEvent.change(url, { target: { value: 'http://hooks.example.com/f' } })
    expect(url).toHaveAttribute('aria-invalid', 'true')
    fireEvent.change(url, { target: { value: 'https://hooks.example.com/f' } })
    const secret = within(form).getByLabelText('Signing secret (required)')
    expect(secret).toHaveAttribute('type', 'password')
    fireEvent.change(secret, { target: { value: 'short' } })
    expect(within(form).getByRole('button', { name: 'Save' })).toBeDisabled()
    fireEvent.change(secret, { target: { value: 'a-long-enough-signing-secret' } })
    fireEvent.click(within(form).getByLabelText('A task is blocked'))
    fireEvent.click(within(form).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(bodies(fetch, 'PUT')).toEqual([{
      url: 'https://hooks.example.com/f', events: ['task_done', 'sprint_closed', 'run_refuted'], enabled: true, secret: 'a-long-enough-signing-secret',
    }]))
  })

  it('keeps the stored secret when the field is left empty', async () => {
    const fetch = routed()
    vi.stubGlobal('fetch', fetch)
    renderAs('Owner')
    const form = await screen.findByRole('form', { name: 'Change the webhook' })
    fireEvent.click(within(form).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(bodies(fetch, 'PUT')[0]).not.toHaveProperty('secret'))
  })

  it('pings, and deletes only after a confirmation', async () => {
    const fetch = routed()
    vi.stubGlobal('fetch', fetch)
    renderAs('Manager')
    fireEvent.click(await screen.findByRole('button', { name: 'Send a test ping' }))
    expect(await screen.findByText('Ping queued')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
    expect(bodies(fetch, 'DELETE')).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: 'Yes, delete' }))
    await waitFor(() => expect(bodies(fetch, 'DELETE')).toHaveLength(1))
  })

  it('names what is wrong with a URL', () => {
    expect(urlProblem('nope')).toBe('invalid')
    expect(urlProblem('http://a.example')).toBe('scheme')
    expect(urlProblem('https://user:pw@a.example')).toBe('credentials')
    expect(urlProblem('https://a.example/h')).toBeNull()
  })
})
