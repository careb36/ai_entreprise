import { useTranslation } from 'react-i18next'
import { GitBranch, RefreshCw } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { integrationApi } from '../services/integration'
import { IntegrationReview } from './IntegrationReview'
import { Panel } from './ui'

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

  if (isLoading) return <div role="status" className="panel p-3 text-xs text-gray-400">{t('common.loading')}</div>
  if (isError || !data) return (
    <div role="alert" className="alert-error items-center text-[13px]">
      {t('sprint.integrationError', { defaultValue: 'Could not load the integration plan.' })}
      <button type="button" onClick={() => refetch()} className="btn-tool ml-auto">
        <RefreshCw size={13} aria-hidden="true" />{t('common.retry')}
      </button>
    </div>
  )

  return (
    <Panel headingLevel={3}
      title={<span className="flex items-center gap-1.5"><GitBranch size={13} aria-hidden="true" className="text-brand-300" />{t('sprint.integration', { defaultValue: 'Sprint integration' })}</span>}>
      <div className="space-y-3 p-3">
        <p className="text-xs text-gray-400">
          {t('sprint.integrationBranch', { defaultValue: 'Integration branch' })}: <code className="chip font-mono text-gray-200">{integration?.integration_branch}</code>
        </p>
        {(integration?.unmet_dependencies?.length ?? 0) > 0 && (
          <div role="alert" className="alert-error p-3 text-xs">
            {t('sprint.unmetDeps', { defaultValue: 'Unmet dependencies' })}: {integration?.unmet_dependencies.map(d => `#${d.task_id}→#${d.missing_dependency}`).join(', ')}
          </div>
        )}
        {(integration?.task_branches?.length ?? 0) > 0 && (
          <ol className="data-list rounded-md border border-surface-700 text-xs">
            {integration?.task_branches?.map((b, i) => (
              <li key={b.task_id} className="flex flex-wrap items-center gap-2 px-3 py-1.5">
                <span className="w-5 text-right tabular-nums text-gray-400">{i + 1}.</span>
                <code className="font-mono text-gray-200">{b.branch}</code>
                <span className="text-gray-400">— {b.title} ({b.status})</span>
              </li>
            ))}
          </ol>
        )}
        <p className="text-meta">
          {t('sprint.integrationHint', { defaultValue: 'Merge these branches in order, run the full suite on the result, then publish one PR.' })}
        </p>
        {integration?.latest && (
          <p className="text-xs text-gray-400">
            {t('integrationReview.latest', { status: t(`integrationReview.status.${integration.latest.status}`, { defaultValue: integration.latest.status }) })}
            {integration.latest.commit && <> <code className="font-mono text-gray-300">{integration.latest.commit.slice(0, 7)}</code></>}
            {integration.review?.latest && (
              <> · {t('integrationReview.latestReview', { author: integration.review.latest.author_name, event: t(`review.event.${integration.review.latest.event}`) })}</>
            )}
          </p>
        )}
        {integration?.latest && companyId !== undefined && (
          <IntegrationReview sprintId={sprintId} companyId={companyId} canReview={canReview} canConfigure={canConfigure} />
        )}
      </div>
    </Panel>
  )
}

export default SprintIntegration
