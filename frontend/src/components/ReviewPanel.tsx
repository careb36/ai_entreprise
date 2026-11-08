/**
 * "Finish your review", as a pull request has it: a Manager comments,
 * approves or requests changes, carrying the comments left on the lines;
 * everyone sees the reviews submitted so far and what each one did.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, MessageSquare } from 'lucide-react'
import { errorDetail } from '../services/api'
import { type ReviewEvent, useReviewComments, useRunReviews, useSubmitReview } from '../services/runReviews'

/** The decisions a review can carry, in the order a pull request offers them. */
export const REVIEW_EVENTS: readonly ReviewEvent[] = ['comment', 'approve', 'request_changes']

const EVENT_BADGES: Record<ReviewEvent, string> = {
  comment: 'bg-gray-800 text-gray-300',
  approve: 'bg-emerald-900/60 text-emerald-300',
  request_changes: 'bg-red-900/60 text-red-300',
}

export function ReviewPanel({ companyId, runId, canReview }: { companyId: number; runId: number; canReview: boolean }) {
  const { t } = useTranslation()
  const reviews = useRunReviews(companyId, runId)
  const comments = useReviewComments(companyId, runId)
  const submit = useSubmitReview(companyId, runId)
  const [event, setEvent] = useState<ReviewEvent>('comment')
  const [body, setBody] = useState('')
  const pendingComments = (comments.data?.comments ?? []).filter(c => c.review_id === null && c.resolved_at === null).length
  const submitted = reviews.data?.reviews ?? []

  const send = (e: React.FormEvent) => {
    e.preventDefault()
    submit.mutate({ event, body: body.trim() }, { onSuccess: () => { setBody(''); setEvent('comment') } })
  }

  return (
    <section aria-labelledby="review-panel-title" className="card space-y-3 p-4">
      <h2 id="review-panel-title" className="flex items-center gap-2 text-sm font-semibold text-white">
        <MessageSquare size={14} aria-hidden="true" />
        {t('review.panel.title')}
      </h2>
      {submitted.length > 0 && (
        <ol className="space-y-2" aria-label={t('review.panel.history')}>
          {submitted.map(review => (
            <li key={review.id} className="rounded-lg border border-surface-700 p-2 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className={`badge ${EVENT_BADGES[review.event]}`}>{t(`review.event.${review.event}`)}</span>
                <span className="text-gray-300">{review.author_name}</span>
                <span className="text-xs text-gray-500">{t(`review.outcome.${review.outcome}`)}</span>
              </div>
              {review.body && <p className="mt-1 whitespace-pre-wrap text-gray-300">{review.body}</p>}
            </li>
          ))}
        </ol>
      )}
      {canReview ? (
        <form onSubmit={send} className="space-y-2">
          <label className="block text-xs text-gray-400">
            {t('review.panel.bodyLabel')}
            <textarea value={body} onChange={e => setBody(e.target.value)} rows={3} maxLength={8000}
              className="mt-1 w-full rounded border border-surface-700 bg-surface-900 p-2 text-sm text-gray-100" />
          </label>
          <fieldset className="flex flex-wrap gap-4 text-sm text-gray-300">
            <legend className="sr-only">{t('review.panel.decision')}</legend>
            {REVIEW_EVENTS.map(name => (
              <label key={name} className="flex items-center gap-1.5">
                <input type="radio" name="review-event" value={name} checked={event === name} onChange={() => setEvent(name)} />
                {t(`review.event.${name}`)}
              </label>
            ))}
          </fieldset>
          <p className="text-xs text-gray-500">{t(`review.panel.hint.${event}`)}</p>
          <div className="flex flex-wrap items-center gap-3">
            <button type="submit" disabled={submit.isPending} className="btn btn-primary btn-sm flex items-center gap-1.5">
              {submit.isPending && <Loader2 size={13} className="animate-spin" />}
              {t('review.panel.submit')}
            </button>
            {pendingComments > 0 && (
              <span className="text-xs text-gray-400">{t('review.panel.pendingComments', { count: pendingComments })}</span>
            )}
          </div>
          {submit.isError && <p role="alert" className="text-sm text-red-300">{errorDetail(submit.error)}</p>}
          {submit.isSuccess && <p role="status" className="text-sm text-emerald-300">{t(`review.outcome.${submit.data.outcome}`)}</p>}
        </form>
      ) : (
        submitted.length === 0 && <p className="text-sm text-gray-500">{t('review.panel.none')}</p>
      )}
    </section>
  )
}
