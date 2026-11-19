import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SsoMemberAction, TeamExclusions, TeamSourceSwitch } from './TeamSsoControls'
import { useTeamChange } from '../hooks/useTeams'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'
import type { Team, TeamCandidate } from '../services/api'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const ana: TeamCandidate = { user_id: 11, full_name: 'Ana', email: 'ana@example.test', human_role: 'Viewer', effective_role: 'Viewer', team_ids: [7] }
const bruno: TeamCandidate = { ...ana, user_id: 12, full_name: 'Bruno', email: 'bruno@example.test' }

const team: Team = {
  id: 7, company_id: 4, name: 'Platform', description: null, role: 'Viewer',
  membership_source: 'sso', members: [11, 12],
  exclusions: [{ id: 3, user_id: 13, created_by: 1, created_at: null, reason: 'Suspected account compromise' }],
  manual_exceptions: [{ id: 9, user_id: 11, created_by: 1, created_at: null }],
  created_by: null, created_at: null, updated_at: null,
}

function Harness({ value }: { value: Team }) {
  const change = useTeamChange(4)
  return (
    <div>
      <TeamSourceSwitch team={value} change={change} />
      <SsoMemberAction team={value} member={ana} change={change} />
      <SsoMemberAction team={value} member={bruno} change={change} />
      <TeamExclusions team={value} members={[ana, bruno, { ...ana, user_id: 13, full_name: 'Carla' }]} change={change} />
    </div>
  )
}

function renderWith(value: Team) {
  useAuthStore.setState({ expiresAt: Date.now() + 60_000, memberships: [{ company_id: 4, human_role: 'Owner' }] })
  return render(<QueryClientProvider client={queryClient}><Harness value={value} /></QueryClientProvider>)
}

function calls(fetch: ReturnType<typeof vi.fn>) {
  return fetch.mock.calls.map(([url, init]) => [
    (init as RequestInit | undefined)?.method ?? 'GET',
    new URL(String(url)).pathname,
    (init as RequestInit | undefined)?.body ? JSON.parse(String((init as RequestInit).body)) : undefined,
  ])
}

describe('TeamSsoControls', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 4, companyName: 'Acme' })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('ends a manual exception through the member route and excludes anyone else with a reason', async () => {
    const fetch = vi.fn(async () => response(200, team))
    vi.stubGlobal('fetch', fetch)
    vi.spyOn(window, 'prompt').mockReturnValue('Suspected account compromise')
    renderWith(team)
    expect(screen.getByText('Managed by SSO')).toBeInTheDocument()
    expect(screen.getByText('Manual exception')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'End exception' }))
    fireEvent.click(screen.getByRole('button', { name: 'Exclude' }))
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))
    expect(calls(fetch)).toEqual(expect.arrayContaining([
      ['DELETE', '/v1/companies/4/teams/7/members/11', undefined],
      ['POST', '/v1/companies/4/teams/7/exclusions', { user_id: 12, reason: 'Suspected account compromise' }],
    ]))
  })

  it('does not exclude or lift without a reason of ten characters', () => {
    const fetch = vi.fn(async () => response(200, team))
    vi.stubGlobal('fetch', fetch)
    vi.spyOn(window, 'prompt').mockReturnValue('short')
    renderWith(team)
    fireEvent.click(screen.getByRole('button', { name: 'Exclude' }))
    fireEvent.click(screen.getByRole('button', { name: 'Lift' }))
    expect(fetch).not.toHaveBeenCalled()
  })

  it('lifts an exclusion with the reason in the body', async () => {
    const fetch = vi.fn(async () => response(200, {}))
    vi.stubGlobal('fetch', fetch)
    vi.spyOn(window, 'prompt').mockReturnValue('Account recovered and checked')
    renderWith(team)
    expect(screen.getByText(/Carla/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Lift' }))
    await vi.waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(calls(fetch)[0]).toEqual(['DELETE', '/v1/companies/4/teams/7/exclusions/3', { reason: 'Account recovered and checked' }])
  })

  it('converts a team with members only through a confirmed preview', async () => {
    const owned: Team = { ...team, membership_source: 'owner', exclusions: [], manual_exceptions: [] }
    const fetch = vi.fn(async (url: string) =>
      String(url).endsWith('/membership-source-preview')
        ? response(200, { id: 21, team_id: 7, members: [11, 12], expires_at: '2026-11-19T10:15:00', kept_as: 'manual_exception' })
        : response(200, owned))
    vi.stubGlobal('fetch', fetch)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderWith(owned)
    fireEvent.click(screen.getByRole('button', { name: 'Manage by SSO' }))
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))
    expect(confirm.mock.calls[0][0]).toContain('2 current members are kept as manual exceptions')
    expect(calls(fetch)[1]).toEqual([
      'PATCH', '/v1/companies/4/teams/7', { membership_source: 'sso', preview_id: 21, confirm: true },
    ])
  })
})
