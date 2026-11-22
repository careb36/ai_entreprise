import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useDroppable } from '@dnd-kit/core'
import { SortableContext, verticalListSortingStrategy } from '@dnd-kit/sortable'
import {
  AlertTriangle, ArrowRight, CheckCircle2, ChevronsLeft, ChevronsRight, Circle, GitPullRequest, Inbox, UserRoundCheck, XCircle,
} from 'lucide-react'
import type { AgentRunListItem, BacklogItem } from '../../services/api'
import { needsPerson, type BoardStatus } from '../../lib/kanban'
import type { BoardDensity } from '../../hooks/useBoardPreferences'
import { CollapsedCard } from './CollapsedCard'
import { TaskCard } from './TaskCard'
import { CountPill } from '../ui/CountPill'

/** Each status's icon and tone: the column's name always says it in words too. */
const COLUMN_LOOK: Record<BoardStatus, { Icon: typeof Circle; tone: string }> = {
  todo: { Icon: Circle, tone: 'text-gray-400' },
  in_progress: { Icon: ArrowRight, tone: 'text-blue-400' },
  in_review: { Icon: GitPullRequest, tone: 'text-yellow-400' },
  done: { Icon: CheckCircle2, tone: 'text-status-success' },
  blocked: { Icon: AlertTriangle, tone: 'text-status-error' },
  discarded: { Icon: XCircle, tone: 'text-gray-500' },
}

export interface BoardColumnProps {
  id: BoardStatus
  title: string
  items: BacklogItem[]
  /** The column's cards before the board's filters. */
  total: number
  filtered: boolean
  sprintId: number
  density: BoardDensity
  collapsed: boolean
  onToggleCollapse: (id: BoardStatus) => void
  onStatusChange: (id: number, status: BoardStatus, announce?: boolean) => void
  updatingId: number | null
  activeItemId: number | null
  evidenceRuns: Map<number, AgentRunListItem>
  cardActions: (item: BacklogItem) => ReactNode
  keyboardHintId: string
}

/** The column's count: `shown/total` while the board's filters hide some of its cards. */
function ColumnCount({ shown, total, filtered }: { shown: number; total: number; filtered: boolean }) {
  const { t } = useTranslation()
  const partial = filtered && shown !== total
  const label = partial
    ? t('kanbanBoard.column.countFiltered', { shown, count: total })
    : t('kanbanBoard.column.count', { count: total })
  return <CountPill count={partial ? shown : total} total={partial ? total : undefined} label={label} />
}

export function BoardColumn(props: BoardColumnProps) {
  const { t } = useTranslation()
  const { id, title, items, total, filtered, sprintId, collapsed, onToggleCollapse, activeItemId } = props
  const { setNodeRef, isOver } = useDroppable({ id })
  const { Icon, tone } = COLUMN_LOOK[id]
  const headingId = `board-column-${id}`
  const attention = items.filter(needsPerson).length
  const attentionLabel = t('kanbanBoard.column.needsPerson', { count: attention })
  const dropRing = isOver ? 'ring-2 ring-brand-500 ring-offset-0' : ''

  if (collapsed) {
    return (
      <section aria-labelledby={headingId}
        className={`flex max-h-full w-11 flex-shrink-0 flex-col items-center gap-2.5 panel py-2 transition-shadow motion-reduce:transition-none ${dropRing}`}>
        <button type="button" data-column-toggle={id} onClick={() => onToggleCollapse(id)} aria-expanded={false}
          aria-label={t('kanbanBoard.column.expand', { column: title })} title={t('kanbanBoard.column.expand', { column: title })}
          className="btn-ghost h-7 w-7 rounded-md p-0">
          <ChevronsRight size={14} aria-hidden="true" />
        </button>
        <Icon size={13} className={tone} aria-hidden="true" />
        <h3 id={headingId} className="select-none text-[13px] font-semibold text-gray-100 [writing-mode:vertical-rl]">{title}</h3>
        <ColumnCount shown={items.length} total={total} filtered={filtered} />
        {attention > 0 && (
          <span role="img" aria-label={attentionLabel} title={attentionLabel}
            className="inline-flex items-center gap-0.5 text-[10px] font-semibold text-amber-300">
            <UserRoundCheck size={10} aria-hidden="true" />{attention}
          </span>
        )}
        <SortableContext items={items.map(i => i.id)} strategy={verticalListSortingStrategy}>
          <div ref={setNodeRef} className="flex min-h-[3rem] w-full flex-col items-center gap-1.5 overflow-y-auto px-0.5 pb-1">
            {items.map(item => <CollapsedCard key={item.id} item={item} sprintId={sprintId} />)}
          </div>
        </SortableContext>
      </section>
    )
  }

  return (
    <section aria-labelledby={headingId}
      className={`flex w-[85vw] max-w-[20rem] flex-shrink-0 snap-start flex-col panel transition-shadow motion-reduce:transition-none sm:w-72 lg:w-auto lg:min-w-[15.5rem] lg:max-w-none lg:flex-1 ${dropRing}`}>
      <header className="sticky top-0 z-10 flex h-11 items-center gap-1.5 rounded-t-xl border-b border-surface-700 bg-surface-900/95 pl-1.5 pr-2.5 backdrop-blur">
        <button type="button" data-column-toggle={id} onClick={() => onToggleCollapse(id)} aria-expanded={true}
          aria-label={t('kanbanBoard.column.collapse', { column: title })} title={t('kanbanBoard.column.collapse', { column: title })}
          className="btn-ghost h-7 w-7 flex-shrink-0 rounded-md p-0">
          <ChevronsLeft size={14} aria-hidden="true" />
        </button>
        <Icon size={13} className={`flex-shrink-0 ${tone}`} aria-hidden="true" />
        <h3 id={headingId} className="truncate text-[13px] font-semibold text-gray-100">{title}</h3>
        <ColumnCount shown={items.length} total={total} filtered={filtered} />
        {attention > 0 && (
          <span role="img" aria-label={attentionLabel} title={attentionLabel}
            className="ml-auto inline-flex flex-shrink-0 items-center gap-1 rounded-md bg-amber-900/40 px-1.5 text-[11px] font-semibold leading-5 text-amber-200 ring-1 ring-inset ring-amber-700/50">
            <UserRoundCheck size={11} aria-hidden="true" />{attention}
          </span>
        )}
      </header>

      <SortableContext items={items.map(i => i.id)} strategy={verticalListSortingStrategy}>
        <div ref={setNodeRef} className="flex min-h-[7rem] flex-1 flex-col gap-2 p-2">
          {items.length === 0 ? (
            <div className={`flex flex-1 flex-col items-center justify-center gap-1.5 rounded-lg border border-dashed px-3 py-6 text-center text-xs transition-colors motion-reduce:transition-none ${
              isOver ? 'border-brand-500 bg-brand-950/40 text-brand-200' : 'border-surface-700 text-gray-500'}`}>
              <Inbox size={16} aria-hidden="true" />
              <span>{filtered ? t('kanbanBoard.column.emptyFiltered') : t('kanban.empty')}</span>
              {activeItemId !== null && <span className="text-[11px] text-gray-400">{t('kanbanBoard.column.dropHere')}</span>}
            </div>
          ) : (
            items.map(item => (
              <TaskCard
                key={item.id}
                item={item}
                sprintId={sprintId}
                density={props.density}
                onStatusChange={props.onStatusChange}
                updating={props.updatingId === item.id}
                isDragging={activeItemId === item.id}
                evidenceRun={props.evidenceRuns.get(item.id)}
                actions={props.cardActions(item)}
                keyboardHintId={props.keyboardHintId}
              />
            ))
          )}
        </div>
      </SortableContext>
    </section>
  )
}
