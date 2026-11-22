import type { KeyboardEvent, ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { GripVertical, Hourglass, MessageSquare } from 'lucide-react'
import type { AgentRunListItem, BacklogItem } from '../../services/api'
import { MOVE_TARGETS, adjacentStatus, needsPerson, relativeTime, type BoardStatus } from '../../lib/kanban'
import type { BoardDensity } from '../../hooks/useBoardPreferences'
import { ObservedSignals, ReportedSignals } from './CardSignals'
import { RoleAvatar } from './RoleAvatar'

const PRIORITY_DOT: Record<string, string> = {
  critical: 'bg-priority-critical',
  high: 'bg-priority-high',
  medium: 'bg-priority-medium',
  low: 'bg-priority-low',
}

/** The left edge says what needs attention; the words on the card say it too. */
function accentOf(item: BacklogItem): string {
  if (item.status === 'blocked') return 'border-l-red-500'
  if (item.status === 'discarded') return 'border-l-surface-600'
  if (needsPerson(item)) return 'border-l-amber-400'
  if (item.status === 'in_review') return 'border-l-yellow-500/60'
  if (item.status === 'done') return 'border-l-emerald-600/60'
  return 'border-l-surface-600'
}

export function TaskCard({
  item, sprintId, density, onStatusChange, updating, isDragging = false, evidenceRun, actions, keyboardHintId,
}: {
  item: BacklogItem
  sprintId: number
  density: BoardDensity
  /** `announce`: the move came from the keyboard, so the board says where the card went. */
  onStatusChange: (id: number, status: BoardStatus, announce?: boolean) => void
  updating: boolean
  isDragging?: boolean
  evidenceRun?: AgentRunListItem
  actions?: ReactNode
  keyboardHintId: string
}) {
  const { t, i18n } = useTranslation()
  const { attributes, listeners, setNodeRef, transform, transition } = useSortable({ id: item.id })
  const compact = density === 'compact'
  const titleId = `card-title-${item.id}`
  const waitingFor = item.waiting_for?.length ?? 0
  const waitingLabel = t('kanban.waitsFor', { count: waitingFor, defaultValue: 'Waits for {{count}} tasks' })
  const comments = item.comments?.length ?? 0
  const updated = relativeTime(item.updated_at, i18n.language)
  const priority = t(`kanbanBoard.priority.${item.priority}`, { defaultValue: item.priority })
  const role = item.assignee ? t(`teamRoles.${item.assignee}`, { defaultValue: item.assignee }) : null

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (!event.altKey || updating || (event.target as HTMLElement).tagName === 'SELECT') return
    const step = event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1 : 0
    if (!step) return
    event.preventDefault()
    const next = adjacentStatus(item.status, step)
    if (next) onStatusChange(item.id, next, true)
  }

  return (
    <article
      ref={setNodeRef}
      aria-labelledby={titleId}
      onKeyDown={onKeyDown}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={`card group relative select-none border-l-[3px] ${accentOf(item)} ${compact ? 'px-2.5 py-2' : 'p-3'}
        transition-[border-color,background-color,opacity] duration-150 hover:border-surface-600 hover:bg-surface-750
        focus-within:border-brand-500/70 motion-reduce:transition-none ${isDragging ? 'opacity-40' : ''}`}
    >
      <div className="flex items-start gap-1.5">
        <button
          type="button"
          {...attributes} {...listeners}
          className="-ml-1 mt-px flex-shrink-0 cursor-grab rounded p-0.5 text-gray-600 opacity-60 transition-opacity hover:text-gray-300 focus-visible:opacity-100 group-hover:opacity-100 active:cursor-grabbing motion-reduce:transition-none"
          aria-label={t('kanban.dragToReorder')}
        >
          <GripVertical size={14} aria-hidden="true" />
        </button>
        <span className="mt-[7px] flex-shrink-0" title={t('kanbanBoard.card.priorityTitle', { priority })}>
          <span aria-hidden="true" className={`block h-2 w-2 rounded-full ${PRIORITY_DOT[item.priority] ?? 'bg-gray-500'}`} />
          <span className="sr-only">{t('kanbanBoard.card.priorityTitle', { priority })}</span>
        </span>
        <Link
          id={titleId}
          to={`/sprints/${sprintId}/tasks/${item.id}`}
          aria-describedby={keyboardHintId}
          aria-keyshortcuts="Alt+ArrowLeft Alt+ArrowRight"
          className={`min-w-0 flex-1 break-words font-medium leading-snug text-gray-100 hover:text-brand-300 hover:underline ${compact ? 'text-[13px]' : 'text-sm'}`}
        >
          {item.title}
        </Link>
        <span className="flex-shrink-0 pt-px text-[11px] tabular-nums text-gray-500">#{item.id}</span>
      </div>

      <div className={`space-y-1.5 ${compact ? 'mt-1.5' : 'mt-2'} pl-5`}>
        <ObservedSignals item={item} evidenceRun={evidenceRun} compact={compact} />
        <ReportedSignals item={item} compact={compact} />
        {(waitingFor > 0 || item.kind === 'acceptance_tests') && (
          <div className="flex flex-wrap items-center gap-1">
            {waitingFor > 0 && (
              <span className="inline-flex items-center gap-0.5 rounded-md bg-amber-900/30 px-1.5 py-0.5 text-[11px] font-medium text-amber-300 ring-1 ring-inset ring-amber-700/40"
                role="img" aria-label={waitingLabel} title={waitingLabel}>
                <Hourglass size={10} aria-hidden="true" />{waitingFor}
              </span>
            )}
            {item.kind === 'acceptance_tests' && (
              <span className="rounded-md bg-violet-900/40 px-1.5 py-0.5 text-[11px] font-medium text-violet-300 ring-1 ring-inset ring-violet-700/40">
                {t('kanban.acceptanceTests')}
              </span>
            )}
          </div>
        )}
      </div>

      <div className={`flex items-center gap-2 pl-5 ${compact ? 'mt-1.5' : 'mt-2.5 border-t border-surface-700/70 pt-2'}`}>
        <span className="flex min-w-0 items-center gap-1.5"
          title={role ? t('kanbanBoard.card.assignee', { role }) : t('kanbanBoard.card.unassigned')}>
          <RoleAvatar role={item.assignee} size={compact ? 'sm' : 'md'} />
          <span className={compact ? 'sr-only' : 'truncate text-xs text-gray-300'}>
            {role ?? t('kanbanBoard.card.unassigned')}
          </span>
        </span>
        <span className="ml-auto flex flex-shrink-0 items-center gap-2 text-[11px] text-gray-500">
          {updated && (
            <time dateTime={item.updated_at} title={t('kanbanBoard.card.updated', { time: updated })}>
              <span className="sr-only">{t('kanbanBoard.card.updated', { time: updated })}</span>
              <span aria-hidden="true">{updated}</span>
            </time>
          )}
          {comments > 0 && (
            <span className="inline-flex items-center gap-0.5" title={t('kanbanBoard.card.messages', { count: comments })}>
              <MessageSquare size={11} aria-hidden="true" />
              <span aria-hidden="true">{comments}</span>
              <span className="sr-only">{t('kanbanBoard.card.messages', { count: comments })}</span>
            </span>
          )}
        </span>
      </div>

      {!compact && (
        <select
          value={item.status}
          disabled={updating}
          onChange={e => onStatusChange(item.id, e.target.value as BoardStatus)}
          className="select mt-2 h-7 w-full border-surface-700 bg-surface-900/60 py-0 text-[11px] text-gray-300 disabled:opacity-50"
          aria-label={t('kanban.changeStatus')}
        >
          {(item.status === 'discarded' ? [...MOVE_TARGETS, item.status] : MOVE_TARGETS).map(s => (
            <option key={s} value={s}>{t(`kanban.status.${s}`, s)}</option>
          ))}
        </select>
      )}
      {!compact && actions}
    </article>
  )
}

/** The card under the pointer while it is dragged. */
export function CardPreview({ item }: { item: BacklogItem }) {
  return (
    <div className="card w-64 rotate-1 border-l-[3px] border-l-brand-500 p-3 shadow-glow motion-reduce:rotate-0">
      <p className="text-sm font-medium text-white">{item.title}</p>
      <div className="mt-2 flex items-center gap-1.5 text-xs text-gray-400">
        <RoleAvatar role={item.assignee} size="sm" />
        <span className="truncate">{item.assignee}</span>
      </div>
    </div>
  )
}
