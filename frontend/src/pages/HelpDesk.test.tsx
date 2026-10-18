import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, onTestFinished, vi } from 'vitest'
import HelpDesk from './HelpDesk'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const TICKET = {
  id: 1, ticket_num: 'BUG-0001', title: 'Board does not load', severity: 'high', category: null,
  status: 'open', reporter_email: 'ana@acme.test', assigned_to: null,
  created_at: '2026-10-01T10:00:00', updated_at: null, resolved_at: null,
}

function stubApi(overrides: Record<string, (init: RequestInit) => Response> = {}) {
  const fetch = vi.fn(async (url: string, init: RequestInit = {}) => {
    const { pathname } = new URL(url)
    const key = `${init.method ?? 'GET'} ${pathname}`
    if (overrides[key]) return overrides[key](init)
    if (key === 'GET /v1/support/tickets') return response(200, { tickets: [TICKET], count: 1 })
    if (key === 'GET /v1/support/tickets/BUG-0001') {
      return response(200, { ...TICKET, description: 'Spinner forever', steps: null, expected: null, actual: null, resolution: null })
    }
    return response(404)
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

function renderAs(role: 'Owner' | 'Manager' | 'Viewer') {
  useAuthStore.setState({ token: 'valid-token', expiresAt: Date.now() + 60_000, memberships: [{ company_id: 4, human_role: role }] })
  return render(
    <QueryClientProvider client={queryClient}>
      <HelpDesk />
    </QueryClientProvider>,
  )
}

describe('HelpDesk', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 4 })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('is offered to managers and owners only', () => {
    const fetch = stubApi()
    renderAs('Viewer')
    expect(screen.getByRole('status')).toHaveTextContent('for Managers and Owners. Your role here is Viewer.')
    expect(fetch).not.toHaveBeenCalled()
  })

  it('filters the ticket list with the parameters the API reads', async () => {
    const fetch = stubApi()
    renderAs('Manager')
    expect(await screen.findByText('Board does not load')).toBeInTheDocument()

    fireEvent.change(screen.getByRole('combobox', { name: 'Status' }), { target: { value: 'waiting' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Impact' }), { target: { value: 'critical' } })
    const email = screen.getByRole('textbox', { name: 'Reporter email' })
    fireEvent.change(email, { target: { value: 'ana@acme.test' } })
    fireEvent.blur(email)

    await waitFor(() => {
      const url = new URL(fetch.mock.calls.at(-1)![0])
      expect(url.pathname).toBe('/v1/support/tickets')
      expect(Object.fromEntries(url.searchParams)).toEqual({ status: 'waiting', severity: 'critical', reporter_email: 'ana@acme.test' })
    })
  })

  it('requires a resolution to close a ticket, then saves it', async () => {
    const patch = vi.fn((init: RequestInit) => {
      void init
      return response(200, { ticket_num: 'BUG-0001', status: 'resolved', updated_at: '2026-10-02T00:00:00' })
    })
    stubApi({ 'PATCH /v1/support/tickets/BUG-0001': patch })
    renderAs('Owner')
    fireEvent.click(await screen.findByRole('button', { name: /Board does not load/ }))

    const detail = await screen.findByRole('region', { name: 'Board does not load' })
    expect(within(detail).getByText('Spinner forever')).toBeInTheDocument()
    const status = within(detail).getByRole('combobox', { name: 'Status' })
    fireEvent.change(status, { target: { value: 'resolved' } })
    expect(within(detail).getByRole('button', { name: 'Save' })).toBeDisabled()
    expect(within(detail).getByText(/Write the resolution/)).toBeInTheDocument()

    fireEvent.change(within(detail).getByRole('textbox', { name: 'Resolution' }), { target: { value: 'Fixed in 1.2' } })
    fireEvent.change(within(detail).getByRole('textbox', { name: 'Assigned to' }), { target: { value: 'rebeca' } })
    fireEvent.click(within(detail).getByRole('button', { name: 'Save' }))

    expect(await within(detail).findByText('Ticket updated')).toBeInTheDocument()
    expect(JSON.parse(String(patch.mock.calls[0][0].body))).toEqual({ status: 'resolved', resolution: 'Fixed in 1.2', assigned_to: 'rebeca' })
  })

  it('downloads the logs, and says so when none exist yet', async () => {
    const blob = new Blob(['{}\n'])
    const logs = vi.fn()
      .mockReturnValueOnce(response(404, { detail: 'Log file not found.' }))
      .mockReturnValueOnce({ ok: true, status: 200, headers: new Headers({ 'Content-Disposition': 'attachment; filename="audit.jsonl"' }), blob: async () => blob } as unknown as Response)
    stubApi({ 'GET /v1/support/logs': logs })
    const createObjectURL = vi.fn(() => 'blob:logs')
    const revokeObjectURL = vi.fn()
    // jsdom has no object URLs; set them for this test and restore them after.
    const original = { createObjectURL: URL.createObjectURL, revokeObjectURL: URL.revokeObjectURL }
    Object.assign(URL, { createObjectURL, revokeObjectURL })
    onTestFinished(() => { Object.assign(URL, original) })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    onTestFinished(() => click.mockRestore())
    renderAs('Manager')

    fireEvent.click(screen.getByRole('button', { name: 'Download logs' }))
    expect(await screen.findByText('No logs have been written yet.')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Download logs' }))
    await waitFor(() => expect(click).toHaveBeenCalledTimes(1))
    expect(createObjectURL).toHaveBeenCalledWith(blob)
    expect((click.mock.instances[0] as unknown as HTMLAnchorElement).download).toBe('audit.jsonl')
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:logs')
    expect(screen.queryByText('No logs have been written yet.')).not.toBeInTheDocument()
  })
})
