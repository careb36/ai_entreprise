import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Wiki from './Wiki'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const SUMMARY = {
  id: 1, slug: 'mod-store', kind: 'module', title: 'MOD-1 · Store', status: 'stale', version: 2,
  person_locked: false, proposed_version_id: 9, module: { id: 'MOD-1', name: 'Store', path: 'src/store' },
  lint_reasons: [{ rule: 'file_changed' }], linted_at: null, updated_at: null,
}
const PAGE = {
  ...SUMMARY,
  body: 'Saves rows [run:12].',
  language: 'en',
  blocks: [
    { kind: 'heading', line: 1, text: '# Store', sources: [], statement: false },
    {
      kind: 'paragraph', line: 3, text: 'Saves rows <img src=x onerror=alert(1)> [run:12].', statement: true,
      sources: [{ kind: 'run', ref: '12', known: true, verdict: 'verified' }],
    },
    { kind: 'fact', line: 5, text: 'store.engine: sqlite [run:12]', statement: true, sources: [{ kind: 'run', ref: '12', known: true, verdict: 'verified' }] },
  ],
  sources: [],
  links: [],
  lint: [{ rule: 'file_changed', text: 'cites src/store/db.py@abcdefabcdef, observed now as 0123456789ab' }],
  current_version: null,
  proposed_version: {
    id: 9, page_id: 1, number: 3, author_kind: 'agent', author: 'TechWriter', sprint_id: 4, title: 'MOD-1 · Store',
    body: 'New text.', sources: [], findings: [{ line: 3, excerpt: 'Uncited claim.' }], status: 'proposed',
    decided_by: null, decided_at: null, decision_reason: null, created_at: null,
    blocks: [{ kind: 'paragraph', line: 3, text: 'Uncited claim.', sources: [], statement: true }],
  },
}

function stubApi() {
  const fetch = vi.fn(async (url: string, init: RequestInit = {}) => {
    const { pathname } = new URL(url)
    const key = `${init.method ?? 'GET'} ${pathname}`
    if (key === 'GET /v1/companies/4/wiki') {
      return response(200, { pages: [SUMMARY], kinds: ['module', 'concept', 'glossary', 'decisions'], language: 'en', settings: { write_to_repo: false } })
    }
    if (key === 'GET /v1/companies/4/wiki/mod-store') return response(200, PAGE)
    if (key === 'POST /v1/wiki_versions/9/accept') return response(200, { ...PAGE, proposed_version: null })
    if (key === 'PUT /v1/companies/4/wiki/mod-store') return response(200, PAGE)
    if (key === 'PUT /v1/companies/4/wiki-settings') return response(200, { write_to_repo: true })
    return response(404)
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

function renderAs(role: 'Manager' | 'Viewer') {
  useAuthStore.setState({ expiresAt: Date.now() + 60_000, memberships: [{ company_id: 4, human_role: role }] })
  return render(
    <QueryClientProvider client={queryClient}>
      <Wiki />
    </QueryClientProvider>,
  )
}

describe('Wiki', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 4 })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('shows a page with its sources beside each statement, its lint and no injected HTML', async () => {
    stubApi()
    const { container } = renderAs('Viewer')
    const article = await screen.findByRole('article')
    expect(within(article).getByRole('heading', { name: 'MOD-1 · Store' })).toBeInTheDocument()
    expect(within(article).getAllByText('Stale').length).toBeGreaterThan(0)
    const lint = within(article).getByRole('alert')
    expect(lint).toHaveTextContent('File changed: cites src/store/db.py@abcdefabcdef')
    expect(within(article).getAllByRole('list', { name: 'Sources' })[0]).toHaveTextContent('run #12 · verified')
    expect(article).toHaveTextContent('store.engine: sqlite [run:12]')
    expect(container.querySelector('img')).toBeNull()
    const proposal = within(article).getByRole('region', { name: 'Proposed version' })
    expect(proposal).toHaveTextContent('Line 3 has no source: Uncited claim.')
    expect(within(proposal).getByText('no source')).toBeInTheDocument()
    // A Viewer reads; only a Manager decides or edits.
    expect(within(proposal).queryByRole('button', { name: 'Accept' })).not.toBeInTheDocument()
    expect(within(article).queryByRole('button', { name: 'Edit' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'New page' })).not.toBeInTheDocument()
  })

  it('lets a Manager accept a proposal, edit a page and change the settings', async () => {
    const fetch = stubApi()
    renderAs('Manager')
    const article = await screen.findByRole('article')
    fireEvent.click(within(article).getByRole('button', { name: 'Accept' }))
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/v1/wiki_versions/9/accept'), expect.objectContaining({ method: 'POST' })))

    fireEvent.click(within(article).getByRole('button', { name: 'Edit' }))
    fireEvent.change(screen.getByLabelText('Body (Markdown)'), { target: { value: 'Person text.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => {
      const put = fetch.mock.calls.find(([url, init]) => String(url).endsWith('/v1/companies/4/wiki/mod-store') && init?.method === 'PUT')
      expect(put).toBeTruthy()
      expect(JSON.parse(String(put?.[1]?.body))).toEqual({ kind: 'module', title: 'MOD-1 · Store', body: 'Person text.' })
    })

    fireEvent.click(screen.getByRole('checkbox'))
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/v1/companies/4/wiki-settings'), expect.objectContaining({ method: 'PUT' })))
  })
})
