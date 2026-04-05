import { useState, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { BacklogItem, SprintBoard } from '../services/api'
import { useSprintBoard, useUpdateBacklogItem } from '../hooks/useQueries'
import {
  DndContext, DragEndEvent, DragOverlay, DragStartEvent,
  PointerSensor, useSensor, useSensors, closestCenter
} from '@dnd-kit/core'
import {
  SortableContext, useSortable, verticalListSortingStrategy
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import {
  LayoutGrid, Circle, ArrowRight, GitPullRequest,
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
          aria-label="Drag to reorder"
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
        {item.comments?.length > 0 && (
          <span className="text-xs text-gray-500">💬 {item.comments.length}</span>
        )}
      </div>

      <select
        value={item.status}
        disabled={updating}
        onChange={e => onStatusChange(item.id, e.target.value as BacklogItem['status'])}
        className="select w-full mt-2 text-xs py-1 disabled:opacity-50"
        aria-label="Change status"
      >
        {VALID_STATUSES.map(s => (
          <option key={s} value={s}>{t(`kanban.status.${s}`, s)}</option>
        ))}
      </select>
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
    const item = Object.values(board?.columns ?? {}).flat().find(x => x.id === id)
    setActiveItem(item ?? null)
  }

  const handleDragEnd = (event: DragEndEvent) => {
    setActiveItem(null)
    const { active, over } = event
    if (!over || active.id === over.id) return
    
    // Find column id from drop target
    const targetCol = COLUMNS.find(c => 
      c.id === over.id || (board?.columns[c.id] ?? []).some(x => x.id === over.id)
    )
    
    if (targetCol) {
      handleStatusChange(active.id as number, targetCol.id as BacklogItem['status'])
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

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-surface-700 flex-shrink-0">
        <div className="flex items-center gap-2">
          <LayoutGrid size={15} className="text-brand-400" />
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

      {isError && (
        <div className="mx-4 mt-3 alert-error flex items-center gap-2 text-sm">
          <AlertCircle size={14} className="flex-shrink-0" />
          {t('kanban.error')}
        </div>
      )}

      {/* Board */}
      {board ? (
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragStart={handleDragStart}
          onDragEnd={handleDragEnd}
        >
          <div className="flex-1 overflow-x-auto overflow-y-hidden p-4">
            <div className="flex gap-3 min-w-max h-full md:h-auto md:min-w-0 md:flex-nowrap">
              {COLUMNS.map(({ id: colId, titleKey, className, icon: Icon }) => {
                const items = board.columns[colId] || []
                return (
                  <div key={colId} className="w-56 lg:w-64 flex-shrink-0 flex flex-col">
                    <div className="flex items-center gap-1.5 mb-2">
                      <Icon className={className} size={13} />
                      <h3 className={`font-semibold text-xs uppercase tracking-wide ${className}`}>
                        {t(titleKey)}
                      </h3>
                      <span className="ml-auto badge text-xs">{items.length}</span>
                    </div>

                    <SortableContext items={items.map(i => i.id)} strategy={verticalListSortingStrategy}>
                      <div className="flex-1 space-y-2 min-h-[80px] rounded-lg border border-dashed border-surface-700 p-2 bg-surface-950/40">
                        {items.length === 0 ? (
                          <div className="flex items-center justify-center h-16 text-gray-600 text-xs">
                            {t('kanban.empty')}
                          </div>
                        ) : (
                          items.map(item => (
                            <ItemCard
                              key={item.id}
                              item={item}
                              onStatusChange={handleStatusChange}
                              updating={updateMutation.isPending && updateMutation.variables?.itemId === item.id}
                              isDragging={activeItem?.id === item.id}
                            />
                          ))
                        )}
                      </div>
                    </SortableContext>
                  </div>
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



