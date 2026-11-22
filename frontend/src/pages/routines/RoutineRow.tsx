import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import { Link } from 'react-router-dom'
import { History, Pencil, Play, Power, Trash2 } from 'lucide-react'
import type { Routine, RoutineInput, RoutineRunResult } from '../../services/api'
import { localTime } from '../../lib/utcTime'
import { RoutineForm } from './RoutineForm'
import { RoutineRevisions } from './RoutineRevisions'

export interface RoutineHandlers {
  update: (routineId: number, data: Partial<RoutineInput>) => void
  remove: (routineId: number) => void
  run: (routineId: number, values?: Record<string, string>) => Promise<RoutineRunResult>
  restore: (routineId: number, number: number) => void
}

function scheduleText(routine: Routine, t: TFunction) {
  const { every, at, timezone, weekday } = routine.schedule
  return every === 'week'
    ? t('routines.scheduleWeek', { day: t(`routines.weekday.${weekday ?? 0}`), at, timezone })
    : t('routines.scheduleDay', { at, timezone })
}

/** One routine: when it runs, its last run, and a Manager's actions on it. */
export function RoutineRow({ routine, roles, readOnly, busy, handlers }: {
  routine: Routine
  roles: string[]
  readOnly: boolean
  busy: boolean
  handlers: RoutineHandlers
}) {
  const { t } = useTranslation()
  const [mode, setMode] = useState<'none' | 'edit' | 'run' | 'revisions'>('none')
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [values, setValues] = useState<Record<string, string>>({})
  const [outcome, setOutcome] = useState<RoutineRunResult | null>(null)
  const toggle = (next: typeof mode) => setMode(current => (current === next ? 'none' : next))

  async function runNow() {
    if (routine.variables.length > 0 && mode !== 'run') return setMode('run')
    const given = Object.fromEntries(Object.entries(values).filter(([, v]) => v !== ''))
    try {
      setOutcome(await handlers.run(routine.id, routine.variables.length ? given : undefined))
      setMode('none')
    } catch {
      // The page shows the backend's refusal.
    }
  }

  return (
    <li className="rounded-xl border border-surface-700 bg-surface-900/40">
      <div className="flex flex-wrap items-start justify-between gap-2 p-3 text-xs">
        <div className="min-w-0 space-y-0.5">
          <p className="text-[13px] font-medium text-gray-100">
            {routine.title}
            {!routine.enabled && <span className="ml-2 text-gray-400">{t('routines.disabled')}</span>}
          </p>
          <p className="text-gray-300">{t('routines.assignedTo', { role: routine.assignee })} · {scheduleText(routine, t)}</p>
          <p className="text-gray-400">
            {routine.next_run_at
              ? t('routines.nextRun', { at: localTime(routine.next_run_at) })
              : t('routines.noNextRun')}
            {' · '}{t('routines.revisionShort', { number: routine.revision })}
            {routine.last_sprint_id !== null && (
              <> · <Link className="text-brand-300 hover:underline" to={`/sprints/${routine.last_sprint_id}`}>
                {t('routines.lastSprint', { id: routine.last_sprint_id })}
              </Link></>
            )}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <button type="button" className="btn-tool border border-surface-700" onClick={() => toggle('revisions')}
            aria-expanded={mode === 'revisions'}>
            <History size={12} aria-hidden="true" /> {t('routines.revisions.title')}
          </button>
          {!readOnly && (
            <>
              <button type="button" className="btn-tool border border-surface-700" disabled={busy} onClick={runNow}>
                <Play size={12} aria-hidden="true" /> {t('routines.runNow')}
              </button>
              <button type="button" className="btn-tool border border-surface-700" disabled={busy}
                onClick={() => handlers.update(routine.id, { enabled: !routine.enabled })}>
                <Power size={12} aria-hidden="true" /> {routine.enabled ? t('routines.disable') : t('routines.enable')}
              </button>
              <button type="button" className="btn-tool border border-surface-700" onClick={() => toggle('edit')}
                aria-expanded={mode === 'edit'}>
                <Pencil size={12} aria-hidden="true" /> {t('routines.edit')}
              </button>
              <button type="button" className="btn-tool border border-surface-700" disabled={busy}
                onClick={() => (confirmDelete ? handlers.remove(routine.id) : setConfirmDelete(true))}>
                <Trash2 size={12} aria-hidden="true" /> {confirmDelete ? t('routines.confirmDelete') : t('routines.delete')}
              </button>
            </>
          )}
        </div>
      </div>
      {outcome && (
        <p role="status" className="px-3 pb-2 text-xs text-gray-300">
          {outcome.skipped
            ? t('routines.skipped', { reason: outcome.skipped })
            : t('routines.ran', { id: outcome.sprint_id, revision: outcome.revision })}
        </p>
      )}
      {mode === 'run' && (
        <div className="space-y-2 border-t border-surface-700 p-3 text-xs">
          {routine.variables.map(v => (
            <label key={v.name} className="block space-y-1">
              <span className="font-mono text-gray-300">{v.label || v.name}{v.required ? ' *' : ''}</span>
              {v.options.length ? (
                <select className="select w-full" value={values[v.name] ?? v.default ?? ''}
                  onChange={e => setValues(vs => ({ ...vs, [v.name]: e.target.value }))}>
                  {!v.required && <option value="">—</option>}
                  {v.options.map(o => <option key={o} value={o}>{o}</option>)}
                </select>
              ) : (
                <input className="input w-full" placeholder={v.default ?? ''} maxLength={500}
                  value={values[v.name] ?? ''} onChange={e => setValues(vs => ({ ...vs, [v.name]: e.target.value }))} />
              )}
            </label>
          ))}
          <div className="flex justify-end gap-2">
            <button type="button" className="btn-tool border border-surface-700" onClick={() => setMode('none')}>{t('routines.cancel')}</button>
            <button type="button" className="btn-primary btn-sm" disabled={busy} onClick={runNow}>{t('routines.runWithValues')}</button>
          </div>
        </div>
      )}
      {mode === 'edit' && (
        <div className="border-t border-surface-700">
          <RoutineForm
            initial={routine}
            roles={roles.includes(routine.assignee) ? roles : [routine.assignee, ...roles]}
            saving={busy}
            submitLabel={t('routines.save')}
            onCancel={() => setMode('none')}
            onSubmit={data => { handlers.update(routine.id, data); setMode('none') }}
          />
        </div>
      )}
      {mode === 'revisions' && (
        <div className="border-t border-surface-700 p-3">
          <RoutineRevisions routineId={routine.id} current={routine.revision} readOnly={readOnly}
            restoring={busy} onRestore={number => handlers.restore(routine.id, number)} />
        </div>
      )}
    </li>
  )
}
