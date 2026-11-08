/**
 * Quick Launch — the sprint planner: a goal (or an approved specification),
 * a scope, the plan the PO proposes, blocked tasks to bring back, and the
 * launch. Shared by the sprint overview and the Sprints page.
 */

import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle, Layers, Loader2, Play, Sparkles } from 'lucide-react'
import type { SprintPlanResponse } from '../services/api'
import { useAppStore } from '../store/useAppStore'
import { useInitCompany, usePlanSprint, usePlanSelfImprovementSprint, useStartSprint, useInbox, useSprints } from '../hooks/useQueries'
import { secondAttempts } from '../lib/learningTrend'
import { useSpecifications } from '../services/specifications'

// ── Constants ─────────────────────────────────────────────────────────────────

export const SCOPE_META: Record<string, { label: string; limit: number; color: string; bg: string }> = {
  S: { label: 'Small',  limit: 4,  color: 'text-emerald-400', bg: 'bg-emerald-500/10 border-emerald-500/20' },
  M: { label: 'Medium', limit: 8,  color: 'text-brand-400',   bg: 'bg-brand-500/10 border-brand-500/20'    },
  L: { label: 'Large',  limit: 12, color: 'text-amber-400',   bg: 'bg-amber-500/10 border-amber-500/20'    },
}

const PRIORITY_LABELS = {
  low: 'Low',
  medium: 'Medium',
  high: 'High',
  critical: 'Critical',
}

export const ROLE_DISPLAY: Record<string, { label: string; initials: string; color: string; bg: string }> = {
  PO:          { label: 'Product Owner', initials: 'PO', color: 'text-purple-300', bg: 'bg-purple-600'  },
  ScrumMaster: { label: 'Scrum Master',  initials: 'SM', color: 'text-blue-300',   bg: 'bg-blue-600'    },
  Developer:   { label: 'Developer',     initials: 'DV', color: 'text-green-300',  bg: 'bg-green-600'   },
  QA:          { label: 'QA Engineer',   initials: 'QA', color: 'text-pink-300',   bg: 'bg-pink-600'    },
  DevOps:      { label: 'DevOps',        initials: 'DO', color: 'text-orange-300', bg: 'bg-orange-600'  },
  Architect:   { label: 'Architect',     initials: 'AR', color: 'text-indigo-300', bg: 'bg-indigo-600'  },
}

// ── Quick Launch ──────────────────────────────────────────────────────────────

export function QuickLaunch({ onSuccess }: { onSuccess: (id: number) => void }) {
  const { t } = useTranslation()
  const [goal, setGoal] = useState('')
  const [scope, setScope] = useState<'S' | 'M' | 'L'>('M')
  const [plannedSprint, setPlannedSprint] = useState<SprintPlanResponse | null>(null)
  const [planSource, setPlanSource] = useState<'manual' | 'autonomous' | null>(null)
  // Blocked tasks of earlier sprints the person wants to try again here.
  const [carryOver, setCarryOver] = useState<number[]>([])
  // An approved specification to plan from: then the goal is optional and
  // each planned task says which acceptance criteria it covers.
  const [specificationId, setSpecificationId] = useState<number | null>(null)
  const { companyId, companyName, setCompany } = useAppStore()
  const specifications = useSpecifications(companyId)
  const approvedSpecifications = (specifications.data?.specifications ?? []).filter(s => s.status === 'approved')
  const hasGoal = goal.trim() !== '' || specificationId !== null
  const inbox = useInbox(companyId, false)
  const blockedTasks = (inbox.data?.blocked_tasks ?? []).filter(task => !task.frozen)
  // How earlier second attempts went, counted from the closed sprints' boards:
  // the evidence beside the ScrumMaster's advice when the person decides.
  const sprints = useSprints(blockedTasks.length > 0 ? companyId : null)
  const record = secondAttempts(sprints.data ?? [])
  // When a plan preview appears, start with the tasks the ScrumMaster's triage
  // recommends retrying, within the room the scope leaves; the person unticks
  // or adds from there. The triage is advice: nothing is sent without the tick.
  // Pre-ticked once per plan, so a later scope change or inbox refetch never
  // overwrites what the person chose.
  const pretickedPlan = useRef<SprintPlanResponse | null>(null)
  useEffect(() => {
    if (!plannedSprint) {
      pretickedPlan.current = null
      setCarryOver([])
      return
    }
    if (pretickedPlan.current === plannedSprint || !inbox.data) return
    pretickedPlan.current = plannedSprint
    const room = Math.max(0, SCOPE_META[scope].limit - plannedSprint.tasks.length)
    setCarryOver(
      inbox.data.blocked_tasks
        .filter(task => !task.frozen && task.triage?.decision === 'retry')
        .slice(0, room)
        .map(task => task.id),
    )
  }, [plannedSprint, inbox.data, scope])
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
      const resolvedCompanyName = companyName?.trim() || t('dashboard.demoCompany', { defaultValue: 'Fabrique Demo' })
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
    setCarryOver([])
  }

  async function generatePlan() {
    if (busy || !hasGoal) return

    initMutation.reset()
    planMutation.reset()
    sprintMutation.reset()

    try {
      const cid = await ensureCompany()
      if (!cid) return

      const plan = await planMutation.mutateAsync({
        company_id: cid,
        ...(goal.trim() ? { prompt: goal.trim() } : {}),
        scope_size: scope,
        ...(specificationId !== null ? { specification_id: specificationId } : {}),
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
        ...(carryOver.length > 0 ? { carry_over_ids: carryOver } : {}),
        // The plan shown, not the selector: a selector changed after planning
        // (or an autonomous plan) must not link unrelated tasks to a specification.
        ...(plannedSprint.specification_id ? { specification_id: plannedSprint.specification_id } : {}),
      })

      onSuccess(sprint.sprint_id)
      setGoal('')
      resetPreview()
      navigate(`/sprints/${sprint.sprint_id}/board`)
    } catch {
      return
    }
  }

  return (
    <div className="rounded-xl border border-surface-700 bg-surface-900 p-4">
      <div className="flex items-center gap-2 mb-3">
        <Sparkles size={14} className="text-brand-400" />
        <h3 className="text-sm font-semibold text-gray-200">{t('dashboard.launchSprint', { defaultValue: 'Launch Sprint' })}</h3>
      </div>

      {approvedSpecifications.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-gray-400">
          <label htmlFor="plan-specification">{t('specifications.plan.label', { defaultValue: 'Plan from a specification' })}</label>
          <select
            id="plan-specification"
            value={specificationId ?? ''}
            onChange={e => {
              setSpecificationId(e.target.value ? Number(e.target.value) : null)
              resetPreview()
            }}
            className="select flex-1 min-w-0 text-xs"
          >
            <option value="">{t('specifications.plan.none', { defaultValue: 'None: plan from the goal' })}</option>
            {approvedSpecifications.map(s => (
              <option key={s.id} value={s.id}>
                {t('specifications.plan.option', { title: s.title, version: s.version, defaultValue: '{{title}} (v{{version}})' })}
              </option>
            ))}
          </select>
        </div>
      )}

      <textarea
        rows={2}
        value={goal}
        onChange={e => {
          setGoal(e.target.value)
          resetPreview()
        }}
        placeholder={specificationId !== null
          ? t('specifications.plan.goalPlaceholder', { defaultValue: 'The specification is the goal; add a note for the PO if you want.' })
          : t('dashboard.goalPlaceholder', { defaultValue: 'Describe the sprint goal…' })}
        className="w-full bg-surface-800 border border-surface-700 rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-brand-500 resize-none mb-3 transition-colors"
      />

      <div className="mb-3 flex items-center justify-between gap-2 rounded-lg border border-surface-700 bg-surface-800/60 px-3 py-2">
        <div>
          <p className="text-xs font-medium text-gray-200">{t('dashboard.autonomousTitle', { defaultValue: 'Autonomous self-improvement' })}</p>
          <p className="text-[11px] text-gray-500">{t('dashboard.autonomousDescription', { defaultValue: 'Let the factory inspect the repo, preserve what already works, and propose the next Scrumban improvement sprint.' })}</p>
        </div>
        <button
          onClick={generateAutonomousPlan}
          disabled={busy}
          className="shrink-0 px-3 py-1.5 rounded-lg border border-brand-500/30 text-brand-300 text-xs font-medium hover:bg-brand-500/10 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {autoPlanMutation.isPending ? t('dashboard.scanning', { defaultValue: 'Scanning…' }) : t('dashboard.autoImprove', { defaultValue: 'Auto-Improve' })}
        </button>
      </div>

      <div className="flex items-center gap-2">
        <div className="flex rounded-lg border border-surface-700 overflow-hidden text-xs">
          {(['S', 'M', 'L'] as const).map(s => (
            <button
              key={s}
              title={t(`dashboard.scope.${s}.label`, { defaultValue: SCOPE_META[s].label })}
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
              {t(`dashboard.scope.${s}.short`, { defaultValue: s })}
            </button>
          ))}
        </div>
        <span className="text-[11px] text-gray-600 flex-1">
          {t('dashboard.scopeSummary', {
            defaultValue: '{{scope}} · up to {{count}} tasks',
            scope: t(`dashboard.scope.${scope}.label`, { defaultValue: SCOPE_META[scope].label }),
            count: SCOPE_META[scope].limit,
          })}
        </span>
        {plannedSprint && (
          <button
            onClick={planSource === 'autonomous' ? generateAutonomousPlan : generatePlan}
            disabled={busy || (planSource !== 'autonomous' && !hasGoal)}
            className="px-3 py-1.5 rounded-lg border border-surface-700 text-gray-300 text-xs font-medium hover:border-brand-500/40 hover:text-white disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {t('dashboard.regenerate', { defaultValue: 'Regenerate' })}
          </button>
        )}
        <button
          onClick={plannedSprint ? launch : generatePlan}
          disabled={busy || (!plannedSprint && !hasGoal)}
          className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg bg-brand-600 text-white text-xs font-semibold hover:bg-brand-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {busy ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
          {busy
            ? (planMutation.isPending
                ? t('dashboard.planning', { defaultValue: 'Planning…' })
                : t('dashboard.launching', { defaultValue: 'Launching…' }))
            : plannedSprint
              ? t('dashboard.launchSprint', { defaultValue: 'Launch Sprint' })
              : t('dashboard.generatePlan', { defaultValue: 'Generate Plan' })}
        </button>
      </div>

      {plannedSprint && (
        <div className="mt-3 rounded-lg border border-brand-500/20 bg-brand-950/10 p-3 space-y-3 animate-fade-in">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-[11px] uppercase tracking-[0.16em] text-brand-300/80">
                {planSource === 'autonomous'
                  ? t('dashboard.autonomousPreview', { defaultValue: 'Autonomous Plan Preview' })
                  : t('dashboard.planPreview', { defaultValue: 'Plan Preview' })}
              </p>
              <h4 className="text-sm font-semibold text-white mt-1">{plannedSprint.sprint_name}</h4>
            </div>
            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[11px] font-semibold ${SCOPE_META[plannedSprint.scope_size].bg} ${SCOPE_META[plannedSprint.scope_size].color}`}>
              <Layers size={10} />
              {t(`dashboard.scope.${plannedSprint.scope_size}.label`, { defaultValue: SCOPE_META[plannedSprint.scope_size].label })}
            </span>
          </div>

          <p className="text-xs text-gray-400">{plannedSprint.goal}</p>

          {planSource === 'autonomous' && (
            <p className="text-[11px] text-brand-200/80">
              {t('dashboard.autonomousPlanDescription', { defaultValue: 'This plan was generated from repository context with a preserve-and-iterate rule: improve the next bottleneck, keep healthy assets, and move in bounded Scrumban slices.' })}
            </p>
          )}

          <div className="space-y-2">
            {plannedSprint.tasks.map((task, index) => (
              <div key={`${task.title}-${index}`} className="rounded-lg border border-surface-700 bg-surface-900/70 p-2.5">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-xs font-semibold text-gray-100">
                      {index + 1}. {task.title}
                      {task.kind === 'acceptance_tests' && (
                        <span className="ml-1.5 badge border bg-violet-900/40 text-violet-300 border-violet-700/40">{t('kanban.acceptanceTests')}</span>
                      )}
                    </p>
                    <p className="text-[11px] text-gray-500 mt-1">{task.description}</p>
                    {task.covers && task.covers.length > 0 && (
                      <p className="mt-1 flex flex-wrap items-center gap-1 text-[10px] text-gray-500">
                        <span>{t('specifications.plan.covers', { defaultValue: 'Covers' })}</span>
                        {task.covers.map(id => (
                          <code key={id} className="rounded bg-surface-800 px-1 py-0.5 font-mono text-[10px] text-gray-300">{id}</code>
                        ))}
                      </p>
                    )}
                    {task.depends_on && task.depends_on.length > 0 && (
                      // Positions in the plan: the tasks this one waits for.
                      <p className="mt-1 text-[10px] text-gray-500">
                        {t('specifications.plan.after', {
                          titles: task.depends_on.map(position => plannedSprint.tasks[position]?.title ?? `#${position + 1}`).join(', '),
                          defaultValue: 'After: {{titles}}',
                        })}
                      </p>
                    )}
                  </div>
                  <div className="text-right shrink-0">
                    <p className="text-[10px] uppercase tracking-wide text-brand-300">
                      {ROLE_DISPLAY[task.assignee]
                        ? t(`dashboard.roles.${task.assignee}.label`, { defaultValue: ROLE_DISPLAY[task.assignee].label })
                        : task.assignee}
                    </p>
                    <p className="text-[10px] text-gray-500 mt-1">
                      {t(`dashboard.priorities.${task.priority}`, { defaultValue: PRIORITY_LABELS[task.priority] ?? task.priority })}
                    </p>
                  </div>
                </div>
              </div>
            ))}
          </div>

          {plannedSprint.uncovered && plannedSprint.uncovered.length > 0 && (
            <p role="note" className="text-[11px] text-amber-300">
              {t('specifications.plan.uncovered', {
                count: plannedSprint.uncovered.length,
                ids: plannedSprint.uncovered.join(', '),
                defaultValue: '{{count}} criteria stay uncovered: {{ids}}. The sprint can still start.',
              })}
            </p>
          )}

          {blockedTasks.length > 0 && (
            <fieldset className="space-y-1.5 rounded-lg border border-surface-700 bg-surface-900/70 p-2.5">
              <legend className="px-1 text-xs font-semibold text-gray-100">
                {t('dashboard.carryOverTitle', { defaultValue: 'Bring back blocked tasks' })}
              </legend>
              <p className="text-[11px] text-gray-500">
                {t('dashboard.carryOverHint', { defaultValue: 'Tasks the agents gave up on. Each one you tick gets a fresh attempt in this sprint, with what blocked it; they count towards the scope.' })}
              </p>
              {record.total > 0 && (
                <p className="text-[11px] text-gray-400">
                  {t('dashboard.carryOverRecord', {
                    done: record.done,
                    total: record.total,
                    briefedDone: record.briefed_done,
                    briefed: record.briefed,
                    defaultValue: "So far {{done}} of {{total}} second attempts got done; with the ScrumMaster's brief, {{briefedDone}} of {{briefed}}.",
                  })}
                </p>
              )}
              {blockedTasks.map(task => (
                <label key={task.id} className="flex items-start gap-2 text-xs text-gray-300">
                  <input type="checkbox" className="mt-0.5" checked={carryOver.includes(task.id)}
                    disabled={!carryOver.includes(task.id) && plannedSprint.tasks.length + carryOver.length >= SCOPE_META[scope].limit}
                    onChange={e => setCarryOver(ids => e.target.checked ? [...ids, task.id] : ids.filter(id => id !== task.id))} />
                  <span className="min-w-0">
                    {t('dashboard.carryOverTask', { title: task.title, sprint: task.sprint_name, defaultValue: '{{title}} · {{sprint}}' })}
                    {task.triage && (
                      // The ScrumMaster's answer to the escalation: a claim for the person to weigh.
                      <span className="block text-[11px] text-sky-200/80">
                        {task.triage.decision === 'retry'
                          ? t('dashboard.carryOverTriageRetry', { why: task.triage.why, defaultValue: 'ScrumMaster: another attempt. {{why}}' })
                          : t('dashboard.carryOverTriagePerson', { why: task.triage.why, defaultValue: 'ScrumMaster: leave it for a person. {{why}}' })}
                      </span>
                    )}
                  </span>
                </label>
              ))}
              {plannedSprint.tasks.length + carryOver.length >= SCOPE_META[scope].limit && carryOver.length < blockedTasks.length && (
                <p className="text-[11px] text-amber-300">{t('dashboard.carryOverFull', { defaultValue: 'The scope is full: untick a task or pick a larger scope.' })}</p>
              )}
            </fieldset>
          )}

          <p className="text-[11px] text-gray-500">
            {t('dashboard.reviewPlan', { defaultValue: 'Review the plan, regenerate if needed, then launch the sprint.' })}
          </p>
        </div>
      )}

      {error && (
        <p className="mt-2 text-[11px] text-red-400 flex items-center gap-1">
          <AlertTriangle size={11} /> {error instanceof Error ? error.message : t('dashboard.launchFailed', { defaultValue: 'Launch failed' })}
        </p>
      )}
    </div>
  )
}
