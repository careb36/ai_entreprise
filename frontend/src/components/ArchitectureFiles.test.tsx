import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AdrFiles } from './AdrFiles'
import { ArchitectureRevision } from './ArchitectureRevision'
import { ManifestDownload } from './documentation/ManifestDownload'
import { queryClient } from '../lib/queryClient'

function response(status: number, body: unknown = {}, headers: Record<string, string> = {}) {
  return {
    ok: status >= 200 && status < 300, status, json: async () => body,
    blob: async () => new Blob([JSON.stringify(body)]), headers: new Headers(headers),
  } as unknown as Response
}

const version = { id: 7, version: 2, status: 'approved', title: 'Arch', specification_id: 3, specification_title: 'S3', specification_ids: [3], supersedes_id: null, drafted_by: null, approved_by: null, approved_at: null, created_at: null }

function renderAt(node: React.ReactNode) {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/here']}>
        <Routes>
          <Route path="/here" element={node} />
          <Route path="/company/specifications/:specId/architectures/:id" element={<p>architecture page</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

let saved: { create: typeof URL.createObjectURL; revoke: typeof URL.revokeObjectURL }
let click: ReturnType<typeof vi.spyOn>

describe('architecture and documentation files', () => {
  beforeEach(() => {
    queryClient.clear()
    saved = { create: URL.createObjectURL, revoke: URL.revokeObjectURL }
    Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() })
    click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    click.mockRestore()
    Object.assign(URL, { createObjectURL: saved.create, revokeObjectURL: saved.revoke })
    queryClient.clear()
  })

  it('opens a revision of the company architecture for the specification, then goes to it', async () => {
    const fetch = vi.fn(async (url: string, init?: RequestInit) => {
      const { pathname } = new URL(url)
      if (pathname === '/v1/companies/4/architecture') return response(200, { company_id: 4, current: { ...version }, draft: null, versions: [version] })
      if (pathname === '/v1/companies/4/architecture/revisions' && init?.method === 'POST') return response(201, { ...version, id: 9, version: 3, status: 'draft', specification_id: 5 })
      return response(404)
    })
    vi.stubGlobal('fetch', fetch)
    renderAt(<ArchitectureRevision companyId={4} specificationId={5} />)
    fireEvent.click(await screen.findByRole('button', { name: /Revise the company's architecture \(version 2\)/ }))
    expect(await screen.findByText('architecture page')).toBeInTheDocument()
    const post = fetch.mock.calls.find(([, init]) => init?.method === 'POST')
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ specification_id: 5 })
  })

  it('links to the open draft instead of offering a second revision', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response(200, { company_id: 4, current: version, draft: { ...version, id: 9, version: 3, status: 'draft' }, versions: [] })))
    renderAt(<ArchitectureRevision companyId={4} specificationId={5} />)
    expect(await screen.findByRole('link', { name: 'Open the draft' })).toHaveAttribute('href', '/company/specifications/3/architectures/9')
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('offers nothing before the company has an approved architecture', async () => {
    const fetch = vi.fn(async () => response(200, { company_id: 4, current: null, draft: null, versions: [] }))
    vi.stubGlobal('fetch', fetch)
    const { container } = renderAt(<ArchitectureRevision companyId={4} specificationId={5} />)
    await waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('prepares the ADR files from the chosen first number and saves one as Markdown', async () => {
    const fetch = vi.fn(async (_url: string) => response(200, {
      architecture_id: 7, files: [{ id: 'D1', number: 12, path: 'docs/adr/0012-use-postgres.md', markdown: '# 12. Use Postgres' }],
    }))
    vi.stubGlobal('fetch', fetch)
    renderAt(<AdrFiles architectureId={7} />)
    fireEvent.change(screen.getByLabelText('Number of the first ADR'), { target: { value: '12' } })
    fireEvent.click(screen.getByRole('button', { name: 'Prepare the files' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Download docs/adr/0012-use-postgres.md' }))
    expect(new URL(String(fetch.mock.calls[0][0])).searchParams.get('first_number')).toBe('12')
    expect(click).toHaveBeenCalled()
  })

  it('downloads a pack manifest under the name the backend gives', async () => {
    const fetch = vi.fn(async (_url: string) => response(200, { manifest: {} }, { 'Content-Disposition': 'attachment; filename="documentation-pack-2-manifest.json"' }))
    vi.stubGlobal('fetch', fetch)
    renderAt(<ManifestDownload packId={2} />)
    fireEvent.click(screen.getByRole('button', { name: 'Signed manifest' }))
    await waitFor(() => expect(click).toHaveBeenCalled())
    expect(String(fetch.mock.calls[0][0])).toContain('/v1/documentation/2/manifest')
  })
})
