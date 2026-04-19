import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { BacklogItem } from '../services/api'
import { useSprintBoard, useUpdateBacklogItem } from '../hooks/useQueries'
import {
  DndContext, DragEndEvent, DragOverlay, DragStartEvent,
  PointerSensor, useDroppable, useSensor, useSensors, closestCenter
} from '@dnd-kit/core'
import {
  SortableContext, useSortable, verticalListSortingStrategy
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import {
  Circle, ArrowRight, GitPullRequest,
  CheckCircle2, AlertTriangle, AlertCircle, RefreshCw,
  Loader2, GripVertical
} from 'lucide-react'

// ── Constants ──────────────────────────────────────────────────────────────────

const COLUMNS = [
  { id: 'todo',        titleKey: 'kanban.todo',       className: 'text-gray-400',   icon: Circle },
  { id: 'in_progress', titleKey: 'kanban.inProgress',  className: 'text-blue-400',   icon: ArrowRight },
  { id: 'in_review',   titleKey: 'kanban.review',      className: 'text-yellow-400', icon: GitPullRequest },
  { id: 'done',        titleKey: 'kanban.done',        className: 'text-status-success', icon: CheckCircle2 },
  { id: 'blocked',     titleKey: 'kanban.blocked',     className: 'text-status-error',   icon: AlertTriangle },
]

const VALID_STATUSES: BacklogItem['status'][] = ['todo', 'in_progress', 'in_review', 'done', 'blocked']

const QA_REWORK_LIMIT = 2
const QA_FAILURE_LIMIT = 3

function findColumnIdByItemId(columns: Record<string, BacklogItem[]>, itemId: number) {
  return COLUMNS.find((column) => (columns[column.id] ?? []).some((item) => item.id === itemId))?.id
}

function getQaMetrics(item: BacklogItem): { reworks: number; failures: number } {
  const comments = Array.isArray(item.comments) ? item.comments : []
  let reworks = 0
  let failures = 0

  for (const comment of comments) {
    const author = String(comment.author || '').toLowerCase()
    const text = String(comment.text || '').toLowerCase()

    const isQaSignal = author === 'qa' || author === 'system'
    if (!isQaSignal) continue

    if (text.startsWith('needs work:') || text.includes('requested rework')) {
      reworks += 1
    }

    if (text.includes('qa review failed') || text.includes('cannot review')) {
      failures += 1
    }
  }

  return { reworks, failures }
}

function metricBadgeClass(value: number, limit: number): string {
  if (value >= limit) return 'bg-red-900/40 text-red-300 border-red-700/40'
  if (value >= Math.max(1, limit - 1)) return 'bg-yellow-900/40 text-yellow-300 border-yellow-700/40'
  return 'bg-surface-800 text-gray-300 border-surface-700'
}

// ── SortableCard ───────────────────────────────────────────────────────────────

function ItemCard({
  item, onStatusChange, updating, isDragging = false
}: {
  item: BacklogItem
  onStatusChange: (id: number, status: BacklogItem['status']) => void
  updating: boolean
  isDragging?: boolean
}) {
  const { t } = useTranslation()
  const { attributes, listeners, setNodeRef, transform, transition } = useSortable({ id: item.id })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.4 : 1,
  }

  const qaMetrics = getQaMetrics(item)

  return (
    <div
      ref={setNodeRef}
      style={style}
      className="card p-3 cursor-default select-none group"
    >
      <div className="flex items-start gap-2">
        {/* Drag handle */}
        <button
          {...attributes} {...listeners}
          className="mt-0.5 text-gray-600 hover:text-gray-400 cursor-grab active:cursor-grabbing flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity"
          aria-label={t('kanban.dragToReorder')}
        >
          <GripVertical size={14} />
        </button>
        <p className="text-white text-sm font-medium leading-snug flex-1">{item.title}</p>
      </div>

      <div className="flex items-center gap-1.5 flex-wrap mt-2 ml-5">
        <span className={`badge badge-priority-${item.priority}`}>{item.priority}</span>
        {item.assignee && (
          <span className="badge bg-brand-900/50 text-brand-300 border-brand-700/40">{item.assignee}</span>
        )}
        {qaMetrics.reworks > 0 && (
          <span
            className={`badge border ${metricBadgeClass(qaMetrics.reworks, QA_REWORK_LIMIT)}`}
            title={`QA reworks: ${qaMetrics.reworks}/${QA_REWORK_LIMIT}`}
          >
            QA rework {qaMetrics.reworks}/{QA_REWORK_LIMIT}
          </span>
        )}
        {qaMetrics.failures > 0 && (
          <span
            className={`badge border ${metricBadgeClass(qaMetrics.failures, QA_FAILURE_LIMIT)}`}
            title={`QA failures: ${qaMetrics.failures}/${QA_FAILURE_LIMIT}`}
          >
            QA fail {qaMetrics.failures}/{QA_FAILURE_LIMIT}
          </span>
        )}
        {item.comments?.length > 0 && (
          <span className="text-xs text-gray-500">💬 {item.comments.length}</span>
        )}
      </div>

      <select
        value={item.status}
        disabled={updating}
        onChange={e => onStatusChange(item.id, e.target.value as BacklogItem['status'])}
        className="select w-full mt-2 text-xs py-1 disabled:opacity-50"
        aria-label={t('kanban.changeStatus')}
      >
        {VALID_STATUSES.map(s => (
          <option key={s} value={s}>{t(`kanban.status.${s}`, s)}</option>
        ))}
      </select>
    </div>
  )
}

function ColumnDropZone({
  columnId,
  items,
  titleKey,
  className,
  Icon,
  t,
  onStatusChange,
  updating,
  activeItemId,
}: {
  columnId: string
  items: BacklogItem[]
  titleKey: string
  className: string
  Icon: typeof Circle
  t: ReturnType<typeof useTranslation>['t']
  onStatusChange: (id: number, status: BacklogItem['status']) => void
  updating: ReturnType<typeof useUpdateBacklogItem>
  activeItemId: number | null
}) {
  const { setNodeRef, isOver } = useDroppable({ id: columnId })

  return (
    <div className="w-56 lg:w-64 flex-shrink-0 flex flex-col">
      <div className="flex items-center gap-1.5 mb-2">
        <Icon className={className} size={13} />
        <h3 className={`font-semibold text-xs uppercase tracking-wide ${className}`}>
          {t(titleKey)}
        </h3>
        <span className="ml-auto badge text-xs">{items.length}</span>
      </div>

      <SortableContext items={items.map(i => i.id)} strategy={verticalListSortingStrategy}>
        <div
          ref={setNodeRef}
          className={`flex-1 space-y-2 min-h-[80px] rounded-lg border border-dashed p-2 transition-colors ${
            isOver
              ? 'border-brand-500 bg-brand-950/40'
              : 'border-surface-700 bg-surface-950/40'
          }`}
        >
          {items.length === 0 ? (
            <div className="flex items-center justify-center h-16 text-gray-600 text-xs">
              {t('kanban.empty')}
            </div>
          ) : (
            items.map(item => (
              <ItemCard
                key={item.id}
                item={item}
                onStatusChange={onStatusChange}
                updating={updating.isPending && updating.variables?.itemId === item.id}
                isDragging={activeItemId === item.id}
              />
            ))
          )}
        </div>
      </SortableContext>
    </div>
  )
}

// ── KanbanBoard ────────────────────────────────────────────────────────────────

export default function KanbanBoard({ sprintId }: { sprintId: string }) {
  const { t } = useTranslation()
  const idNum = parseInt(sprintId, 10) || 1
  
  const { data: board, isLoading, isError, refetch } = useSprintBoard(idNum)
  const updateMutation = useUpdateBacklogItem()

  const [activeItem, setActiveItem] = useState<BacklogItem | null>(null)

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }))

  const handleStatusChange = (itemId: number, newStatus: BacklogItem['status']) => {
    updateMutation.mutate({ itemId, data: { status: newStatus } })
  }

  const handleDragStart = (event: DragStartEvent) => {
    const id = event.active.id as number
    const allItems = (Object.values(board?.columns ?? {}) as BacklogItem[][]).flat()
    const item = allItems.find((entry) => entry.id === id)
    setActiveItem(item ?? null)
  }

  const handleDragEnd = (event: DragEndEvent) => {
    setActiveItem(null)
    const { active, over } = event
    if (!over || active.id === over.id) return

    const columns = board?.columns ?? {}
    const overId = String(over.id)
    const targetColumnId = COLUMNS.find((column) => column.id === overId)?.id
      ?? findColumnIdByItemId(columns, Number(over.id))

    if (targetColumnId) {
      handleStatusChange(active.id as number, targetColumnId as BacklogItem['status'])
    }
  }

  // ── Skeleton ──────────────────────────────────────────────────────────────────

  if (isLoading && !board) {
    return (
      <div className="flex gap-4 p-4 overflow-x-auto min-w-0">
        {COLUMNS.map(c => (
          <div key={c.id} className="w-56 flex-shrink-0 space-y-2">
            <div className="skeleton h-4 w-24 rounded mb-3" />
            {[1, 2].map(i => <div key={i} className="skeleton h-20 rounded-lg" />)}
          </div>
        ))}
      </div>
    )
  }

  if (isError && !board) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 text-gray-500 py-12">
        <AlertCircle size={28} className="text-red-400" />
        <p className="text-sm text-red-400">{t('kanban.error')}</p>
        <button onClick={() => refetch()} className="btn-ghost btn-sm flex items-center gap-1.5 mt-1">
          <RefreshCw size={12} />
          <span>{t('kanban.refresh')}</span>
        </button>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header with sprint name and refresh */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-surface-700 flex-shrink-0">
        <div className="flex items-center gap-2">
          <span className="font-medium text-gray-200 text-sm">
            {board ? board.sprint.sprint_name : t('kanban.title')}
          </span>
          {board && (
            <span className="text-xs text-gray-500">· {board.total} {t('kanban.items')}</span>
          )}
        </div>
        <button
          onClick={() => refetch()}
          disabled={isLoading}
          className="btn-ghost btn-sm flex items-center gap-1"
        >
          <RefreshCw size={12} className={isLoading ? 'animate-spin' : ''} />
          <span className="hidden sm:inline">{t('kanban.refresh')}</span>
        </button>
      </div>

      {/* Board */}
      {board ? (
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragStart={handleDragStart}
          onDragEnd={handleDragEnd}
        >
          <div className="flex-1 overflow-x-auto overflow-y-auto p-4">
            <div className="flex gap-3 min-w-max h-full md:h-auto md:min-w-0 md:flex-nowrap">
              {COLUMNS.map(({ id: colId, titleKey, className, icon: Icon }) => {
                const items = board.columns[colId] || []
                return (
                  <ColumnDropZone
                    key={colId}
                    columnId={colId}
                    items={items}
                    titleKey={titleKey}
                    className={className}
                    Icon={Icon}
                    t={t}
                    onStatusChange={handleStatusChange}
                    updating={updateMutation}
                    activeItemId={activeItem?.id ?? null}
                  />
                )
              })}
            </div>
          </div>

          <DragOverlay>
            {activeItem && (
              <div className="card p-3 shadow-glow rotate-1 w-56">
                <p className="text-white text-sm font-medium">{activeItem.title}</p>
              </div>
            )}
          </DragOverlay>
        </DndContext>
      ) : (
        <div className="flex-1 flex items-center justify-center gap-2 text-gray-500">
          <Loader2 className="animate-spin" size={20} />
          <span className="text-sm">{t('kanban.loading')}</span>
        </div>
      )}
    </div>
  )
}

