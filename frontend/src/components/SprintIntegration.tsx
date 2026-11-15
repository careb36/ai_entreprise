import { useTranslation } from 'react-i18next'
import { GitBranch } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { integrationApi } from '../services/integration'
import { IntegrationReview } from './IntegrationReview'

/** The sprint's integration plan, its latest integration and, once there is one, a person's review of it. */
export function SprintIntegration({ sprintId, companyId, canReview = false, canConfigure = false }: {
  sprintId: number; companyId?: number; canReview?: boolean; canConfigure?: boolean
}) {
  const { t } = useTranslation()
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['sprint-integration', sprintId],
    queryFn: () => integrationApi.plan(sprintId),
  })
  const integration = data

  if (isLoading) return <div role="status" className="text-sm text-gray-400">{t('common.loading')}</div>
  if (isError || !data) return (
    <div role="alert" className="alert-error text-sm flex items-center gap-2">
      {t('sprint.integrationError', { defaultValue: 'Could not load the integration plan.' })}
      <button onClick={() => refetch()} className="btn-ghost btn-sm">{t('common.retry')}</button>
    </div>
  )

  return (
    <section className="card space-y-3 p-4">
      <div className="flex items-center gap-2">
        <GitBranch size={16} className="text-brand-400" />
        <h3 className="font-semibold">{t('sprint.integration', { defaultValue: 'Sprint integration' })}</h3>
      </div>
      <p className="text-xs text-gray-400">
        {t('sprint.integrationBranch', { defaultValue: 'Integration branch' })}: <code className="text-gray-200">{integration?.integration_branch}</code>
      </p>
      {(integration?.unmet_dependencies?.length ?? 0) > 0 && (
        <div role="alert" className="alert-error text-xs">
          {t('sprint.unmetDeps', { defaultValue: 'Unmet dependencies' })}: {integration?.unmet_dependencies.map(d => `#${d.task_id}→#${d.missing_dependency}`).join(', ')}
        </div>
      )}
      <ol className="space-y-1 text-xs">
        {integration?.task_branches?.map((b, i) => (
          <li key={b.task_id} className="flex items-center gap-2">
            <span className="text-gray-500">{i + 1}.</span>
            <code className="text-gray-300">{b.branch}</code>
            <span className="text-gray-500">— {b.title} ({b.status})</span>
          </li>
        ))}
      </ol>
      <p className="text-xs text-gray-500">
        {t('sprint.integrationHint', { defaultValue: 'Merge these branches in order, run the full suite on the result, then publish one PR.' })}
      </p>
      {integration?.latest && (
        <p className="text-xs text-gray-400">
          {t('integrationReview.latest', { status: t(`integrationReview.status.${integration.latest.status}`, { defaultValue: integration.latest.status }) })}
          {integration.latest.commit && <> <code className="text-gray-300">{integration.latest.commit.slice(0, 7)}</code></>}
          {integration.review?.latest && (
            <> · {t('integrationReview.latestReview', { author: integration.review.latest.author_name, event: t(`review.event.${integration.review.latest.event}`) })}</>
          )}
        </p>
      )}
      {integration?.latest && companyId !== undefined && (
        <IntegrationReview sprintId={sprintId} companyId={companyId} canReview={canReview} canConfigure={canConfigure} />
      )}
    </section>
  )
}

export default SprintIntegration