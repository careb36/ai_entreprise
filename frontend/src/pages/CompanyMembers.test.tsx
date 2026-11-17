import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import CompanyMembers from './CompanyMembers'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

function renderAs(role: 'Owner' | 'Manager' | 'Viewer') {
  useAuthStore.setState({ expiresAt: Date.now() + 60_000, memberships: [{ company_id: 4, human_role: role }] })
  return render(
    <QueryClientProvider client={queryClient}>
      <CompanyMembers />
    </QueryClientProvider>,
  )
}

/** Answers by path: the pending list is read beside the invite. */
function backend(invite: Response[], pending: unknown[] = []) {
  const queue = [...invite]
  return vi.fn(async (url: string, init?: RequestInit) => {
    const path = new URL(url).pathname
    if (path === '/v1/companies/4/invitations' && (!init || !init.method || init.method === 'GET')) {
      return response(200, { invitations: pending })
    }
    return queue.shift() ?? response(500)
  })
}

const TOKEN = ['tok', 'en', '-for-link'].join('')

describe('CompanyMembers', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 4, companyName: 'Acme' })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('tells a viewer that only managers and owners invite', () => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    renderAs('Viewer')
    expect(screen.getByRole('status')).toHaveTextContent('Only managers and owners can invite members. Your role here is Viewer.')
    expect(screen.queryByRole('button', { name: 'Invite' })).not.toBeInTheDocument()
    expect(fetch).not.toHaveBeenCalled()
  })

  it('offers a manager only the roles up to their own', () => {
    vi.stubGlobal('fetch', backend([]))
    renderAs('Manager')
    const options = within(screen.getByRole('combobox', { name: 'Role' })).getAllByRole('option')
    expect(options.map(option => option.getAttribute('value'))).toEqual(['Viewer', 'Manager'])
  })

  it('records a pending invitation and shows the link, whatever the address', async () => {
    const fetch = backend([response(202, { status: 'pending', detail: 'x', token: TOKEN })])
    vi.stubGlobal('fetch', fetch)
    renderAs('Owner')

    fireEvent.change(screen.getByRole('textbox', { name: 'Email address' }), { target: { value: ' ana@acme.test ' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Role' }), { target: { value: 'Manager' } })
    fireEvent.click(screen.getByRole('button', { name: 'Invite' }))

    expect(await screen.findByText(/An invitation is waiting for ana@acme.test as Manager/)).toBeInTheDocument()
    const post = fetch.mock.calls.find(([, init]) => init?.method === 'POST')
    expect(post).toBeDefined()
    expect(new URL(post![0]).pathname).toBe('/v1/company/invite')
    expect(JSON.parse(post![1]!.body as string)).toEqual({ company_id: 4, email: 'ana@acme.test', human_role: 'Manager' })
    const link = screen.getByRole('textbox', { name: /Link you can send them/ }) as HTMLInputElement
    expect(link.value).toContain(`/invitations?token=${TOKEN}`)
    expect(screen.getByRole('textbox', { name: 'Email address' })).toHaveValue('')
  })

  it('explains a refusal of the role and lets the person retry', async () => {
    const fetch = backend([
      response(403, { detail: 'Your role here (Manager) cannot invite or revoke a Owner' }),
      response(202, { status: 'pending', detail: 'x', token: TOKEN }),
    ])
    vi.stubGlobal('fetch', fetch)
    renderAs('Owner')

    fireEvent.change(screen.getByRole('textbox', { name: 'Email address' }), { target: { value: 'ana@acme.test' } })
    fireEvent.click(screen.getByRole('button', { name: 'Invite' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('only an owner invites an owner')
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText(/An invitation is waiting for ana@acme.test/)).toBeInTheDocument()
  })

  it('lists the pending invitations and revokes one', async () => {
    const pending = [{
      id: 9, company_id: 4, email: 'bo@acme.test', human_role: 'Viewer', invited_by: 'o@acme.test',
      status: 'pending', created_at: '2026-11-17T10:00:00', expires_at: '2026-11-24T10:00:00',
    }]
    const fetch = backend([response(204)], pending)
    vi.stubGlobal('fetch', fetch)
    renderAs('Owner')

    expect(await screen.findByText('bo@acme.test as Viewer')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Revoke the invitation of bo@acme.test' }))
    await vi.waitFor(() => {
      const revoke = fetch.mock.calls.find(([, init]) => init?.method === 'DELETE')
      expect(revoke && new URL(revoke[0]).pathname).toBe('/v1/companies/4/invitations/9')
    })
  })
})
