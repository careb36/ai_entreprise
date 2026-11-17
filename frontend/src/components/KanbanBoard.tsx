/**
 * The sprint board: five status columns of task cards, moved by drag and
 * drop, by the keyboard (Alt+←/→ on a card, or dnd-kit's Space and arrows on
 * its handle) or with the status select on the card.
 *
 * The design follows the board of AixleHQ/flow (app/frontend/pages/Projects/
 * Board/, Apache-2.0, Copyright 2026 Dualboot Partners, LLC): columns with a sticky header,
 * a count and a fold into a narrow strip that keeps its tasks as draggable
 * chips, horizontal scrolling, cards with the assignee's avatar and the time
 * of the last change, built-in presets beside the filters, an activity side
 * panel and the "/" shortcut to the search. Re-implemented here in React and
 * TailwindCSS; no code was copied. The parts are in components/board/.
 */
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertCircle, Loader2, RefreshCw } from 'lucide-react'
import {
  DndContext, DragOverlay, KeyboardSensor, PointerSensor, closestCenter, pointerWithin, rectIntersection,
  useSensor, useSensors, type CollisionDetection, type DragEndEvent, type DragStartEvent,
} from '@dnd-kit/core'
import { sortableKeyboardCoordinates } from '@dnd-kit/sortable'
import { BacklogItem, errorDetail } from '../services/api'
import type { BoardMoveResult } from '../services/boardMoves'
import { useAgentRuns, useCurrentRole, useSprintBoard, useUpdateBacklogItem } from '../hooks/useQueries'
import { useBoardPreferences } from '../hooks/useBoardPreferences'
import { useAppStore } from '../store/useAppStore'
import { TASK_WORKER_PRODUCER, latestRunsByTask } from '../lib/taskEvidence'
import {
  BOARD_COLUMNS, BOARD_PRESETS, EMPTY_FILTERS, MOVABLE_STATUSES, describeCarry, describeMove, filterColumns,
  filtersActive, findColumnIdByItemId, matchesPreset, rolesOf, type BoardFilters, type BoardPreset, type BoardStatus,
} from '../lib/kanban'
import { MoveToSprint } from './MoveToSprint'
import { ActivityPanel } from './board/ActivityPanel'
import { BoardColumn } from './board/BoardColumn'
import { BoardToolbar } from './board/BoardToolbar'
import { CardPreview } from './board/TaskCard'

const COLUMN_IDS = BOARD_COLUMNS.map(column => column.id)

function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

function BoardSkeleton() {
  return (
    <div className="flex min-w-0 gap-3 overflow-x-auto p-4" aria-hidden="true">
      {BOARD_COLUMNS.map(c => (
        <div key={c.id} className="w-64 flex-shrink-0 space-y-2 rounded-xl border border-surface-700 bg-surface-900/80 p-2">
          <div className="skeleton mb-3 h-5 w-28 rounded" />
          {[1, 2].map(i => <div key={i} className="skeleton h-24 rounded-lg" />)}
        </div>
      ))}
    </div>
  )
}

export default function KanbanBoard({ sprintId }: { sprintId: string | null }) {
  const { t } = useTranslation()
  const idNum = sprintId ? parseInt(sprintId, 10) : null
  const uid = useId()
  const hintId = `${uid}-keys`
  const activityPanelId = `${uid}-activity`

  const { data: board, isLoading, isError, refetch } = useSprintBoard(idNum)
  const updateMutation = useUpdateBacklogItem()
  const companyId = useAppStore(state => state.companyId)
  // One request per board, not one per card: every task-worker run in this sprint.
  const { data: sprintRuns } = useAgentRuns(idNum ? companyId : null, {
    producer: TASK_WORKER_PRODUCER,
    sprint_id: idNum ?? undefined,
    limit: 500,
  })
  const evidenceRuns = useMemo(() => latestRunsByTask(sprintRuns?.agent_runs ?? []), [sprintRuns])

  const [activeItem, setActiveItem] = useState<BacklogItem | null>(null)
  // What the last move did: queued, reopened, or why the task waits.
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null)
  const [filters, setFilters] = useState<BoardFilters>(EMPTY_FILTERS)
  const [activityOpen, setActivityOpen] = useState(false)
  const searchRef = useRef<HTMLInputElement>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  // The task a keyboard move sent to another column: focus follows it there.
  const [focusTarget, setFocusTarget] = useState<{ id: number; status: BoardStatus } | null>(null)
  const prefs = useBoardPreferences(idNum)
  const role = useCurrentRole()
  const canManage = role === 'Owner' || role === 'Manager'

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    // Space or Enter on a card's handle picks it up, the arrows move it, Space drops it.
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  // pointerWithin gives priority to whatever the pointer is directly over (including
  // empty columns). Falls back to rectIntersection then closestCenter for overlapping cases.
  const collisionDetection: CollisionDetection = useCallback((args) => {
    const pointer = pointerWithin(args)
    if (pointer.length > 0) return pointer
    const rect = rectIntersection(args)
    if (rect.length > 0) return rect
    return closestCenter(args)
  }, [])

  // "/" anywhere on the page but a field puts the cursor in the board's search.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey) return
      if (target && (['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName) || target.isContentEditable)) return
      if (!searchRef.current) return
      event.preventDefault()
      searchRef.current.focus()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  const columns = useMemo(() => board?.columns ?? {}, [board])
  const allItems = useMemo(() => Object.values(columns).flat(), [columns])
  const visible = useMemo(() => filterColumns(columns, filters), [columns, filters])
  const roles = useMemo(() => rolesOf(columns), [columns])
  const presetCounts = useMemo(() => Object.fromEntries(
    BOARD_PRESETS.map(preset => [preset, allItems.filter(item => matchesPreset(item, preset)).length]),
  ) as Record<BoardPreset, number>, [allItems])

  // Once the refreshed board shows the moved task in its new column (the move
  // remounts its card there), focus its title, or its chip in a folded column,
  // or, when a filter now hides it, that column's fold button.
  useEffect(() => {
    if (!focusTarget || !rootRef.current) return
    const current = allItems.find(entry => entry.id === focusTarget.id)
    if (current && current.status !== focusTarget.status) return
    const root = rootRef.current
    const target = root.querySelector<HTMLElement>(`#card-title-${focusTarget.id}`)
      ?? root.querySelector<HTMLElement>(`[data-task-chip="${focusTarget.id}"]`)
      ?? root.querySelector<HTMLElement>(`[data-column-toggle="${focusTarget.status}"]`)
    target?.focus()
    setFocusTarget(null)
  }, [focusTarget, allItems, visible, prefs.collapsed])

  const handleStatusChange = (itemId: number, newStatus: BoardStatus, announce = false) => {
    setNotice(null)
    const item = allItems.find(entry => entry.id === itemId)
    const column = BOARD_COLUMNS.find(c => c.id === newStatus)
    updateMutation.mutate({ itemId, data: { status: newStatus } }, {
      onSuccess: result => {
        const text = describeMove(t, result as BoardMoveResult)
          ?? (announce && item && column ? t('kanbanBoard.card.moved', { title: item.title, column: t(column.titleKey) }) : null)
        setNotice(text ? { text } : null)
        if (announce) setFocusTarget({ id: itemId, status: newStatus })
      },
      onError: error => setNotice({ text: errorDetail(error), error: true }),
    })
  }

  const cardActions = (item: BacklogItem) => (canManage && idNum && MOVABLE_STATUSES.includes(item.status) ? (
    <MoveToSprint item={item} sprintId={idNum} companyId={companyId}
      onMoved={result => setNotice({ text: describeCarry(t, result) })} />
  ) : null)

  const handleDragStart = (event: DragStartEvent) => {
    setActiveItem(allItems.find(entry => entry.id === event.active.id) ?? null)
  }

  const handleDragEnd = (event: DragEndEvent) => {
    setActiveItem(null)
    const { active, over } = event
    if (!over || active.id === over.id) return
    const overId = String(over.id)
    const targetColumnId = BOARD_COLUMNS.find(column => column.id === overId)?.id
      ?? findColumnIdByItemId(columns, Number(over.id))
    if (targetColumnId) handleStatusChange(active.id as number, targetColumnId)
  }

  if (!idNum) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 py-12 text-gray-500">
        <AlertCircle size={28} className="text-gray-500" aria-hidden="true" />
        <p className="text-sm text-gray-300">{t('kanban.noActiveSprint')}</p>
      </div>
    )
  }

  if (isLoading && !board) return <BoardSkeleton />

  if (isError && !board) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 py-12 text-gray-500">
        <AlertCircle size={28} className="text-red-400" aria-hidden="true" />
        <p className="text-sm text-red-400">{t('kanban.error')}</p>
        <button onClick={() => refetch()} className="btn-ghost btn-sm mt-1 flex items-center gap-1.5">
          <RefreshCw size={12} aria-hidden="true" />
          <span>{t('kanban.refresh')}</span>
        </button>
      </div>
    )
  }

  if (!board) {
    return (
      <div className="flex flex-1 items-center justify-center gap-2 py-12 text-gray-500">
        <Loader2 className="animate-spin motion-reduce:animate-none" size={20} aria-hidden="true" />
        <span className="text-sm">{t('kanban.loading')}</span>
      </div>
    )
  }

  const filtering = filtersActive(filters)
  const shown = Object.values(visible).reduce((sum, items) => sum + items.length, 0)
  const allCollapsed = COLUMN_IDS.every(id => prefs.collapsed.includes(id))

  return (
    <div ref={rootRef} className="flex h-full flex-col">
      <BoardToolbar
        title={board.sprint.sprint_name || t('kanban.title')}
        total={board.total}
        shown={shown}
        filters={filters}
        onFiltersChange={setFilters}
        roles={roles}
        presetCounts={presetCounts}
        density={prefs.density}
        onDensityChange={prefs.setDensity}
        allCollapsed={allCollapsed}
        onToggleAll={() => prefs.setAllCollapsed(allCollapsed ? [] : [...COLUMN_IDS])}
        activityOpen={activityOpen}
        onToggleActivity={() => setActivityOpen(open => !open)}
        activityPanelId={activityPanelId}
        onRefresh={() => refetch()}
        refreshing={isLoading}
        searchRef={searchRef}
      />
      <p id={hintId} className="sr-only">{t('kanbanBoard.keyboardHint')}</p>

      {notice && (
        <p role={notice.error ? 'alert' : 'status'}
          className={`mx-4 mt-3 rounded-lg border px-3 py-2 text-xs ${notice.error ? 'border-red-700/50 bg-red-950/30 text-red-300' : 'border-brand-700/50 bg-brand-950/30 text-brand-200'}`}>
          {notice.text}
        </p>
      )}

      <div className="flex min-h-0 flex-1">
        <DndContext
          sensors={sensors}
          collisionDetection={collisionDetection}
          onDragStart={handleDragStart}
          onDragEnd={handleDragEnd}
          onDragCancel={() => setActiveItem(null)}
        >
          <div
            role="region"
            aria-label={t('kanbanBoard.columnsRegion')}
            tabIndex={0}
            className="min-h-[26rem] min-w-0 flex-1 snap-x snap-mandatory overflow-auto overscroll-x-contain focus-visible:outline-offset-[-2px] sm:snap-none lg:max-h-[calc(100dvh-14rem)]"
          >
            <div className="flex min-w-max items-start gap-3 p-3 sm:p-4 lg:min-w-0">
              {BOARD_COLUMNS.map(({ id, titleKey }) => (
                <BoardColumn
                  key={id}
                  id={id}
                  title={t(titleKey)}
                  items={visible[id] ?? []}
                  total={(columns[id] ?? []).length}
                  filtered={filtering}
                  sprintId={idNum}
                  density={prefs.density}
                  collapsed={prefs.collapsed.includes(id)}
                  onToggleCollapse={prefs.toggleColumn}
                  onStatusChange={handleStatusChange}
                  updatingId={updateMutation.isPending ? updateMutation.variables?.itemId ?? null : null}
                  activeItemId={activeItem?.id ?? null}
                  evidenceRuns={evidenceRuns}
                  cardActions={cardActions}
                  keyboardHintId={hintId}
                />
              ))}
            </div>
          </div>

          <DragOverlay dropAnimation={prefersReducedMotion() ? null : undefined}>
            {activeItem && <CardPreview item={activeItem} />}
          </DragOverlay>
        </DndContext>
        <ActivityPanel open={activityOpen} onClose={() => setActivityOpen(false)}
          columns={columns} sprintId={idNum} panelId={activityPanelId} />
      </div>
    </div>
  )
}
