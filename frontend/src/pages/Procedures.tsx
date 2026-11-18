import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Archive, Check, CircleCheck, CircleHelp, Gauge, Plus, RotateCcw, ShieldCheck, Undo2, X, type LucideIcon } from 'lucide-react'
import { useAgents, useCreateProcedure, useCurrentRole, useDecideProcedure, useProcedures, useStandards } from '../hooks/useQueries'
import { ALL_ROLES, ApiError, errorDetail, type Procedure, type ProcedureMetrics } from '../services/api'
import type { Standard } from '../services/standards'
import { LoadErrorAlert, LoadingLine, Spinner } from '../components/knowledge/PageStates'
import { CountPill, PageHeader, Panel, StatusBadge, type StatusTone } from '../components/ui'
import { useAppStore } from '../store/useAppStore'

/** What the board's numbers decided, as an icon and a word: never the colour alone. */
const RESULT_LOOK: Record<string, { icon: LucideIcon; tone: StatusTone }> = {
  kept: { icon: CircleCheck, tone: 'success' },
  reverted: { icon: RotateCcw, tone: 'danger' },
  unmeasured: { icon: CircleHelp, tone: 'neutral' },
  superseded: { icon: Archive, tone: 'neutral' },
  restored: { icon: Undo2, tone: 'info' },
  retired: { icon: Archive, tone: 'neutral' },
}

function percent(value: number) {
  return `${Math.round(value * 100)}%`
}

/**
 * The company's procedures: the rules each role follows. The ScrumMaster
 * proposes them after a sprint with rework or blocked tasks; a Manager adopts
 * or rejects them (with autonomy level 3 they are adopted directly). After two
 * more sprints the board's numbers keep or revert each one.
 */
export default function Procedures() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const role = useCurrentRole()
  const canDecide = role === 'Owner' || role === 'Manager'
  const query = useProcedures(companyId)
  const all = query.data?.procedures ?? []
  const proposed = all.filter(p => p.status === 'proposed')
  const active = all.filter(p => p.status === 'active')
  const history = all.filter(p => p.status === 'rejected' || p.status === 'retired')

  return (
    <div className="page-container max-w-4xl space-y-4">
      <PageHeader
        title={t('procedures.title', { defaultValue: 'Procedures' })}
        subtitle={t('procedures.subtitle', { defaultValue: 'The rules each role follows. The ScrumMaster proposes them after a sprint with rework; the board\'s numbers keep or revert them.' })}
      />

      {query.isLoading ? (
        <LoadingLine>{t('procedures.loading', { defaultValue: 'Loading procedures…' })}</LoadingLine>
      ) : query.isError ? (
        <LoadErrorAlert onRetry={() => query.refetch()}>
          {t('procedures.loadError', { message: errorDetail(query.error), defaultValue: 'Could not load the procedures: {{message}}' })}
        </LoadErrorAlert>
      ) : (
        <>
          <Section title={t('procedures.proposed', { defaultValue: 'Waiting for a decision' })}
            empty={t('procedures.noProposed', { defaultValue: 'No proposals waiting.' })} items={proposed}
            render={p => <ProcedureRow key={p.id} procedure={p} actions={canDecide ? ['adopt', 'reject'] : []} />} />
          <Section title={t('procedures.active', { defaultValue: 'In force' })}
            empty={t('procedures.noActive', { defaultValue: 'No procedures in force yet.' })} items={active}
            render={p => <ProcedureRow key={p.id} procedure={p} actions={canDecide ? ['retire'] : []} />} />
          {canDecide && companyId !== null && <NewProcedure companyId={companyId} />}
          <Section title={t('procedures.history', { defaultValue: 'History' })}
            empty={t('procedures.noHistory', { defaultValue: 'Nothing retired or rejected yet.' })} items={history}
            render={p => <ProcedureRow key={p.id} procedure={p} actions={[]} />} />
          <Standards />
        </>
      )}
    </div>
  )
}

/** The factory's quality standards: fixed, by discipline; a procedure adds to them, never relaxes them. */
function Standards() {
  const { t } = useTranslation()
  const query = useStandards()
  const byArea = new Map<string, Standard[]>()
  for (const standard of query.data?.standards ?? []) {
    byArea.set(standard.area, [...(byArea.get(standard.area) ?? []), standard])
  }
  return (
    <Panel title={<span className="inline-flex items-center gap-2"><ShieldCheck size={14} className="text-brand-300" aria-hidden="true" />{t('standards.title')}</span>}>
      <p className="text-meta max-w-prose px-3 pt-2">{t('standards.subtitle')}</p>
      {query.isLoading && <div className="px-3"><LoadingLine>{t('standards.loading')}</LoadingLine></div>}
      {query.isError && <div className="p-3"><LoadErrorAlert>{t('standards.loadError', { message: errorDetail(query.error) })}</LoadErrorAlert></div>}
      {[...byArea.entries()].map(([area, items]) => (
        <div key={area} className="mt-2 border-t border-surface-700 first-of-type:border-t-0">
          <h3 className="px-3 pb-1 pt-2.5 text-[11px] font-semibold uppercase tracking-wider text-gray-400">{t(`standards.areas.${area}`, { defaultValue: area })}</h3>
          <ul className="data-list">
            {items.map(standard => (
              <li key={standard.id} className="data-row text-[13px] text-gray-300">
                <div className="flex flex-wrap items-center gap-2">
                  <code className="rounded-md bg-surface-800 px-1.5 py-0.5 font-mono text-[11px] text-brand-300">{standard.id}</code>
                  <span className="font-semibold text-gray-100">{standard.title}</span>
                  {standard.roles.map(role => (
                    <span key={role} className="chip">{role === '*' ? t('procedures.everyRole') : role}</span>
                  ))}
                  {standard.checked && (
                    <span className="status-badge border-emerald-700/50 bg-emerald-950/50 text-emerald-300" title={standard.checked}>
                      <ShieldCheck size={11} aria-hidden="true" className="flex-shrink-0" />{t('standards.checked')}
                    </span>
                  )}
                </div>
                <p className="mt-1 max-w-prose text-xs leading-relaxed text-gray-400">{standard.rule}</p>
                {standard.checked && <p className="mt-0.5 text-[11px] text-emerald-300">{t('standards.checkedBy', { how: standard.checked })}</p>}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </Panel>
  )
}

function Section({ title, empty, items, render }: {
  title: string
  empty: string
  items: Procedure[]
  render: (procedure: Procedure) => React.ReactNode
}) {
  const { t } = useTranslation()
  return (
    <Panel title={title} count={<CountPill count={items.length} label={t('procedures.countLabel', { count: items.length })} />}>
      {items.length === 0
        ? <p className="px-3 py-2.5 text-[13px] text-gray-400">{empty}</p>
        : <ul className="data-list">{items.map(render)}</ul>}
    </Panel>
  )
}

type Decision = 'adopt' | 'reject' | 'retire'

function ProcedureRow({ procedure, actions }: { procedure: Procedure; actions: Decision[] }) {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const decide = useDecideProcedure(companyId)
  const roleLabel = procedure.role === ALL_ROLES ? t('procedures.everyRole', { defaultValue: 'Every role' }) : procedure.role
  const result = procedure.outcome?.result
  const look = result ? RESULT_LOOK[result] ?? { icon: CircleHelp, tone: 'neutral' as const } : null

  return (
    <li className="data-row space-y-1.5 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="chip">{roleLabel}</span>
        <span className="text-[13px] font-semibold text-gray-100">{procedure.title}</span>
        <span className="text-meta tabular-nums">{t('procedures.version', { version: procedure.version, defaultValue: 'v{{version}}' })}</span>
        {look && result && (
          <span className="ml-auto">
            <StatusBadge icon={look.icon} tone={look.tone} label={t(`procedures.results.${result}`, { defaultValue: result })} />
          </span>
        )}
      </div>
      <p className="max-w-prose whitespace-pre-wrap break-words text-[13px] leading-relaxed text-gray-200">{procedure.body}</p>
      {procedure.rationale && (
        <p className="max-w-prose text-xs text-gray-400">
          {t('procedures.why', { why: procedure.rationale, defaultValue: 'Why: {{why}}' })}
        </p>
      )}
      <p className="text-meta">
        {t('procedures.proposedBy', { who: procedure.proposed_by, defaultValue: 'Proposed by {{who}}' })}
        {procedure.decided_by && ` · ${t('procedures.decidedBy', { who: procedure.decided_by, defaultValue: 'decided by {{who}}' })}`}
      </p>
      <Numbers baseline={procedure.baseline} outcome={procedure.outcome} status={procedure.status} />
      {actions.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 pt-1">
          {actions.map(decision => (
            <button key={decision} type="button" disabled={decide.isPending}
              onClick={() => decide.mutate({ id: procedure.id, decision })}
              className={decision === 'adopt' ? 'btn btn-primary btn-sm' : 'btn-tool'}>
              {decision === 'adopt' ? <Check size={14} aria-hidden="true" /> : <X size={13} aria-hidden="true" />}
              {t(`procedures.actions.${decision}`)}
            </button>
          ))}
          {decide.isError && (
            <span role="alert" className="text-xs text-red-300">
              {decide.error instanceof ApiError && decide.error.status === 409
                ? t('procedures.changed', { defaultValue: 'Someone already decided this one. Reload to see it.' })
                : t('procedures.decideError', { message: errorDetail(decide.error), defaultValue: 'Could not save the decision: {{message}}' })}
            </span>
          )}
        </div>
      )}
    </li>
  )
}

/** The board's numbers before and after: measured by the platform, not reported by an agent. */
function Numbers({ baseline, outcome, status }: {
  baseline: ProcedureMetrics | null
  outcome: ProcedureMetrics | null
  status: Procedure['status']
}) {
  const { t } = useTranslation()
  if (!baseline) return null
  const measured = outcome && ['kept', 'reverted'].includes(outcome.result ?? '') ? outcome : null
  return (
    <p className="flex items-start gap-1.5 text-xs tabular-nums text-gray-400">
      <Gauge size={12} aria-hidden="true" className="mt-0.5 flex-shrink-0" />
      <span>
        {t('procedures.before', {
          rework: baseline.rework_per_task,
          blocked: percent(baseline.blocked_rate),
          sprints: baseline.sprints,
          defaultValue: 'Before: {{rework}} rework requests per task, {{blocked}} blocked ({{sprints}} sprints).',
        })}
        {' '}
        {measured
          ? t('procedures.after', {
            rework: measured.rework_per_task,
            blocked: percent(measured.blocked_rate),
            sprints: measured.sprints,
            defaultValue: 'After: {{rework}} per task, {{blocked}} blocked ({{sprints}} sprints).',
          })
          : status === 'active' && !outcome
            ? t('procedures.measuring', { defaultValue: 'Measured after the next two completed sprints.' })
            : null}
      </span>
    </p>
  )
}

function NewProcedure({ companyId }: { companyId: number }) {
  const { t } = useTranslation()
  const { data } = useAgents(companyId)
  const create = useCreateProcedure()
  const roles = [ALL_ROLES, ...Array.from(new Set((data?.agents ?? []).map(agent => agent.role).filter(Boolean))).sort()]
  const [role, setRole] = useState(ALL_ROLES)
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')

  function submit() {
    if (!title.trim() || !body.trim()) return
    create.mutate(
      { company_id: companyId, role, title: title.trim(), body: body.trim() },
      { onSuccess: () => { setTitle(''); setBody('') } },
    )
  }

  return (
    <Panel title={t('procedures.newTitle', { defaultValue: 'Write a procedure' })}>
      <form onSubmit={e => { e.preventDefault(); submit() }} className="space-y-3 p-3">
        <p className="max-w-prose text-xs text-gray-400">
          {t('procedures.newHint', { defaultValue: 'It is in force at once, and measured like the ones the ScrumMaster proposes.' })}
        </p>
        <div className="grid gap-3 sm:grid-cols-[auto_1fr]">
          <label className="block">
            <span className="input-label">{t('procedures.role', { defaultValue: 'Role' })}</span>
            <select value={role} onChange={e => setRole(e.target.value)} className="select w-full">
              {roles.map(value => (
                <option key={value} value={value}>{value === ALL_ROLES ? t('procedures.everyRole', { defaultValue: 'Every role' }) : value}</option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="input-label">{t('procedures.titleLabel', { defaultValue: 'Title' })}</span>
            <input value={title} onChange={e => setTitle(e.target.value)} maxLength={120} required className="input w-full" />
          </label>
        </div>
        <label className="block">
          <span className="input-label">{t('procedures.body', { defaultValue: 'The rule' })}</span>
          <textarea value={body} onChange={e => setBody(e.target.value)} maxLength={2000} required rows={3} className="input w-full" />
        </label>
        <div className="flex flex-wrap items-center gap-3">
          <button type="submit" disabled={create.isPending || !title.trim() || !body.trim()} className="btn btn-primary btn-sm">
            {create.isPending ? <Spinner /> : <Plus size={14} aria-hidden="true" />}
            {t('procedures.create', { defaultValue: 'Put in force' })}
          </button>
          {create.isError && (
            <span role="alert" className="text-[13px] text-red-300">
              {create.error instanceof ApiError && create.error.status === 409
                ? t('procedures.duplicate', { defaultValue: 'That procedure is already in force or proposed.' })
                : t('procedures.createError', { message: errorDetail(create.error), defaultValue: 'Could not save it: {{message}}' })}
            </span>
          )}
        </div>
      </form>
    </Panel>
  )
}
