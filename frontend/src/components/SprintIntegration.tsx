import { useTranslation } from 'react-i18next'
import { GitBranch } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../services/api'

export function SprintIntegration({ sprintId }: { sprintId: number }) {
  const { t } = useTranslation()
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['sprint-integration', sprintId],
    queryFn: () => api.sprintIntegration(sprintId),
  })

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
        {t('sprint.integrationBranch', { defaultValue: 'Integration branch' })}: <code className="text-gray-200">{data.integration_branch}</code>
      </p>
      {data.unmet_dependencies?.length > 0 && (
        <div role="alert" className="alert-error text-xs">
          {t('sprint.unmetDeps', { defaultValue: 'Unmet dependencies' })}: {data.unmet_dependencies.map(d => `#${d.task_id}→#${d.missing_dependency}`).join(', ')}
        </div>
      )}
      <ol className="space-y-1 text-xs">
        {data.task_branches.map((b, i) => (
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
    </section>
  )
}

export default SprintIntegration