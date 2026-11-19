import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { RunSecretsPanel } from './RunSecretsPanel'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

function routed() {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const { pathname } = new URL(url)
    if (pathname === '/v1/run-secrets' && init?.method === 'POST') return response(200, { id: 8, name: 'API_KEY', task_id: 12 })
    if (pathname === '/v1/run-secrets') return response(200, { secrets: [{ id: 5, name: 'DATABASE_URL', task_id: 12 }] })
    if (pathname === '/v1/run-secrets/5' && init?.method === 'DELETE') return response(200, { deleted: true })
    return response(404)
  })
}

function renderAs(role: 'Owner' | 'Manager' | 'Viewer') {
  useAuthStore.setState({ expiresAt: Date.now() + 60_000, memberships: [{ company_id: 4, human_role: role }] })
  return render(
    <QueryClientProvider client={queryClient}>
      <RunSecretsPanel companyId={4} taskId={12} />
    </QueryClientProvider>,
  )
}

describe('RunSecretsPanel', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 4 })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('shows a viewer the names only, with no form and no delete', async () => {
    vi.stubGlobal('fetch', routed())
    renderAs('Viewer')
    expect(await screen.findByText('DATABASE_URL')).toBeInTheDocument()
    expect(screen.getByText('value hidden')).toBeInTheDocument()
    expect(screen.queryByRole('form', { name: 'Add a run secret' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Delete/ })).not.toBeInTheDocument()
  })

  it('adds a secret through a password field and clears it after the save', async () => {
    const fetch = routed()
    vi.stubGlobal('fetch', fetch)
    renderAs('Manager')
    const value = await screen.findByLabelText('Value')
    expect(value).toHaveAttribute('type', 'password')
    expect(value).toHaveAttribute('autocomplete', 'new-password')
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'API_KEY' } })
    fireEvent.change(value, { target: { value: 's3cret-value' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add secret' }))
    expect(await screen.findByText('API_KEY saved')).toBeInTheDocument()
    expect(value).toHaveValue('')
    const post = fetch.mock.calls.find(([, init]) => init?.method === 'POST')
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ company_id: 4, task_id: 12, name: 'API_KEY', value: 's3cret-value' })
  })

  it('refuses a name the backend would refuse, before sending it', async () => {
    const fetch = routed()
    vi.stubGlobal('fetch', fetch)
    renderAs('Owner')
    fireEvent.change(await screen.findByLabelText('Name'), { target: { value: '9-bad' } })
    fireEvent.change(screen.getByLabelText('Value'), { target: { value: 'x' } })
    expect(screen.getByLabelText('Name')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByRole('button', { name: 'Add secret' })).toBeDisabled()
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })

  it('asks before deleting a secret', async () => {
    const fetch = routed()
    vi.stubGlobal('fetch', fetch)
    renderAs('Manager')
    fireEvent.click(await screen.findByRole('button', { name: 'Delete the secret DATABASE_URL' }))
    fireEvent.click(screen.getByRole('button', { name: 'Yes, delete' }))
    await waitFor(() => expect(fetch.mock.calls.some(([url, init]) =>
      init?.method === 'DELETE' && String(url).includes('/v1/run-secrets/5?company_id=4'))).toBe(true))
  })
})
