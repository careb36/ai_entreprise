/**
 * Sprint Overview — the default tab of a sprint page (`/sprints/:id`).
 *
 * Layout: KPI strip → alert banner → main grid (sprint card + agent roster) → quick launch.
 * Scope: read-only overview + sprint launch. Heavy state lives in stores/queries.
 * Navigation (tabs, breadcrumb, shell status) belongs to the sprint page and the
 * app shell, so nothing here repeats it. Styled with the design system
 * (docs/contexts/frontend.md, "Design system").
 */

import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import {
  Activity, AlertTriangle, Bot, CheckCircle2, ChevronRight, CircleCheck, CircleDashed,
  CircleDot, Cpu, Hourglass, Layers, Loader2, Moon, Settings2,
  SplitSquareVertical, Target, Zap,
} from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import { useAgentStore } from '../store/agentStore'
import { useSprintBoard, useAnalyticsSummary, useQuotas } from '../hooks/useQueries'
import { QuickLaunch, ROLE_DISPLAY, SCOPE_META } from '../components/QuickLaunch'
import { SprintHealthChart } from '../components/SprintHealthChart'
import { CountPill, EmptyState, Panel, StatusBadge } from '../components/ui'
import { SprintStatusBadge } from './sprint/SprintStatusBadge'

// ── Atoms ─────────────────────────────────────────────────────────────────────

const KPI_TONES = {
  neutral: { value: 'text-gray-100', icon: 'text-gray-400' },
  accent: { value: 'text-brand-300', icon: 'text-brand-300' },
  warn: { value: 'text-amber-300', icon: 'text-amber-300' },
}

/** One figure of the KPI strip: a label, the number in tabular figures, and a line saying what it means. */
function KpiCard({
  icon: Icon, label, value, sub, accent = false, warn = false,
}: {
  icon: typeof Activity
  label: string
  value: string | number
  sub?: string
  accent?: boolean
  warn?: boolean
}) {
  const tone = KPI_TONES[warn ? 'warn' : accent ? 'accent' : 'neutral']
  return (
    <div className={`panel px-3 py-2.5 ${warn ? 'border-amber-700/50' : ''}`}>
      <p className="text-meta flex items-center gap-1.5 font-medium">
        <Icon size={12} aria-hidden="true" className={`flex-shrink-0 ${tone.icon}`} />
        <span className="truncate">{label}</span>
      </p>
      <p className={`mt-1 text-xl font-semibold leading-tight tabular-nums ${tone.value}`}>{value}</p>
      {sub && <p className="text-meta mt-0.5 truncate">{sub}</p>}
    </div>
  )
}

function ScopeBadge({ scope }: { scope?: string }) {
  const { t } = useTranslation()
  const scopeKey = scope && SCOPE_META[scope] ? scope : 'M'
  const meta = SCOPE_META[scopeKey]
  return (
    <span className="chip flex-shrink-0 gap-1">
      <Layers size={10} aria-hidden="true" />
      {t(`dashboard.scope.${scopeKey}.label`, { defaultValue: meta.label })}
    </span>
  )
}

function ProgressBar({ done, total }: { done: number; total: number }) {
  const { t } = useTranslation()
  const pct = total > 0 ? Math.round((done / total) * 100) : 0
  return (
    <div>
      <div className="text-meta mb-1 flex justify-between tabular-nums">
        <span>{t('dashboard.tasksDone', { defaultValue: '{{done}} of {{total}} tasks done', done, total })}</span>
        <span className="tabular-nums">{pct}%</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-surface-700" aria-hidden="true">
        <div
          className="h-full rounded-full bg-brand-500 transition-all duration-500 motion-reduce:transition-none"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

// ── Needs Split Alert ──────────────────────────────────────────────────────────

function NeedsSplitAlert({ sprintId, sprintName }: { sprintId: number; sprintName: string }) {
  const { t } = useTranslation()
  return (
    <div className="alert-warning items-center py-3 animate-fade-in motion-reduce:animate-none">
      <SplitSquareVertical size={16} aria-hidden="true" className="flex-shrink-0" />
      <div className="min-w-0 flex-1">
        <p className="text-[13px] font-semibold text-amber-200">{t('dashboard.needsSplit', { defaultValue: 'Sprint needs splitting' })}</p>
        <p className="mt-0.5 text-xs text-amber-100/80">
          <strong className="font-medium text-amber-100">{sprintName}</strong> {t('dashboard.needsSplitDesc', { defaultValue: 'This sprint exceeded agent capacity. Split it into smaller scopes.' })}
        </p>
      </div>
      <Link to={`/sprints/${sprintId}/live`} className="btn-tool flex-shrink-0 text-amber-200 hover:text-amber-100">
        {t('dashboard.review', { defaultValue: 'Review' })} <ChevronRight size={12} aria-hidden="true" />
      </Link>
    </div>
  )
}

/** An agent's state as an icon with a spoken name, never a coloured dot alone. */
function AgentState({ fsmState }: { fsmState: string }) {
  const { t } = useTranslation()
  const state = fsmState === 'active' || fsmState === 'error' || fsmState === 'waiting' ? fsmState : 'idle'
  const label = t(`dashboard.agentState.${state}`)
  const Icon = state === 'active' ? Loader2 : state === 'error' ? AlertTriangle : state === 'waiting' ? Hourglass : Moon
  const tone = state === 'active' ? 'text-emerald-300 animate-spin motion-reduce:animate-none'
    : state === 'error' ? 'text-red-300'
    : state === 'waiting' ? 'text-brand-300' : 'text-gray-400'
  return (
    <span role="img" aria-label={label} title={label} className="flex-shrink-0">
      <Icon size={12} aria-hidden="true" className={tone} />
    </span>
  )
}

function AgentRow({ role, fsmState, activity }: { role: string; fsmState: string; activity: string }) {
  const { t } = useTranslation()
  const meta = ROLE_DISPLAY[role] ?? { label: role, initials: role.slice(0, 2).toUpperCase(), color: 'text-gray-300', bg: 'bg-gray-600' }

  return (
    <li className="data-row flex items-center gap-2.5 py-2">
      <div aria-hidden="true" className={`flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-md text-[10px] font-bold text-white ${meta.bg}`}>
        {ROLE_DISPLAY[role] ? t(`dashboard.roles.${role}.initials`, { defaultValue: meta.initials }) : meta.initials}
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-xs font-medium text-gray-100">{ROLE_DISPLAY[role] ? t(`dashboard.roles.${role}.label`, { defaultValue: meta.label }) : meta.label}</p>
        {activity ? (
          <p className="text-meta truncate">{activity}</p>
        ) : (
          <p className="text-meta italic">{t('dashboard.idle', { defaultValue: 'idle' })}</p>
        )}
      </div>
      <AgentState fsmState={fsmState} />
    </li>
  )
}

// ── Sprint Card ───────────────────────────────────────────────────────────────

const COUNT_TONES = {
  inProgress: 'text-brand-300',
  blocked: 'text-red-300',
  done: 'text-emerald-300',
}

function SprintCard({
  sprint, columns, total,
}: {
  sprint: any; columns: Record<string, any[]>; total: number
}) {
  const { t }   = useTranslation()
  const done    = columns.done?.length ?? 0
  const blocked = columns.blocked?.length ?? 0
  const inProg  = columns.in_progress?.length ?? 0

  return (
    <Panel title={sprint.sprint_name} headingLevel={3}
      actions={<SprintStatusBadge status={sprint.status ?? 'active'} />}>
      <div className="space-y-3 p-3">
        <div className="flex min-w-0 items-center gap-2">
          <ScopeBadge scope={sprint.scope_size} />
          {sprint.goal && (
            <p className="truncate text-xs text-gray-400">{sprint.goal}</p>
          )}
        </div>
        <ProgressBar done={done} total={total} />

        <dl className="grid grid-cols-3 gap-2">
          {[
            { label: t('dashboard.inProgress', { defaultValue: 'In Progress' }), count: inProg, tone: COUNT_TONES.inProgress },
            { label: t('dashboard.blocked', { defaultValue: 'Blocked' }), count: blocked, tone: COUNT_TONES.blocked },
            { label: t('dashboard.done', { defaultValue: 'Done' }), count: done, tone: COUNT_TONES.done },
          ].map(({ label, count, tone }) => (
            <div key={label} className="flex flex-col-reverse rounded-md border border-surface-700 bg-surface-950/40 px-2 py-1.5 text-center">
              <dt className="text-meta">{label}</dt>
              <dd className={`text-base font-semibold tabular-nums ${tone}`}>{count}</dd>
            </div>
          ))}
        </dl>
      </div>
    </Panel>
  )
}

function QuotaWidget({ companyId }: { companyId: number | null }) {
  const { t } = useTranslation()
  const { data } = useQuotas(companyId)
  const quotas = Array.isArray(data) ? data : []
  const totalUsed = quotas.reduce((s: number, q: any) => s + (q.token_quota_used || 0), 0)
  const totalLimit = quotas.reduce((s: number, q: any) => s + (q.token_quota_limit || 0), 0)
  if (!totalLimit) return null
  const pct = Math.min(100, Math.round((totalUsed / totalLimit) * 100))
  const color = pct >= 90 ? 'text-red-300' : pct >= 70 ? 'text-amber-300' : 'text-emerald-300'
  return (
    <div className="flex h-8 items-center gap-1.5 rounded-md border border-surface-700 bg-surface-900 px-2.5 text-xs font-medium tabular-nums text-gray-300">
      <Zap size={12} aria-hidden="true" className={color} />
      <span>{t('dashboard.tokenUsage', { used: totalUsed.toLocaleString(), limit: totalLimit.toLocaleString(), defaultValue: '{{used}} / {{limit}} tokens' })}</span>
      <span className={color}>({pct}%)</span>
    </div>
  )
}

// ── Sprint Overview ───────────────────────────────────────────────────────────

export default function SprintOverview({ sprintId }: { sprintId: number | null }) {
  const { t } = useTranslation()
  const { setActiveSprint, companyId, companyName } = useAppStore()
  const { data: board, isLoading: boardLoading } = useSprintBoard(sprintId)
  const { data: analytics } = useAnalyticsSummary(sprintId)
  const { data: quotaData } = useQuotas(companyId)
  const connectToSprintStream = useAgentStore(s => s.connect)
  const disconnectFromSprintStream = useAgentStore(s => s.disconnect)
  const agentStates = useAgentStore(s => s.agentStates)
  const isSSEConnected = useAgentStore(s => s.isConnected)

  useEffect(() => {
    if (!sprintId) return
    connectToSprintStream(String(sprintId))
    return () => {
      disconnectFromSprintStream()
    }
  }, [sprintId, connectToSprintStream, disconnectFromSprintStream])

  const sprint  = board?.sprint
  const columns = board?.columns ?? {}
  const total   = board?.total ?? 0
  const done    = columns.done?.length ?? 0
  const blocked = columns.blocked?.length ?? 0
  const inProg  = columns.in_progress?.length ?? 0

  const healthScore    = analytics?.health_score ?? null
  const needsSplit     = sprint?.status === 'needs_split'

  const activeAgentCount  = Object.values(agentStates).filter(a => a.fsmState === 'active').length
  const visibleAgents     = Object.entries(agentStates)
  const hasAgents         = visibleAgents.length > 0

  return (
    <div className="min-w-0 space-y-4">

      {/* ── Overview header ───────────────────────────────────────── */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <h2 className="truncate text-[15px] font-semibold text-gray-100">
            {companyName ? `${companyName}` : t('dashboard.title', { defaultValue: 'La Fabrique' })}
          </h2>
          <p className="mt-0.5 text-xs text-gray-400">
            {t('dashboard.commandCenter', { defaultValue: 'AI Sprint command center' })}
            {sprint && (
              <> · <span className="text-gray-300">{sprint.sprint_name}</span></>
            )}
          </p>
        </div>
        <QuotaWidget companyId={companyId} />
      </div>

      {/* ── Quota alert ────────────────────────────────────────────── */}
      {Array.isArray(quotaData) && quotaData.some((q: any) => q.limit > 0 && (q.used / q.limit) >= 0.8) && (
        <div className="alert-warning items-center py-2.5">
          <AlertTriangle size={15} aria-hidden="true" className="flex-shrink-0" />
          <span className="text-[13px]">{t('dashboard.quotaAlert', { defaultValue: 'Token quota is above 80% for one or more business units. Consider resetting or increasing limits.' })}</span>
          <Link to="/company/quotas" className="ml-auto flex-shrink-0 text-xs text-amber-200 underline hover:text-amber-100">{t('dashboard.manageQuotas', { defaultValue: 'Manage quotas' })}</Link>
        </div>
      )}

      {/* ── KPI strip ─────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-5">
        <KpiCard
          icon={Target}
          label={t('dashboard.sprintTasks', { defaultValue: 'Sprint Tasks' })}
          value={total}
          sub={sprint ? sprint.sprint_name : t('dashboard.noActiveSprint', { defaultValue: 'No active sprint' })}
        />
        <KpiCard
          icon={CheckCircle2}
          label={t('dashboard.completed', { defaultValue: 'Completed' })}
          value={done}
          sub={total > 0 ? t('dashboard.percentDone', { defaultValue: '{{percent}}% done', percent: Math.round((done / total) * 100) }) : '—'}
          accent
        />
        <KpiCard
          icon={CircleDot}
          label={t('dashboard.blocked', { defaultValue: 'Blocked' })}
          value={blocked}
          sub={blocked > 0 ? t('dashboard.needsAttention', { defaultValue: 'Needs attention' }) : t('dashboard.clear', { defaultValue: 'Clear' })}
          warn={blocked > 0}
        />
        <KpiCard
          icon={Bot}
          label={t('dashboard.agentsWorking', { defaultValue: 'Agents Working' })}
          value={activeAgentCount}
          sub={isSSEConnected ? t('dashboard.streamConnected', { defaultValue: 'Live stream connected' }) : t('dashboard.disconnected', { defaultValue: 'Disconnected' })}
          accent={activeAgentCount > 0}
        />
        <KpiCard
          icon={Zap}
          label={t('dashboard.healthScore', { defaultValue: 'Health Score' })}
          value={healthScore !== null ? `${healthScore}` : '—'}
          sub={healthScore !== null
            ? (healthScore >= 80 ? t('dashboard.sprintHealthy', { defaultValue: 'Sprint healthy' }) : t('dashboard.reviewNeeded', { defaultValue: 'Review needed' }))
            : t('dashboard.noHealthData', { defaultValue: 'No data yet' })}
          accent={healthScore !== null && healthScore >= 80}
          warn={healthScore !== null && healthScore < 60}
        />
      </div>

      {/* ── Needs Split alert ─────────────────────────────────────── */}
      {needsSplit && sprint && sprintId && (
        <NeedsSplitAlert sprintId={sprintId} sprintName={sprint.sprint_name} />
      )}

      {/* ── Main grid ─────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">

        {/* Sprint card — takes 2 cols */}
        <div className="space-y-4 lg:col-span-2">
          {boardLoading ? (
            <div role="status" className="panel flex items-center justify-center gap-2 p-6 text-xs text-gray-400">
              <Loader2 size={15} aria-hidden="true" className="animate-spin text-brand-400 motion-reduce:animate-none" />
              {t('dashboard.loadingSprint', { defaultValue: 'Loading sprint data...' })}
            </div>
          ) : sprint && sprintId ? (
            <>
              <SprintCard sprint={sprint} columns={columns} total={total} />
              <SprintHealthChart
                total={total}
                done={done}
                inProgress={inProg}
                blocked={blocked}
                healthScore={healthScore}
              />
            </>
          ) : (
            <EmptyState icon={Cpu} dashed
              title={t('dashboard.noActiveSprint', { defaultValue: 'No active sprint' })}
              description={t('dashboard.launchHint', { defaultValue: 'Launch a sprint below to start working with your agents' })} />
          )}

          {/* Quick launch */}
          <QuickLaunch onSuccess={id => setActiveSprint(id)} />
        </div>

        {/* Right column: agent roster + system status */}
        <div className="space-y-4">
          <Panel headingLevel={3}
            title={<span className="flex items-center gap-1.5"><Bot size={13} aria-hidden="true" className="text-brand-300" />{t('dashboard.agentRoster', { defaultValue: 'Agent Roster' })}</span>}
            count={hasAgents
              ? <CountPill count={visibleAgents.length} label={t('dashboard.agentCount', { defaultValue: '{{count}} agents', count: visibleAgents.length })} />
              : undefined}>
            {hasAgents ? (
              <ul className="data-list">
                {visibleAgents.map(([role, state]) => (
                  <AgentRow
                    key={role}
                    role={role}
                    fsmState={state.fsmState}
                    activity={state.currentActivity}
                  />
                ))}
              </ul>
            ) : (
              <div className="px-3 py-4 text-center">
                <p className="text-xs text-gray-300">{t('dashboard.noAgents', { defaultValue: 'No agents seen yet.' })}</p>
                <p className="text-meta mt-0.5">{t('dashboard.activateAgents', { defaultValue: 'Launch a sprint to activate agents.' })}</p>
              </div>
            )}
            <div className="border-t border-surface-700 px-1.5 py-1.5">
              <Link to="/agents" className="btn-tool text-brand-300 hover:text-brand-200">
                <Settings2 size={12} aria-hidden="true" />
                {t('dashboard.manageAgents', { defaultValue: 'Manage agents' })} <ChevronRight size={12} aria-hidden="true" />
              </Link>
            </div>
          </Panel>

          <Panel headingLevel={3} title={t('dashboard.system', { defaultValue: 'System' })}>
            <ul className="data-list">
              {[
                { label: t('dashboard.agentStream', { defaultValue: 'Agent Stream' }), ok: isSSEConnected },
              ].map(({ label, ok }) => (
                <li key={label} className="data-row flex items-center justify-between gap-2 py-2">
                  <span className="text-xs text-gray-300">{label}</span>
                  <StatusBadge icon={ok ? CircleCheck : CircleDashed} tone={ok ? 'success' : 'neutral'}
                    label={ok ? t('dashboard.online', { defaultValue: 'Online' }) : t('dashboard.offline', { defaultValue: 'Offline' })} />
                </li>
              ))}
            </ul>
          </Panel>
        </div>
      </div>
    </div>
  )
}
