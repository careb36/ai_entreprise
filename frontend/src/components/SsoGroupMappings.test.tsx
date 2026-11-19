import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SsoGroupMappings } from './SsoGroupMappings'
import { queryClient } from '../lib/queryClient'
import { useAuthStore } from '../store/useAuthStore'
import type { GroupMappingView } from '../services/api'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const view: GroupMappingView = {
  connection_id: 3,
  preset: 'entra',
  groups_enabled: true,
  groups_claim: 'groups',
  group_role_cap: 'Manager',
  proposed_claim: 'roles',
  compares_lower: true,
  groups_available: true,
  caps: ['Viewer', 'Manager'],
  max_pairs: 200,
  mappings: [{ id: 1, group_value: 'eng', team_id: 7, approval_id: null, created_by: 11, created_at: null }],
  approvals: [
    {
      id: 5, group_value: 'leads', team_id: 8, team_role: 'Manager', group_role_cap: 'Manager',
      requested_by: 12, requested_at: null, expires_at: null, state: 'pending',
      decided_by: null, decided_at: null, reason: null, reviewed_in: null,
    },
  ],
  sso_teams: [{ id: 7, name: 'Platform', role: 'Viewer' }, { id: 8, name: 'Leads', role: 'Manager' }],
  last_overage: { observed_at: '2026-11-19T10:00:00', host: 'graph.windows.net' },
}

function renderAs(userId: number) {
  useAuthStore.setState({
    expiresAt: Date.now() + 60_000,
    memberships: [{ company_id: 4, human_role: 'Owner' }],
    user: { user_id: userId, email: 'owner@example.test', full_name: 'Owner', support_admin: false },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <SsoGroupMappings companyId={4} />
    </QueryClientProvider>,
  )
}

describe('SsoGroupMappings', () => {
  beforeEach(() => queryClient.clear())

  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('shows the pairs, the Entra advice, the cap warning and the last overage', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response(200, view)))
    renderAs(11)
    expect(await screen.findByDisplayValue('eng')).toBeInTheDocument()
    expect(screen.getByText('Recommended for this provider: roles')).toBeInTheDocument()
    expect(screen.getByText(/can make people Managers of the mapped teams/)).toBeInTheDocument()
    expect(screen.getByText(/Entra groups are named by their object id/)).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('graph.windows.net')
  })

  it('saves the pairs with the pending proposals, so a save keeps them', async () => {
    const fetch = vi.fn(async (_url: string, _init?: RequestInit) => response(200, view))
    vi.stubGlobal('fetch', fetch)
    renderAs(11)
    fireEvent.click(await screen.findByRole('button', { name: 'Save the mapping' }))
    await screen.findByText('Mapping saved')
    const put = fetch.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === 'PUT')
    expect(new URL(String(put?.[0])).pathname).toBe('/v1/companies/4/sso/group-mappings')
    expect(JSON.parse(String((put?.[1] as RequestInit).body))).toEqual({
      groups_enabled: true,
      groups_claim: 'groups',
      group_role_cap: 'Manager',
      mappings: [{ group_value: 'eng', team_id: 7 }, { group_value: 'leads', team_id: 8 }],
    })
  })

  it('lets another Owner approve a Manager pair, never its proposer', async () => {
    const fetch = vi.fn(async (_url: string, _init?: RequestInit) => response(200, view))
    vi.stubGlobal('fetch', fetch)
    renderAs(11)
    const pending = await screen.findByRole('listitem', { name: 'leads' })
    fireEvent.click(within(pending).getByRole('button', { name: 'Approve' }))
    await vi.waitFor(() => {
      const post = fetch.mock.calls.find(([url]) => String(url).endsWith('/approvals/5/approve'))
      expect(post).toBeTruthy()
    })
  })

  it('offers the proposer to withdraw or confirm alone, with the team typed back', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response(200, view)))
    renderAs(12)
    const pending = await screen.findByRole('listitem', { name: 'leads' })
    expect(within(pending).queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
    expect(within(pending).getByRole('button', { name: 'Withdraw' })).toBeInTheDocument()
    fireEvent.click(within(pending).getByRole('button', { name: 'Confirm alone' }))
    const submit = within(pending).getAllByRole('button', { name: 'Confirm alone' })[1]
    expect(submit).toBeDisabled()
    fireEvent.change(within(pending).getByLabelText('Type the team\'s name to confirm: Leads'), { target: { value: 'Leads' } })
    fireEvent.change(within(pending).getByLabelText('Reason (10 to 500 characters)'), { target: { value: 'We are the only Owner here' } })
    expect(submit).toBeEnabled()
  })

  it('says Google carries no groups, and shows nothing without a connection', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response(200, { ...view, preset: 'google', groups_available: false, last_overage: null })))
    const { unmount } = renderAs(11)
    expect(await screen.findByText(/Google's ID token carries no groups/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add a pair' })).toBeDisabled()
    unmount()
    queryClient.clear()
    vi.stubGlobal('fetch', vi.fn(async () => response(404, { detail: 'no SSO connection' })))
    const { container } = renderAs(11)
    await vi.waitFor(() => expect(container).toBeEmptyDOMElement())
  })
})
