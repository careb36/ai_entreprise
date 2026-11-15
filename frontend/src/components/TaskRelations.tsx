import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Link2 } from 'lucide-react'
import {
  useAddTaskRelation, useConfirmTaskRelation, useRejectTaskRelation, useRemoveTaskRelation, useTaskRelations,
} from '../hooks/useTaskRelations'
import { useCurrentRole } from '../hooks/useQueries'
import { TASK_RELATION_NAMES, errorDetail, type TaskRelation, type TaskRelationName } from '../services/api'
import { SkeletonBlock } from './Skeleton'

/**
 * A task's typed relations to other tasks, across sprints (issue #533): each
 * named from this task's side, with its state. A proposed relation is a claim
 * until a Manager confirms it; only a confirmed "blocked by" within one sprint
 * holds the task back from dispatch.
 */
export function TaskRelations({ taskId }: { taskId: number }) {
  const { t } = useTranslation()
  const role = useCurrentRole()
  const canManage = role === 'Owner' || role === 'Manager'
  const query = useTaskRelations(taskId)
  const rows = query.data?.relations ?? []

  return (
    <section aria-labelledby="task-relations-title" className="card p-4 mb-4 space-y-3">
      <div>
        <h2 id="task-relations-title" className="text-xs text-gray-500 uppercase tracking-wider font-semibold flex items-center gap-1">
          <Link2 size={10} aria-hidden="true" /> {t('taskRelations.title')}
        </h2>
        <p className="text-xs text-gray-500 mt-1">{t('taskRelations.claimsNote')}</p>
      </div>
      {query.isLoading ? (
        <div role="status" aria-label={t('taskRelations.loading')}>
          <SkeletonBlock className="h-10" />
        </div>
      ) : query.isError ? (
        <div role="alert" className="alert-error flex flex-wrap items-center gap-2 text-sm">
          <span>{t('taskRelations.error')}</span>
          <button type="button" onClick={() => query.refetch()} className="underline">{t('common.retry')}</button>
        </div>
      ) : rows.length === 0 ? (
        <p className="text-sm text-gray-400">{t('taskRelations.empty')}</p>
      ) : (
        <ul className="space-y-2" aria-label={t('taskRelations.listLabel')}>
          {rows.map(row => <RelationItem key={row.id} relation={row} canManage={canManage} />)}
        </ul>
      )}
      {canManage && <AddRelationForm taskId={taskId} />}
    </section>
  )
}

function RelationItem({ relation, canManage }: { relation: TaskRelation; canManage: boolean }) {
  const { t } = useTranslation()
  const confirm = useConfirmTaskRelation()
  const reject = useRejectTaskRelation()
  const remove = useRemoveTaskRelation()
  const other = relation.other_task
  const proposer = relation.proposed_role === 'person' ? relation.proposed_by
    : relation.proposed_role === 'system' ? t('taskRelations.theSystem') : relation.proposed_role
  const failed = confirm.error ?? reject.error ?? remove.error
  const busy = confirm.isPending || reject.isPending || remove.isPending
  const target = other.sprint_id !== null ? `/sprints/${other.sprint_id}/tasks/${other.id}` : `/task/${other.id}`

  return (
    <li className="rounded border border-surface-700 p-2 text-xs">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-semibold text-gray-200">{t(`taskRelations.names.${relation.relation}`, { defaultValue: relation.relation })}</span>
        <Link to={target} className="text-brand-300 hover:underline">
          {other.title
            ? t('taskRelations.taskWithTitle', { id: other.id, title: other.title })
            : t('taskRelations.task', { id: other.id })}
        </Link>
        {other.sprint_name && <span className="text-gray-500">{t('taskRelations.inSprint', { sprint: other.sprint_name })}</span>}
        <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${relation.state === 'confirmed'
          ? 'border-emerald-700/60 text-emerald-300' : 'border-amber-700/60 text-amber-300'}`}>
          {t(`taskRelations.states.${relation.state}`)}
        </span>
        {relation.drives_dispatch && relation.relation === 'blocked_by' && <span className="text-amber-300">{t('taskRelations.drivesDispatch')}</span>}
      </div>
      <p className="mt-1 text-gray-500">
        {t('taskRelations.proposedBy', { by: proposer })}
        {relation.confirmed_by ? ` · ${t('taskRelations.confirmedBy', { by: relation.confirmed_by.startsWith('rule:')
          ? t('taskRelations.aRule', { rule: relation.confirmed_by.slice(5) }) : relation.confirmed_by })}` : ''}
      </p>
      {canManage && (
        <div className="mt-2 flex flex-wrap gap-2">
          {relation.state === 'proposed' && (
            <>
              <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={() => confirm.mutate(relation.id)}>
                {t('taskRelations.confirm')}
              </button>
              <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={() => reject.mutate(relation.id)}>
                {t('taskRelations.reject')}
              </button>
            </>
          )}
          {relation.state === 'confirmed' && (
            <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={() => remove.mutate(relation.id)}>
              {t('taskRelations.remove')}
            </button>
          )}
        </div>
      )}
      {failed && <div role="alert" className="alert-error mt-1 text-xs">{errorDetail(failed)}</div>}
    </li>
  )
}

function AddRelationForm({ taskId }: { taskId: number }) {
  const { t } = useTranslation()
  const add = useAddTaskRelation(taskId)
  const [relation, setRelation] = useState<TaskRelationName>('relates_to')
  const [other, setOther] = useState('')
  const [confirmNow, setConfirmNow] = useState(false)
  const otherId = /^\d+$/.test(other.trim()) ? Number(other.trim()) : null

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (otherId === null) return
    add.mutate(
      { related_task_id: otherId, relation, confirm: confirmNow },
      { onSuccess: () => { setOther(''); setConfirmNow(false) } },
    )
  }

  return (
    <form onSubmit={submit} className="flex flex-wrap items-end gap-2" aria-label={t('taskRelations.addTitle')}>
      <label className="text-xs text-gray-400">
        <span className="input-label">{t('taskRelations.relationLabel')}</span>
        <select value={relation} onChange={e => setRelation(e.target.value as TaskRelationName)} className="select py-1 text-xs">
          {TASK_RELATION_NAMES.map(name => (
            <option key={name} value={name}>{t(`taskRelations.names.${name}`)}</option>
          ))}
        </select>
      </label>
      <label className="text-xs text-gray-400">
        <span className="input-label">{t('taskRelations.otherLabel')}</span>
        <input value={other} onChange={e => setOther(e.target.value)} inputMode="numeric" className="input w-28 text-xs"
          placeholder={t('taskRelations.otherPlaceholder')} />
      </label>
      <label className="flex items-center gap-1 text-xs text-gray-400">
        <input type="checkbox" checked={confirmNow} onChange={e => setConfirmNow(e.target.checked)} />
        {t('taskRelations.confirmNow')}
      </label>
      <button type="submit" className="btn btn-primary btn-sm" disabled={add.isPending || otherId === null}>
        {t('taskRelations.add')}
      </button>
      {add.isError && <div role="alert" className="alert-error w-full text-xs">{errorDetail(add.error)}</div>}
    </form>
  )
}
