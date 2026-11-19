/**
 * Sprints — every sprint of the selected company, active ones first, plus the
 * planner to launch the next one.
 */
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  Building2, CalendarDays, CircleCheck, CircleDashed, CirclePause, CirclePlay, CircleX, Layers, RefreshCw, Split,
  type LucideIcon,
} from 'lucide-react'
import { useState } from 'react'
import { useAppStore } from '../store/useAppStore'
import { useCurrentRole, useSprints } from '../hooks/useQueries'
import { useBenchScorecards } from '../hooks/useBench'
import type { SprintSummary } from '../services/api'
import { useSprintsWithArchived, type ManagedSprint } from '../services/sprintAdmin'
import { CostLine } from '../components/CostSummary'
import { SprintPromptWeight } from '../components/PromptWeight'
import { SprintAdminActions } from '../components/SprintAdminActions'
import { QuickLaunch } from '../components/QuickLaunch'
import { sprintPath } from './SprintWorkspace'
import { LearningTrend } from '../components/LearningTrend'
import { ManagerRecordCard } from '../components/ManagerRecordCard'
import { RoutingRecord } from '../components/RoutingRecord'
import { GateRefutations } from '../components/GateRefutations'
import { MissingModuleMentions } from '../components/MissingModuleMentions'
import { CountPill, EmptyState, PageHeader, Panel, StatusBadge, type StatusTone } from '../components/ui'

/** Each status as an icon, a word and a tone: never the colour alone. */
const STATUS_LOOK: Record<string, { icon: LucideIcon; tone: StatusTone }> = {
  active: { icon: CirclePlay, tone: 'success' },
  needs_split: { icon: Split, tone: 'warning' },
  paused: { icon: CirclePause, tone: 'warning' },
  completed: { icon: CircleCheck, tone: 'neutral' },
  cancelled: { icon: CircleX, tone: 'danger' },
}

function activeFirst<T extends SprintSummary>(sprints: T[]): T[] {
  // Array.prototype.sort is stable, so the API's newest-first order survives within each group.
  return [...sprints].sort((a, b) => Number(b.status === 'active') - Number(a.status === 'active'))
}

function SprintRow({ sprint, current, companyId, role }: { sprint: ManagedSprint; current: boolean; companyId: number; role: string | null }) {
  const { t } = useTranslation()
  const pct = sprint.total > 0 ? Math.round((sprint.done / sprint.total) * 100) : 0
  const look: { icon: LucideIcon; tone: StatusTone } = STATUS_LOOK[sprint.status] ?? { icon: CircleDashed, tone: 'neutral' }
  return (
    <li className="data-row grid gap-x-4 gap-y-2 sm:grid-cols-[minmax(0,1fr)_7.5rem_12rem_7rem] sm:items-center">
      <div className="min-w-0">
        <Link to={sprintPath(sprint.id)} className="block truncate rounded-sm text-[13px] font-semibold text-gray-100 hover:text-brand-300">
          {sprint.sprint_name}
        </Link>
        {sprint.goal && <p className="mt-0.5 truncate text-xs text-gray-400">{sprint.goal}</p>}
        {current && (
          <p className="mt-0.5 text-[11px] text-brand-300">{t('sprints.lastOpened', { defaultValue: 'Last opened' })}</p>
        )}
        {sprint.archived_at && (
          <p className="text-meta mt-0.5">
            {t('sprintAdmin.archivedBy', { defaultValue: 'Archived by {{who}}', who: sprint.archived_by ?? '—' })}
          </p>
        )}
      </div>
      <StatusBadge icon={look.icon} tone={look.tone}
        label={t(`dashboard.status.${sprint.status}`, { defaultValue: sprint.status })} />
      <div>
        <div className="text-meta mb-1 flex justify-between tabular-nums">
          <span>{t('sprints.progress', { defaultValue: '{{done}} / {{total}} done', done: sprint.done, total: sprint.total })}</span>
          <span className="tabular-nums">{pct}%</span>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-surface-700" aria-hidden="true">
          <div className="h-full rounded-full bg-brand-500" style={{ width: `${pct}%` }} />
        </div>
        {(sprint.blocked > 0 || sprint.rework > 0) && (
          <p className="text-meta mt-1 tabular-nums">
            {[
              sprint.blocked > 0 && t('sprints.blockedCount', { count: sprint.blocked, defaultValue: '{{count}} blocked' }),
              sprint.rework > 0 && t('sprints.reworkCount', { count: sprint.rework, defaultValue: '{{count}} rework requests' }),
            ].filter(Boolean).join(' · ')}
          </p>
        )}
        {sprint.cost && <div className="mt-1"><CostLine cost={sprint.cost} /></div>}
        {sprint.cost && sprint.cost.turns > 0 && <SprintPromptWeight sprintId={sprint.id} />}
      </div>
      <p className="flex items-center gap-1 text-xs tabular-nums text-gray-400">
        <CalendarDays size={12} className="shrink-0" aria-hidden="true" />
        {sprint.start_date ?? t('sprints.noStartDate', { defaultValue: 'Not started' })}
      </p>
      <SprintAdminActions sprint={sprint} companyId={companyId} role={role} />
    </li>
  )
}

export default function Sprints() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const activeSprintId = useAppStore(state => state.activeSprintId)
  const setActiveSprint = useAppStore(state => state.setActiveSprint)
  const role = useCurrentRole()
  const [showArchived, setShowArchived] = useState(false)
  const listed = useSprints(companyId)
  const withArchived = useSprintsWithArchived(companyId, showArchived)
  const shown = showArchived ? withArchived : listed
  const { isLoading, isError, refetch } = shown
  const sprints = activeFirst<ManagedSprint>(shown.data ?? [])
  // The learning trend reads the company's listed sprints, never the archived ones.
  const trend = activeFirst<SprintSummary>(listed.data ?? [])
  const bench = useBenchScorecards()

  return (
    <div className="page-container max-w-6xl space-y-6">
      <PageHeader
        title={t('nav.sprints', { defaultValue: 'Sprints' })}
        subtitle={t('sprints.subtitle', { defaultValue: 'Every sprint of this company, active ones first.' })}
        actions={companyId !== null && (
          <label className="flex h-8 items-center gap-2 rounded-md border border-surface-700 px-2.5 text-xs text-gray-300 hover:bg-surface-800">
            <input type="checkbox" className="h-3.5 w-3.5 accent-brand-500" checked={showArchived} onChange={e => setShowArchived(e.target.checked)} />
            {t('sprintAdmin.showArchived', { defaultValue: 'Show archived sprints' })}
          </label>
        )}
      />

      {companyId === null ? (
        <EmptyState icon={Building2} dashed
          title={t('sprints.selectCompany', { defaultValue: 'Select a company to see its sprints.' })}
          actions={<Link to="/companies/select" className="btn-tool border border-surface-700">{t('workspace.selectCompany', { defaultValue: 'Select company' })}</Link>} />
      ) : isLoading ? (
        <div role="status" aria-label={t('common.loading', { defaultValue: 'Loading...' })} className="space-y-2">
          {[0, 1, 2].map(index => <div key={index} className="skeleton h-14 rounded-xl" />)}
        </div>
      ) : isError ? (
        <div role="alert" className="alert-error items-center">
          {t('sprints.loadError', { defaultValue: 'Failed to load sprints.' })}
          <button type="button" className="btn-tool ml-auto" onClick={() => refetch()}>
            <RefreshCw size={13} aria-hidden="true" />{t('common.retry', { defaultValue: 'Retry' })}
          </button>
        </div>
      ) : sprints.length === 0 ? (
        <EmptyState icon={Layers} dashed
          title={t('sprints.emptyTitle', { defaultValue: 'No sprints yet' })}
          description={t('sprints.emptyDescription', { defaultValue: 'Plan a sprint below, or create a new company with a foundation sprint.' })}
          actions={<>
            <a href="#plan-sprint" className="btn btn-primary btn-sm">{t('sprints.planSprint', { defaultValue: 'Plan a sprint' })}</a>
            <Link to="/companies/new" className="btn-tool border border-surface-700">{t('nav.newCompany', { defaultValue: 'New company' })}</Link>
          </>} />
      ) : (
        <>
          <LearningTrend sprints={trend} benchCards={bench.data?.cards} />
          <RoutingRecord companyId={companyId} />
          <GateRefutations companyId={companyId} />
          <MissingModuleMentions companyId={companyId} />
          <ManagerRecordCard companyId={companyId} />
          <Panel title={t('sprints.listTitle')}
            count={<CountPill count={sprints.length} label={t('sprints.count', { count: sprints.length })} />}>
            <ul className="data-list" aria-label={t('nav.sprints', { defaultValue: 'Sprints' })}>
              {sprints.map(sprint => (
                <SprintRow key={sprint.id} sprint={sprint} current={sprint.id === activeSprintId} companyId={companyId} role={role} />
              ))}
            </ul>
          </Panel>
        </>
      )}

      <section id="plan-sprint" aria-label={t('sprints.planSprint', { defaultValue: 'Plan a sprint' })} className="scroll-mt-20">
        <QuickLaunch onSuccess={id => setActiveSprint(id)} />
      </section>
    </div>
  )
}
