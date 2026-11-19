import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ExternalLink, GitMerge, GitPullRequest, Merge } from 'lucide-react'
import { useForgePolicy } from '../hooks/useDuties'
import { errorDetail } from '../services/api'
import { useIntegrationAction, type SprintIntegrationPlan } from '../services/integration'
import { Spinner } from './ui'

function formatWhen(value: string, locale: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

/**
 * What a Manager does with a sprint's integration (issue #314): integrate
 * now, publish the integration branch as one request when the company's
 * forge policy allows it (issue #727), and merge it into the base branch
 * locally. The backend decides each one (the segregation of duties: the
 * person who started the sprint does not merge it; the review gate when the
 * company requires an approval); this view only hides what the role cannot
 * do, explains what is held back, and asks before the merge. Everyone sees
 * where the latest integration was published and merged.
 */
export function IntegrationActions({ plan, companyId, canManage }: {
  plan: SprintIntegrationPlan; companyId: number; canManage: boolean
}) {
  const { t, i18n } = useTranslation()
  const sprintId = plan.sprint_id
  const integrate = useIntegrationAction(sprintId, 'integrate')
  const publish = useIntegrationAction(sprintId, 'publish')
  const merge = useIntegrationAction(sprintId, 'merge')
  const forge = useForgePolicy(canManage ? companyId : null)
  const [confirming, setConfirming] = useState(false)
  const noteId = useId()
  const latest = plan.latest
  const publication = latest?.publication ?? null
  const localMerge = latest?.details?.local_merge ?? null
  const forgeAllows = forge.data?.effective === 'publish'
  const reviewHolds = Boolean(plan.review?.required && !plan.review?.approved)
  const busy = integrate.isPending || publish.isPending || merge.isPending
  const failed = [integrate, publish, merge].find(m => m.isError)

  return (
    <div className="space-y-2">
      {publication && (
        <p className="flex flex-wrap items-center gap-1.5 text-xs text-gray-400">
          {t('integrationActions.published', { name: publication.published_by, commit: publication.commit.slice(0, 7), base: publication.base })}
          {publication.url && (
            <a href={publication.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-brand-300 hover:underline">
              {t('integrationActions.openRequest')}<ExternalLink size={12} aria-hidden="true" />
              <span className="sr-only">{t('integrationActions.newTab')}</span>
            </a>
          )}
        </p>
      )}
      {localMerge && (
        <p className="text-xs text-gray-400">{t('integrationActions.merged', { name: localMerge.merged_by, when: formatWhen(localMerge.merged_at, i18n.language) })}</p>
      )}
      {canManage && (
        <div className="space-y-2 border-t border-surface-700 pt-2">
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className="btn-tool border border-surface-700" disabled={busy} onClick={() => integrate.mutate()}>
              {integrate.isPending ? <Spinner size={13} /> : <Merge size={13} aria-hidden="true" />}
              {latest ? t('integrationActions.integrateAgain') : t('integrationActions.integrate')}
            </button>
            {latest && (
              <button type="button" className="btn-tool border border-surface-700" disabled={busy || !forgeAllows}
                aria-describedby={!forgeAllows ? `${noteId}-forge` : undefined} onClick={() => publish.mutate()}>
                {publish.isPending ? <Spinner size={13} /> : <GitPullRequest size={13} aria-hidden="true" />}
                {publication ? t('integrationActions.publishAgain') : t('integrationActions.publish')}
              </button>
            )}
            {latest && !localMerge && (confirming ? (
              <span role="group" aria-label={t('integrationActions.confirmLabel')} className="inline-flex flex-wrap items-center gap-1">
                <span className="text-xs text-amber-300">{t('integrationActions.confirmMerge', { base: latest.base })}</span>
                <button type="button" className="btn-tool border border-amber-700/60 text-amber-200" disabled={busy}
                  onClick={() => merge.mutate(undefined, { onSettled: () => setConfirming(false) })}>
                  {merge.isPending && <Spinner size={13} />}{t('integrationActions.mergeConfirm')}
                </button>
                <button type="button" className="btn-tool" onClick={() => setConfirming(false)}>{t('common.cancel')}</button>
              </span>
            ) : (
              <button type="button" className="btn-tool border border-surface-700" disabled={busy || reviewHolds}
                aria-describedby={reviewHolds ? `${noteId}-review` : undefined} onClick={() => setConfirming(true)}>
                <GitMerge size={13} aria-hidden="true" />{t('integrationActions.merge')}
              </button>
            ))}
          </div>
          {latest && !forgeAllows && forge.data && (
            <p id={`${noteId}-forge`} className="text-meta">{t('integrationActions.forgeHolds', { policy: t(`forgePolicy.${forge.data.effective}.label`) })}</p>
          )}
          {latest && !localMerge && reviewHolds && (
            <p id={`${noteId}-review`} className="text-meta">{t('integrationActions.reviewHolds')}</p>
          )}
          <p className="text-meta">{t('integrationActions.hint', { branch: plan.integration_branch })}</p>
          {integrate.isSuccess && <p role="status" className="text-xs text-gray-300">{t('integrationActions.integrated')}</p>}
          {failed && <p role="alert" className="alert-error text-sm">{errorDetail(failed.error)}</p>}
        </div>
      )}
    </div>
  )
}
