import { useId, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronRight, CircleCheck, CircleDashed, Link2 } from 'lucide-react'
import { useSprintRelations } from '../hooks/useTaskRelations'
import { errorDetail, type TaskRelation } from '../services/api'
import { CountPill, LoadError, LoadingRows, Panel, StatusBadge, TABLE, TD, TH, TR } from './ui'

/** The sprint task a relation is read from: the side that is not `other_task`. */
export const ownTaskId = (relation: TaskRelation) =>
  relation.other_task.id === relation.related_task_id ? relation.task_id : relation.related_task_id

/**
 * The relations of a sprint's tasks in one view (`GET /v1/sprints/{id}/relations`,
 * Viewer, issue #533): each relation from its sprint task's side with its
 * state, a proposed one marked as its proposer's claim, and the numbers,
 * among them the confirmed duplicates both delivered (work run twice). Read
 * when opened; a task's page is where a Manager changes them.
 */
export function SprintRelations({ sprintId }: { sprintId: number }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const regionId = useId()
  const query = useSprintRelations(sprintId, open)
  const data = query.data

  return (
    <Panel headingLevel={3}
      title={<span className="flex items-center gap-1.5"><Link2 size={13} aria-hidden="true" className="text-gray-400" />{t('sprintRelations.title')}</span>}
      count={data ? <CountPill count={data.metrics.total} label={t('sprintRelations.count', { count: data.metrics.total })} /> : undefined}
      actions={(
        <button type="button" className="btn-tool" aria-expanded={open} aria-controls={regionId} onClick={() => setOpen(value => !value)}>
          {open ? <ChevronDown size={13} aria-hidden="true" /> : <ChevronRight size={13} aria-hidden="true" />}
          {open ? t('sprintRelations.hide') : t('sprintRelations.show')}
        </button>
      )}>
      <div id={regionId} hidden={!open}>
        {open && (query.isLoading ? (
          <div className="p-3"><LoadingRows rows={1} label={t('sprintRelations.loading')} /></div>
        ) : query.isError ? (
          <div className="p-3"><LoadError onRetry={() => query.refetch()}>{t('sprintRelations.loadError', { message: errorDetail(query.error) })}</LoadError></div>
        ) : data ? (
          <>
            <p className="border-b border-surface-700 px-3 py-2 text-xs text-gray-300">
              {t('sprintRelations.metrics', { confirmed: data.metrics.confirmed, proposed: data.metrics.proposed })}
              {data.metrics.duplicates_delivered > 0 && (
                <span className="ml-1 text-amber-300">{t('sprintRelations.duplicatesDelivered', { count: data.metrics.duplicates_delivered })}</span>
              )}
            </p>
            {data.relations.length === 0 ? (
              <p className="px-3 py-3 text-xs text-gray-400">{t('sprintRelations.empty')}</p>
            ) : (
              <div className="overflow-x-auto">
                <table className={TABLE}>
                  <caption className="sr-only">{t('sprintRelations.title')}</caption>
                  <thead>
                    <tr>
                      <th scope="col" className={TH}>{t('sprintRelations.columns.task')}</th>
                      <th scope="col" className={TH}>{t('sprintRelations.columns.relation')}</th>
                      <th scope="col" className={TH}>{t('sprintRelations.columns.other')}</th>
                      <th scope="col" className={TH}>{t('sprintRelations.columns.state')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.relations.map(relation => <RelationRow key={relation.id} relation={relation} sprintId={sprintId} />)}
                  </tbody>
                </table>
              </div>
            )}
          </>
        ) : null)}
      </div>
    </Panel>
  )
}

function RelationRow({ relation, sprintId }: { relation: TaskRelation; sprintId: number }) {
  const { t } = useTranslation()
  const own = ownTaskId(relation)
  const other = relation.other_task
  const confirmed = relation.state === 'confirmed'
  return (
    <tr className={TR}>
      <td className={TD}><Link to={`/sprints/${sprintId}/tasks/${own}`} className="text-brand-300 hover:underline">{t('taskRelations.task', { id: own })}</Link></td>
      <td className={`${TD} text-gray-300`}>
        {t(`taskRelations.names.${relation.relation}`)}
        {relation.drives_dispatch && <span className="text-meta block">{t('taskRelations.drivesDispatch')}</span>}
      </td>
      <td className={TD}>
        <Link to={other.sprint_id ? `/sprints/${other.sprint_id}/tasks/${other.id}` : `/task/${other.id}`} className="text-brand-300 hover:underline">
          {other.title ? t('taskRelations.taskWithTitle', { id: other.id, title: other.title }) : t('taskRelations.task', { id: other.id })}
        </Link>
        {other.sprint_id !== sprintId && other.sprint_name && <span className="text-meta block">{t('taskRelations.inSprint', { sprint: other.sprint_name })}</span>}
      </td>
      <td className={TD}>
        {confirmed ? (
          <StatusBadge icon={CircleCheck} tone="success" label={t('taskRelations.states.confirmed')} />
        ) : (
          // A proposal is its proposer's claim: dashed, never styled as an observation.
          <span className="status-badge border-dashed border-surface-600 text-gray-300">
            <CircleDashed size={11} aria-hidden="true" className="flex-shrink-0" />{t('taskRelations.states.proposed')}
          </span>
        )}
        {!confirmed && <span className="text-meta block">{t('taskRelations.proposedBy', { by: relation.proposed_by })}</span>}
      </td>
    </tr>
  )
}
