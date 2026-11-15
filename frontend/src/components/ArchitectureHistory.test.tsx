import { render, screen, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ArchitectureHistory, ArchitectureVersionLine } from './ArchitectureHistory'
import { queryClient } from '../lib/queryClient'
import type { Architecture, CompanyArchitecture } from '../services/architectures'
import { APPROVED_ARCHITECTURE } from '../test/architectureFixtures'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const CURRENT: Architecture = {
  ...APPROVED_ARCHITECTURE,
  is_current: true,
  specification_ids: [3, 1],
  earlier_specifications: [{ id: 1, title: 'Task board', status: 'approved' }],
}

const COMPANY: CompanyArchitecture = {
  company_id: 4,
  current: CURRENT,
  draft: null,
  versions: [
    {
      id: 22, version: 2, status: 'approved', title: 'Login design', specification_id: 3, specification_title: 'Login',
      specification_ids: [3, 1], supersedes_id: 21, drafted_by: 'ana@acme.test', approved_by: 'ana@acme.test',
      approved_at: '2026-10-03T12:00:00Z', created_at: null,
    },
    {
      id: 21, version: 1, status: 'superseded', title: 'Board design', specification_id: 1, specification_title: 'Task board',
      specification_ids: [1], supersedes_id: null, drafted_by: 'Architect', approved_by: 'ana@acme.test',
      approved_at: '2026-09-01T12:00:00Z', created_at: null,
    },
  ],
}

function renderWith(node: React.ReactNode) {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{node}</MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('ArchitectureHistory', () => {
  beforeEach(() => queryClient.clear())

  afterEach(() => {
    vi.unstubAllGlobals()
    queryClient.clear()
  })

  it('lists every version of the company architecture, newest first, the current one marked', async () => {
    const fetch = vi.fn(async (url: string) =>
      new URL(url).pathname === '/v1/companies/4/architecture' ? response(200, COMPANY) : response(404, { detail: 'Not found' }),
    )
    vi.stubGlobal('fetch', fetch)
    renderWith(<ArchitectureHistory architecture={CURRENT} />)
    const items = await screen.findAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(items[0]).toHaveAttribute('aria-current', 'page')
    expect(within(items[0]).getByText('Current')).toBeInTheDocument()
    expect(within(items[0]).getByText('covers 2 specifications')).toBeInTheDocument()
    const older = within(items[1]).getByRole('link', { name: 'v1' })
    expect(older).toHaveAttribute('href', '/company/specifications/1/architectures/21')
    expect(within(items[1]).getByText('drafted for Task board')).toBeInTheDocument()
    expect(within(items[1]).getByText('Superseded')).toBeInTheDocument()
  })

  it('says which version a page shows and what else it covers', () => {
    renderWith(<ArchitectureVersionLine architecture={CURRENT} />)
    expect(screen.getByText("Version 2 of the company's architecture")).toBeInTheDocument()
    expect(screen.getByText('Also covers: Task board')).toBeInTheDocument()
  })
})
