import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import OwnerActions from './OwnerActions'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const NAME = 'self_host_container_run'

const finishedRun = {
  id: 7, company_id: 4, action: NAME, status: 'succeeded',
  params: { checkout_name: 'lf-701', scope: 'S', timeout_seconds: 3600, token_budget: 2000000, urgent: false },
  started_by_user_id: 1, started_at: '2026-11-24T10:00:00', finished_at: '2026-11-24T10:40:00',
  cancel_requested_at: null, exit_status: 0, reason: null,
  observations: {
    company_id: 31, sprint_id: 42, issue_specification_id: 8, issue_architecture_id: 9,
    agent_run_ids: [11, 12], checkout: { path: '/x/lf-701', created: true, head: 'a'.repeat(40), source_head: 'b'.repeat(40), files: 3 },
    sandbox: { asked: 'container', observed: 'container', observed_by: 'daemon', container_mode: true, modes: { container: 2 }, images: ['sha256:ab'] },
    tokens_observed: 1234, steps_done: ['checkout', 'company', 'records', 'issue', 'wait', 'sandbox'],
  },
}

function catalogue(lastRuns: unknown[] = [], running: number | null = null) {
  return {
    actions: [{
      name: NAME, issue: 701, available: true, reason: null, running_run_id: running, last_runs: lastRuns,
      required: ['checkout_name'], records: ['sprint_id', 'agent_run_ids', 'sandbox'],
      params: {
        checkout_name: { type: 'string', pattern: '^[a-z0-9][a-z0-9._-]{0,62}$' },
        scope: { enum: ['S', 'M'], default: 'S' },
        timeout_seconds: { type: 'integer', default: 3600, minimum: 60, maximum: 21600 },
        token_budget: { type: 'integer', default: 2000000, minimum: 10000, maximum: 10000000 },
        urgent: { type: 'boolean', default: false },
      },
    }],
  }
}

const preview = {
  action: NAME,
  params: { checkout_name: 'lf-701', scope: 'S', timeout_seconds: 3600, token_budget: 2000000, urgent: true },
  effect: [
    { key: 'copy', values: { name: 'lf-701' } },
    { key: 'company', values: {} },
    { key: 'sprint', values: { scope: 'S' } },
    { key: 'wait', values: { minutes: 60 } },
    { key: 'sandbox', values: {} },
    { key: 'urgent', values: {} },
  ],
  cost: { models: ['PO', 'Developer', 'QA'], token_budget: 2000000, max_seconds: 4200, budget_checked_between_calls: true },
  confirmation: 'digest-1',
}

function renderAs(role: 'Owner' | 'Manager') {
  useAuthStore.setState({ expiresAt: Date.now() + 60_000, memberships: [{ company_id: 4, human_role: role, effective_role: role }] })
  return render(<QueryClientProvider client={queryClient}><OwnerActions /></QueryClientProvider>)
}

describe('OwnerActions', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 4, companyName: 'Acme' })
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('tells a Manager only the Owner runs them, without asking the backend', () => {
    const fetch = vi.fn(async () => response(200, catalogue()))
    vi.stubGlobal('fetch', fetch)
    renderAs('Manager')
    expect(screen.getByText("Only the company's Owner can run owner actions.")).toBeInTheDocument()
    expect(fetch).not.toHaveBeenCalled()
  })

  it('shows the cost and effect, then starts the run with the digest the dialog showed', async () => {
    const fetch = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes('/preview')) return response(200, preview)
      if (init?.method === 'POST' && url.includes('/runs')) return response(202, { ...finishedRun, id: 8, status: 'running', observations: {} })
      return response(200, catalogue())
    })
    vi.stubGlobal('fetch', fetch)
    renderAs('Owner')
    const name = await screen.findByLabelText('Snapshot folder')
    fireEvent.change(name, { target: { value: 'lf-701' } })
    fireEvent.click(screen.getByLabelText('Mark the sprint urgent (it does not wait for the run window)'))
    fireEvent.click(screen.getByRole('button', { name: 'Review cost and effect' }))

    const dialog = await screen.findByRole('dialog', { name: 'Run Self-hosting run in container mode?' })
    expect(within(dialog).getByText(/copies the La Fabrique repository into the folder lf-701/)).toBeInTheDocument()
    expect(within(dialog).getByText('Waits up to 60 minutes for the sprint to complete.')).toBeInTheDocument()
    expect(within(dialog).getByText('Marks the sprint urgent, so the run window does not hold it.')).toBeInTheDocument()
    expect(within(dialog).getByText('PO, Developer, QA')).toBeInTheDocument()
    expect(within(dialog).getByText((2000000).toLocaleString())).toBeInTheDocument()
    expect(within(dialog).getByText('70 minutes')).toBeInTheDocument()
    const previewCall = fetch.mock.calls.find(c => String(c[0]).includes('/preview'))!
    expect(JSON.parse(String((previewCall[1] as RequestInit).body))).toEqual({
      company_id: 4,
      params: { checkout_name: 'lf-701', scope: 'S', timeout_seconds: 3600, token_budget: 2000000, urgent: true },
    })

    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirm and run' }))
    await waitFor(() => expect(fetch.mock.calls.some(c => String(c[0]).endsWith('/runs') && (c[1] as RequestInit)?.method === 'POST')).toBe(true))
    const start = fetch.mock.calls.find(c => String(c[0]).endsWith(`/v1/owner_actions/${NAME}/runs`))!
    // What runs is what the dialog showed: its parameters and its digest.
    expect(JSON.parse(String((start[1] as RequestInit).body))).toEqual({
      company_id: 4, params: preview.params, confirmation: 'digest-1',
    })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('closing the dialog starts nothing', async () => {
    const fetch = vi.fn(async (url: string) => (url.includes('/preview') ? response(200, preview) : response(200, catalogue())))
    vi.stubGlobal('fetch', fetch)
    renderAs('Owner')
    fireEvent.change(await screen.findByLabelText('Snapshot folder'), { target: { value: 'lf-701' } })
    fireEvent.click(screen.getByRole('button', { name: 'Review cost and effect' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(fetch.mock.calls.some(c => String(c[0]).endsWith('/runs'))).toBe(false)
  })

  it('lists the ids a finished run recorded and copies them', async () => {
    const writeText = vi.fn(async (text: string) => { void text })
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })
    vi.stubGlobal('fetch', vi.fn(async (url: string) => (url.includes('/runs/7') ? response(200, finishedRun) : response(200, catalogue([finishedRun])))))
    renderAs('Owner')
    const run = await screen.findByRole('article', { name: 'Run 7' })
    expect(within(run).getByText('Succeeded')).toBeInTheDocument()
    expect(within(run).getByText('11, 12')).toBeInTheDocument()
    expect(within(run).getByText('Sandbox asked: container; observed by the daemon: container.')).toBeInTheDocument()
    fireEvent.click(within(run).getByRole('button', { name: 'Copy ids' }))
    await waitFor(() => expect(writeText).toHaveBeenCalled())
    const copied = writeText.mock.calls[0][0] as string
    expect(copied).toContain('Sprint: 42')
    expect(copied).toContain('Agent runs: 11, 12')
    expect(copied).toContain('Container mode: true')
    expect(await within(run).findByRole('button', { name: 'Copied' })).toBeInTheDocument()
  })

  it('offers cancel on a running run and sends it', async () => {
    const running = { ...finishedRun, status: 'running', finished_at: null, exit_status: null }
    const fetch = vi.fn(async (url: string, init?: RequestInit) => {
      void init
      if (url.includes('/cancel')) return response(200, { ...running, cancel_requested_at: '2026-11-24T10:05:00' })
      if (url.includes('/runs/7')) return response(200, running)
      return response(200, catalogue([running], 7))
    })
    vi.stubGlobal('fetch', fetch)
    renderAs('Owner')
    const run = await screen.findByRole('article', { name: 'Run 7' })
    expect(screen.getByRole('button', { name: 'A run is in progress' })).toBeDisabled()
    fireEvent.click(within(run).getByRole('button', { name: 'Cancel run' }))
    await waitFor(() => expect(fetch.mock.calls.some(c => String(c[0]).includes('/runs/7/cancel'))).toBe(true))
    const cancel = fetch.mock.calls.find(c => String(c[0]).includes('/cancel'))!
    expect(JSON.parse(String((cancel[1] as RequestInit).body))).toEqual({ company_id: 4 })
  })
})
