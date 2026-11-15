import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import type { FilteredTask, GroupField, ViewColumn, ViewLayout } from '../services/api'

const STATUSES: FilteredTask['status'][] = ['todo', 'in_progress', 'in_review', 'done', 'blocked']

type Translate = ReturnType<typeof useTranslation>['t']

function groupLabel(t: Translate, field: GroupField, value: string): string {
  if (value === '') return t('taskViews.noValue')
  if (field === 'status' || field === 'verdict' || field === 'risk_tier' || field === 'waits_for_person') {
    return t(`taskViews.value.${value}`, value)
  }
  return value
}

function groupKey(task: FilteredTask, field: GroupField): string {
  const value = task[field]
  return value === null || value === undefined ? '' : String(value)
}

/** The value of one column of a task, in the person's language. */
function Cell({ task, column, sprintId }: { task: FilteredTask; column: ViewColumn; sprintId: number }) {
  const { t } = useTranslation()
  switch (column) {
    case 'title':
      return (
        <Link to={`/sprints/${task.sprint_id || sprintId}/tasks/${task.id}`} className="text-white hover:text-brand-300 hover:underline">
          {task.title}
        </Link>
      )
    case 'status':
      return <>{t(`taskViews.value.${task.status}`)}</>
    case 'verdict':
    case 'risk_tier':
      return <>{task[column] ? t(`taskViews.value.${task[column]}`) : t('taskViews.noValue')}</>
    case 'waits_for_person':
      return <>{t(`taskViews.value.${task.waits_for_person ? 'true' : 'false'}`)}</>
    case 'covers':
      return <>{task.covers.join(', ') || t('taskViews.noValue')}</>
    case 'updated_at':
      return <>{task.updated_at ? new Date(task.updated_at).toLocaleString() : t('taskViews.noValue')}</>
    default: {
      const value = task[column]
      return <>{value === null || value === undefined || value === '' ? t('taskViews.noValue') : String(value)}</>
    }
  }
}

function Table({ tasks, columns, sprintId, caption }: {
  tasks: FilteredTask[]; columns: ViewColumn[]; sprintId: number; caption: string
}) {
  const { t } = useTranslation()
  return (
    <table className="w-full text-left text-xs">
      <caption className="sr-only">{caption}</caption>
      <thead className="text-gray-400">
        <tr>
          {columns.map(column => (
            <th key={column} scope="col" className="px-2 py-1.5 font-medium">{t(`taskViews.field.${column}`)}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {tasks.map(task => (
          <tr key={task.id} className="border-t border-surface-800 text-gray-300">
            {columns.map(column => (
              <td key={column} className="px-2 py-1.5"><Cell task={task} column={column} sprintId={sprintId} /></td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/**
 * The tasks a filter returned, as a list (a table, grouped when the view says
 * so) or as read-only columns by status. Moving a card stays on the kanban.
 */
export function TaskListView({
  tasks, columns, layout, groupBy = null, sprintId,
}: {
  tasks: FilteredTask[]
  columns: ViewColumn[]
  layout: ViewLayout
  groupBy?: GroupField | null
  sprintId: number
}) {
  const { t } = useTranslation()
  const shown: ViewColumn[] = columns.length ? columns : ['title', 'status']

  if (tasks.length === 0) {
    return <p className="p-4 text-sm text-gray-500" role="status">{t('taskViews.noTasks')}</p>
  }

  if (layout === 'board') {
    return (
      <div className="flex gap-3 overflow-x-auto p-4" data-testid="filtered-board">
        {STATUSES.map(status => {
          const cards = tasks.filter(task => task.status === status)
          return (
            <section key={status} aria-label={t(`taskViews.value.${status}`)} className="w-56 flex-shrink-0">
              <h3 className="mb-2 flex items-center text-xs font-semibold uppercase text-gray-400">
                {t(`taskViews.value.${status}`)}<span className="ml-auto badge">{cards.length}</span>
              </h3>
              <ul className="space-y-2">
                {cards.map(task => (
                  <li key={task.id} className="card p-2 text-sm">
                    <Cell task={task} column="title" sprintId={sprintId} />
                    <p className="mt-1 text-xs text-gray-500">
                      {task.verdict ? t(`taskViews.value.${task.verdict}`) : t('taskViews.noVerdict')}
                      {task.risk_tier && ` · ${t(`taskViews.value.${task.risk_tier}`)}`}
                    </p>
                  </li>
                ))}
              </ul>
            </section>
          )
        })}
      </div>
    )
  }

  if (!groupBy) {
    return <div className="overflow-x-auto p-2"><Table tasks={tasks} columns={shown} sprintId={sprintId} caption={t('taskViews.listCaption')} /></div>
  }

  const groups = new Map<string, FilteredTask[]>()
  for (const task of tasks) {
    const key = groupKey(task, groupBy)
    groups.set(key, [...(groups.get(key) ?? []), task])
  }
  return (
    <div className="space-y-3 overflow-x-auto p-2">
      {[...groups.entries()].map(([key, members]) => {
        const label = groupLabel(t, groupBy, key)
        return (
          <section key={key} aria-label={label}>
            <h3 className="px-2 text-xs font-semibold text-gray-400">
              {label} <span className="badge">{members.length}</span>
            </h3>
            <Table tasks={members} columns={shown} sprintId={sprintId} caption={label} />
          </section>
        )
      })}
    </div>
  )
}
