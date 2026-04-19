import React from 'react'
import { useTranslation } from 'react-i18next'
import { useAnalyticsSummary } from '../hooks/useQueries'
import { TrendingUp, Zap, CheckSquare, XSquare, Info, RefreshCw } from 'lucide-react'

function HealthBadge({ score }: { score: number }) {
  const { t } = useTranslation()
  const color = score >= 80 ? 'text-status-success' : score >= 50 ? 'text-yellow-400' : 'text-status-error'
  const bg    = score >= 80 ? 'bg-green-900/30 border-green-700/40' : score >= 50 ? 'bg-yellow-900/30 border-yellow-700/40' : 'bg-red-900/30 border-red-700/40'

  return (
    <div className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border ${bg} group relative`}>
      <span className={`text-2xl font-black ${color}`}>{score}</span>
      <div>
        <p className="text-xs text-gray-400 leading-none">{t('analytics.healthScore')}</p>
        <p className="text-xs text-gray-500">/100</p>
      </div>
      {/* Tooltip */}
      <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 w-56 bg-surface-800 border border-surface-700 rounded-lg p-3 text-xs text-gray-300 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-10 shadow-card-md">
        {t('analytics.healthExplainer', 'Composite score: task completion rate, blocked items, and sprint velocity')}
      </div>
    </div>
  )
}

export const AnalyticsDashboard: React.FC<{ sprintId: number }> = ({ sprintId }) => {
  const { t } = useTranslation()
  const { data, isLoading, isError, refetch } = useAnalyticsSummary(sprintId)

  if (isLoading) {
    return (
      <div className="space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {[1,2,3].map(i => <div key={i} className="skeleton h-24 rounded-lg" />)}
        </div>
        <div className="skeleton h-32 rounded-lg" />
      </div>
    )
  }

  if (isError || !data) {
    return (
      <div className="alert-error flex items-center gap-2">
        <XSquare size={16} /> {t('analytics.error')}
      </div>
    )
  }

  const analytics = data

  const done    = analytics.tasks_completed ?? 0
  const failed  = analytics.tasks_failed    ?? 0
  const inProg  = analytics.tasks_in_progress ?? 0

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-3 justify-between">
        <div className="flex items-center gap-2">
          <TrendingUp className="text-brand-400" size={22} />
          <div>
            <h2 className="text-xl font-bold text-white">{t('analytics.title')}</h2>
            <p className="text-gray-500 text-sm">{t('analytics.subtitle')}</p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <HealthBadge score={analytics.health_score} />
          <button onClick={() => refetch()} className="btn-ghost btn-icon" title={t('common.refresh','Refresh')}>
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="card p-4">
          <div className="flex items-center gap-2 mb-2">
            <Zap size={14} className="text-brand-400" />
            <p className="text-xs text-gray-500 uppercase tracking-wider font-semibold">{t('analytics.tokensConsumed')}</p>
          </div>
          <p className="text-3xl font-black text-white">{analytics.tokens_consumed.toLocaleString()}</p>
        </div>

        <div className="card p-4">
          <div className="flex items-center gap-2 mb-2">
            <CheckSquare size={14} className="text-status-success" />
            <p className="text-xs text-gray-500 uppercase tracking-wider font-semibold">{t('analytics.tasksCompleted')}</p>
          </div>
          <p className="text-3xl font-black text-white">{done}</p>
          {inProg > 0 && <p className="text-xs text-gray-500 mt-1">{inProg} {t('analytics.inProgress')}</p>}
        </div>

        <div className="card p-4">
          <div className="flex items-center gap-2 mb-2">
            <XSquare size={14} className="text-status-error" />
            <p className="text-xs text-gray-500 uppercase tracking-wider font-semibold">{t('analytics.tasksFailed')}</p>
          </div>
          <p className="text-3xl font-black text-white">{failed}</p>
        </div>
      </div>

      {/* Tokens by agent */}
      {analytics.agent_breakdown && analytics.agent_breakdown.length > 0 && (
        <div className="card p-4">
          <p className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-3 flex items-center gap-1.5">
            <Info size={12} />
            {t('analytics.tokensByAgent')}
          </p>
          <div className="space-y-2">
            {analytics.agent_breakdown.map(({ role, tokens, percentage }) => (
              <div key={role}>
                <div className="flex justify-between text-xs mb-1">
                  <span className="text-gray-400">{role}</span>
                  <span className="text-gray-500">{tokens.toLocaleString()} ({percentage}%)</span>
                </div>
                <div className="h-1.5 bg-surface-700 rounded-full overflow-hidden">
                  <div className="h-full bg-brand-500 rounded-full" style={{ width: `${percentage}%` }} />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Executive summary */}
      {analytics.executive_summary && (
        <div className="card p-4 border-l-4 border-l-brand-500">
          <p className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-2">{t('analytics.executiveSummary')}</p>
          <p className="text-gray-300 leading-relaxed italic">"{analytics.executive_summary}"</p>
        </div>
      )}
    </div>
  )
}
