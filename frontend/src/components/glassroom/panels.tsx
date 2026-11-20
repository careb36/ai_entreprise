import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import type { SprintRetro } from '../../store/agentStore'
import { GitBranch, HeartPulse, ShieldAlert } from 'lucide-react'
import { taskPath } from './identity'
import { phaseLabel } from './format'
import type {
  HealthPanelEntry, QuotaTelemetry, SprintTaskEntry, TaskAutonomyEntry,
} from './types'

export function SprintTaskPanel({ tasks, sprintId }: { tasks: SprintTaskEntry[]; sprintId: string }) {
  const { t } = useTranslation()
  if (tasks.length === 0) return null

  const statusTone: Record<SprintTaskEntry['status'], string> = {
    todo: 'bg-surface-800 text-gray-300',
    in_progress: 'bg-blue-500/10 text-blue-300',
    in_review: 'bg-amber-500/10 text-amber-300',
    done: 'bg-emerald-500/10 text-emerald-300',
    blocked: 'bg-red-500/10 text-red-300',
  }

  const priorityTone: Record<SprintTaskEntry['priority'], string> = {
    low: 'text-gray-500',
    medium: 'text-sky-300',
    high: 'text-amber-300',
    critical: 'text-red-300',
  }

  return (
    <div className="border-b border-surface-700/60 p-3 space-y-2">
      <div>
        <p className="text-[10px] font-semibold text-gray-600 uppercase tracking-wider">
          {t('glassroom.taskSummaryTitle')}
        </p>
        <p className="text-[11px] text-gray-500 mt-1">
          {t('glassroom.taskSummarySubtitle')}
        </p>
      </div>

      <div className="space-y-2">
        {tasks.map(task => (
          <div key={task.id} className="rounded-lg border border-surface-700 bg-surface-900/70 p-2.5">
            <div className="flex items-start gap-2">
              <div className="flex-1 min-w-0">
                <Link
                  to={taskPath(sprintId, task.id)}
                  className="block text-[11px] font-medium text-gray-200 leading-tight break-words hover:text-brand-300 hover:underline"
                >
                  {task.title}
                </Link>
                <div className="mt-1 flex items-center gap-2 flex-wrap text-[10px]">
                  <span className={`rounded-full px-1.5 py-0.5 font-medium ${statusTone[task.status]}`}>
                    {t(`glassroom.taskStatus.${task.status}`)}
                  </span>
                  <span className={priorityTone[task.priority]}>
                    {t('glassroom.taskPriority')}: {t(`glassroom.taskPriorityValue.${task.priority}`)}
                  </span>
                </div>
              </div>
            </div>
            <p className="mt-1.5 text-[10px] text-gray-500">
              {t('glassroom.taskOwner')}: {task.assignee || t('glassroom.unassigned')}
            </p>
          </div>
        ))}
      </div>
    </div>
  )
}

export function HealthPanel({ entries }: { entries: HealthPanelEntry[] }) {
  const { t } = useTranslation()
  if (entries.length === 0) return null

  const toneClass: Record<HealthPanelEntry['tone'], string> = {
    neutral: 'bg-surface-800 text-gray-300',
    good: 'bg-emerald-500/10 text-emerald-300',
    warn: 'bg-amber-500/10 text-amber-300',
    danger: 'bg-red-500/10 text-red-300',
  }

  return (
    <div className="border-b border-surface-700/60 p-3 space-y-2">
      <div className="flex items-center gap-2">
        <HeartPulse size={12} className="text-emerald-400" />
        <p className="text-[10px] font-semibold text-gray-600 uppercase tracking-wider">
          {t('glassroom.healthTitle', 'Operational Health')}
        </p>
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-1 2xl:grid-cols-2">
        {entries.map(entry => (
          <div key={entry.label} className="rounded-lg border border-surface-700 bg-surface-900/70 px-3 py-2.5 min-w-0">
            <p className="text-[9px] uppercase tracking-wider text-gray-500">{entry.label}</p>
            <p className={`mt-2 inline-flex rounded-full px-2 py-1 text-[10px] font-medium ${toneClass[entry.tone]}`}>
              {entry.value}
            </p>
          </div>
        ))}
      </div>
    </div>
  )
}

export function QuotaTelemetryPanel({ quota }: { quota?: QuotaTelemetry }) {
  const { t } = useTranslation()
  if (!quota || quota.quota <= 0) return null

  const percent = Math.round(quota.ratio * 100)
  const barTone = percent >= 90 ? 'bg-red-400' : percent >= 70 ? 'bg-amber-400' : 'bg-emerald-400'

  return (
    <div className="border-b border-surface-700/60 p-3 space-y-2">
      <div className="flex items-center gap-2">
        <ShieldAlert size={12} className="text-sky-300" />
        <p className="text-[10px] font-semibold text-gray-600 uppercase tracking-wider">
          {t('glassroom.quotaTitle', 'Quota Telemetry')}
        </p>
      </div>
      <div className="rounded-lg border border-surface-700 bg-surface-900/70 p-2.5">
        <div className="flex items-center justify-between text-[10px] text-gray-400">
          <span>{t('glassroom.quotaUsed', 'Used')}</span>
          <span className="tabular-nums">{quota.used} / {quota.quota}</span>
        </div>
        <div className="mt-2 h-2 rounded-full bg-surface-800 overflow-hidden">
          <div className={`h-full ${barTone}`} style={{ width: `${Math.max(4, percent)}%` }} />
        </div>
        <div className="mt-2 flex items-center justify-between text-[10px] text-gray-500">
          <span>{percent}%</span>
          <span>{t('glassroom.quotaRemaining', 'Remaining')}: {quota.remaining}</span>
        </div>
      </div>
    </div>
  )
}

export function TaskAutonomyTree({ tasks }: { tasks: TaskAutonomyEntry[] }) {
  const { t } = useTranslation()
  if (tasks.length === 0) return null

  const stateTone: Record<TaskAutonomyEntry['stepStates'][number]['state'], string> = {
    pending: 'bg-surface-800 text-gray-500',
    active: 'bg-sky-500/10 text-sky-300',
    done: 'bg-emerald-500/10 text-emerald-300',
    blocked: 'bg-red-500/10 text-red-300',
  }

  return (
    <div className="border-b border-surface-700/60 p-3 space-y-2">
      <div className="flex items-center gap-2">
        <GitBranch size={12} className="text-violet-300" />
        <p className="text-[10px] font-semibold text-gray-600 uppercase tracking-wider">
          {t('glassroom.autonomyTreeTitle', 'Autonomy Tree')}
        </p>
      </div>
      <div className="space-y-2">
        {tasks.map(task => (
          <div key={task.id} className="rounded-lg border border-surface-700 bg-surface-900/70 p-2.5">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0 flex-1">
                <p className="text-[11px] font-medium text-gray-200 break-words">{task.title}</p>
                <p className="mt-1 text-[10px] text-gray-500">
                  {phaseLabel(t, task.currentPhase)} · {t('glassroom.reworksCount', { n: task.reworkCount, defaultValue: '{{n}} reworks' })} · {t('glassroom.qaReviewsCount', { n: task.reviewCount, defaultValue: '{{n}} QA reviews' })}
                </p>
              </div>
              <span className="text-[10px] text-gray-500">#{task.id}</span>
            </div>
            <div className="mt-2 space-y-1.5">
              {task.stepStates.map(step => (
                <div key={step.key} className="flex items-start gap-2">
                  <span className={`mt-0.5 h-2 w-2 rounded-full ${
                    step.state === 'done' ? 'bg-emerald-400' :
                    step.state === 'active' ? 'bg-sky-400' :
                    step.state === 'blocked' ? 'bg-red-400' : 'bg-surface-700'
                  }`} />
                  <div className="min-w-0 flex-1">
                    <span className={`inline-flex rounded-full px-1.5 py-0.5 text-[9px] font-medium ${stateTone[step.state]}`}>
                      {step.label}
                    </span>
                    {step.detail && <p className="mt-1 text-[10px] text-gray-500 break-words">{step.detail}</p>}
                  </div>
                </div>
              ))}
            </div>
            {task.blockedReason && (
              <div className="mt-2 flex flex-wrap gap-2 text-[10px] text-gray-500">
                <span className="text-red-300">{task.blockedReason}</span>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Sprint Retrospective Panel ────────────────────────────────────────────────

export function SprintRetroPanel({ retro }: { retro: SprintRetro | null }) {
  const { t } = useTranslation()
  if (!retro) return null

  const rate = retro.completionRate
  const rateTone = rate >= 80 ? 'text-emerald-400' : rate >= 50 ? 'text-amber-400' : 'text-red-400'
  const barColor = rate >= 80 ? 'bg-emerald-500' : rate >= 50 ? 'bg-amber-500' : 'bg-red-500'

  return (
    <div className="border-b border-surface-700/60 px-3 py-2.5">
      <div className="flex items-center gap-2 mb-2">
        <HeartPulse size={11} className="text-fuchsia-400" />
        <p className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider">
          {t('glassroom.retroTitle', 'Last Sprint Retro')}
        </p>
      </div>
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <span className="text-[11px] text-gray-400">{t('glassroom.retroCompletion', 'Completion')}</span>
          <span className={`text-[11px] font-semibold ${rateTone}`}>{rate}%</span>
        </div>
        <div className="w-full bg-surface-800 rounded-full h-1">
          <div className={`${barColor} h-1 rounded-full transition-all`} style={{ width: `${rate}%` }} />
        </div>
        <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 mt-1">
          <span className="text-[10px] text-gray-500">{t('glassroom.retroDone', 'Done')}</span>
          <span className="text-[10px] text-gray-300 text-right">{retro.doneTasks}/{retro.totalTasks}</span>
          <span className="text-[10px] text-gray-500">{t('glassroom.retroBlocked', 'Blocked')}</span>
          <span className={`text-[10px] text-right ${retro.blockedTasks > 0 ? 'text-amber-400' : 'text-gray-300'}`}>{retro.blockedTasks}</span>
          <span className="text-[10px] text-gray-500">{t('glassroom.retroRework', 'Rework cycles')}</span>
          <span className={`text-[10px] text-right ${retro.reworkCycles > 3 ? 'text-amber-400' : 'text-gray-300'}`}>{retro.reworkCycles}</span>
          <span className="text-[10px] text-gray-500">{t('glassroom.retroQA', 'QA failures')}</span>
          <span className={`text-[10px] text-right ${retro.qaFailures > 2 ? 'text-red-400' : 'text-gray-300'}`}>{retro.qaFailures}</span>
        </div>
      </div>
    </div>
  )
}
