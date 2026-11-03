import { fireEvent, render, screen, within } from '@testing-library/react'
import i18n from 'i18next'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ReviewPanel } from './ReviewPanel'
import { ApiError } from '../services/api'
import { type RunReview, useReviewComments, useRunReviews, useSubmitReview } from '../services/runReviews'

vi.mock('../services/runReviews', () => ({
  useReviewComments: vi.fn(),
  useRunReviews: vi.fn(),
  useSubmitReview: vi.fn(),
}))

const submit = vi.fn()

function setup({ reviews = [] as RunReview[], pending = 0, mutation = {} as Record<string, unknown>, canReview = true } = {}) {
  vi.mocked(useRunReviews).mockReturnValue({ data: { reviews } } as never)
  vi.mocked(useReviewComments).mockReturnValue({
    data: { comments: Array.from({ length: pending }, (_, i) => ({ id: i, review_id: null, resolved_at: null })) },
  } as never)
  vi.mocked(useSubmitReview).mockReturnValue({ mutate: submit, isPending: false, isError: false, isSuccess: false, ...mutation } as never)
  return render(<ReviewPanel companyId={42} runId={7} canReview={canReview} />)
}

describe('the review panel', () => {
  beforeEach(() => vi.clearAllMocks())

  it('submits the decision a Manager picks, with the review text', () => {
    setup({ pending: 2 })
    expect(screen.getByText(i18n.t('review.panel.pendingComments', { count: 2 }))).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText(i18n.t('review.panel.bodyLabel')), { target: { value: '  Add the tests.  ' } })
    fireEvent.click(screen.getByRole('radio', { name: i18n.t('review.event.request_changes') }))
    expect(screen.getByText(i18n.t('review.panel.hint.request_changes'))).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: i18n.t('review.panel.submit') }))

    expect(submit).toHaveBeenCalledWith({ event: 'request_changes', body: 'Add the tests.' }, expect.anything())
  })

  it('says why the backend refused a review', () => {
    setup({ mutation: { isError: true, error: new ApiError(409, 'QA decides this task.', 'QA decides this task.') } })
    expect(screen.getByRole('alert')).toHaveTextContent('QA decides this task.')
  })

  it('says what a submitted review did', () => {
    setup({ mutation: { isSuccess: true, data: { outcome: 'sent_back' } } })
    expect(screen.getByRole('status')).toHaveTextContent(i18n.t('review.outcome.sent_back'))
  })

  it('lists the reviews submitted, and a Viewer only reads them', () => {
    setup({
      canReview: false,
      reviews: [{ id: 1, agent_run_id: 7, event: 'request_changes', body: 'Not yet.', outcome: 'sent_back', author_name: 'Ana', created_at: '' }],
    })
    const history = screen.getByRole('list', { name: i18n.t('review.panel.history') })
    expect(within(history).getByText(i18n.t('review.event.request_changes'))).toBeInTheDocument()
    expect(within(history).getByText('Not yet.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: i18n.t('review.panel.submit') })).not.toBeInTheDocument()
  })

  it('says there is no review yet to a Viewer', () => {
    setup({ canReview: false })
    expect(screen.getByText(i18n.t('review.panel.none'))).toBeInTheDocument()
  })
})
