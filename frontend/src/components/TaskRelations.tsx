import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { CircleCheck, CircleDashed, Link2, RefreshCw } from 'lucide-react'
import {
  useAddTaskRelation, useConfirmTaskRelation, useRejectTaskRelation, useRemoveTaskRelation, useTaskRelations,
} from '../hooks/useTaskRelations'
import { useCurrentRole } from '../hooks/useQueries'
import { TASK_RELATION_NAMES, errorDetail, type TaskRelation, type TaskRelationName } from '../services/api'
import { SkeletonBlock } from './Skeleton'
import { Panel, StatusBadge } from './ui'

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
    <Panel
      title={<span className="flex items-center gap-1.5"><Link2 size={13} aria-hidden="true" className="text-gray-400" />{t('taskRelations.title')}</span>}>
      <p className="text-meta border-b border-surface-700 px-3 py-2">{t('taskRelations.claimsNote')}</p>
      {query.isLoading ? (
        <div role="status" aria-label={t('taskRelations.loading')} className="p-3">
          <SkeletonBlock className="h-10 motion-reduce:animate-none" />
        </div>
      ) : query.isError ? (
        <div className="p-3">
          <div role="alert" className="alert-error items-center p-3 text-[13px]">
            <span>{t('taskRelations.error')}</span>
            <button type="button" onClick={() => query.refetch()} className="btn-tool ml-auto">
              <RefreshCw size={13} aria-hidden="true" />{t('common.retry')}
            </button>
          </div>
        </div>
      ) : rows.length === 0 ? (
        <p className="px-3 py-3 text-xs text-gray-400">{t('taskRelations.empty')}</p>
      ) : (
        <ul className="data-list" aria-label={t('taskRelations.listLabel')}>
          {rows.map(row => <RelationItem key={row.id} relation={row} canManage={canManage} />)}
        </ul>
      )}
      {canManage && <AddRelationForm taskId={taskId} />}
    </Panel>
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
    <li className="data-row text-xs">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-semibold text-gray-200">{t(`taskRelations.names.${relation.relation}`, { defaultValue: relation.relation })}</span>
        <Link to={target} className="rounded-sm text-brand-300 hover:underline">
          {other.title
            ? t('taskRelations.taskWithTitle', { id: other.id, title: other.title })
            : t('taskRelations.task', { id: other.id })}
        </Link>
        {other.sprint_name && <span className="text-gray-400">{t('taskRelations.inSprint', { sprint: other.sprint_name })}</span>}
        <StatusBadge icon={relation.state === 'confirmed' ? CircleCheck : CircleDashed}
          tone={relation.state === 'confirmed' ? 'success' : 'warning'}
          label={t(`taskRelations.states.${relation.state}`)} />
        {relation.drives_dispatch && relation.relation === 'blocked_by' && <span className="text-amber-300">{t('taskRelations.drivesDispatch')}</span>}
      </div>
      <p className="text-meta mt-1">
        {t('taskRelations.proposedBy', { by: proposer })}
        {relation.confirmed_by ? ` · ${t('taskRelations.confirmedBy', { by: relation.confirmed_by.startsWith('rule:')
          ? t('taskRelations.aRule', { rule: relation.confirmed_by.slice(5) }) : relation.confirmed_by })}` : ''}
      </p>
      {canManage && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {relation.state === 'proposed' && (
            <>
              <button type="button" className="btn-tool border border-surface-700" disabled={busy} onClick={() => confirm.mutate(relation.id)}>
                {t('taskRelations.confirm')}
              </button>
              <button type="button" className="btn-tool border border-surface-700" disabled={busy} onClick={() => reject.mutate(relation.id)}>
                {t('taskRelations.reject')}
              </button>
            </>
          )}
          {relation.state === 'confirmed' && (
            <button type="button" className="btn-tool border border-surface-700" disabled={busy} onClick={() => remove.mutate(relation.id)}>
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
    <form onSubmit={submit} className="flex flex-wrap items-end gap-2 border-t border-surface-700 px-3 py-2.5" aria-label={t('taskRelations.addTitle')}>
      <label className="text-xs text-gray-400">
        <span className="input-label">{t('taskRelations.relationLabel')}</span>
        <select value={relation} onChange={e => setRelation(e.target.value as TaskRelationName)} className="select control-sm w-auto pr-7">
          {TASK_RELATION_NAMES.map(name => (
            <option key={name} value={name}>{t(`taskRelations.names.${name}`)}</option>
          ))}
        </select>
      </label>
      <label className="text-xs text-gray-400">
        <span className="input-label">{t('taskRelations.otherLabel')}</span>
        <input value={other} onChange={e => setOther(e.target.value)} inputMode="numeric" className="input control-sm w-28"
          placeholder={t('taskRelations.otherPlaceholder')} />
      </label>
      <label className="flex h-8 items-center gap-1.5 text-xs text-gray-300">
        <input type="checkbox" checked={confirmNow} onChange={e => setConfirmNow(e.target.checked)} />
        {t('taskRelations.confirmNow')}
      </label>
      <button type="submit" className="btn btn-primary btn-sm h-8" disabled={add.isPending || otherId === null}>
        {t('taskRelations.add')}
      </button>
      {add.isError && <div role="alert" className="alert-error w-full text-xs">{errorDetail(add.error)}</div>}
    </form>
  )
}
