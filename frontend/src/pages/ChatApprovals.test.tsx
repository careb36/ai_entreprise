import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ChatApprovals from './ChatApprovals'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const slack = {
  id: 1,
  company_id: 4,
  provider: 'slack',
  workspace_id: 'TFAKE0001',
  channel: 'CFAKE0001',
  enabled: true,
  has_post_credential: true,
  has_signing_secret: true,
  inbound_url: 'http://backend.test/v1/integrations/slack/abc/interactions',
  tested_at: null,
  test_ok: null,
  test_detail: null,
  last_status: null,
  last_error: null,
  updated_at: '2026-11-15T10:00:00',
}

const links = (linked: unknown[] = []) => ({
  company_id: 4,
  connected: { slack: true, teams: false },
  links: linked,
})

function renderAs(role: 'Owner' | 'Viewer') {
  useAuthStore.setState({ expiresAt: Date.now() + 60_000, memberships: [{ company_id: 4, human_role: role }] })
  return render(
    <QueryClientProvider client={queryClient}>
      <ChatApprovals />
    </QueryClientProvider>,
  )
}

function urlOf(call: unknown[]): string {
  return String(call[0])
}

describe('ChatApprovals', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 4, companyName: 'Acme' })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('lets a viewer ask for a link code without reading the connection settings', async () => {
    const fetch = vi.fn(async (url: string) => {
      if (url.endsWith('/chat/links')) return response(200, links())
      if (url.endsWith('/chat/links/slack/code')) {
        return response(200, { code: 'ABCDEFGH23', expires_at: '2026-11-15T10:10:00Z', provider: 'slack', instruction: 'x' })
      }
      return response(404)
    })
    vi.stubGlobal('fetch', fetch)
    renderAs('Viewer')

    fireEvent.click(await screen.findByRole('button', { name: 'Link my Slack account' }))

    expect(await screen.findByText('ABCDEFGH23')).toBeInTheDocument()
    expect(screen.getByText('In Slack, type: /fabrique link ABCDEFGH23')).toBeInTheDocument()
    expect(screen.getByText('Not connected by the company')).toBeInTheDocument()
    expect(fetch.mock.calls.map(urlOf).some(u => u.endsWith('/v1/companies/4/chat'))).toBe(false)
  })

  it('shows a linked account and unlinks it', async () => {
    const fetch = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/chat/links/slack') && init?.method === 'DELETE') return response(200, { unlinked: true })
      if (url.endsWith('/chat/links')) {
        return response(200, links([{ provider: 'slack', workspace_id: 'TFAKE0001', external_user_id: 'UFAKE0001', display_name: 'ana', created_at: null }]))
      }
      return response(404)
    })
    vi.stubGlobal('fetch', fetch)
    renderAs('Viewer')

    expect(await screen.findByText('Linked to ana in TFAKE0001')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Unlink' }))
    await vi.waitFor(() => expect(fetch.mock.calls.some(c => (c[1] as RequestInit | undefined)?.method === 'DELETE')).toBe(true))
  })

  it('lets an owner save a connection, keeping write-only credentials out of the form', async () => {
    const fetch = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/chat/links')) return response(200, links())
      if (url.endsWith('/v1/companies/4/chat')) {
        return response(200, { company_id: 4, providers: ['slack', 'teams'], connections: { slack, teams: null } })
      }
      if (url.endsWith('/chat/slack') && init?.method === 'PUT') return response(200, slack)
      return response(404)
    })
    vi.stubGlobal('fetch', fetch)
    renderAs('Owner')

    expect(await screen.findByText(slack.inbound_url)).toBeInTheDocument()
    const token = screen.getByLabelText('Bot token') as HTMLInputElement
    expect(token.value).toBe('')
    expect(token.placeholder).toBe('Set — leave empty to keep it')
    fireEvent.click(screen.getAllByRole('button', { name: 'Save' })[0])

    await vi.waitFor(() => {
      const put = fetch.mock.calls.find(c => (c[1] as RequestInit | undefined)?.method === 'PUT')
      expect(put).toBeDefined()
      const body = JSON.parse(String((put?.[1] as RequestInit).body))
      expect(body).toEqual({ workspace_id: 'TFAKE0001', channel: 'CFAKE0001', enabled: true })
    })
    expect(screen.getByText('Tenant ID')).toBeInTheDocument()
  })
})
