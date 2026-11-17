import { useTranslation } from 'react-i18next'
import type { AgentActivity } from '../../services/api'
import { formatCost, formatRelativeTime } from './display'

export function ActivityRow({
  activity,
  className = '',
}: {
  activity: AgentActivity | undefined
  className?: string
}) {
  const { t } = useTranslation()
  if (!activity || activity.total_tasks === 0) {
    return (
      <div className={`flex items-center gap-2 text-[11px] text-gray-400 ${className}`}>
        <span>•</span>
        <span>{t('agentsConfig.noActivity')}</span>
      </div>
    )
  }
  return (
    <div className={`flex items-center gap-3 text-[11px] text-gray-400 flex-wrap ${className}`}>
      <span className="font-mono text-gray-300">
        {activity.total_tasks} {activity.total_tasks === 1 ? t('agentsConfig.taskSingular') : t('agentsConfig.taskPlural')}
      </span>
      <span className="text-gray-400">·</span>
      <span className="font-mono text-gray-300">{formatCost(activity.total_cost_usd)}</span>
      <span className="text-gray-400">·</span>
      <span>{formatRelativeTime(activity.last_run_at, t)}</span>
      {activity.last_model && (
        <>
          <span className="text-gray-400">·</span>
          <span className="font-mono text-gray-400">{activity.last_model}</span>
        </>
      )}
    </div>
  )
}
