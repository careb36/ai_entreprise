import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import i18n from 'i18next'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SprintIntegration } from './SprintIntegration'
import { queryClient } from '../lib/queryClient'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const COMMIT = 'c'.repeat(40)

const reviewState = {
  integration_id: 5,
  required: true,
  approved: false,
  approving_review_id: null,
  latest: { id: 1, event: 'request_changes', outcome: 'changes_requested', author_name: 'Ana', commit: COMMIT, created_at: '' },
  open_comments: 0,
  published_url: null,
}

const plan = {
  sprint_id: 3,
  sprint_name: 'Sprint 3',
  integration_branch: 'fabrique/sprint-3',
  task_branches: [],
  merge_order: [],
  unmet_dependencies: [],
  latest: { id: 5, status: 'tests_passed', commit: COMMIT, branch: 'fabrique/sprint-3', base: 'main' },
  review: reviewState,
}

const reviews = {
  ...reviewState,
  reviews: [{ id: 1, integration_id: 5, event: 'request_changes', body: 'Rename the helper.', outcome: 'changes_requested', commit: COMMIT, diff_sha256: 'd'.repeat(64), files: 1, published_url: null, sod: null, author_name: 'Ana', created_at: '' }],
  comments: [],
}

const diff = {
  integration_id: 5,
  commit: COMMIT,
  merge_base: 'b'.repeat(40),
  base: 'main',
  base_commit: 'b'.repeat(40),
  truncated: false,
  diff_sha256: 'd'.repeat(64),
  files: [{ path: 's.py', old_path: null, change: 'added', additions: 1, deletions: 0, patch: '@@ -0,0 +1 @@\n+s = 1', omitted: null, sha256_old: null, sha256_new: 'e'.repeat(64) }],
}

function api(url: string, init?: RequestInit) {
  if (init?.method === 'POST' && url.includes('/integration/comments')) return response(201, { id: 9 })
  if (init?.method === 'POST' && url.includes('/integration/reviews')) return response(201, { id: 2, outcome: 'approved' })
  if (init?.method === 'PUT') return response(200, { company_id: 4, required: false })
  if (url.includes('/integration/diff')) return response(200, diff)
  if (url.includes('/integration/reviews')) return response(200, reviews)
  if (url.includes('/integration_review')) return response(200, { company_id: 4, required: true })
  if (url.includes('/integration')) return response(200, plan)
  return response(404)
}

function renderView(props: { canReview?: boolean; canConfigure?: boolean }) {
  return render(
    <QueryClientProvider client={queryClient}>
      <SprintIntegration sprintId={3} companyId={4} {...props} />
    </QueryClientProvider>,
  )
}

const bodyOf = (fetch: ReturnType<typeof vi.fn>, part: string) => {
  const call = fetch.mock.calls.find(c => String(c[0]).includes(part) && (c[1] as RequestInit | undefined)?.method === 'POST')
  return call ? JSON.parse(String((call[1] as RequestInit).body)) : null
}

describe('the review of a sprint integration', () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => {
    vi.unstubAllGlobals()
    queryClient.clear()
  })

  it('shows the latest review and the merge gate, and a Viewer only reads', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => api(url, init)))
    renderView({})
    const history = await screen.findByRole('list', { name: i18n.t('integrationReview.history') })
    expect(within(history).getByText('Rename the helper.')).toBeInTheDocument()
    expect(screen.getByText(i18n.t('integrationReview.gate.waiting'))).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: i18n.t('integrationReview.submit') })).not.toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  })

  it('lets a Manager comment on a line of the daemon diff and approve', async () => {
    const fetch = vi.fn(async (url: string, init?: RequestInit) => api(url, init))
    vi.stubGlobal('fetch', fetch)
    renderView({ canReview: true })
    fireEvent.click(await screen.findByRole('button', { name: i18n.t('integrationReview.showDiff') }))
    fireEvent.click(await screen.findByRole('button', { name: i18n.t('integrationReview.comment.add', { path: 's.py', line: 1 }) }))
    fireEvent.change(screen.getByLabelText(i18n.t('integrationReview.comment.label', { path: 's.py', line: 1 })), { target: { value: 'Name it better' } })
    const lineForm = screen.getByLabelText(i18n.t('integrationReview.comment.label', { path: 's.py', line: 1 })).closest('form') as HTMLElement
    fireEvent.click(within(lineForm).getByRole('button', { name: i18n.t('integrationReview.comment.submit') }))
    await waitFor(() => expect(bodyOf(fetch, '/integration/comments')).toEqual({
      integration_id: 5, path: 's.py', side: 'new', line: 1, body: 'Name it better',
    }))

    fireEvent.click(screen.getByRole('radio', { name: i18n.t('review.event.approve') }))
    fireEvent.click(screen.getByRole('button', { name: i18n.t('integrationReview.submit') }))
    await waitFor(() => expect(bodyOf(fetch, '/integration/reviews')).toEqual({ integration_id: 5, event: 'approve', body: null }))
    expect(await screen.findByRole('status')).toHaveTextContent(i18n.t('integrationReview.outcome.approved'))
  })

  it('lets an Owner turn the merge gate off', async () => {
    const fetch = vi.fn(async (url: string, init?: RequestInit) => api(url, init))
    vi.stubGlobal('fetch', fetch)
    renderView({ canReview: true, canConfigure: true })
    const box = await screen.findByRole('checkbox', { name: i18n.t('integrationReview.setting.label') })
    await waitFor(() => expect(box).toBeChecked())
    fireEvent.click(box)
    await waitFor(() => {
      const put = fetch.mock.calls.find(c => (c[1] as RequestInit | undefined)?.method === 'PUT')
      expect(put && JSON.parse(String((put[1] as RequestInit).body))).toEqual({ required: false })
    })
  })
})
