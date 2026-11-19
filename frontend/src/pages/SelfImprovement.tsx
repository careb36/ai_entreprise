import { useId, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  CircleCheck, CircleDashed, CircleStop, GitMerge, Hourglass, Play, RotateCcw, Upload, Wallet, type LucideIcon,
} from 'lucide-react'
import { useCurrentRole } from '../hooks/useQueries'
import { errorDetail } from '../services/api'
import { FULL_SHA, useRsiAction, useRsiCycles, type RsiCycle, type RsiState } from '../services/rsiCycles'
import { useAppStore } from '../store/useAppStore'
import {
  CountPill, EmptyState, FormActions, LoadError, LoadingRows, PageHeader, Panel, SavedNote, Spinner, StatusBadge, statusLook,
  type StatusLook, TABLE, TD, TH, TR,
} from '../components/ui'

const CYCLE_LOOK: Record<string, StatusLook> = {
  running: { icon: Play, tone: 'info' },
  awaiting_merge: { icon: GitMerge, tone: 'warning' },
  awaiting_person: { icon: Hourglass, tone: 'warning' },
  merged: { icon: CircleCheck, tone: 'success' },
  reverted: { icon: RotateCcw, tone: 'neutral' },
  unmeasured: { icon: CircleDashed, tone: 'neutral' },
  budget_exceeded: { icon: Wallet, tone: 'danger' },
  stopped: { icon: CircleStop, tone: 'danger' },
}

const short = (sha: string | null) => (sha ? sha.slice(0, 12) : '—')

/**
 * Self-improvement (`/company/self-improvement`, docs/design/rsi-loop.md):
 * the loop's switches, limits and counts, and its cycles. Every member reads
 * them. A signed-in Owner resumes the loop after its stop, records the merge
 * a person made of a kept cycle and imports a cycle runner's record; nothing
 * here starts a cycle or decides one.
 */
export default function SelfImprovement() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const role = useCurrentRole()
  const isOwner = role === 'Owner'
  const query = useRsiCycles(companyId)

  return (
    <div className="page-container max-w-5xl space-y-4">
      <PageHeader title={t('rsi.title')} subtitle={t('rsi.subtitle')} />
      {query.isLoading ? (
        <LoadingRows rows={2} label={t('rsi.loading')} />
      ) : query.isError ? (
        <LoadError onRetry={() => query.refetch()}>{t('rsi.loadError', { message: errorDetail(query.error) })}</LoadError>
      ) : query.data ? (
        <>
          <StatePanel state={query.data.state} isOwner={isOwner} />
          <CyclesPanel cycles={query.data.cycles} isOwner={isOwner} />
          {isOwner && <ImportRecord />}
        </>
      ) : null}
    </div>
  )
}

function StatePanel({ state, isOwner }: { state: RsiState; isOwner: boolean }) {
  const { t } = useTranslation()
  const companyId = useAppStore(s => s.companyId)
  const action = useRsiAction(companyId)
  const facts = [
    { label: t('rsi.state.enabled'), value: state.rsi_enabled ? t('rsi.yes') : t('rsi.no') },
    { label: t('rsi.state.withoutKeep'), value: `${state.consecutive_without_keep}/${state.stop_after}` },
    { label: t('rsi.state.today'), value: `${state.cycles_today}/${state.cycles_per_day}` },
    { label: t('rsi.state.personMerges'), value: `${state.kept_merged_by_person}/${state.person_merges_required}` },
    { label: t('rsi.state.budget'), value: state.token_budget === null ? '—' : state.token_budget.toLocaleString() },
    { label: t('rsi.state.diffLimit'), value: t('rsi.state.diffValue', { files: state.max_diff_files, lines: state.max_diff_lines }) },
  ]
  return (
    <Panel title={t('rsi.state.title')}>
      <div className="space-y-3 p-3">
        <p className="text-[13px] text-gray-200">
          {state.stopped ? t('rsi.state.stopped') : state.may_start ? t('rsi.state.mayStart') : t('rsi.state.blocked', { why: state.why_not_start ?? '—' })}
        </p>
        <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-md border border-surface-700 bg-surface-700 sm:grid-cols-3">
          {facts.map(fact => (
            <div key={fact.label} className="bg-surface-900 px-3 py-2">
              <dt className="text-meta">{fact.label}</dt>
              <dd className="mt-0.5 text-[13px] font-semibold tabular-nums text-gray-100">{fact.value}</dd>
            </div>
          ))}
        </dl>
        {state.budget_reason && <p className="text-meta">{state.budget_reason}</p>}
        {isOwner && state.stopped && (
          <FormActions>
            <button type="button" className="btn-tool border border-surface-700" disabled={action.isPending} onClick={() => action.mutate({ kind: 'resume' })}>
              {action.isPending ? <Spinner size={13} /> : <Play size={13} aria-hidden="true" />}{t('rsi.resume')}
            </button>
            <span className="text-meta">{t('rsi.resumeHint')}</span>
            {action.isError && <span role="alert" className="text-xs text-red-300">{errorDetail(action.error)}</span>}
          </FormActions>
        )}
      </div>
    </Panel>
  )
}

function CyclesPanel({ cycles, isOwner }: { cycles: RsiCycle[]; isOwner: boolean }) {
  const { t } = useTranslation()
  return (
    <Panel title={t('rsi.cycles.title')} count={cycles.length > 0 ? <CountPill count={cycles.length} label={t('rsi.cycles.count', { count: cycles.length })} /> : undefined}>
      {cycles.length === 0 ? (
        <EmptyState icon={CircleDashed} title={t('rsi.cycles.empty')} />
      ) : (
        <div className="overflow-x-auto">
          <table className={TABLE}>
            <caption className="sr-only">{t('rsi.cycles.title')}</caption>
            <thead>
              <tr>
                <th scope="col" className={TH}>{t('rsi.cycles.cycle')}</th>
                <th scope="col" className={TH}>{t('rsi.cycles.status')}</th>
                <th scope="col" className={TH}>{t('rsi.cycles.shas')}</th>
                <th scope="col" className={TH}>{t('rsi.cycles.size')}</th>
                <th scope="col" className={TH}>{t('rsi.cycles.merge')}</th>
              </tr>
            </thead>
            <tbody>{cycles.map(cycle => <CycleRow key={cycle.id} cycle={cycle} isOwner={isOwner} />)}</tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}

function CycleRow({ cycle, isOwner }: { cycle: RsiCycle; isOwner: boolean }) {
  const { t } = useTranslation()
  const look: StatusLook = statusLook(CYCLE_LOOK, cycle.status)
  const icon: LucideIcon = look.icon
  return (
    <tr className={`${TR} align-top`}>
      <td className={TD}>
        <div className="font-mono text-gray-100">{cycle.cycle_key}</div>
        {cycle.finding_id !== null && (
          <Link to={`/company/findings/${cycle.finding_id}`} className="text-[11px] text-brand-300 hover:underline">{t('rsi.cycles.finding', { id: cycle.finding_id })}</Link>
        )}
      </td>
      <td className={TD}>
        <StatusBadge icon={icon} tone={look.tone} label={t(`rsi.status.${cycle.status}`, { defaultValue: cycle.status })} />
        {cycle.decision && <div className="text-meta mt-0.5">{t(`findings.decision.${cycle.decision}`, { defaultValue: cycle.decision })}</div>}
      </td>
      <td className={`${TD} font-mono text-[11px] text-gray-300`}>
        <span title={cycle.baseline_sha ?? undefined}>{t('rsi.cycles.b', { sha: short(cycle.baseline_sha) })}</span><br />
        <span title={cycle.candidate_sha ?? undefined}>{t('rsi.cycles.c', { sha: short(cycle.candidate_sha) })}</span>
      </td>
      <td className={`${TD} tabular-nums text-gray-300`}>
        {cycle.diff_files === null ? '—' : t('rsi.cycles.diff', { files: cycle.diff_files, lines: cycle.diff_lines ?? 0 })}
      </td>
      <td className={TD}>
        {cycle.merged_sha ? (
          <span className="text-gray-300" title={cycle.merged_sha}>{t('rsi.cycles.merged', { sha: short(cycle.merged_sha), by: cycle.merged_by ?? '—' })}</span>
        ) : isOwner && cycle.status === 'awaiting_merge' ? (
          <RecordMerge cycle={cycle} />
        ) : '—'}
      </td>
    </tr>
  )
}

/** The merge a person made of the kept cycle's request, pinned by its full sha. */
function RecordMerge({ cycle }: { cycle: RsiCycle }) {
  const { t } = useTranslation()
  const companyId = useAppStore(s => s.companyId)
  const action = useRsiAction(companyId)
  const fieldId = useId()
  const [sha, setSha] = useState('')
  const clean = sha.trim().toLowerCase()
  const valid = FULL_SHA.test(clean)
  return (
    <form className="space-y-1" aria-label={t('rsi.recordMergeFor', { key: cycle.cycle_key })}
      onSubmit={event => { event.preventDefault(); if (valid) action.mutate({ kind: 'merge', cycleId: cycle.id, sha: clean }) }}>
      <label htmlFor={fieldId} className="input-label">{t('rsi.mergedSha')}</label>
      <div className="flex gap-1">
        <input id={fieldId} value={sha} spellCheck={false} onChange={event => setSha(event.target.value)} aria-invalid={sha !== '' && !valid}
          className="input control-sm w-48 font-mono" />
        <button type="submit" className="btn-tool" disabled={!valid || action.isPending}>{t('rsi.recordMerge')}</button>
      </div>
      {action.isError && <p role="alert" className="text-xs text-red-300">{errorDetail(action.error)}</p>}
    </form>
  )
}

/** A cycle runner's record (`scripts/rsi/cycle.py`, schema 1), pasted as JSON; the backend validates it and its hashes. */
function ImportRecord() {
  const { t } = useTranslation()
  const companyId = useAppStore(s => s.companyId)
  const action = useRsiAction(companyId)
  const ids = { field: useId(), hint: useId() }
  const [text, setText] = useState('')
  const [done, setDone] = useState(false)
  let record: Record<string, unknown> | null = null
  try {
    const value: unknown = text.trim() ? JSON.parse(text) : null
    record = value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
  } catch {
    record = null
  }
  const invalid = text.trim() !== '' && record === null
  return (
    <Panel title={<span className="flex items-center gap-1.5"><Upload size={13} aria-hidden="true" className="text-gray-400" />{t('rsi.import.title')}</span>}>
      <form className="space-y-2 p-3" aria-label={t('rsi.import.title')}
        onSubmit={event => {
          event.preventDefault()
          if (!record) return
          setDone(false)
          action.mutate({ kind: 'import', record }, { onSuccess: () => { setText(''); setDone(true) } })
        }}>
        <label htmlFor={ids.field} className="input-label">{t('rsi.import.label')}</label>
        <textarea id={ids.field} rows={6} value={text} spellCheck={false} onChange={event => setText(event.target.value)}
          aria-invalid={invalid} aria-describedby={ids.hint} className="input w-full font-mono text-xs" />
        <p id={ids.hint} className={`text-meta ${invalid ? 'text-red-300' : ''}`}>{invalid ? t('rsi.import.invalid') : t('rsi.import.hint')}</p>
        <FormActions>
          <button type="submit" className="btn-tool border border-surface-700" disabled={!record || action.isPending}>
            {action.isPending && <Spinner size={13} />}{t('rsi.import.submit')}
          </button>
          {done && <SavedNote>{t('rsi.import.done')}</SavedNote>}
          {action.isError && <span role="alert" className="text-xs text-red-300">{errorDetail(action.error)}</span>}
        </FormActions>
      </form>
    </Panel>
  )
}
