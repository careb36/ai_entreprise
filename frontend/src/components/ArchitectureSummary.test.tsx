import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ArchitectureSummary } from './ArchitectureSummary'
import { queryClient } from '../lib/queryClient'
import type { Specification } from '../services/specifications'
import { APPROVED_ARCHITECTURE, DRAFT_ARCHITECTURE } from '../test/architectureFixtures'
import { APPROVED, DRAFT } from '../test/specificationFixtures'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

function stubApi(overrides: Record<string, () => Response> = {}) {
  const fetch = vi.fn(async (url: string, init: RequestInit = {}) => {
    const { pathname } = new URL(url)
    const key = `${init.method ?? 'GET'} ${pathname}`
    if (overrides[key]) return overrides[key]()
    return response(404, { detail: 'Not found' })
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

function renderSummary(specification: Specification, canWrite: boolean) {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/company/specifications/${specification.id}`]}>
        <Routes>
          <Route path="/company/specifications/:id" element={<ArchitectureSummary specification={specification} canWrite={canWrite} />} />
          <Route path="/company/specifications/:specId/architectures/:id" element={<div data-testid="architecture" />} />
          <Route path="/company/specifications/:specId/architecture/new" element={<div data-testid="editor" />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('ArchitectureSummary', () => {
  beforeEach(() => queryClient.clear())

  afterEach(() => {
    vi.unstubAllGlobals()
    queryClient.clear()
  })

  it('shows nothing for a draft specification, and asks for no architectures', () => {
    const fetch = stubApi()
    const { container } = renderSummary(DRAFT, true)
    expect(container).toBeEmptyDOMElement()
    expect(fetch).not.toHaveBeenCalled()
  })

  it('lets a Manager ask the Architect for a design of an approved specification and opens it', async () => {
    const fetch = stubApi({
      'GET /v1/architectures': () => response(200, { architectures: [] }),
      'POST /v1/architectures/draft': () => response(200, DRAFT_ARCHITECTURE),
    })
    renderSummary(APPROVED, true)

    const section = await screen.findByRole('region', { name: 'Architecture' })
    expect(await within(section).findByText(/No architecture yet/)).toBeInTheDocument()
    expect(new URL(fetch.mock.calls[0][0]).searchParams.get('specification_id')).toBe('3')
    fireEvent.click(within(section).getByRole('button', { name: 'Ask the Architect for a design' }))

    expect(await screen.findByTestId('architecture')).toBeInTheDocument()
    const call = fetch.mock.calls.find(([url, init]) => new URL(url).pathname === '/v1/architectures/draft' && init?.method === 'POST')!
    expect(JSON.parse(String(call[1]!.body))).toEqual({ specification_id: 3 })
  })

  it('opens the editor to write one, and shows the Architect\'s refusal', async () => {
    stubApi({
      'GET /v1/architectures': () => response(200, { architectures: [] }),
      'POST /v1/architectures/draft': () => response(502, { detail: 'The Architect gave nothing usable' }),
    })
    renderSummary(APPROVED, true)

    const section = await screen.findByRole('region', { name: 'Architecture' })
    fireEvent.click(await within(section).findByRole('button', { name: 'Ask the Architect for a design' }))
    expect(await within(section).findByRole('alert')).toHaveTextContent('The Architect could not design it: The Architect gave nothing usable')
    fireEvent.click(within(section).getByRole('button', { name: 'Write it yourself' }))
    expect(await screen.findByTestId('editor')).toBeInTheDocument()
  })

  it('links to the approved architecture before a newer draft, with nothing to ask for', async () => {
    stubApi({ 'GET /v1/architectures': () => response(200, { architectures: [{ ...DRAFT_ARCHITECTURE, id: 25, version: 3 }, APPROVED_ARCHITECTURE] }) })
    renderSummary(APPROVED, true)

    const section = await screen.findByRole('region', { name: 'Architecture' })
    const link = await within(section).findByRole('link', { name: 'Login design' })
    expect(link).toHaveAttribute('href', '/company/specifications/3/architectures/22')
    expect(section).toHaveTextContent('v2')
    expect(section).toHaveTextContent('Approved')
    expect(section).toHaveTextContent('Ready to approve')
    expect(within(section).queryByRole('button')).not.toBeInTheDocument()
  })

  it('offers a new one when the latest is superseded, but not to a Viewer', async () => {
    stubApi({ 'GET /v1/architectures': () => response(200, { architectures: [{ ...APPROVED_ARCHITECTURE, status: 'superseded' }] }) })
    const { unmount } = renderSummary(APPROVED, true)
    const section = await screen.findByRole('region', { name: 'Architecture' })
    expect(await within(section).findByRole('button', { name: 'Ask the Architect for a design' })).toBeInTheDocument()
    expect(section).toHaveTextContent('Superseded')
    unmount()

    renderSummary(APPROVED, false)
    const viewed = await screen.findByRole('region', { name: 'Architecture' })
    expect(await within(viewed).findByRole('link', { name: 'Login design' })).toBeInTheDocument()
    expect(within(viewed).queryByRole('button')).not.toBeInTheDocument()
  })
})
