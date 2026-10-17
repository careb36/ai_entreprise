/**
 * Dashboard — enterprise command center.
 *
 * Layout: KPI strip → alert banner → main grid (sprint card + agent roster) → quick launch.
 * Scope: read-only overview + sprint launch. Heavy state lives in stores/queries.
 */

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'
import {
  Activity, AlertTriangle, Bot, CheckCircle2, ChevronRight,
  CircleDot, Cpu, Layers, Loader2, Play, Plus, Radio,
  ShieldAlert, Sparkles, SplitSquareVertical, Target, Zap,
} from 'lucide-react'
import type { SprintPlanResponse } from '../services/api'
import { useAppStore } from '../store/useAppStore'
import { useAgentStore } from '../store/agentStore'
import { useSprintBoard, useAnalyticsSummary, useHealth, useStartSprint, useInitCompany, usePlanSprint, usePlanSelfImprovementSprint } from '../hooks/useQueries'
import { SprintHealthChart } from '../components/SprintHealthChart'
import { LoadingSkeleton } from '../components/LoadingSkeleton'

// ── Constants ─────────────────────────────────────────────────────────────────

const SCOPE_META: Record<string, { label: string; limit: number; color: string; bg: string }> = {
  S: { label: 'Small',  limit: 4,  color: 'text-emerald-400', bg: 'bg-emerald-500/10 border-emerald-500/20' },
  M: { label: 'Medium', limit: 8,  color: 'text-brand-400',   bg: 'bg-brand-500/10 border-brand-500/20'    },
  L: { label: 'Large',  limit: 12, color: 'text-amber-400',   bg: 'bg-amber-500/10 border-amber-500/20'    },
}

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
  const meta = SCOPE_META[scope ?? 'M']
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[11px] font-semibold ${meta.bg} ${meta.color}`}>
      <Layers size={10} />
      {meta.label}
    </span>
  )
}

function StatusBadge({ status }: { status?: string }) {
  const meta = STATUS_META[status ?? 'active'] ?? STATUS_META.active
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] font-medium">
      <span className={`w-1.5 h-1.5 rounded-full ${meta.dot}`} />
      <span className={meta.color}>{meta.label}</span>
    </span>
  )
}

function ProgressBar({ done, total }: { done: number; total: number }) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0
  return (
    <div>
      <div className="flex justify-between text-[11px] text-gray-500 mb-1">
        <span>{done} of {total} tasks done</span>
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
        <p className="text-sm font-semibold text-amber-300">{t('dashboard.needsSplit')}</p>
        <p className="text-xs text-amber-200/60 mt-0.5">
          <strong className="text-amber-200/80">{sprintName}</strong> {t('dashboard.needsSplitDesc')}
        </p>
      </div>
      <Link
        to={`/workspace/${sprintId}`}
        className="shrink-0 flex items-center gap-1 text-xs font-medium text-amber-400 hover:text-amber-300 transition-colors"
      >
        Review <ChevronRight size={12} />
      </Link>
    </div>
  )
}

// ── Agent Roster ──────────────────────────────────────────────────────────────

const ROLE_DISPLAY: Record<string, { label: string; initials: string; color: string; bg: string }> = {
  PO:          { label: 'Product Owner', initials: 'PO', color: 'text-purple-300', bg: 'bg-purple-600'  },
  ScrumMaster: { label: 'Scrum Master',  initials: 'SM', color: 'text-blue-300',   bg: 'bg-blue-600'    },
  Developer:   { label: 'Developer',     initials: 'DV', color: 'text-green-300',  bg: 'bg-green-600'   },
  QA:          { label: 'QA Engineer',   initials: 'QA', color: 'text-pink-300',   bg: 'bg-pink-600'    },
  DevOps:      { label: 'DevOps',        initials: 'DO', color: 'text-orange-300', bg: 'bg-orange-600'  },
  Architect:   { label: 'Architect',     initials: 'AR', color: 'text-indigo-300', bg: 'bg-indigo-600'  },
}

function AgentRow({ role, fsmState, activity }: { role: string; fsmState: string; activity: string }) {
  const meta = ROLE_DISPLAY[role] ?? { label: role, initials: role.slice(0, 2).toUpperCase(), color: 'text-gray-300', bg: 'bg-gray-600' }
  const isActive = fsmState === 'active'
  const isError = fsmState === 'error'

  return (
    <div className="flex items-center gap-2.5 px-3 py-2 rounded-lg hover:bg-surface-800/60 transition-colors">
      <div className={`w-7 h-7 ${meta.bg} rounded-md flex items-center justify-center text-[10px] font-bold text-white shrink-0`}>
        {meta.initials}
      </div>
      <div className="flex-1 min-w-0">
        <p className={`text-xs font-medium ${meta.color}`}>{meta.label}</p>
        {activity ? (
          <p className="text-[11px] text-gray-500 truncate">{activity}</p>
        ) : (
          <p className="text-[11px] text-gray-600 italic">idle</p>
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
  sprint, columns, total, sprintId,
}: {
  sprint: any; columns: Record<string, any[]>; total: number; sprintId: number
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
            { label: 'In Progress', count: inProg,  color: 'text-brand-400'   },
            { label: 'Blocked',     count: blocked, color: 'text-red-400'     },
            { label: 'Done',        count: done,    color: 'text-emerald-400' },
          ].map(({ label, count, color }) => (
            <div key={label} className="text-center p-2 rounded-lg bg-surface-800/60">
              <p className={`text-lg font-bold tabular-nums ${color}`}>{count}</p>
              <p className="text-[10px] text-gray-500 mt-0.5">{label}</p>
            </div>
          ))}
        </div>
      </div>

      <div className="px-4 pb-3">
        <Link
          to={`/workspace/${sprintId}`}
          className="flex items-center justify-center gap-1.5 w-full py-2 rounded-lg bg-brand-600/15 border border-brand-600/25 text-brand-400 text-xs font-medium hover:bg-brand-600/25 transition-colors"
        >
          <Radio size={12} />
          {t('dashboard.liveView')}
        </Link>
      </div>
    </div>
  )
}

// ── Quick Launch ──────────────────────────────────────────────────────────────

function QuickLaunch({ onSuccess }: { onSuccess: (id: number) => void }) {
  const [goal, setGoal] = useState('')
  const [scope, setScope] = useState<'S' | 'M' | 'L'>('M')
  const [plannedSprint, setPlannedSprint] = useState<SprintPlanResponse | null>(null)
  const [planSource, setPlanSource] = useState<'manual' | 'autonomous' | null>(null)
  const { companyId, companyName, setCompany } = useAppStore()
  const initMutation    = useInitCompany()
  const planMutation    = usePlanSprint()
  const autoPlanMutation = usePlanSelfImprovementSprint()
  const sprintMutation  = useStartSprint()
  const navigate        = useNavigate()

  const busy  = initMutation.isPending || planMutation.isPending || autoPlanMutation.isPending || sprintMutation.isPending
  const error = sprintMutation.error ?? autoPlanMutation.error ?? planMutation.error ?? initMutation.error

  async function ensureCompany() {
    let cid = companyId

    if (!cid) {
      const resolvedCompanyName = companyName?.trim() || 'Fabrique Demo'
      const company = await initMutation.mutateAsync({
        company_name: resolvedCompanyName,
        default_scope_size: scope,
      })
      cid = company.company_id
      setCompany(cid, resolvedCompanyName)
    }

    return cid
  }

  function resetPreview() {
    setPlannedSprint(null)
    setPlanSource(null)
  }

  async function generatePlan() {
    if (busy || !goal.trim()) return

    initMutation.reset()
    planMutation.reset()
    sprintMutation.reset()

    try {
      const cid = await ensureCompany()
      if (!cid) return

      const plan = await planMutation.mutateAsync({
        company_id: cid,
        prompt: goal.trim(),
        scope_size: scope,
      })

      setPlannedSprint(plan)
      setPlanSource('manual')
    } catch {
      return
    }
  }

  async function generateAutonomousPlan() {
    if (busy) return

    initMutation.reset()
    planMutation.reset()
    autoPlanMutation.reset()
    sprintMutation.reset()

    try {
      const cid = await ensureCompany()
      if (!cid) return

      const plan = await autoPlanMutation.mutateAsync({
        company_id: cid,
        scope_size: scope,
      })

      setPlannedSprint(plan)
      setPlanSource('autonomous')
    } catch {
      return
    }
  }

  async function launch() {
    if (busy || !plannedSprint) return

    initMutation.reset()
    planMutation.reset()
    autoPlanMutation.reset()
    sprintMutation.reset()

    try {
      const cid = await ensureCompany()
      if (!cid) return

      const sprint = await sprintMutation.mutateAsync({
        company_id: cid,
        sprint_name: plannedSprint.sprint_name,
        start_date: new Date().toISOString().split('T')[0],
        scope_size: scope,
        goal: plannedSprint.goal,
        backlog_items: plannedSprint.tasks,
      })

      onSuccess(sprint.sprint_id)
      setGoal('')
      resetPreview()
      navigate(`/workspace/${sprint.sprint_id}`)
    } catch {
      return
    }
  }

  return (
    <div className="rounded-xl border border-surface-700 bg-surface-900 p-4">
      <div className="flex items-center gap-2 mb-3">
        <Sparkles size={14} className="text-brand-400" />
        <h3 className="text-sm font-semibold text-gray-200">Launch Sprint</h3>
      </div>

      <textarea
        rows={2}
        value={goal}
        onChange={e => {
          setGoal(e.target.value)
          resetPreview()
        }}
        placeholder="Describe the sprint goal…"
        className="w-full bg-surface-800 border border-surface-700 rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-brand-500 resize-none mb-3 transition-colors"
      />

      <div className="mb-3 flex items-center justify-between gap-2 rounded-lg border border-surface-700 bg-surface-800/60 px-3 py-2">
        <div>
          <p className="text-xs font-medium text-gray-200">Autonomous self-improvement</p>
          <p className="text-[11px] text-gray-500">Let the factory inspect the repo, preserve what already works, and propose the next Scrumban improvement sprint.</p>
        </div>
        <button
          onClick={generateAutonomousPlan}
          disabled={busy}
          className="shrink-0 px-3 py-1.5 rounded-lg border border-brand-500/30 text-brand-300 text-xs font-medium hover:bg-brand-500/10 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {autoPlanMutation.isPending ? 'Scanning…' : 'Auto-Improve'}
        </button>
      </div>

      <div className="flex items-center gap-2">
        <div className="flex rounded-lg border border-surface-700 overflow-hidden text-xs">
          {(['S', 'M', 'L'] as const).map(s => (
            <button
              key={s}
              onClick={() => {
                setScope(s)
                resetPreview()
              }}
              className={`px-3 py-1.5 font-medium transition-colors ${
                scope === s
                  ? 'bg-brand-600/20 text-brand-300 border-r border-brand-600/30 last:border-r-0'
                  : 'text-gray-500 hover:text-gray-300 border-r border-surface-700 last:border-r-0'
              }`}
            >
              {s}
            </button>
          ))}
        </div>
        <span className="text-[11px] text-gray-600 flex-1">
          {SCOPE_META[scope].label} · up to {SCOPE_META[scope].limit} tasks
        </span>
        {plannedSprint && (
          <button
            onClick={planSource === 'autonomous' ? generateAutonomousPlan : generatePlan}
            disabled={busy || (planSource !== 'autonomous' && !goal.trim())}
            className="px-3 py-1.5 rounded-lg border border-surface-700 text-gray-300 text-xs font-medium hover:border-brand-500/40 hover:text-white disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            Regenerate
          </button>
        )}
        <button
          onClick={plannedSprint ? launch : generatePlan}
          disabled={busy || (!plannedSprint && !goal.trim())}
          className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg bg-brand-600 text-white text-xs font-semibold hover:bg-brand-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {busy ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
          {busy ? (planMutation.isPending ? 'Planning…' : 'Launching…') : plannedSprint ? 'Launch Sprint' : 'Generate Plan'}
        </button>
      </div>

      {plannedSprint && (
        <div className="mt-3 rounded-lg border border-brand-500/20 bg-brand-950/10 p-3 space-y-3 animate-fade-in">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-[11px] uppercase tracking-[0.16em] text-brand-300/80">
                {planSource === 'autonomous' ? 'Autonomous Plan Preview' : 'Plan Preview'}
              </p>
              <h4 className="text-sm font-semibold text-white mt-1">{plannedSprint.sprint_name}</h4>
            </div>
            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[11px] font-semibold ${SCOPE_META[plannedSprint.scope_size].bg} ${SCOPE_META[plannedSprint.scope_size].color}`}>
              <Layers size={10} />
              {SCOPE_META[plannedSprint.scope_size].label}
            </span>
          </div>

          <p className="text-xs text-gray-400">{plannedSprint.goal}</p>

          {planSource === 'autonomous' && (
            <p className="text-[11px] text-brand-200/80">
              This plan was generated from repository context with a preserve-and-iterate rule: improve the next bottleneck, keep healthy assets, and move in bounded Scrumban slices.
            </p>
          )}

          <div className="space-y-2">
            {plannedSprint.tasks.map((task, index) => (
              <div key={`${task.title}-${index}`} className="rounded-lg border border-surface-700 bg-surface-900/70 p-2.5">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-xs font-semibold text-gray-100">{index + 1}. {task.title}</p>
                    <p className="text-[11px] text-gray-500 mt-1">{task.description}</p>
                  </div>
                  <div className="text-right shrink-0">
                    <p className="text-[10px] uppercase tracking-wide text-brand-300">{task.assignee}</p>
                    <p className="text-[10px] text-gray-500 mt-1">{task.priority}</p>
                  </div>
                </div>
              </div>
            ))}
          </div>

          <p className="text-[11px] text-gray-500">
            Review the plan, regenerate if needed, then launch the sprint.
          </p>
        </div>
      )}

      {error && (
        <p className="mt-2 text-[11px] text-red-400 flex items-center gap-1">
          <AlertTriangle size={11} /> {error instanceof Error ? error.message : 'Launch failed'}
        </p>
      )}
    </div>
  )
}

// ── Main Dashboard ─────────────────────────────────────────────────────────────

export default function Dashboard() {
  const { t } = useTranslation()
  const { activeSprintId, setActiveSprint, companyName } = useAppStore()
  const { data: board, isLoading: boardLoading } = useSprintBoard(activeSprintId)
  const { data: analytics } = useAnalyticsSummary(activeSprintId)
  const { data: health } = useHealth()
  const connectToSprintStream = useAgentStore(s => s.connect)
  const disconnectFromSprintStream = useAgentStore(s => s.disconnect)
  const agentStates = useAgentStore(s => s.agentStates)
  const isSSEConnected = useAgentStore(s => s.isConnected)

  useEffect(() => {
    if (!activeSprintId) return
    connectToSprintStream(String(activeSprintId))
    return () => {
      disconnectFromSprintStream()
    }
  }, [activeSprintId, connectToSprintStream, disconnectFromSprintStream])

  const sprint  = board?.sprint
  const columns = board?.columns ?? {}
  const total   = board?.total ?? 0
  const done    = columns.done?.length ?? 0
  const blocked = columns.blocked?.length ?? 0
  const inProg  = columns.in_progress?.length ?? 0

  const healthScore    = analytics?.health_score ?? null
  const needsSplit     = sprint?.status === 'needs_split'
  const backendOnline  = health?.status === 'healthy'

  const activeAgentCount  = Object.values(agentStates).filter(a => a.fsmState === 'active').length
  const visibleAgents     = Object.entries(agentStates)
  const hasAgents         = visibleAgents.length > 0

  return (
    <div className="min-h-full bg-surface-950 p-4 lg:p-6">
      <div className="max-w-6xl mx-auto space-y-5">

        {/* ── Page header ───────────────────────────────────────────── */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-white tracking-tight">
              {companyName ? `${companyName}` : 'La Fabrique'}
            </h1>
            <p className="text-xs text-gray-500 mt-0.5">
              AI Sprint command center
              {sprint && (
                <> · <span className="text-gray-400">{sprint.sprint_name}</span></>
              )}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <div className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full border text-[11px] font-medium transition-colors ${
              backendOnline
                ? 'bg-emerald-500/5 border-emerald-500/20 text-emerald-400'
                : 'bg-red-500/5 border-red-500/20 text-red-400'
            }`}>
              <span className={`w-1.5 h-1.5 rounded-full ${backendOnline ? 'bg-emerald-400 animate-pulse' : 'bg-red-400'}`} />
              {backendOnline ? t('dashboard.systemOnline') : t('dashboard.systemOffline')}
            </div>
            <Link
              to="/companies/new"
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-surface-700 bg-surface-900 text-xs font-medium text-gray-300 hover:text-white hover:border-surface-600 transition-colors"
            >
              <Plus size={12} /> New Factory
            </Link>
          </div>
        </div>

        {/* ── KPI strip ─────────────────────────────────────────────── */}
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
          <KpiCard
            icon={Target}
            label="Sprint Tasks"
            value={total}
            sub={sprint ? sprint.sprint_name : 'No active sprint'}
          />
          <KpiCard
            icon={CheckCircle2}
            label="Completed"
            value={done}
            sub={total > 0 ? `${Math.round((done / total) * 100)}% done` : '—'}
            accent
          />
          <KpiCard
            icon={CircleDot}
            label="Blocked"
            value={blocked}
            sub={blocked > 0 ? 'Needs attention' : 'Clear'}
            warn={blocked > 0}
          />
          <KpiCard
            icon={Bot}
            label="Agents Working"
            value={activeAgentCount}
            sub={isSSEConnected ? 'Live stream connected' : 'Disconnected'}
            accent={activeAgentCount > 0}
          />
          <KpiCard
            icon={Zap}
            label="Health Score"
            value={healthScore !== null ? `${healthScore}` : '—'}
            sub={healthScore !== null ? (healthScore >= 80 ? 'Sprint healthy' : 'Review needed') : 'No data yet'}
            accent={healthScore !== null && healthScore >= 80}
            warn={healthScore !== null && healthScore < 60}
          />
        </div>

        {/* ── Needs Split alert ─────────────────────────────────────── */}
        {needsSplit && sprint && activeSprintId && (
          <NeedsSplitAlert sprintId={activeSprintId} sprintName={sprint.sprint_name} />
        )}

        {/* ── Main grid ─────────────────────────────────────────────── */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">

          {/* Sprint card — takes 2 cols */}
          <div className="lg:col-span-2 space-y-4">
            {boardLoading ? (
              <LoadingSkeleton message="Loading sprint data..." />
            ) : sprint && activeSprintId ? (
              <>
                <SprintCard sprint={sprint} columns={columns} total={total} sprintId={activeSprintId} />
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
                <p className="text-sm font-medium text-gray-400">No active sprint</p>
                <p className="text-xs text-gray-600 mt-1">Launch a sprint below to start working with your agents</p>
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
                  <span className="text-xs font-semibold text-gray-300">Agent Roster</span>
                </div>
                {hasAgents && (
                  <span className="text-[10px] text-gray-500">{visibleAgents.length} agents</span>
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
                    <p className="text-[11px] text-gray-600">No agents seen yet.</p>
                    <p className="text-[11px] text-gray-600 mt-0.5">Launch a sprint to activate agents.</p>
                  </div>
                )}
              </div>

              {activeSprintId && (
                <div className="px-3 pb-2.5 pt-1 border-t border-surface-700/60">
                  <Link
                    to={`/workspace/${activeSprintId}`}
                    className="flex items-center gap-1 text-[11px] text-brand-400 hover:text-brand-300 transition-colors"
                  >
                    <Activity size={10} />
                    View live stream <ChevronRight size={10} />
                  </Link>
                </div>
              )}
            </div>

            {/* System Status */}
            <div className="rounded-xl border border-surface-700 bg-surface-900 p-3">
              <div className="flex items-center gap-1.5 mb-3">
                <ShieldAlert size={13} className="text-gray-400" />
                <span className="text-xs font-semibold text-gray-300">System</span>
              </div>
              <div className="space-y-2">
                {[
                  { label: 'Backend API',   ok: backendOnline },
                  { label: 'Agent Stream',  ok: isSSEConnected },
                ].map(({ label, ok }) => (
                  <div key={label} className="flex items-center justify-between text-[11px]">
                    <span className="text-gray-500">{label}</span>
                    <span className={`flex items-center gap-1 font-medium ${ok ? 'text-emerald-400' : 'text-gray-600'}`}>
                      <span className={`w-1.5 h-1.5 rounded-full ${ok ? 'bg-emerald-400' : 'bg-surface-600'}`} />
                      {ok ? 'Online' : 'Offline'}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* Quick nav */}
            <div className="grid grid-cols-2 gap-2">
              {[
                { to: '/governance', icon: ShieldAlert, label: 'Governance'  },
                { to: '/analytics',  icon: Activity,   label: 'Analytics'   },
                { to: '/memory',     icon: Cpu,        label: 'Memory'      },
                { to: '/generate',   icon: Sparkles,   label: 'Generate'    },
              ].map(({ to, icon: Icon, label }) => (
                <Link
                  key={to}
                  to={to}
                  className="flex items-center gap-1.5 p-2.5 rounded-lg border border-surface-700 bg-surface-900 text-[11px] font-medium text-gray-400 hover:text-white hover:border-surface-600 hover:bg-surface-800 transition-colors"
                >
                  <Icon size={12} />
                  {label}
                </Link>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
