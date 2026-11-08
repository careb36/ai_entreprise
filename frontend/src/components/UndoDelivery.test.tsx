import { fireEvent, render, screen } from '@testing-library/react'
import i18n from 'i18next'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { UndoDelivery } from './UndoDelivery'
import { ReviewChanges } from './ReviewChanges'
import { ApiError } from '../services/api'
import type { DeliverySnapshot, RunChanges } from '../services/reviewChanges'
import { useRunChanges } from '../services/reviewChanges'
import { useUndoDelivery } from '../services/runReviews'

vi.mock('../services/runReviews', () => ({
  useUndoDelivery: vi.fn(),
  usePublishRun: vi.fn(() => ({ mutate: vi.fn(), isPending: false, isError: false })),
  useMergeRun: vi.fn(() => ({ mutate: vi.fn(), isPending: false, isError: false })),
  useRunPublications: vi.fn(() => ({ data: { publications: [] } })),
  useReviewComments: vi.fn(() => ({ data: { comments: [] } })),
  useSyncComments: vi.fn(() => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false })),
  useAddReviewComment: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useResolveReviewComment: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}))
vi.mock('../services/reviewChanges', async importOriginal => ({
  ...(await importOriginal<typeof import('../services/reviewChanges')>()),
  useRunChanges: vi.fn(),
}))

const undo = vi.fn()

const SNAPSHOT: DeliverySnapshot = {
  id: 'a'.repeat(32),
  files: [
    { path: 'app.py', existed: true, before_sha256: 'b', after_sha256: 'c' },
    { path: 'pkg/new.py', existed: false, before_sha256: null, after_sha256: 'd' },
  ],
  undone_at: null,
}

function mockUndo(state: Record<string, unknown> = {}) {
  vi.mocked(useUndoDelivery).mockReturnValue({ mutate: undo, isPending: false, isError: false, isSuccess: false, ...state } as never)
}

const CHANGES: RunChanges = {
  run_id: 7, source: 'workspace', observed_by: { verification_id: 3, observed_at: '' }, git: null,
  snapshot: SNAPSHOT, reason: null,
  files: [{ path: 'app.py', old_path: null, change: 'modified', additions: 1, deletions: 1, patch: null, omitted: null }],
  files_not_shown: 0, totals: { files: 1, additions: 1, deletions: 1 }, declared_not_observed: [],
}

describe('undoing a delivery', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => vi.restoreAllMocks())

  it('undoes only after the Manager confirms', () => {
    mockUndo()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
    render(<UndoDelivery companyId={42} runId={7} snapshot={SNAPSHOT} />)
    const button = screen.getByRole('button', { name: i18n.t('review.undo.button') })
    fireEvent.click(button)
    expect(confirm).toHaveBeenCalledWith(i18n.t('review.undo.confirm', { count: 2 }))
    expect(undo).not.toHaveBeenCalled()
    fireEvent.click(button)
    expect(undo).toHaveBeenCalledTimes(1)
  })

  it('shows what was restored and removed', () => {
    mockUndo({
      isSuccess: true,
      data: { run_id: 7, snapshot: SNAPSHOT.id, restored: [{ path: 'app.py', sha256: 'b' }], removed: [{ path: 'pkg/new.py' }], undone_by: 'Ana', audit_log_id: 1 },
    })
    render(<UndoDelivery companyId={42} runId={7} snapshot={SNAPSHOT} />)
    expect(screen.getByRole('status')).toHaveTextContent(i18n.t('review.undo.done', { restored: 1, removed: 1 }))
    expect(screen.getByText(i18n.t('review.undo.restored', { path: 'app.py' }))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('review.undo.removed', { path: 'pkg/new.py' }))).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('shows the refusal', () => {
    mockUndo({ isError: true, error: new ApiError(409, 'Conflict', 'Nothing was undone; app.py (changed since the delivery)') })
    render(<UndoDelivery companyId={42} runId={7} snapshot={SNAPSHOT} />)
    expect(screen.getByRole('alert')).toHaveTextContent('app.py (changed since the delivery)')
  })

  it('offers nothing once the delivery was undone', () => {
    mockUndo()
    render(<UndoDelivery companyId={42} runId={7} snapshot={{ ...SNAPSHOT, undone_at: '2026-11-08T10:00:00' }} />)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.getByRole('note')).toBeInTheDocument()
  })

  it('appears on Files changed for a Manager, never for a Viewer or a Git branch', () => {
    mockUndo()
    vi.mocked(useRunChanges).mockReturnValue({ isLoading: false, isError: false, data: CHANGES } as never)
    const { unmount } = render(<ReviewChanges companyId={42} runId={7} canComment />)
    expect(screen.getByRole('button', { name: i18n.t('review.undo.button') })).toBeInTheDocument()
    unmount()

    const viewer = render(<ReviewChanges companyId={42} runId={7} canComment={false} />)
    expect(screen.queryByRole('button', { name: i18n.t('review.undo.button') })).not.toBeInTheDocument()
    viewer.unmount()

    vi.mocked(useRunChanges).mockReturnValue({
      isLoading: false, isError: false,
      data: { ...CHANGES, git: { branch: 'fabrique/task-7', base: '', commit: 'abc1234', parent: 'def5678', worktree: '/w', repository: '/r' } },
    } as never)
    render(<ReviewChanges companyId={42} runId={7} canComment />)
    expect(screen.queryByRole('button', { name: i18n.t('review.undo.button') })).not.toBeInTheDocument()
  })
})
