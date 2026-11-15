import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { LayoutGrid, List, Save } from 'lucide-react'
import KanbanBoard from './KanbanBoard'
import { TaskFilterBuilder } from './TaskFilterBuilder'
import { TaskListView } from './TaskListView'
import { useFilteredTasks, useSaveView, useSavedViews } from '../hooks/useTaskViews'
import { errorDetail, type SavedView, type ViewAccess, type ViewLayout } from '../services/api'
import { DEFAULT_COLUMNS, filterToRows, rowsToFilter, type FilterJoin, type FilterRow } from '../lib/taskFilters'

const LAYOUTS: { value: ViewLayout; Icon: typeof List; labelKey: string }[] = [
  { value: 'board', Icon: LayoutGrid, labelKey: 'taskViews.layoutBoard' },
  { value: 'list', Icon: List, labelKey: 'taskViews.layoutList' },
]

/**
 * A sprint's tasks as the kanban or as a list, filtered on the evidence and
 * saved as views (issue #535). With no filter and the board layout it is the
 * kanban as before (moves included); otherwise the server evaluates the
 * filter from the task projections, limited to this sprint, and the tasks are
 * shown read-only. A saved view is the company's or this person's own.
 */
export function SprintTaskViews({ sprintId, companyId }: { sprintId: number; companyId: number | null }) {
  const { t } = useTranslation()
  const [layout, setLayout] = useState<ViewLayout>('board')
  const [rows, setRows] = useState<FilterRow[]>([])
  const [join, setJoin] = useState<FilterJoin>('and')
  const [view, setView] = useState<SavedView | null>(null)
  const [name, setName] = useState('')
  const [access, setAccess] = useState<ViewAccess>('private')
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null)

  const views = useSavedViews(companyId)
  const save = useSaveView(companyId ?? 0)
  // A saved filter the builder cannot show (nested groups) is sent by its id.
  const nested = view !== null && filterToRows(view.filters) === null
  const filtering = rows.length > 0 || nested
  const filter = useMemo(() => rowsToFilter(rows, join, sprintId), [rows, join, sprintId])
  const query = companyId !== null && (filtering || layout === 'list')
    ? {
        companyId,
        filter,
        viewId: nested && view ? view.id : null,
        orderBy: view?.order_by ?? null,
        groupBy: layout === 'list' ? view?.group_by ?? null : null,
        limit: 1000,
      }
    : null
  const tasks = useFilteredTasks(query)

  const choose = (id: string) => {
    setNotice(null)
    const chosen = views.data?.views.find(v => String(v.id) === id) ?? null
    setView(chosen)
    if (!chosen) {
      setRows([])
      return
    }
    const loaded = filterToRows(chosen.filters)
    setRows(loaded ? loaded.rows.filter(r => r.field !== 'sprint_id') : [])
    setJoin(loaded?.join ?? 'and')
    setLayout(chosen.layout)
    setName(chosen.name)
    setAccess(chosen.access)
  }

  const saveView = () => {
    const companyFilter = rowsToFilter(rows, join)
    const data = {
      name: name.trim(),
      filters: companyFilter ?? {},
      layout,
      access,
      visible_columns: view?.visible_columns ?? [...DEFAULT_COLUMNS],
      group_by: view?.group_by ?? null,
      order_by: view?.order_by ?? null,
    }
    const editing = view && view.can_edit && view.name === data.name ? view.id : undefined
    save.mutate({ id: editing, data: nested && editing && view ? { ...data, filters: view.filters } : data }, {
      onSuccess: saved => { setView(saved); setNotice({ text: t('taskViews.saved', { name: saved.name }) }) },
      onError: error => setNotice({ text: errorDetail(error), error: true }),
    })
  }

  const canShare = views.data?.can_share ?? false

  return (
    <div className="flex h-full flex-col">
      <div className="space-y-2 border-b border-surface-700 px-4 py-2">
        <div className="flex flex-wrap items-center gap-2">
          <div role="group" aria-label={t('taskViews.layout')} className="flex gap-1">
            {LAYOUTS.map(({ value, Icon, labelKey }) => (
              <button key={value} type="button" aria-pressed={layout === value} onClick={() => setLayout(value)}
                className={`btn-ghost btn-sm inline-flex items-center gap-1 ${layout === value ? 'text-brand-300' : ''}`}>
                <Icon size={12} aria-hidden="true" />{t(labelKey)}
              </button>
            ))}
          </div>
          <label className="flex items-center gap-1.5 text-xs text-gray-400">
            {t('taskViews.savedView')}
            <select className="select text-xs py-1" value={view ? String(view.id) : ''} onChange={e => choose(e.target.value)}>
              <option value="">{t('taskViews.noView')}</option>
              {(views.data?.views ?? []).map(v => (
                <option key={v.id} value={v.id} disabled={!v.valid}>
                  {v.name}{v.access === 'company' ? ` · ${t('taskViews.access.company')}` : ''}{v.locked ? ` · ${t('taskViews.locked')}` : ''}
                </option>
              ))}
            </select>
          </label>
          {query && tasks.data && (
            <span className="text-xs text-gray-500" role="status">{t('taskViews.count', { count: tasks.data.count })}</span>
          )}
        </div>
        {view && !view.valid && <p role="alert" className="text-xs text-red-300">{t('taskViews.invalidView')}</p>}
        {nested && <p className="text-xs text-gray-500">{t('taskViews.nestedView')}</p>}
        <TaskFilterBuilder rows={rows} join={join} onChange={(next, nextJoin) => { setRows(next); setJoin(nextJoin) }} />
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs text-gray-400">
            {t('taskViews.viewName')}
            <input className="input text-xs py-1 w-44" value={name} maxLength={120} onChange={e => setName(e.target.value)} />
          </label>
          <label className="flex items-center gap-1.5 text-xs text-gray-400">
            {t('taskViews.accessLabel')}
            <select className="select text-xs py-1" value={access} onChange={e => setAccess(e.target.value as ViewAccess)}>
              <option value="private">{t('taskViews.access.private')}</option>
              <option value="company" disabled={!canShare}>{t('taskViews.access.company')}</option>
            </select>
          </label>
          <button type="button" className="btn-ghost btn-sm inline-flex items-center gap-1"
            disabled={!name.trim() || companyId === null || save.isPending} onClick={saveView}>
            <Save size={12} aria-hidden="true" />{t('taskViews.save')}
          </button>
        </div>
        {notice && (
          <p role={notice.error ? 'alert' : 'status'} className={`text-xs ${notice.error ? 'text-red-300' : 'text-brand-200'}`}>{notice.text}</p>
        )}
      </div>
      <div className="min-h-0 flex-1">
        {!query ? (
          <KanbanBoard sprintId={String(sprintId)} />
        ) : tasks.isError ? (
          <p role="alert" className="p-4 text-sm text-red-300">{errorDetail(tasks.error)}</p>
        ) : tasks.data ? (
          <TaskListView tasks={tasks.data.tasks} columns={view?.visible_columns ?? [...DEFAULT_COLUMNS]}
            layout={layout} groupBy={layout === 'list' ? view?.group_by ?? null : null} sprintId={sprintId} />
        ) : (
          <p className="p-4 text-sm text-gray-500" role="status">{t('taskViews.loading')}</p>
        )}
      </div>
    </div>
  )
}
