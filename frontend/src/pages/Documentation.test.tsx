import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Documentation from './Documentation'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'
import { pack } from '../test/documentationFixtures'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

function stubApi(overrides: Record<string, () => Response> = {}) {
  const fetch = vi.fn(async (url: string, init: RequestInit = {}) => {
    const { pathname } = new URL(url)
    const key = `${init.method ?? 'GET'} ${pathname}`
    if (overrides[key]) return overrides[key]()
    if (key === 'GET /v1/companies/4/documentation') return response(200, { packs: [pack()] })
    if (key === 'GET /v1/documentation/11') return response(200, pack())
    return response(404)
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

function renderAs(role: 'Owner' | 'Manager' | 'Viewer') {
  useAuthStore.setState({ token: 'valid-token', expiresAt: Date.now() + 60_000, memberships: [{ company_id: 4, human_role: role }] })
  return render(
    <QueryClientProvider client={queryClient}>
      <Documentation />
    </QueryClientProvider>,
  )
}

describe('Documentation', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 4 })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('shows a viewer the documents and the diagrams without actions', async () => {
    stubApi()
    const { container } = renderAs('Viewer')
    expect(await screen.findByRole('tab', { name: 'Project charter' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('The pack matches the current records.')).toBeInTheDocument()
    expect(screen.getByText(/1 section has no record behind it/)).toBeInTheDocument()
    expect(screen.getByText(/1 section written by a model/)).toBeInTheDocument()
    expect(screen.getByText(/2 sections for a person to complete/)).toHaveTextContent('PMBOK Guide 8th ed.: Governance, Initiating')
    expect(container.querySelector('script')).toBeNull()
    expect(screen.queryByRole('button', { name: /Compile/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: 'Diagrams' }))
    expect(screen.getAllByTestId('c4-node')).toHaveLength(4)
  })

  it('lets a manager compile but not approve', async () => {
    const fetch = stubApi({ 'POST /v1/companies/4/documentation': () => response(201, pack({ id: 11, version: 3 })) })
    renderAs('Manager')
    await screen.findByRole('tab', { name: 'Project charter' })
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: /Compile from the records/ }))
    await waitFor(() => expect(fetch.mock.calls.some(([, init]) => (init as RequestInit)?.method === 'POST')).toBe(true))
    const post = fetch.mock.calls.find(([, init]) => (init as RequestInit)?.method === 'POST')!
    expect(JSON.parse(String((post[1] as RequestInit).body))).toEqual({ prose: false })
  })

  it('lets an owner approve, and shows the drift that refuses it', async () => {
    stubApi({
      'POST /v1/documentation/11/approve': () => response(409, {
        detail: { message: '1 finding(s) block the approval.', findings: [{ severity: 'blocking', where: 'wbs', message: 'The records it was compiled from changed since: compile the pack again.' }] },
      }),
    })
    renderAs('Owner')
    fireEvent.click(await screen.findByRole('button', { name: 'Approve' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('The records it was compiled from changed since')
  })

  it('disables approval while the lint blocks and offers writing an approved pack', async () => {
    stubApi({
      'GET /v1/documentation/11': () => response(200, pack({
        lint: { ok: false, stale: true, blocking: 1, findings: [{ severity: 'blocking', where: 'c4-context', message: 'The architecture changed.' }], documents: [], diagrams: [] },
      })),
    })
    renderAs('Owner')
    expect(await screen.findByRole('button', { name: 'Approve' })).toBeDisabled()
    expect(screen.getByText(/1 finding blocks approval/)).toBeInTheDocument()
    expect(screen.getByText(/The architecture changed./)).toBeInTheDocument()
  })

  it('offers an approved pack to be written to the repository', async () => {
    stubApi({ 'GET /v1/documentation/11': () => response(200, pack({ status: 'approved' })) })
    renderAs('Manager')
    expect(await screen.findByRole('button', { name: 'Write to the repository' })).toBeInTheDocument()
  })
})
