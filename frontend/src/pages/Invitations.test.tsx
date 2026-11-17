import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Invitations from './Invitations'
import { queryClient } from '../lib/queryClient'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const PENDING = {
  id: 7, company_id: 4, company_name: 'Acme', email: 'ana@acme.test', human_role: 'Manager',
  invited_by: 'owner@acme.test', status: 'pending', created_at: '2026-11-17T10:00:00',
  expires_at: '2026-11-24T10:00:00',
}
const PROFILE = { user_id: 1, email: 'ana@acme.test', full_name: 'Ana', memberships: [{ company_id: 4, human_role: 'Manager' }] }

function renderAt(path: string) {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes><Route path="/invitations" element={<Invitations />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Invitations', () => {
  beforeEach(() => {
    queryClient.clear()
    useAuthStore.setState({ expiresAt: Date.now() + 60_000, memberships: [] })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('lists the pending invitations and accepts one, then reads the memberships again', async () => {
    let accepted = false
    const fetch = vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url).pathname
      if (path === '/v1/invitations') return response(200, { invitations: accepted ? [] : [PENDING] })
      if (path === '/v1/invitations/7/accept' && init?.method === 'POST') {
        accepted = true
        return response(200, { status: 'accepted', company_id: 4, human_role: 'Manager' })
      }
      if (path === '/v1/auth/me') return response(200, PROFILE)
      return response(404, { detail: 'No pending invitation' })
    })
    vi.stubGlobal('fetch', fetch)
    renderAt('/invitations')

    expect(await screen.findByText('Acme invites you as Manager (from owner@acme.test).')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Accept' }))
    expect(await screen.findByText('You have no pending invitations.')).toBeInTheDocument()
    await vi.waitFor(() => expect(useAuthStore.getState().memberships).toEqual(PROFILE.memberships))
  })

  it('accepts the token of a link once and says when it matches nothing', async () => {
    const fetch = vi.fn(async (url: string, _init?: RequestInit) => {
      const path = new URL(url).pathname
      if (path === '/v1/invitations') return response(200, { invitations: [] })
      return response(404, { detail: 'No pending invitation' })
    })
    vi.stubGlobal('fetch', fetch)
    const token = ['abc', 'def', 'ghi', 'jkl', 'mnop'].join('')
    renderAt(`/invitations?token=${token}`)

    expect(await screen.findByRole('alert')).toHaveTextContent('does not match a pending invitation')
    const posts = fetch.mock.calls.filter(([url]) => new URL(url).pathname === '/v1/invitations/accept')
    expect(posts).toHaveLength(1)
    expect(JSON.parse(posts[0][1]?.body as string)).toEqual({ token })
  })
})
