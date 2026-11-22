import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Trash2 } from 'lucide-react'
import type { RoutineInput, RoutineVariable } from '../../services/api'

export const WEEKDAYS = [0, 1, 2, 3, 4, 5, 6]

export function emptyRoutine(assignee: string): RoutineInput {
  return {
    title: '',
    description: '',
    assignee,
    schedule: { every: 'day', at: '09:00', timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC' },
    enabled: true,
    variables: [],
  }
}

type VariableDraft = { name: string; default: string; required: boolean; options: string }

const toDraft = (v: RoutineVariable): VariableDraft => ({
  name: v.name,
  default: v.default ?? '',
  required: v.required,
  options: v.options.join(', '),
})

const fromDraft = (v: VariableDraft): RoutineVariable => ({
  name: v.name.trim(),
  default: v.default.trim() || null,
  required: v.required,
  options: v.options.split(',').map(o => o.trim()).filter(Boolean),
})

/**
 * A routine's fields (issue #774): its task, the hired role that does it, when
 * it runs, and the variables its description uses as `{{name}}`. The backend
 * checks every field; its refusal is shown as it says it.
 */
export function RoutineForm({ initial, roles, saving, submitLabel, onSubmit, onCancel }: {
  initial: RoutineInput
  roles: string[]
  saving: boolean
  submitLabel: string
  onSubmit: (data: RoutineInput) => void
  onCancel: () => void
}) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState<RoutineInput>(initial)
  const [variables, setVariables] = useState<VariableDraft[]>(initial.variables.map(toDraft))
  const schedule = draft.schedule
  const setSchedule = (patch: Partial<RoutineInput['schedule']>) =>
    setDraft(d => ({ ...d, schedule: { ...d.schedule, ...patch } }))

  function submit(event: React.FormEvent) {
    event.preventDefault()
    const { weekday, ...rest } = schedule
    onSubmit({
      ...draft,
      schedule: schedule.every === 'week' ? { ...rest, weekday: weekday ?? 0 } : rest,
      variables: variables.filter(v => v.name.trim()).map(fromDraft),
    })
  }

  return (
    <form onSubmit={submit} className="space-y-3 p-3 text-xs">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="space-y-1">
          <span className="text-gray-300">{t('routines.field.title')}</span>
          <input className="input w-full" required maxLength={200} value={draft.title}
            onChange={e => setDraft(d => ({ ...d, title: e.target.value }))} />
        </label>
        <label className="space-y-1">
          <span className="text-gray-300">{t('routines.field.assignee')}</span>
          <select className="select w-full" value={draft.assignee}
            onChange={e => setDraft(d => ({ ...d, assignee: e.target.value }))}>
            {roles.map(role => <option key={role} value={role}>{role}</option>)}
          </select>
        </label>
      </div>
      <label className="block space-y-1">
        <span className="text-gray-300">{t('routines.field.description')}</span>
        <textarea className="input min-h-[80px] w-full" maxLength={10000} value={draft.description}
          onChange={e => setDraft(d => ({ ...d, description: e.target.value }))} />
        <span className="text-gray-400">{t('routines.field.descriptionHint', { placeholder: '{{name}}' })}</span>
      </label>
      <fieldset className="grid gap-3 sm:grid-cols-4">
        <legend className="mb-1 text-gray-300">{t('routines.field.schedule')}</legend>
        <label className="space-y-1">
          <span className="text-gray-400">{t('routines.field.every')}</span>
          <select className="select w-full" value={schedule.every}
            onChange={e => setSchedule({ every: e.target.value as 'day' | 'week' })}>
            <option value="day">{t('routines.every.day')}</option>
            <option value="week">{t('routines.every.week')}</option>
          </select>
        </label>
        {schedule.every === 'week' && (
          <label className="space-y-1">
            <span className="text-gray-400">{t('routines.field.weekday')}</span>
            <select className="select w-full" value={schedule.weekday ?? 0}
              onChange={e => setSchedule({ weekday: Number(e.target.value) })}>
              {WEEKDAYS.map(day => <option key={day} value={day}>{t(`routines.weekday.${day}`)}</option>)}
            </select>
          </label>
        )}
        <label className="space-y-1">
          <span className="text-gray-400">{t('routines.field.at')}</span>
          <input type="time" className="input w-full" required value={schedule.at}
            onChange={e => setSchedule({ at: e.target.value })} />
        </label>
        <label className="space-y-1">
          <span className="text-gray-400">{t('routines.field.timezone')}</span>
          <input className="input w-full" required value={schedule.timezone}
            onChange={e => setSchedule({ timezone: e.target.value })} />
        </label>
      </fieldset>
      <fieldset className="space-y-2">
        <legend className="text-gray-300">{t('routines.field.variables')}</legend>
        {variables.map((v, index) => (
          <div key={index} className="grid items-end gap-2 sm:grid-cols-[1fr_1fr_1.5fr_auto_auto]">
            <label className="space-y-1">
              <span className="text-gray-400">{t('routines.variable.name')}</span>
              <input className="input w-full font-mono" value={v.name} maxLength={40}
                onChange={e => setVariables(vs => vs.map((x, i) => i === index ? { ...x, name: e.target.value } : x))} />
            </label>
            <label className="space-y-1">
              <span className="text-gray-400">{t('routines.variable.default')}</span>
              <input className="input w-full" value={v.default} maxLength={500}
                onChange={e => setVariables(vs => vs.map((x, i) => i === index ? { ...x, default: e.target.value } : x))} />
            </label>
            <label className="space-y-1">
              <span className="text-gray-400">{t('routines.variable.options')}</span>
              <input className="input w-full" value={v.options}
                onChange={e => setVariables(vs => vs.map((x, i) => i === index ? { ...x, options: e.target.value } : x))} />
            </label>
            <label className="flex items-center gap-1 pb-2 text-gray-300">
              <input type="checkbox" checked={v.required}
                onChange={e => setVariables(vs => vs.map((x, i) => i === index ? { ...x, required: e.target.checked } : x))} />
              {t('routines.variable.required')}
            </label>
            <button type="button" className="btn-tool mb-1" aria-label={t('routines.variable.remove', { name: v.name || index + 1 })}
              onClick={() => setVariables(vs => vs.filter((_, i) => i !== index))}>
              <Trash2 size={12} aria-hidden="true" />
            </button>
          </div>
        ))}
        <button type="button" className="btn-tool border border-surface-700"
          onClick={() => setVariables(vs => [...vs, { name: '', default: '', required: false, options: '' }])}>
          <Plus size={12} aria-hidden="true" /> {t('routines.variable.add')}
        </button>
      </fieldset>
      <div className="flex justify-end gap-2 border-t border-surface-700 pt-3">
        <button type="button" className="btn-tool border border-surface-700" onClick={onCancel}>{t('routines.cancel')}</button>
        <button type="submit" className="btn-primary btn-sm" disabled={saving}>{submitLabel}</button>
      </div>
    </form>
  )
}
