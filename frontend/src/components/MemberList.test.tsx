import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemberList } from './MemberList'
import { queryClient } from '../lib/queryClient'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const member = (id: number, label: string, extra: Record<string, unknown> = {}) => ({
  kind: 'member', id, label, email: `${label.toLowerCase()}@example.invalid`, own_role: 'Viewer', teams: [], effective_role: 'Viewer',
  sso_linked: false, chat_links: [], last_activity: null, inactive: false, ...extra,
})
const listing = {
  company_id: 4, schedule: {}, tokens: [], history: [],
  members: [member(1, 'Owner', { own_role: 'Owner', effective_role: 'Owner' }), member(2, 'Dana', { effective_role: 'Manager' })],
}

function routed(remove: Response = response(204)) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const { pathname } = new URL(url)
    if (pathname === '/v1/companies/4/access-review') return response(200, listing)
    if (pathname === '/v1/company/4/members/2' && init?.method === 'DELETE') return remove
    return response(404)
  })
}

function renderList() {
  useAuthStore.setState({
    expiresAt: Date.now() + 60_000, memberships: [{ company_id: 4, human_role: 'Owner' }],
    user: { user_id: 1, email: 'owner@example.invalid', full_name: 'Owner' },
  })
  return render(<QueryClientProvider client={queryClient}><MemberList companyId={4} /></QueryClientProvider>)
}

describe('MemberList', () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('lists the members with a team-raised role, and no removal on the owner\'s own row', async () => {
    vi.stubGlobal('fetch', routed())
    renderList()
    const table = await screen.findByRole('table', { name: 'Members' })
    expect(within(table).getByText('Viewer, Manager through a team')).toBeInTheDocument()
    expect(within(table).getByText('You')).toBeInTheDocument()
    expect(within(table).queryByRole('button', { name: 'Remove Owner from the company' })).not.toBeInTheDocument()
  })

  it('removes a member only after a confirmation that says their sessions end', async () => {
    const fetch = routed()
    vi.stubGlobal('fetch', fetch)
    renderList()
    fireEvent.click(await screen.findByRole('button', { name: 'Remove Dana from the company' }))
    expect(screen.getByText(/Their sessions end everywhere/)).toBeInTheDocument()
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Yes, remove' }))
    await waitFor(() => expect(fetch.mock.calls.some(([url, init]) => init?.method === 'DELETE' && String(url).endsWith('/v1/company/4/members/2'))).toBe(true))
  })

  it('shows the backend refusal', async () => {
    vi.stubGlobal('fetch', routed(response(409, { detail: "The company's last Owner cannot be removed" })))
    renderList()
    fireEvent.click(await screen.findByRole('button', { name: 'Remove Dana from the company' }))
    fireEvent.click(screen.getByRole('button', { name: 'Yes, remove' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('last Owner')
  })
})
