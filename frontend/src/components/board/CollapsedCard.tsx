import type { PointerEventHandler } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { AlertTriangle, CheckCircle2, Circle, Lock, XCircle } from 'lucide-react'
import type { BacklogItem } from '../../services/api'
import { needsPerson } from '../../lib/kanban'

type Signal = 'held' | 'blocked' | 'refuted' | 'verified' | 'none'

function signalOf(item: BacklogItem): Signal {
  if (needsPerson(item)) return 'held'
  if (item.status === 'blocked') return 'blocked'
  if (item.observed?.verdict === 'refuted') return 'refuted'
  if (item.observed?.verdict === 'verified') return 'verified'
  return 'none'
}

const PRESENTATION: Record<Signal, { className: string; Icon: typeof Circle }> = {
  held: { className: 'bg-amber-900/60 text-amber-200 ring-amber-600/60', Icon: Lock },
  blocked: { className: 'bg-red-900/60 text-red-200 ring-red-700/60', Icon: AlertTriangle },
  refuted: { className: 'bg-red-950/70 text-red-200 ring-red-800/60', Icon: XCircle },
  verified: { className: 'bg-emerald-950/70 text-emerald-200 ring-emerald-800/60', Icon: CheckCircle2 },
  none: { className: 'bg-surface-800 text-gray-300 ring-surface-600', Icon: Circle },
}

/**
 * A task in a folded column: its number and one icon for what most needs
 * attention, still a link to the task and still draggable out of the column.
 * The title and the signal are in its accessible name and its tooltip.
 */
export function CollapsedCard({ item, sprintId }: { item: BacklogItem; sprintId: number }) {
  const { t } = useTranslation()
  const { listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: item.id })
  // The pointer only: Enter on the chip opens the task, as on any link.
  const onPointerDown = listeners?.onPointerDown as PointerEventHandler<HTMLAnchorElement> | undefined
  const signal = signalOf(item)
  const { className, Icon } = PRESENTATION[signal]
  const label = signal === 'none'
    ? t('kanbanBoard.chip.label', { id: item.id, title: item.title })
    : t('kanbanBoard.chip.labelWithSignal', { id: item.id, title: item.title, signal: t(`kanbanBoard.chip.signal.${signal}`) })
  return (
    <Link
      ref={setNodeRef}
      to={`/sprints/${sprintId}/tasks/${item.id}`}
      onPointerDown={onPointerDown}
      aria-label={label}
      title={label}
      style={{ transform: CSS.Transform.toString(transform), transition, touchAction: 'none' }}
      className={`inline-flex h-6 w-10 flex-shrink-0 items-center justify-center gap-0.5 rounded text-[10px] font-semibold tabular-nums ring-1 ring-inset ${className} ${isDragging ? 'opacity-40' : ''} hover:brightness-125`}
    >
      <Icon size={9} aria-hidden="true" />
      <span aria-hidden="true">{item.id}</span>
    </Link>
  )
}
