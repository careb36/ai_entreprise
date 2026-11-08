/**
 * Sprint Overview — the default tab of a sprint page (`/sprints/:id`).
 *
 * Layout: KPI strip → alert banner → main grid (sprint card + agent roster) → quick launch.
 * Scope: read-only overview + sprint launch. Heavy state lives in stores/queries.
 * Navigation (tabs, breadcrumb, shell status) belongs to the sprint page and the
 * app shell, so nothing here repeats it.
 */

import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import {
  Activity, AlertTriangle, Bot, CheckCircle2, ChevronRight,
  CircleDot, Cpu, Layers, Loader2, Settings2,
  ShieldAlert, SplitSquareVertical, Target, Zap,
} from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import { useAgentStore } from '../store/agentStore'
import { useSprintBoard, useAnalyticsSummary, useQuotas } from '../hooks/useQueries'
import { QuickLaunch, ROLE_DISPLAY, SCOPE_META } from '../components/QuickLaunch'
import { SprintHealthChart } from '../components/SprintHealthChart'
import { LoadingSkeleton } from '../components/LoadingSkeleton'

// ── Constants ─────────────────────────────────────────────────────────────────

const STATUS_META: Record<string, { label: string; color: string; dot: string }> = {
  active:      { label: 'Active',      color: 'text-emerald-400', dot: 'bg-emerald-400' },
  needs_split: { label: 'Needs Split', color: 'text-amber-400',   dot: 'bg-amber-400 animate-pulse' },
  completed:   { label: 'Completed',   color: 'text-gray-400',    dot: 'bg-gray-400'    },
  cancelled:   { label: 'Cancelled',   color: 'text-red-400',     dot: 'bg-red-400'     },
}

// ── Atoms ─────────────────────────────────────────────────────────────────────

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
  return (
    <div className={`relative overflow-hidden rounded-xl border p-4 transition-shadow hover:shadow-card-md ${
      warn
        ? 'bg-amber-950/20 border-amber-500/25'
        : accent
          ? 'bg-brand-950/40 border-brand-600/25'
          : 'bg-surface-900 border-surface-700'
    }`}>
      <div className="flex items-start justify-between">
        <div>
          <p className="text-[11px] font-medium text-gray-500 uppercase tracking-wider mb-1">{label}</p>
          <p className={`text-2xl font-bold tabular-nums ${
            warn ? 'text-amber-400' : accent ? 'text-brand-300' : 'text-white'
          }`}>
            {value}
          </p>
          {sub && <p className="text-[11px] text-gray-500 mt-0.5">{sub}</p>}
        </div>
        <div className={`p-2 rounded-lg ${
          warn ? 'bg-amber-500/10' : accent ? 'bg-brand-500/10' : 'bg-surface-800'
        }`}>
          <Icon size={16} className={warn ? 'text-amber-400' : accent ? 'text-brand-400' : 'text-gray-400'} />
        </div>
      </div>
    </div>
  )
}

function ScopeBadge({ scope }: { scope?: string }) {
  const { t } = useTranslation()
  const scopeKey = scope && SCOPE_META[scope] ? scope : 'M'
  const meta = SCOPE_META[scopeKey]
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[11px] font-semibold ${meta.bg} ${meta.color}`}>
      <Layers size={10} />
      {t(`dashboard.scope.${scopeKey}.label`, { defaultValue: meta.label })}
    </span>
  )
}

function StatusBadge({ status }: { status?: string }) {
  const { t } = useTranslation()
  const statusKey = status && STATUS_META[status] ? status : 'active'
  const meta = STATUS_META[statusKey]
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] font-medium">
      <span className={`w-1.5 h-1.5 rounded-full ${meta.dot}`} />
      <span className={meta.color}>{t(`dashboard.status.${statusKey}`, { defaultValue: meta.label })}</span>
    </span>
  )
}

function ProgressBar({ done, total }: { done: number; total: number }) {
  const { t } = useTranslation()
  const pct = total > 0 ? Math.round((done / total) * 100) : 0
  return (
    <div>
      <div className="flex justify-between text-[11px] text-gray-500 mb-1">
        <span>{t('dashboard.tasksDone', { defaultValue: '{{done}} of {{total}} tasks done', done, total })}</span>
        <span className="tabular-nums">{pct}%</span>
      </div>
      <div className="h-1.5 bg-surface-700 rounded-full overflow-hidden">
        <div
          className="h-full bg-gradient-to-r from-brand-500 to-brand-400 rounded-full transition-all duration-500"
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
    <div className="flex items-start gap-3 p-4 rounded-xl border border-amber-500/30 bg-amber-950/20 animate-fade-in">
      <div className="p-2 rounded-lg bg-amber-500/10 shrink-0">
        <SplitSquareVertical size={16} className="text-amber-400" />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-amber-300">{t('dashboard.needsSplit', { defaultValue: 'Sprint needs splitting' })}</p>
        <p className="text-xs text-amber-200/60 mt-0.5">
          <strong className="text-amber-200/80">{sprintName}</strong> {t('dashboard.needsSplitDesc', { defaultValue: 'This sprint exceeded agent capacity. Split it into smaller scopes.' })}
        </p>
      </div>
      <Link
        to={`/sprints/${sprintId}/live`}
        className="shrink-0 flex items-center gap-1 text-xs font-medium text-amber-400 hover:text-amber-300 transition-colors"
      >
        {t('dashboard.review', { defaultValue: 'Review' })} <ChevronRight size={12} />
      </Link>
    </div>
  )
}

function AgentRow({ role, fsmState, activity }: { role: string; fsmState: string; activity: string }) {
  const { t } = useTranslation()
  const meta = ROLE_DISPLAY[role] ?? { label: role, initials: role.slice(0, 2).toUpperCase(), color: 'text-gray-300', bg: 'bg-gray-600' }
  const isActive = fsmState === 'active'
  const isError = fsmState === 'error'

  return (
    <div className="flex items-center gap-2.5 px-3 py-2 rounded-lg hover:bg-surface-800/60 transition-colors">
      <div className={`w-7 h-7 ${meta.bg} rounded-md flex items-center justify-center text-[10px] font-bold text-white shrink-0`}>
        {ROLE_DISPLAY[role] ? t(`dashboard.roles.${role}.initials`, { defaultValue: meta.initials }) : meta.initials}
      </div>
      <div className="flex-1 min-w-0">
        <p className={`text-xs font-medium ${meta.color}`}>{ROLE_DISPLAY[role] ? t(`dashboard.roles.${role}.label`, { defaultValue: meta.label }) : meta.label}</p>
        {activity ? (
          <p className="text-[11px] text-gray-500 truncate">{activity}</p>
        ) : (
          <p className="text-[11px] text-gray-600 italic">{t('dashboard.idle', { defaultValue: 'idle' })}</p>
        )}
      </div>
      {isActive && <Loader2 size={12} className="text-emerald-400 animate-spin shrink-0" />}
      {isError  && <AlertTriangle size={12} className="text-red-400 shrink-0" />}
      {!isActive && !isError && (
        <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${
          fsmState === 'waiting' ? 'bg-brand-400' : 'bg-surface-600'
        }`} />
      )}
    </div>
  )
}

// ── Sprint Card ───────────────────────────────────────────────────────────────

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
    <div className="rounded-xl border border-surface-700 bg-surface-900 overflow-hidden">
      <div className="px-4 pt-4 pb-3 border-b border-surface-700/60">
        <div className="flex items-start justify-between gap-2 mb-2">
          <h3 className="text-sm font-semibold text-white truncate">{sprint.sprint_name}</h3>
          <StatusBadge status={sprint.status} />
        </div>
        <div className="flex items-center gap-2">
          <ScopeBadge scope={sprint.scope_size} />
          {sprint.goal && (
            <p className="text-[11px] text-gray-500 truncate">{sprint.goal}</p>
          )}
        </div>
      </div>

      <div className="px-4 py-3 space-y-3">
        <ProgressBar done={done} total={total} />

        <div className="grid grid-cols-3 gap-2">
          {[
            { label: t('dashboard.inProgress', { defaultValue: 'In Progress' }), count: inProg,  color: 'text-brand-400'   },
            { label: t('dashboard.blocked', { defaultValue: 'Blocked' }), count: blocked, color: 'text-red-400'     },
            { label: t('dashboard.done', { defaultValue: 'Done' }), count: done,    color: 'text-emerald-400' },
          ].map(({ label, count, color }) => (
            <div key={label} className="text-center p-2 rounded-lg bg-surface-800/60">
              <p className={`text-lg font-bold tabular-nums ${color}`}>{count}</p>
              <p className="text-[10px] text-gray-500 mt-0.5">{label}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
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
  const color = pct >= 90 ? 'text-red-400' : pct >= 70 ? 'text-yellow-400' : 'text-emerald-400'
  return (
    <div className="flex items-center gap-2 px-3 py-1.5 rounded-full border border-surface-700 bg-surface-900 text-[11px] font-medium text-gray-300">
      <span className={color}>⚡ {t('dashboard.tokenUsage', { used: totalUsed.toLocaleString(), limit: totalLimit.toLocaleString(), defaultValue: '{{used}} / {{limit}} tokens' })}</span>
      <span className="text-gray-500">({pct}%)</span>
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
    <div className="min-w-0">
      <div className="space-y-5">

        {/* ── Overview header ───────────────────────────────────────── */}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-lg font-bold text-white tracking-tight">
              {companyName ? `${companyName}` : t('dashboard.title', { defaultValue: 'La Fabrique' })}
            </h2>
            <p className="text-xs text-gray-500 mt-0.5">
              {t('dashboard.commandCenter', { defaultValue: 'AI Sprint command center' })}
              {sprint && (
                <> · <span className="text-gray-400">{sprint.sprint_name}</span></>
              )}
            </p>
          </div>
          <QuotaWidget companyId={companyId} />
        </div>

        {/* ── Quota alert ────────────────────────────────────────────── */}
        {Array.isArray(quotaData) && quotaData.some((q: any) => q.limit > 0 && (q.used / q.limit) >= 0.8) && (
          <div className="mb-3 flex items-center gap-2 rounded-lg border border-yellow-500/30 bg-yellow-500/10 px-4 py-2 text-sm text-yellow-200">
            <AlertTriangle size={16} />
            <span>{t('dashboard.quotaAlert', { defaultValue: 'Token quota is above 80% for one or more business units. Consider resetting or increasing limits.' })}</span>
            <Link to="/company/quotas" className="ml-auto text-yellow-300 underline text-xs">{t('dashboard.manageQuotas', { defaultValue: 'Manage quotas' })}</Link>
          </div>
        )}

        {/* ── KPI strip ─────────────────────────────────────────────── */}
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
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
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">

          {/* Sprint card — takes 2 cols */}
          <div className="lg:col-span-2 space-y-4">
            {boardLoading ? (
              <LoadingSkeleton message={t('dashboard.loadingSprint', { defaultValue: 'Loading sprint data...' })} />
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
              <div className="rounded-xl border border-dashed border-surface-700 bg-surface-900/40 p-8 text-center">
                <Cpu size={28} className="text-gray-600 mx-auto mb-3" />
                <p className="text-sm font-medium text-gray-400">{t('dashboard.noActiveSprint', { defaultValue: 'No active sprint' })}</p>
                <p className="text-xs text-gray-600 mt-1">{t('dashboard.launchHint', { defaultValue: 'Launch a sprint below to start working with your agents' })}</p>
              </div>
            )}

            {/* Quick launch */}
            <QuickLaunch onSuccess={id => setActiveSprint(id)} />
          </div>

          {/* Right column: agent roster + system status */}
          <div className="space-y-4">
            {/* Agent Roster */}
            <div className="rounded-xl border border-surface-700 bg-surface-900">
              <div className="flex items-center justify-between px-3 py-2.5 border-b border-surface-700/60">
                <div className="flex items-center gap-1.5">
                  <Bot size={13} className="text-brand-400" />
                  <span className="text-xs font-semibold text-gray-300">{t('dashboard.agentRoster', { defaultValue: 'Agent Roster' })}</span>
                </div>
                {hasAgents && (
                  <span className="text-[10px] text-gray-500">{t('dashboard.agentCount', { defaultValue: '{{count}} agents', count: visibleAgents.length })}</span>
                )}
              </div>

              <div className="py-1">
                {hasAgents ? (
                  visibleAgents.map(([role, state]) => (
                    <AgentRow
                      key={role}
                      role={role}
                      fsmState={state.fsmState}
                      activity={state.currentActivity}
                    />
                  ))
                ) : (
                  <div className="px-3 py-4 text-center">
                    <p className="text-[11px] text-gray-600">{t('dashboard.noAgents', { defaultValue: 'No agents seen yet.' })}</p>
                    <p className="text-[11px] text-gray-600 mt-0.5">{t('dashboard.activateAgents', { defaultValue: 'Launch a sprint to activate agents.' })}</p>
                  </div>
                )}
              </div>

              <div className="px-3 pb-2.5 pt-1 border-t border-surface-700/60">
                <Link
                  to="/agents"
                  className="flex items-center gap-1 text-[11px] text-brand-400 hover:text-brand-300 transition-colors"
                >
                  <Settings2 size={10} />
                  {t('dashboard.manageAgents', { defaultValue: 'Manage agents' })} <ChevronRight size={10} />
                </Link>
              </div>
            </div>

            {/* System Status */}
            <div className="rounded-xl border border-surface-700 bg-surface-900 p-3">
              <div className="flex items-center gap-1.5 mb-3">
                <ShieldAlert size={13} className="text-gray-400" />
                <span className="text-xs font-semibold text-gray-300">{t('dashboard.system', { defaultValue: 'System' })}</span>
              </div>
              <div className="space-y-2">
                {[
                  { label: t('dashboard.agentStream', { defaultValue: 'Agent Stream' }), ok: isSSEConnected },
                ].map(({ label, ok }) => (
                  <div key={label} className="flex items-center justify-between text-[11px]">
                    <span className="text-gray-500">{label}</span>
                    <span className={`flex items-center gap-1 font-medium ${ok ? 'text-emerald-400' : 'text-gray-600'}`}>
                      <span className={`w-1.5 h-1.5 rounded-full ${ok ? 'bg-emerald-400' : 'bg-surface-600'}`} />
                      {ok ? t('dashboard.online', { defaultValue: 'Online' }) : t('dashboard.offline', { defaultValue: 'Offline' })}
                    </span>
                  </div>
                ))}
              </div>
            </div>

          </div>
        </div>
      </div>
    </div>
  )
}
