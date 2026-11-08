import { fireEvent, render, screen } from '@testing-library/react'
import i18n from 'i18next'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PublishBranch } from './PublishBranch'
import { ApiError } from '../services/api'
import { type ReviewComment, type RunPublication, useMergeRun, usePublishRun, useReviewComments, useRunPublications, useSyncComments } from '../services/runReviews'

vi.mock('../services/runReviews', () => ({
  useMergeRun: vi.fn(),
  usePublishRun: vi.fn(),
  useRunPublications: vi.fn(),
  useReviewComments: vi.fn(),
  useSyncComments: vi.fn(),
  useResolveReviewComment: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}))

const publish = vi.fn()
const merge = vi.fn()
const sync = vi.fn()

const FROM_GITHUB: ReviewComment = {
  id: 9, agent_run_id: 7, path: null, line: null, side: 'new', body: 'Needs tests.', author_name: 'ana',
  review_id: null, created_at: '', resolved_at: null, source: 'github', external_id: 'github:review:10',
  external_url: 'https://github.com/ana/app/pull/12#pullrequestreview-10', external_state: 'CHANGES_REQUESTED',
}

const PULL_REQUEST: RunPublication = {
  id: 1, agent_run_id: 7, provider: 'github', branch: 'fabrique/task-7', base: 'main',
  commit: 'abcdef0123456789', url: 'https://github.com/ana/app/pull/12', how: 'gh',
  published_by: 'Ana', created_at: '', updated: false,
}

function setup({
  publications = [] as RunPublication[], mutation = {} as Record<string, unknown>, canPublish = true,
  comments = [] as ReviewComment[], syncState = {} as Record<string, unknown>, shownPaths = [] as string[],
  mergeState = {} as Record<string, unknown>,
} = {}) {
  vi.mocked(useRunPublications).mockReturnValue({ data: { publications } } as never)
  vi.mocked(usePublishRun).mockReturnValue({ mutate: publish, isPending: false, isError: false, ...mutation } as never)
  vi.mocked(useMergeRun).mockReturnValue({ mutate: merge, isPending: false, isError: false, ...mergeState } as never)
  vi.mocked(useReviewComments).mockReturnValue({ data: { comments } } as never)
  vi.mocked(useSyncComments).mockReturnValue({ mutate: sync, isPending: false, isError: false, isSuccess: false, ...syncState } as never)
  return render(<PublishBranch companyId={42} runId={7} branch="fabrique/task-7" canPublish={canPublish} shownPaths={shownPaths} />)
}

describe('publishing a run branch', () => {
  beforeEach(() => vi.clearAllMocks())

  it('lets a Manager publish the branch, as an option', () => {
    setup()
    const hint = `${i18n.t('review.publish.mergeHint', { branch: 'fabrique/task-7' })} ${i18n.t('review.publish.hint', { branch: 'fabrique/task-7' })}`
    expect(screen.getByText(hint)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: i18n.t('review.publish.button') }))
    expect(publish).toHaveBeenCalled()
    expect(merge).not.toHaveBeenCalled()
  })

  it('lets a Manager merge the branch locally, with no remote', () => {
    setup()
    fireEvent.click(screen.getByRole('button', { name: i18n.t('review.publish.merge') }))
    expect(merge).toHaveBeenCalled()
    expect(publish).not.toHaveBeenCalled()
  })

  it('shows a local merge without a link or comments to bring', () => {
    setup({ publications: [{ ...PULL_REQUEST, provider: null, url: null, how: 'local' }] })
    expect(screen.getByText(i18n.t('review.publish.merged', { name: 'Ana', commit: 'abcdef0', branch: 'fabrique/task-7', base: 'main' }))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('review.publish.how.local'))).toBeInTheDocument()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: i18n.t('review.publish.sync') })).not.toBeInTheDocument()
  })

  it('shows why the local merge was refused', () => {
    setup({ mergeState: { isError: true, error: new ApiError(409, 'the merge conflicts in a.py; nothing was changed', 'the merge conflicts in a.py; nothing was changed') } })
    expect(screen.getByRole('alert')).toHaveTextContent('conflicts in a.py')
  })

  it('links the request it opened, for everyone', () => {
    setup({ publications: [PULL_REQUEST], canPublish: false })
    const link = screen.getByRole('link', { name: i18n.t('review.publish.how.gh') })
    expect(link).toHaveAttribute('href', 'https://github.com/ana/app/pull/12')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(screen.getByText(i18n.t('review.publish.pushed', { name: 'Ana', commit: 'abcdef0', branch: 'fabrique/task-7', base: 'main' }))).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('says when an open request had its description updated', () => {
    setup({ publications: [{ ...PULL_REQUEST, updated: true }] })
    expect(screen.getByText(i18n.t('review.publish.updated'))).toBeInTheDocument()
  })

  it('says a remote without a web page was still pushed', () => {
    setup({ publications: [{ ...PULL_REQUEST, url: null, how: 'compare' }] })
    expect(screen.getByText(i18n.t('review.publish.noUrl'))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: i18n.t('review.publish.again') })).toBeInTheDocument()
  })

  it('shows nothing to a Viewer when it was never published', () => {
    const { container } = setup({ canPublish: false })
    expect(container).toBeEmptyDOMElement()
  })

  it('says why the daemon refused', () => {
    setup({ mutation: { isError: true, error: new ApiError(409, 'the repository has no origin to push to', 'the repository has no origin to push to') } })
    expect(screen.getByRole('alert')).toHaveTextContent('no origin')
  })

  it('lets a Manager bring the comments of an opened request, and says what came', () => {
    setup({ publications: [PULL_REQUEST], syncState: { isSuccess: true, data: { added: 2, updated: 1 } } })
    fireEvent.click(screen.getByRole('button', { name: i18n.t('review.publish.sync') }))
    expect(sync).toHaveBeenCalledWith(1)
    expect(screen.getByRole('status')).toHaveTextContent(i18n.t('review.publish.synced', { added: 2, updated: 1 }))
  })

  it('warns when only part of the comments came', () => {
    setup({ publications: [PULL_REQUEST], syncState: { isSuccess: true, data: { added: 500, updated: 0, truncated: true } } })
    expect(screen.getByRole('note')).toHaveTextContent(i18n.t('review.publish.truncated'))
  })

  it('offers no comments to bring for a compare page, nor to a Viewer', () => {
    setup({ publications: [{ ...PULL_REQUEST, how: 'compare' }] })
    expect(screen.queryByRole('button', { name: i18n.t('review.publish.sync') })).not.toBeInTheDocument()
    setup({ publications: [PULL_REQUEST], canPublish: false })
    expect(screen.queryByRole('button', { name: i18n.t('review.publish.sync') })).not.toBeInTheDocument()
  })

  it('shows the comments from the request that are on no line shown, linked to the platform', () => {
    const onShownLine = { ...FROM_GITHUB, id: 10, path: 'a.py', line: 3, body: 'On a line.' }
    setup({ publications: [PULL_REQUEST], comments: [FROM_GITHUB, onShownLine], shownPaths: ['a.py'] })
    expect(screen.getByText('Needs tests.')).toBeInTheDocument()
    expect(screen.queryByText('On a line.')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: i18n.t('review.comment.on.github') })).toHaveAttribute('href', FROM_GITHUB.external_url)
    expect(screen.getByText(i18n.t('review.comment.state.CHANGES_REQUESTED'))).toBeInTheDocument()
  })
})
