import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { AlertTriangle, CheckCircle2, RefreshCw, ShieldCheck } from 'lucide-react'
import { useAgentRuns } from '../hooks/useQueries'
import { useAppStore } from '../store/useAppStore'
import type { AgentRunListItem } from '../services/api'

function PendingRuns({ companyId }: { companyId: number }) {
  const { t } = useTranslation()
  const { data, isLoading, isError, refetch } = useAgentRuns(companyId, { limit: 500 })
  const runs = (data?.agent_runs ?? []).filter(run => run.latest_approval === null)

  if (isLoading) {
    return <div role="status" className="space-y-3" aria-label={t('common.loading')}>
      {[1, 2, 3].map(item => <div key={item} className="skeleton h-20 rounded-lg" />)}
    </div>
  }

  if (isError) {
    return <div role="alert" className="alert-error flex items-center justify-between gap-3">
      <span>{t('governance.fetchError')}</span>
      <button onClick={() => refetch()} className="btn-ghost btn-sm"><RefreshCw size={14} />{t('common.refresh')}</button>
    </div>
  }

  if (runs.length === 0) {
    return <div className="empty-state py-12">
      <CheckCircle2 size={36} className="mx-auto mb-3 text-status-success" />
      <p className="font-semibold text-gray-300">{t('governance.noPendingRuns')}</p>
    </div>
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-gray-400">{runs.length} {t('governance.pendingRuns')}</p>
      {runs.map((run: AgentRunListItem) => (
        <article key={run.id} className="card flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-gray-200">{run.producer}{run.agent_role ? ` · ${run.agent_role}` : ''}</p>
            <p className="truncate font-mono text-xs text-gray-500">{run.external_ref}</p>
          </div>
          <Link to={`/evidence/${run.id}`} className="btn btn-primary btn-sm self-start sm:self-auto">
            {t('governance.reviewRun')}
          </Link>
        </article>
      ))}
    </div>
  )
}

export const GovernancePanel: React.FC = () => {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)

  return (
    <div>
      <div className="mb-6 flex items-center gap-2">
        <ShieldCheck className="text-brand-400" size={22} />
        <h1 className="text-xl font-bold text-white">{t('nav.review', { defaultValue: 'Review' })}</h1>
      </div>
      {companyId === null
        ? <div className="empty-state py-12"><AlertTriangle size={32} className="mx-auto mb-3 text-gray-500" /><p className="text-gray-400">{t('evidence.selectCompany')}</p></div>
        : <PendingRuns companyId={companyId} />}
    </div>
  )
}
