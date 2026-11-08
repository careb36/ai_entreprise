/**
 * A sprint's edit, delete and unarchive actions on the Sprints page.
 *
 * A Manager (or Owner) edits the name, goal and duration; only an Owner
 * deletes, and a sprint with recorded work is archived instead, its evidence
 * kept; an Owner unarchives it. A Viewer sees none of them.
 */
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { ArchiveRestore, Pencil, Trash2 } from 'lucide-react'
import { errorDetail } from '../services/api'
import {
  REMOVABLE_STATUSES,
  useRemoveSprint,
  useUnarchiveSprint,
  useUpdateSprint,
  type ManagedSprint,
} from '../services/sprintAdmin'
import { useToast } from '../store/toastStore'

const MAX_DURATION_DAYS = 90

function EditForm({ sprint, companyId, onDone }: { sprint: ManagedSprint; companyId: number; onDone: () => void }) {
  const { t } = useTranslation()
  const toast = useToast()
  const update = useUpdateSprint(companyId)
  const [name, setName] = useState(sprint.sprint_name)
  const [goal, setGoal] = useState(sprint.goal ?? '')
  const [duration, setDuration] = useState(sprint.duration_days ? String(sprint.duration_days) : '')
  const [error, setError] = useState<string | null>(null)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const days = duration.trim() === '' ? null : Number(duration)
    if (!name.trim()) {
      setError(t('sprintAdmin.nameRequired', { defaultValue: 'The sprint needs a name.' }))
      return
    }
    if (days !== null && (!Number.isInteger(days) || days < 1 || days > MAX_DURATION_DAYS)) {
      setError(t('sprintAdmin.durationRange', { defaultValue: 'Duration is a whole number of days from 1 to {{max}}.', max: MAX_DURATION_DAYS }))
      return
    }
    setError(null)
    update.mutate(
      { sprintId: sprint.id, edit: { sprint_name: name.trim(), goal: goal.trim() || null, duration_days: days } },
      {
        onSuccess: () => {
          toast.success(t('sprintAdmin.saved', { defaultValue: 'Sprint updated' }))
          onDone()
        },
        onError: err => setError(errorDetail(err)),
      },
    )
  }

  return (
    <form noValidate onSubmit={submit} aria-label={t('sprintAdmin.editTitle', { defaultValue: 'Edit sprint' })}
      className="mt-3 grid gap-2 rounded-card border border-surface-700 p-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)_7rem_auto] sm:items-end">
      <label className="text-xs text-gray-400">
        {t('sprintAdmin.name', { defaultValue: 'Name' })}
        <input className="input mt-1 w-full" value={name} maxLength={255} onChange={e => setName(e.target.value)} />
      </label>
      <label className="text-xs text-gray-400">
        {t('sprintAdmin.goal', { defaultValue: 'Goal' })}
        <input className="input mt-1 w-full" value={goal} maxLength={2000} onChange={e => setGoal(e.target.value)} />
      </label>
      <label className="text-xs text-gray-400">
        {t('sprintAdmin.duration', { defaultValue: 'Days' })}
        <input className="input mt-1 w-full" type="number" min={1} max={MAX_DURATION_DAYS} value={duration}
          onChange={e => setDuration(e.target.value)} />
      </label>
      <div className="flex gap-1">
        <button type="submit" className="btn btn-primary btn-sm" disabled={update.isPending}>
          {t('common.save', { defaultValue: 'Save' })}
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onDone}>
          {t('common.cancel', { defaultValue: 'Cancel' })}
        </button>
      </div>
      {error && <p role="alert" className="text-xs text-red-400 sm:col-span-4">{error}</p>}
    </form>
  )
}

export function SprintAdminActions({ sprint, companyId, role }: {
  sprint: ManagedSprint
  companyId: number
  role: string | null
}) {
  const { t } = useTranslation()
  const toast = useToast()
  const [editing, setEditing] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const remove = useRemoveSprint(companyId)
  const unarchive = useUnarchiveSprint(companyId)
  const isOwner = role === 'Owner'
  const canEdit = isOwner || role === 'Manager'
  const archived = Boolean(sprint.archived_at)
  if (!canEdit) return null

  const removable = REMOVABLE_STATUSES.includes(sprint.status)
  const onDelete = () => {
    if (!window.confirm(t('sprintAdmin.deleteConfirm', {
      defaultValue: 'Delete "{{name}}"? A sprint with recorded work is archived, not deleted: its evidence stays and an Owner can unarchive it. A sprint nothing was recorded on is deleted with its tasks.',
      name: sprint.sprint_name,
    }))) return
    setFailure(null)
    remove.mutate(sprint.id, {
      onSuccess: result => toast.success('deleted' in result
        ? t('sprintAdmin.deleted', { defaultValue: 'Sprint deleted' })
        : t('sprintAdmin.archived', { defaultValue: 'Sprint archived: it had recorded work, which stays' })),
      onError: err => setFailure(errorDetail(err)),
    })
  }
  const onUnarchive = () => {
    setFailure(null)
    unarchive.mutate(sprint.id, {
      onSuccess: () => toast.success(t('sprintAdmin.unarchived', { defaultValue: 'Sprint unarchived' })),
      onError: err => setFailure(errorDetail(err)),
    })
  }

  return (
    <div className="sm:col-span-4">
      <div className="flex flex-wrap items-center gap-1">
        {!archived && (
          <button type="button" className="btn btn-ghost btn-sm flex items-center gap-1" onClick={() => setEditing(v => !v)}
            aria-expanded={editing}>
            <Pencil size={12} />{t('sprintAdmin.edit', { defaultValue: 'Edit' })}
          </button>
        )}
        {isOwner && !archived && (
          <button type="button" className="btn btn-ghost btn-sm flex items-center gap-1 text-red-400"
            disabled={!removable || remove.isPending} onClick={onDelete}
            title={removable ? undefined : t('sprintAdmin.stopFirst', { defaultValue: 'Stop the sprint first' })}>
            <Trash2 size={12} />{t('sprintAdmin.delete', { defaultValue: 'Delete' })}
          </button>
        )}
        {isOwner && archived && (
          <button type="button" className="btn btn-ghost btn-sm flex items-center gap-1" disabled={unarchive.isPending}
            onClick={onUnarchive}>
            <ArchiveRestore size={12} />{t('sprintAdmin.unarchive', { defaultValue: 'Unarchive' })}
          </button>
        )}
      </div>
      {failure && <p role="alert" className="mt-1 text-xs text-red-400">{failure}</p>}
      {editing && !archived && <EditForm sprint={sprint} companyId={companyId} onDone={() => setEditing(false)} />}
    </div>
  )
}
