import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { IntegrationActions } from './IntegrationActions'
import type { SprintIntegrationPlan } from '../services/integration'
import { queryClient } from '../lib/queryClient'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const latest = { id: 21, status: 'tests_passed', commit: 'abcdef1234', branch: 'sprint-3-integration', base: 'main' }
const plan: SprintIntegrationPlan = {
  sprint_id: 3, sprint_name: 'S3', integration_branch: 'sprint-3-integration', task_branches: [], merge_order: [],
  unmet_dependencies: [], latest,
  review: { integration_id: 21, required: false, approved: false, approving_review_id: null, latest: null, open_comments: 0, published_url: null },
}

function routed(forge = 'publish', merge: Response = response(201, latest)) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const { pathname } = new URL(url)
    if (pathname === '/v1/companies/4/forge_policy') {
      return response(200, { company_id: 4, forge_policy: forge, effective: forge, ceiling: 'publish', policies: ['none', 'fetch', 'publish'] })
    }
    if (init?.method === 'POST' && pathname === '/v1/sprints/3/integrate') return response(201, { id: 22, status: 'tests_passed' })
    if (init?.method === 'POST' && pathname === '/v1/sprints/3/integration/publish') return response(201, latest)
    if (init?.method === 'POST' && pathname === '/v1/sprints/3/integration/merge') return merge
    return response(404)
  })
}

function renderActions(props: { plan?: SprintIntegrationPlan; canManage?: boolean } = {}) {
  useAuthStore.setState({ expiresAt: Date.now() + 60_000, memberships: [{ company_id: 4, human_role: 'Manager' }] })
  return render(
    <QueryClientProvider client={queryClient}>
      <IntegrationActions plan={props.plan ?? plan} companyId={4} canManage={props.canManage ?? true} />
    </QueryClientProvider>,
  )
}

const posts = (fetch: ReturnType<typeof routed>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === 'POST').map(([url]) => new URL(String(url)).pathname)

describe('IntegrationActions', () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('offers a viewer no action and reads no forge policy, but shows where it was published', () => {
    const fetch = routed()
    vi.stubGlobal('fetch', fetch)
    renderActions({
      canManage: false,
      plan: { ...plan, latest: { ...latest, publication: { provider: 'github', branch: 'b', base: 'main', commit: 'abcdef1234', url: 'https://forge.example/pr/1', how: 'gh', updated: false, published_by: 'manager' } } },
    })
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Open the request/ })).toHaveAttribute('href', 'https://forge.example/pr/1')
    expect(fetch).not.toHaveBeenCalled()
  })

  it('integrates now', async () => {
    const fetch = routed()
    vi.stubGlobal('fetch', fetch)
    renderActions({ plan: { ...plan, latest: null } })
    expect(screen.queryByRole('button', { name: 'Merge locally' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Integrate now' }))
    expect(await screen.findByText('Integrated. The plan below shows the result.')).toBeInTheDocument()
    expect(posts(fetch)).toEqual(['/v1/sprints/3/integrate'])
  })

  it('holds publishing back when the forge policy does not allow it, and says why', async () => {
    vi.stubGlobal('fetch', routed('fetch'))
    renderActions()
    const button = screen.getByRole('button', { name: 'Publish as one request' })
    await waitFor(() => expect(screen.getByText(/Publishing is off/)).toBeInTheDocument())
    expect(button).toBeDisabled()
    expect(button).toHaveAccessibleDescription(/Read the base from its remote/)
  })

  it('publishes when the forge policy allows it', async () => {
    const fetch = routed('publish')
    vi.stubGlobal('fetch', fetch)
    renderActions()
    const button = screen.getByRole('button', { name: 'Publish as one request' })
    await waitFor(() => expect(button).toBeEnabled())
    fireEvent.click(button)
    await waitFor(() => expect(posts(fetch)).toEqual(['/v1/sprints/3/integration/publish']))
  })

  it('asks before merging, and shows the backend refusal (segregation of duties)', async () => {
    const fetch = routed('publish', response(403, { detail: 'The person who started this sprint cannot release it' }))
    vi.stubGlobal('fetch', fetch)
    renderActions()
    fireEvent.click(screen.getByRole('button', { name: 'Merge locally' }))
    expect(posts(fetch)).toEqual([])
    expect(screen.getByText(/Nothing is pushed/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Yes, merge' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('cannot release it')
    expect(posts(fetch)).toEqual(['/v1/sprints/3/integration/merge'])
  })

  it('holds the merge back while a required review has not approved it', () => {
    vi.stubGlobal('fetch', routed())
    renderActions({ plan: { ...plan, review: { ...plan.review!, required: true, approved: false } } })
    const button = screen.getByRole('button', { name: 'Merge locally' })
    expect(button).toBeDisabled()
    expect(button).toHaveAccessibleDescription(/approving review/)
  })

  it('shows a merge already made instead of the merge action', () => {
    vi.stubGlobal('fetch', routed())
    renderActions({ plan: { ...plan, latest: { ...latest, details: { local_merge: { merged_by: 'reviewer', merged_at: '2026-11-19T10:00:00+00:00' } } } } })
    expect(screen.getByText(/Merged locally by reviewer/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Merge locally' })).not.toBeInTheDocument()
  })
})
