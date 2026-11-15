import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import CompanyTeams from './CompanyTeams'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const team = {
  id: 7,
  company_id: 4,
  name: 'Release crew',
  description: 'Ships',
  role: 'Manager',
  members: [11],
  created_by: null,
  created_at: null,
  updated_at: null,
}

const listing = {
  company_id: 4,
  roles: ['Viewer', 'Manager'],
  teams: [team],
  members: [
    { user_id: 11, full_name: 'Ana', email: 'ana@example.test', human_role: 'Viewer', effective_role: 'Manager', team_ids: [7] },
    { user_id: 12, full_name: 'Bruno', email: 'bruno@example.test', human_role: 'Viewer', effective_role: 'Viewer', team_ids: [] },
  ],
}

function renderAs(role: 'Owner' | 'Manager' | 'Viewer', effective?: 'Owner' | 'Manager' | 'Viewer') {
  useAuthStore.setState({
    token: 'valid-token',
    expiresAt: Date.now() + 60_000,
    memberships: [{ company_id: 4, human_role: role, effective_role: effective ?? role }],
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <CompanyTeams />
    </QueryClientProvider>,
  )
}

describe('CompanyTeams', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 4, companyName: 'Acme' })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('tells a manager made by a team that only owners manage teams, without asking', async () => {
    const fetch = vi.fn(async () => response(200, listing))
    vi.stubGlobal('fetch', fetch)
    renderAs('Viewer', 'Manager')
    expect(screen.getByRole('status')).toHaveTextContent('Only owners manage teams. Your role here is Manager.')
    expect(fetch).not.toHaveBeenCalled()
  })

  it('lists the teams with their members and the effective roles', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response(200, listing)))
    renderAs('Owner')
    const card = await screen.findByRole('region', { name: 'Release crew' })
    expect(within(card).getByText('Ana')).toBeInTheDocument()
    expect(within(card).getByRole('combobox', { name: 'Role' })).toHaveValue('Manager')
    const roles = screen.getByRole('region', { name: 'Members and their roles' })
    const ana = within(roles).getByText('Ana').closest('tr') as HTMLElement
    expect(within(ana).getAllByRole('cell').map(c => c.textContent)).toEqual(['Ana', 'Viewer', 'Manager', 'Release crew'])
  })

  it('creates a team and adds a member through the API', async () => {
    const fetch = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === 'POST') return response(201, { ...team, id: 8, name: 'Ops', role: 'Viewer', members: [] })
      if (init?.method === 'PUT') return response(200, { ...team, members: [11, 12] })
      return response(200, listing)
    })
    vi.stubGlobal('fetch', fetch)
    renderAs('Owner')
    await screen.findByRole('region', { name: 'Release crew' })

    const form = screen.getByRole('form', { name: 'New team' })
    fireEvent.change(within(form).getByLabelText('Name'), { target: { value: 'Ops' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Create team' }))
    await vi.waitFor(() => expect(fetch.mock.calls.some(c => (c[1] as RequestInit | undefined)?.method === 'POST')).toBe(true))
    const post = fetch.mock.calls.find(c => (c[1] as RequestInit | undefined)?.method === 'POST')!
    expect(String(post[0])).toContain('/v1/companies/4/teams')
    expect(JSON.parse(String((post[1] as RequestInit).body))).toEqual({ name: 'Ops', description: null, role: 'Viewer' })

    const card = screen.getByRole('region', { name: 'Release crew' })
    fireEvent.change(within(card).getByLabelText('Add a member'), { target: { value: '12' } })
    fireEvent.click(within(card).getByRole('button', { name: 'Add' }))
    await vi.waitFor(() => expect(fetch.mock.calls.some(c => (c[1] as RequestInit | undefined)?.method === 'PUT')).toBe(true))
    const put = fetch.mock.calls.find(c => (c[1] as RequestInit | undefined)?.method === 'PUT')!
    expect(String(put[0])).toContain('/v1/companies/4/teams/7/members/12')
  })
})
