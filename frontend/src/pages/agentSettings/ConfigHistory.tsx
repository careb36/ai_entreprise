import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { History, RotateCcw } from 'lucide-react'
import { useAgentConfigRevisions, useRollbackAgentConfig } from '../../hooks/useAgentConfigRevisions'
import { errorDetail, type AgentConfigRevision } from '../../services/api'
import { LoadError, LoadingLine } from '../../components/ui'
import { localTime } from '../../lib/utcTime'

function shown(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  return typeof value === 'string' ? value : JSON.stringify(value)
}

/**
 * Every change to an agent's configuration (issue #774), newest first: what
 * changed, before and after, who and why. A Manager puts back what a
 * revision replaced; the rollback is recorded as a revision of its own.
 */
export function ConfigHistory({ agentId, companyId, readOnly }: {
  agentId: number
  companyId: number | null
  readOnly: boolean
}) {
  const { t } = useTranslation()
  const history = useAgentConfigRevisions(agentId)
  const rollback = useRollbackAgentConfig(agentId, companyId)
  const [confirming, setConfirming] = useState<number | null>(null)

  if (history.isLoading) return <LoadingLine>{t('agentHistory.loading')}</LoadingLine>
  if (history.isError) {
    return <LoadError onRetry={() => history.refetch()}>{t('agentHistory.loadError')}</LoadError>
  }
  const revisions: AgentConfigRevision[] = history.data?.revisions ?? []

  return (
    <section aria-labelledby={`history-${agentId}`} className="space-y-2 border-t border-surface-700 pt-3">
      <h3 id={`history-${agentId}`} className="flex items-center gap-1.5 text-xs font-semibold text-gray-200">
        <History size={13} aria-hidden="true" />
        {t('agentHistory.title')}
      </h3>
      {revisions.length === 0 ? (
        <p className="text-xs text-gray-400">{t('agentHistory.empty')}</p>
      ) : (
        <ol className="space-y-2">
          {revisions.map(rev => (
            <li key={rev.revision_id} className="rounded-lg border border-surface-700 p-2 text-xs">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-gray-200">
                  {rev.source === 'rollback'
                    ? t('agentHistory.rollbackOf', { id: rev.rolled_back_from })
                    : t('agentHistory.changed', { fields: rev.changed.join(', ') })}
                  <span className="ml-2 text-gray-400">
                    {t('agentHistory.byAt', { by: rev.by || '—', at: localTime(rev.at) })}
                  </span>
                </p>
                {!readOnly && (
                  <button
                    type="button"
                    className="btn-tool border border-surface-700"
                    disabled={rollback.isPending}
                    onClick={() => {
                      if (confirming !== rev.revision_id) return setConfirming(rev.revision_id)
                      rollback.mutate(rev.revision_id, { onSettled: () => setConfirming(null) })
                    }}
                  >
                    <RotateCcw size={12} aria-hidden="true" />
                    {confirming === rev.revision_id ? t('agentHistory.confirmRollback') : t('agentHistory.rollback')}
                  </button>
                )}
              </div>
              {rev.reason && <p className="mt-1 text-gray-300">{t('agentHistory.reason', { reason: rev.reason })}</p>}
              <details className="mt-1">
                <summary className="cursor-pointer text-gray-400">{t('agentHistory.details')}</summary>
                <dl className="mt-1 space-y-1">
                  {rev.changed.map(field => (
                    <div key={field}>
                      <dt className="font-mono text-gray-300">{field}</dt>
                      <dd className="whitespace-pre-wrap break-words text-gray-400">
                        {shown(rev.from[field])} → {shown(rev.to[field])}
                      </dd>
                    </div>
                  ))}
                </dl>
              </details>
            </li>
          ))}
        </ol>
      )}
      {rollback.isError && <p role="alert" className="alert-error text-xs">{errorDetail(rollback.error)}</p>}
      {rollback.isSuccess && rollback.data.changed_since.length > 0 && (
        <p role="status" className="text-xs text-amber-300">
          {t('agentHistory.changedSince', { fields: rollback.data.changed_since.join(', ') })}
        </p>
      )}
    </section>
  )
}
