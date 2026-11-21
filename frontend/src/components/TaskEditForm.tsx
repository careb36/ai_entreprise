import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, Pencil, Save, X } from 'lucide-react'
import { errorDetail, type BacklogItem } from '../services/api'
import { EDITABLE_STATUSES, REASON_MIN, useEditTask } from '../services/boardMoves'

interface Props {
  task: BacklogItem
  sprintId: number
}

/**
 * A Manager's edit of a task's title or description, with a reason, while
 * it is in To do or Blocked (`PUT /v1/backlog/{id}/content`). The change is
 * recorded as the task's `edited` event with the text before and after;
 * "send back to work" also restarts it with a new rework budget.
 */
export function TaskEditForm({ task, sprintId }: Props) {
  const { t } = useTranslation()
  const edit = useEditTask(sprintId, task.id)
  const [open, setOpen] = useState(false)
  const [title, setTitle] = useState(task.title)
  const [description, setDescription] = useState(task.description ?? '')
  const [reason, setReason] = useState('')
  const [sendBack, setSendBack] = useState(task.status === 'blocked')

  if (!EDITABLE_STATUSES.includes(task.status)) {
    return <p className="text-meta text-xs">{t('taskEdit.notEditable')}</p>
  }
  if (!open) {
    return (
      <button type="button" className="btn-tool border border-surface-700" onClick={() => {
        setTitle(task.title)
        setDescription(task.description ?? '')
        setReason('')
        edit.reset()
        setOpen(true)
      }}>
        <Pencil size={13} aria-hidden="true" />{t('taskEdit.open')}
      </button>
    )
  }

  const changed = title.trim() !== task.title || description.trim() !== (task.description ?? '')
  const ready = changed && title.trim().length > 0 && reason.trim().length >= REASON_MIN

  function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!ready) return
    edit.mutate(
      {
        title: title.trim() !== task.title ? title.trim() : undefined,
        description: description.trim() !== (task.description ?? '') ? description.trim() : undefined,
        reason: reason.trim(),
        send_back: sendBack,
      },
      { onSuccess: () => setOpen(false) },
    )
  }

  return (
    <form onSubmit={submit} aria-label={t('taskEdit.formLabel')} className="space-y-2.5">
      <p className="text-meta text-xs">{t('taskEdit.hint')}</p>
      <label className="block space-y-1">
        <span className="text-meta text-xs font-semibold">{t('taskEdit.title')}</span>
        <input className="input w-full" value={title} maxLength={200} onChange={e => setTitle(e.target.value)} />
      </label>
      <label className="block space-y-1">
        <span className="text-meta text-xs font-semibold">{t('taskEdit.description')}</span>
        <textarea className="input min-h-32 w-full" value={description} maxLength={10000}
          onChange={e => setDescription(e.target.value)} />
      </label>
      <label className="block space-y-1">
        <span className="text-meta text-xs font-semibold">{t('taskEdit.reason')}</span>
        <input className="input w-full" value={reason} maxLength={500} placeholder={t('taskEdit.reasonPlaceholder')}
          onChange={e => setReason(e.target.value)} />
      </label>
      <label className="flex items-center gap-2 text-xs text-gray-300">
        <input type="checkbox" checked={sendBack} onChange={e => setSendBack(e.target.checked)} />
        {t('taskEdit.sendBack')}
      </label>
      {edit.isError && <p role="alert" className="alert-error text-xs">{t('taskEdit.error', { message: errorDetail(edit.error) })}</p>}
      <div className="flex gap-2">
        <button type="submit" className="btn-primary" disabled={!ready || edit.isPending}>
          {edit.isPending ? <Loader2 size={13} className="animate-spin" aria-hidden="true" /> : <Save size={13} aria-hidden="true" />}
          {t('taskEdit.save')}
        </button>
        <button type="button" className="btn-tool border border-surface-700" onClick={() => setOpen(false)}>
          <X size={13} aria-hidden="true" />{t('common.cancel')}
        </button>
      </div>
    </form>
  )
}
