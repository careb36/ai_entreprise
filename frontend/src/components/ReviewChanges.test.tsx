import { fireEvent, render, screen, within } from '@testing-library/react'
import i18n from 'i18next'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ReviewChanges, ROWS_SHOWN } from './ReviewChanges'
import { type FileChange, type RunChanges, useRunChanges } from '../services/reviewChanges'
import { type ReviewComment, useReviewComments } from '../services/runReviews'

vi.mock('../services/reviewChanges', () => ({ useRunChanges: vi.fn() }))

const addComment = vi.fn()
const resolveComment = vi.fn()
vi.mock('../services/runReviews', () => ({
  useReviewComments: vi.fn(() => ({ data: { comments: [] } })),
  useAddReviewComment: vi.fn(() => ({ mutate: addComment, isPending: false, isError: false })),
  useResolveReviewComment: vi.fn(() => ({ mutate: resolveComment, isPending: false })),
}))

const refetch = vi.fn()

function file(overrides: Partial<FileChange>): FileChange {
  return { path: 'src/app.py', old_path: null, change: 'modified', additions: 1, deletions: 1, patch: '@@ -1,2 +1,2 @@\n keep\n-old\n+new', omitted: null, ...overrides }
}

function changes(overrides: Partial<RunChanges> = {}): RunChanges {
  const files = overrides.files ?? [file({})]
  return {
    run_id: 7, source: 'workspace', observed_by: { verification_id: 3, observed_at: '2026-11-03T10:00:00Z' }, reason: null,
    files, files_not_shown: 0,
    totals: { files: files.length, additions: files.reduce((n, f) => n + f.additions, 0), deletions: files.reduce((n, f) => n + f.deletions, 0) },
    declared_not_observed: [], ...overrides,
  }
}

function answer(state: { data?: RunChanges; isLoading?: boolean; isError?: boolean }) {
  vi.mocked(useRunChanges).mockReturnValue({ isLoading: false, isError: false, refetch, ...state } as never)
}

describe('files changed', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows each line with its old and new number, marked as a review does', () => {
    answer({ data: changes() })
    render(<ReviewChanges companyId={42} runId={7} />)

    expect(screen.getByText(i18n.t('review.summary', { count: 1 }))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('review.sourceWorkspace', { id: 3 }))).toBeInTheDocument()
    const card = screen.getByRole('article', { name: 'src/app.py' })
    expect(within(card).getByText(i18n.t('review.change.modified'))).toBeInTheDocument()
    const rows = card.querySelectorAll('tr')
    expect([...rows].map(row => row.getAttribute('data-kind'))).toEqual(['hunk', 'context', 'del', 'add'])
    expect([...rows[2].querySelectorAll('td')].map(cell => cell.textContent)).toEqual(['2', '', '-', 'old'])
    expect([...rows[3].querySelectorAll('td')].map(cell => cell.textContent)).toEqual(['', '2', '+', 'new'])
  })

  it('lists the files when there are several, and folds one marked as viewed', () => {
    answer({ data: changes({ files: [file({}), file({ path: 'docs/new.md', change: 'added', additions: 2, deletions: 0, patch: '@@ -0,0 +1,2 @@\n+# A\n+b' })] }) })
    render(<ReviewChanges companyId={42} runId={7} />)

    const list = screen.getByRole('navigation', { name: i18n.t('review.fileList') })
    expect(within(list).getByRole('link', { name: 'docs/new.md' })).toHaveAttribute('href', '#file-docs-new-md')
    const card = screen.getByRole('article', { name: 'docs/new.md' })
    fireEvent.click(within(card).getByLabelText(i18n.t('review.viewed')))
    expect(card.querySelector('table')).toBeNull()
  })

  it('says why a file has no diff', () => {
    answer({ data: changes({ files: [
      file({ path: 'logo.png', patch: null, omitted: 'binary', additions: 0, deletions: 0 }),
      file({ path: 'same.txt', change: 'unchanged', patch: null, additions: 0, deletions: 0 }),
      file({ path: 'README.md', change: 'not_written', status: 'skipped_protected', patch: null, additions: 0, deletions: 0 }),
      file({ path: 'old.py', change: null, patch: null, omitted: 'not_recorded', additions: 0, deletions: 0 }),
    ] }) })
    render(<ReviewChanges companyId={42} runId={7} />)

    expect(screen.getByText(i18n.t('review.omitted.binary'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('review.unchanged'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('review.notWritten', { status: 'skipped_protected' }))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('review.omitted.not_recorded'))).toBeInTheDocument()
  })

  it('keeps what the producer only declared apart from the changes', () => {
    answer({ data: changes({ declared_not_observed: ['README.md', 'docs/x.md'] }) })
    render(<ReviewChanges companyId={42} runId={7} />)
    expect(screen.getByRole('note')).toHaveTextContent('README.md, docs/x.md')
    expect(screen.queryByRole('article', { name: 'README.md' })).not.toBeInTheDocument()
  })

  it('shows a long file in part, then all of it on request', () => {
    const lines = Array.from({ length: ROWS_SHOWN + 50 }, (_, i) => `+line ${i}`).join('\n')
    answer({ data: changes({ files: [file({ change: 'added', patch: `@@ -0,0 +1,${ROWS_SHOWN + 50} @@\n${lines}`, additions: ROWS_SHOWN + 50, deletions: 0 })] }) })
    render(<ReviewChanges companyId={42} runId={7} />)

    expect(document.querySelectorAll('tr')).toHaveLength(ROWS_SHOWN)
    fireEvent.click(screen.getByRole('button', { name: i18n.t('review.showAllLines', { count: ROWS_SHOWN + 51 }) }))
    expect(document.querySelectorAll('tr')).toHaveLength(ROWS_SHOWN + 51)
  })

  it('says nothing was observed, with the reason', () => {
    answer({ data: changes({ source: null, observed_by: null, reason: 'no report from the workspace daemon', files: [], declared_not_observed: ['app.py'] }) })
    render(<ReviewChanges companyId={42} runId={7} />)
    expect(screen.getByText(i18n.t('review.nothingObserved'))).toBeInTheDocument()
    expect(screen.getByText('no report from the workspace daemon')).toBeInTheDocument()
    expect(screen.getByRole('note')).toHaveTextContent('app.py')
  })

  it('names the pull request as the source of its diff', () => {
    answer({ data: changes({ source: 'pull_request', observed_by: null }) })
    render(<ReviewChanges companyId={42} runId={7} />)
    expect(screen.getByText(i18n.t('review.sourcePullRequest'))).toBeInTheDocument()
  })

  it('says when the diff is only what the producer sent', () => {
    answer({ data: changes({ source: 'artifact', observed_by: null }) })
    render(<ReviewChanges companyId={42} runId={7} />)
    expect(screen.getByText(i18n.t('review.sourceArtifact'))).toBeInTheDocument()
  })

  it('lets a Manager comment on a line, and shows the thread under it', () => {
    answer({ data: changes() })
    const comment: ReviewComment = {
      id: 5, agent_run_id: 7, path: 'src/app.py', line: 2, side: 'new', body: 'Name it better.',
      author_name: 'Ana', review_id: null, created_at: '2026-11-03T10:00:00Z', resolved_at: null,
    }
    vi.mocked(useReviewComments).mockReturnValue({ data: { comments: [comment] } } as never)
    render(<ReviewChanges companyId={42} runId={7} canComment />)

    expect(screen.getByText('Name it better.')).toBeInTheDocument()
    expect(screen.getByText(i18n.t('review.comment.pending'))).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: i18n.t('review.comment.resolve') }))
    expect(resolveComment).toHaveBeenCalledWith(5)

    // The deleted line is commented on its old side.
    fireEvent.click(within(document.querySelector('tr[data-kind="del"]') as HTMLElement).getByRole('button', { name: i18n.t('review.comment.add', { line: 2 }) }))
    fireEvent.change(screen.getByLabelText(i18n.t('review.comment.label', { line: 2 })), { target: { value: ' Why? ' } })
    fireEvent.click(screen.getByRole('button', { name: i18n.t('review.comment.submit') }))
    expect(addComment).toHaveBeenCalledWith({ path: 'src/app.py', side: 'old', line: 2, body: 'Why?' }, expect.anything())
  })

  it('offers a Viewer no comment button', () => {
    answer({ data: changes() })
    vi.mocked(useReviewComments).mockReturnValue({ data: { comments: [] } } as never)
    render(<ReviewChanges companyId={42} runId={7} />)
    expect(screen.queryByRole('button', { name: i18n.t('review.comment.add', { line: 1 }) })).not.toBeInTheDocument()
  })

  it('offers a retry when the changes could not be loaded', () => {
    answer({ isError: true })
    render(<ReviewChanges companyId={42} runId={7} />)
    fireEvent.click(within(screen.getByRole('alert')).getByRole('button', { name: i18n.t('common.retry') }))
    expect(refetch).toHaveBeenCalled()
  })
})
