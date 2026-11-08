import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Specifications from './Specifications'
import { queryClient } from '../lib/queryClient'
import { APPROVED, DRAFT } from '../test/specificationFixtures'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

function stubApi(overrides: Record<string, () => Response> = {}) {
  const fetch = vi.fn(async (url: string, init: RequestInit = {}) => {
    const { pathname } = new URL(url)
    const key = `${init.method ?? 'GET'} ${pathname}`
    if (overrides[key]) return overrides[key]()
    if (key === 'GET /v1/specifications') return response(200, { specifications: [DRAFT, APPROVED] })
    return response(404)
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

function renderAs(role: 'Owner' | 'Manager' | 'Viewer') {
  useAuthStore.setState({ token: 'valid-token', expiresAt: Date.now() + 60_000, memberships: [{ company_id: 4, human_role: role }] })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/company/specifications']}>
        <Routes>
          <Route path="/company/specifications" element={<Specifications />} />
          <Route path="/company/specifications/:id" element={<div data-testid="detail" />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Specifications', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 4 })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('lists each specification with its version, status, author and check summary', async () => {
    const fetch = stubApi()
    renderAs('Viewer')

    const list = await screen.findByRole('list', { name: 'Specifications' })
    const rows = within(list).getAllByRole('listitem')
    expect(rows).toHaveLength(2)
    expect(within(rows[0]).getByRole('link', { name: 'Checkout' })).toHaveAttribute('href', '/company/specifications/5')
    expect(rows[0]).toHaveTextContent('v1')
    expect(rows[0]).toHaveTextContent('Draft')
    expect(rows[0]).toHaveTextContent('Drafted by PO')
    expect(rows[0]).toHaveTextContent('1 blocking · 1 warnings')
    expect(rows[1]).toHaveTextContent('Approved')
    expect(rows[1]).toHaveTextContent('v2')
    expect(rows[1]).toHaveTextContent('Ready to approve')
    expect(new URL(fetch.mock.calls[0][0]).searchParams.get('company_id')).toBe('4')
    // A Viewer reads: no form to ask the PO, nothing to write.
    expect(screen.queryByRole('region', { name: 'Ask the PO for a draft' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Write one yourself' })).not.toBeInTheDocument()
  })

  it('lets a Manager ask the PO for a draft from a brief and opens it', async () => {
    const fetch = stubApi({ 'POST /v1/specifications/draft': () => response(200, { ...DRAFT, id: 7 }) })
    renderAs('Manager')

    const form = await screen.findByRole('region', { name: 'Ask the PO for a draft' })
    fireEvent.change(within(form).getByRole('textbox', { name: 'Brief' }), { target: { value: ' Sell shoes online ' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Ask the PO for a draft' }))

    expect(await screen.findByTestId('detail')).toBeInTheDocument()
    const call = fetch.mock.calls.find(([url, init]) => new URL(url).pathname === '/v1/specifications/draft' && init?.method === 'POST')!
    expect(JSON.parse(String(call[1]!.body))).toEqual({ company_id: 4, brief: 'Sell shoes online' })
  })

  it("shows the backend's reason when the PO gave nothing usable", async () => {
    stubApi({ 'POST /v1/specifications/draft': () => response(502, { detail: 'The PO gave nothing usable' }) })
    renderAs('Manager')

    const form = await screen.findByRole('region', { name: 'Ask the PO for a draft' })
    fireEvent.change(within(form).getByRole('textbox', { name: 'Brief' }), { target: { value: 'Sell shoes' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Ask the PO for a draft' }))
    expect(await within(form).findByRole('alert')).toHaveTextContent('The PO could not draft it: The PO gave nothing usable')
  })

  it('lets a Manager write a specification and opens it once saved', async () => {
    const fetch = stubApi({ 'POST /v1/specifications': () => response(201, { ...DRAFT, id: 8, drafted_by: 'ana@acme.test' }) })
    renderAs('Owner')

    fireEvent.click(await screen.findByRole('button', { name: 'Write one yourself' }))
    const editor = screen.getByRole('form', { name: 'Specification editor' })
    fireEvent.change(within(editor).getByRole('textbox', { name: 'Title' }), { target: { value: 'Returns' } })
    fireEvent.change(within(editor).getByRole('textbox', { name: 'Title of requirement 1' }), { target: { value: 'Refund' } })
    fireEvent.change(within(editor).getByRole('textbox', { name: 'Given of criterion 1.1' }), { target: { value: 'a paid order' } })
    fireEvent.change(within(editor).getByRole('textbox', { name: 'When of criterion 1.1' }), { target: { value: 'it is returned' } })
    fireEvent.change(within(editor).getByRole('textbox', { name: 'Then of criterion 1.1' }), { target: { value: 'the money comes back' } })
    fireEvent.click(within(editor).getByRole('button', { name: 'Save' }))

    expect(await screen.findByTestId('detail')).toBeInTheDocument()
    const call = fetch.mock.calls.find(([url, init]) => new URL(url).pathname === '/v1/specifications' && init?.method === 'POST')!
    expect(JSON.parse(String(call[1]!.body))).toEqual({
      company_id: 4, title: 'Returns', summary: '',
      requirements: [{ title: 'Refund', description: '', criteria: [{ given: 'a paid order', when: 'it is returned', then: 'the money comes back' }] }],
      quality: [], constraints: [],
    })
  })

  it('offers a retry when loading fails', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(response(403, { detail: 'Not a member of this company' }))
      .mockResolvedValue(response(200, { specifications: [] }))
    vi.stubGlobal('fetch', fetch)
    renderAs('Viewer')
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Could not load the specifications: Not a member of this company')
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('No specifications yet')).toBeInTheDocument()
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))
  })
})
