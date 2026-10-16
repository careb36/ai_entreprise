import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw, RotateCcw, ShieldCheck } from 'lucide-react'
import { useAgentRuns, useQuotas, useResetQuota } from '../hooks/useQueries'
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
  const { data: quotaData } = useQuotas(companyId)
  const resetQuotaMutation = useResetQuota()
  const [pendingResets, setPendingResets] = useState<Set<number>>(new Set())

  return (
    <div>
      <div className="mb-6 flex items-center gap-2">
        <ShieldCheck className="text-brand-400" size={22} />
        <h1 className="text-xl font-bold text-white">{t('governance.title')}</h1>
      </div>

      {Array.isArray(quotaData) && quotaData.length > 0 && (
        <section className="mb-6">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-gray-400">{t('governance.quotaTitle')}</h2>
          <div className="space-y-3">
            {quotaData.map((unit: any) => {
              const pct = unit.limit > 0 ? Math.min(100, Math.round((unit.used / unit.limit) * 100)) : 0
              const isWarning = pct >= 80 && pct < 100
              const isExceeded = unit.limit > 0 && unit.used >= unit.limit
              const barColor = isExceeded ? 'bg-status-error' : isWarning ? 'bg-yellow-500' : 'bg-brand-500'
              return (
                <div key={unit.id ?? unit.unit} className="card p-3">
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <span className="text-sm font-medium text-gray-300">{unit.unit}</span>
                    <div className="flex items-center gap-2">
                      {isExceeded && <span className="badge bg-red-900/60 text-red-300">{t('governance.quotaExceeded')}</span>}
                      {isWarning && !isExceeded && <span className="badge bg-yellow-900/60 text-yellow-300"><AlertTriangle size={10} />{t('governance.quotaWarning')}</span>}
                      <span className="text-xs text-gray-500">
                        {unit.used.toLocaleString()} / {unit.limit > 0 ? unit.limit.toLocaleString() : '∞'} {t('governance.tokens')}
                      </span>
                      {unit.id && (
                        <button
                          onClick={() => {
                            setPendingResets(previous => new Set(previous).add(unit.id))
                            resetQuotaMutation.mutate(unit.id, {
                              onSettled: () => setPendingResets(previous => {
                                const next = new Set(previous)
                                next.delete(unit.id)
                                return next
                              }),
                            })
                          }}
                          disabled={pendingResets.has(unit.id)}
                          className="btn-ghost btn-sm p-1"
                          title={t('governance.resetQuota')}
                        >
                          {pendingResets.has(unit.id)
                            ? <Loader2 size={12} className="animate-spin" />
                            : <RotateCcw size={12} />}
                        </button>
                      )}
                    </div>
                  </div>
                  <div className="h-2 w-full rounded-full bg-surface-700">
                    <div className={`h-2 rounded-full transition-all duration-500 ${barColor}`} style={{ width: `${unit.limit > 0 ? pct : 0}%` }} />
                  </div>
                  {unit.limit > 0 && <p className="mt-1 text-right text-xs text-gray-600">{pct}%</p>}
                </div>
              )
            })}
          </div>
        </section>
      )}

      {companyId === null
        ? <div className="empty-state py-12"><AlertTriangle size={32} className="mx-auto mb-3 text-gray-500" /><p className="text-gray-400">{t('evidence.selectCompany')}</p></div>
        : <PendingRuns companyId={companyId} />}
    </div>
  )
}
