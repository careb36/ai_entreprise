/**
 * SprintHealthChart — visualizes sprint health metrics over time.
 *
 * Displays a simple bar chart showing task completion progress
 * and health score trends.
 */

import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { BarChart3 } from 'lucide-react'
import { EmptyState, Panel } from './ui'

interface SprintHealthChartProps {
  total: number
  done: number
  inProgress: number
  blocked: number
  healthScore: number | null
}

export function SprintHealthChart({
  total,
  done,
  inProgress,
  blocked,
  healthScore,
}: SprintHealthChartProps) {
  const { t } = useTranslation()

  const percentages = useMemo(() => {
    if (total === 0) return { done: 0, inProgress: 0, blocked: 0, remaining: 100 }
    return {
      done: Math.round((done / total) * 100),
      inProgress: Math.round((inProgress / total) * 100),
      blocked: Math.round((blocked / total) * 100),
      remaining: Math.round(((total - done - inProgress - blocked) / total) * 100),
    }
  }, [total, done, inProgress, blocked])

  const healthColor = useMemo(() => {
    if (healthScore === null) return 'text-gray-400'
    if (healthScore >= 80) return 'text-emerald-400'
    if (healthScore >= 60) return 'text-amber-400'
    return 'text-red-400'
  }, [healthScore])

  if (total === 0) {
    return (
      <Panel>
        <EmptyState icon={BarChart3} title={t('dashboard.noData', { defaultValue: 'No sprint data available' })} />
      </Panel>
    )
  }

  return (
    <Panel headingLevel={3} title={t('dashboard.sprintHealth', { defaultValue: 'Sprint Health' })}
      actions={healthScore !== null && (
        <span className={`text-base font-semibold tabular-nums ${healthColor}`}>
          {healthScore}
        </span>
      )}>
    <div className="space-y-4 p-3">

      {/* Stacked progress bar */}
      <div className="space-y-2">
        <div className="flex h-2.5 overflow-hidden rounded-full bg-surface-700">
          {percentages.done > 0 && (
            <div
              className="bg-emerald-500 transition-all duration-500 motion-reduce:transition-none"
              style={{ width: `${percentages.done}%` }}
              title={t('dashboard.chartTooltip', {
                defaultValue: '{{label}}: {{percent}}%',
                label: t('dashboard.done', { defaultValue: 'Done' }),
                percent: percentages.done,
              })}
            />
          )}
          {percentages.inProgress > 0 && (
            <div
              className="bg-brand-500 transition-all duration-500 motion-reduce:transition-none"
              style={{ width: `${percentages.inProgress}%` }}
              title={t('dashboard.chartTooltip', {
                defaultValue: '{{label}}: {{percent}}%',
                label: t('dashboard.inProgress', { defaultValue: 'In Progress' }),
                percent: percentages.inProgress,
              })}
            />
          )}
          {percentages.blocked > 0 && (
            <div
              className="bg-red-500 transition-all duration-500 motion-reduce:transition-none"
              style={{ width: `${percentages.blocked}%` }}
              title={t('dashboard.chartTooltip', {
                defaultValue: '{{label}}: {{percent}}%',
                label: t('dashboard.blocked', { defaultValue: 'Blocked' }),
                percent: percentages.blocked,
              })}
            />
          )}
          {percentages.remaining > 0 && (
            <div
              className="bg-surface-600 transition-all duration-500 motion-reduce:transition-none"
              style={{ width: `${percentages.remaining}%` }}
              title={t('dashboard.chartTooltip', {
                defaultValue: '{{label}}: {{percent}}%',
                label: t('dashboard.remaining', { defaultValue: 'Remaining' }),
                percent: percentages.remaining,
              })}
            />
          )}
        </div>

        {/* Legend */}
        <div className="text-meta flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-3">
            <span className="flex items-center gap-1">
              <span aria-hidden="true" className="w-2 h-2 rounded-full bg-emerald-500" />
              <span className="tabular-nums">{t('dashboard.done', { defaultValue: 'Done' })} ({done})</span>
            </span>
            <span className="flex items-center gap-1">
              <span aria-hidden="true" className="w-2 h-2 rounded-full bg-brand-500" />
              <span className="tabular-nums">{t('dashboard.inProgress', { defaultValue: 'In Progress' })} ({inProgress})</span>
            </span>
            <span className="flex items-center gap-1">
              <span aria-hidden="true" className="w-2 h-2 rounded-full bg-red-500" />
              <span className="tabular-nums">{t('dashboard.blocked', { defaultValue: 'Blocked' })} ({blocked})</span>
            </span>
          </div>
          <span className="tabular-nums">
            {done}/{total} {t('dashboard.tasks', { defaultValue: 'tasks' })}
          </span>
        </div>
      </div>

      {/* Health score indicator */}
      {healthScore !== null && (
        <div className="space-y-1">
          <div className="text-meta flex justify-between">
            <span>{t('dashboard.healthScore', { defaultValue: 'Health Score' })}</span>
            <span className="tabular-nums">{healthScore}/100</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-surface-700" aria-hidden="true">
            <div
              className={`h-full rounded-full transition-all duration-500 motion-reduce:transition-none ${
                healthScore >= 80
                  ? 'bg-emerald-500'
                  : healthScore >= 60
                    ? 'bg-amber-500'
                    : 'bg-red-500'
              }`}
              style={{ width: `${healthScore}%` }}
            />
          </div>
        </div>
      )}
    </div>
    </Panel>
  )
}
