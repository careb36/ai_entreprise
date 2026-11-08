import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowRightLeft, Loader2 } from 'lucide-react'
import { errorDetail, type BacklogItem } from '../services/api'
import { useCarryOver, useCarryTargets, type CarryResult } from '../services/boardMoves'

const NEW_SPRINT = 'new'

/**
 * "Move to sprint…" on a blocked or To do card: the company's open sprints
 * (planned, active or paused, not archived, not this one) and a new sprint.
 * The task is carried there once; the board says where it went.
 */
export function MoveToSprint({
  item, sprintId, companyId, onMoved,
}: {
  item: BacklogItem
  sprintId: number
  companyId: number | null
  onMoved: (result: CarryResult) => void
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [choice, setChoice] = useState('')
  const targets = useCarryTargets(item.id, open)
  const carry = useCarryOver(sprintId, companyId)
  const errorText = carry.error ? errorDetail(carry.error) : null

  const submit = () => {
    if (!choice) return
    carry.mutate(
      { itemId: item.id, targetSprintId: choice === NEW_SPRINT ? null : Number(choice) },
      { onSuccess: result => { setOpen(false); setChoice(''); onMoved(result) } },
    )
  }

  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)}
        className="btn-ghost btn-sm mt-2 w-full flex items-center justify-center gap-1 text-xs">
        <ArrowRightLeft size={12} aria-hidden="true" />
        <span>{t('kanban.moveToSprint')}</span>
      </button>
    )
  }

  return (
    <div className="mt-2 space-y-1.5">
      <select value={choice} onChange={e => setChoice(e.target.value)}
        className="select w-full text-xs py-1" aria-label={t('kanban.moveTarget')}
        disabled={targets.isLoading || carry.isPending}>
        <option value="">{t('kanban.moveChoose')}</option>
        {(targets.data?.sprints ?? []).map(sprint => (
          <option key={sprint.id} value={String(sprint.id)}>
            {t('kanban.moveTargetOption', {
              name: sprint.sprint_name,
              status: t(`kanban.sprintStatus.${sprint.status}`, sprint.status),
            })}
          </option>
        ))}
        <option value={NEW_SPRINT}>{t('kanban.moveNewSprint')}</option>
      </select>
      <div className="flex gap-1.5">
        <button type="button" onClick={submit} disabled={!choice || carry.isPending}
          className="btn-primary btn-sm flex-1 text-xs flex items-center justify-center gap-1 disabled:opacity-50">
          {carry.isPending && <Loader2 size={12} className="animate-spin" aria-hidden="true" />}
          <span>{t('kanban.moveConfirm')}</span>
        </button>
        <button type="button" onClick={() => { setOpen(false); setChoice('') }} className="btn-ghost btn-sm text-xs">
          {t('kanban.moveCancel')}
        </button>
      </div>
      {errorText && <p role="alert" className="text-xs text-red-400">{errorText}</p>}
    </div>
  )
}
