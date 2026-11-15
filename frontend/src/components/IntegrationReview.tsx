/**
 * A person's review of the sprint's latest integration, as on a run
 * (`ReviewPanel`): whether the company asks for an approval before a merge,
 * the reviews submitted so far, the comments, the diff the daemon reads and,
 * for a Manager, the review form. An Owner turns the merge gate on or off.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ExternalLink, Loader2, ShieldCheck } from 'lucide-react'
import { errorDetail } from '../services/api'
import type { ReviewEvent } from '../services/runReviews'
import {
  useIntegrationDiff,
  useIntegrationReviews,
  useIntegrationReviewSetting,
  useSetIntegrationReviewRequired,
  useSubmitIntegrationReview,
} from '../services/integrationReviews'
import { REVIEW_EVENTS } from './ReviewPanel'
import { IntegrationCommentForm, IntegrationCommentList, IntegrationDiffView } from './IntegrationDiff'

const EVENT_BADGES: Record<ReviewEvent, string> = {
  comment: 'bg-gray-800 text-gray-300',
  approve: 'bg-emerald-900/60 text-emerald-300',
  request_changes: 'bg-red-900/60 text-red-300',
}

function GateSetting({ companyId, sprintId }: { companyId: number; sprintId: number }) {
  const { t } = useTranslation()
  const setting = useIntegrationReviewSetting(companyId)
  const change = useSetIntegrationReviewRequired(companyId, sprintId)
  const required = setting.data?.required ?? false
  return (
    <label className="flex items-center gap-2 text-xs text-gray-300">
      <input type="checkbox" checked={required} disabled={setting.isLoading || change.isPending}
        onChange={event => change.mutate(event.target.checked)} />
      {t('integrationReview.setting.label')}
      {change.isError && <span role="alert" className="text-red-300">{errorDetail(change.error)}</span>}
    </label>
  )
}

export function IntegrationReview({ sprintId, companyId, canReview, canConfigure }: {
  sprintId: number; companyId: number; canReview: boolean; canConfigure: boolean
}) {
  const { t } = useTranslation()
  const reviews = useIntegrationReviews(sprintId)
  const [showDiff, setShowDiff] = useState(false)
  const diff = useIntegrationDiff(sprintId, showDiff)
  const submit = useSubmitIntegrationReview(sprintId)
  const [event, setEvent] = useState<ReviewEvent>('comment')
  const [body, setBody] = useState('')

  if (reviews.isLoading) return <div role="status" className="text-xs text-gray-400">{t('common.loading')}</div>
  if (reviews.isError || !reviews.data || reviews.data.integration_id === null) {
    return <p role="alert" className="text-xs text-red-300">{t('integrationReview.loadError')}</p>
  }
  const data = reviews.data
  const integrationId = data.integration_id as number
  const general = data.comments.filter(c => c.path === null)
  const send = (e: React.FormEvent) => {
    e.preventDefault()
    submit.mutate({ integrationId, event, body: body.trim() }, { onSuccess: () => { setBody(''); setEvent('comment') } })
  }

  return (
    <section aria-labelledby="integration-review-title" className="space-y-3 border-t border-surface-700 pt-3">
      <h4 id="integration-review-title" className="flex items-center gap-2 text-sm font-semibold text-white">
        <ShieldCheck size={14} aria-hidden="true" />
        {t('integrationReview.title')}
      </h4>
      <p className="text-xs text-gray-400">
        {data.required
          ? t(data.approved ? 'integrationReview.gate.approved' : 'integrationReview.gate.waiting')
          : t('integrationReview.gate.off')}
      </p>
      {data.published_url && (
        <p className="flex items-center gap-1 text-xs text-gray-400">
          {t('integrationReview.published')}
          <a href={data.published_url} target="_blank" rel="noopener noreferrer" className="flex items-center gap-1 text-brand-300 hover:underline">
            {t('integrationReview.publishedLink')}
            <ExternalLink size={11} aria-hidden="true" />
          </a>
        </p>
      )}
      {canConfigure && <GateSetting companyId={companyId} sprintId={sprintId} />}
      {data.reviews.length > 0 ? (
        <ol className="space-y-2" aria-label={t('integrationReview.history')}>
          {data.reviews.map(review => (
            <li key={review.id} className="rounded-lg border border-surface-700 p-2 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className={`badge ${EVENT_BADGES[review.event]}`}>{t(`review.event.${review.event}`)}</span>
                <span className="text-gray-300">{review.author_name}</span>
                <span className="text-xs text-gray-500">{t(`integrationReview.outcome.${review.outcome}`)}</span>
                <code className="text-xs text-gray-500">{review.commit.slice(0, 7)}</code>
              </div>
              {review.body && <p className="mt-1 whitespace-pre-wrap text-gray-300">{review.body}</p>}
            </li>
          ))}
        </ol>
      ) : (
        <p className="text-xs text-gray-500">{t('integrationReview.none')}</p>
      )}
      {general.length > 0 && <IntegrationCommentList sprintId={sprintId} comments={general} canReview={canReview} />}
      <button type="button" className="btn btn-ghost btn-sm" onClick={() => setShowDiff(value => !value)}>
        {showDiff ? t('integrationReview.hideDiff') : t('integrationReview.showDiff')}
      </button>
      {showDiff && (diff.isLoading ? (
        <div role="status" className="text-xs text-gray-400">{t('integrationReview.readingDiff')}</div>
      ) : diff.isError || !diff.data ? (
        <p role="alert" className="text-xs text-red-300">{errorDetail(diff.error)}</p>
      ) : (
        <>
          {diff.data.truncated && <p className="text-xs text-amber-300">{t('integrationReview.truncated')}</p>}
          <IntegrationDiffView sprintId={sprintId} integrationId={integrationId} files={diff.data.files}
            comments={data.comments} canReview={canReview} />
        </>
      ))}
      {canReview && (
        <>
          <IntegrationCommentForm sprintId={sprintId} integrationId={integrationId} path={null} side="new" line={null} />
          <form onSubmit={send} className="space-y-2">
            <label className="block text-xs text-gray-400">
              {t('integrationReview.bodyLabel')}
              <textarea value={body} onChange={e => setBody(e.target.value)} rows={3} maxLength={8000}
                className="mt-1 w-full rounded border border-surface-700 bg-surface-900 p-2 text-sm text-gray-100" />
            </label>
            <fieldset className="flex flex-wrap gap-4 text-sm text-gray-300">
              <legend className="sr-only">{t('integrationReview.decision')}</legend>
              {REVIEW_EVENTS.map(name => (
                <label key={name} className="flex items-center gap-1.5">
                  <input type="radio" name="integration-review-event" value={name} checked={event === name} onChange={() => setEvent(name)} />
                  {t(`review.event.${name}`)}
                </label>
              ))}
            </fieldset>
            <p className="text-xs text-gray-500">{t(`integrationReview.hint.${event}`)}</p>
            <div className="flex flex-wrap items-center gap-3">
              <button type="submit" disabled={submit.isPending} className="btn btn-primary btn-sm flex items-center gap-1.5">
                {submit.isPending && <Loader2 size={13} className="animate-spin" />}
                {t('integrationReview.submit')}
              </button>
              {data.open_comments > 0 && (
                <span className="text-xs text-gray-400">{t('integrationReview.pendingComments', { count: data.open_comments })}</span>
              )}
            </div>
            {submit.isError && <p role="alert" className="text-sm text-red-300">{errorDetail(submit.error)}</p>}
            {submit.isSuccess && <p role="status" className="text-sm text-emerald-300">{t(`integrationReview.outcome.${submit.data.outcome}`)}</p>}
          </form>
        </>
      )}
    </section>
  )
}

export default IntegrationReview
