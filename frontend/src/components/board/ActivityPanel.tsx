import { useEffect, useMemo, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { MessageSquareQuote, X } from 'lucide-react'
import type { BacklogItem } from '../../services/api'
import { latestMessages, relativeTime } from '../../lib/kanban'
import { RoleAvatar } from './RoleAvatar'

/**
 * The board's activity panel: what agents and the system last said on the
 * sprint's tasks. Every entry is a message, so it is marked as a claim: what
 * was observed is on the cards, under Observed, never here.
 */
export function ActivityPanel({ open, onClose, columns, sprintId, panelId }: {
  open: boolean
  onClose: () => void
  columns: Record<string, BacklogItem[]>
  sprintId: number
  panelId: string
}) {
  const { t, i18n } = useTranslation()
  const headingRef = useRef<HTMLHeadingElement>(null)
  const entries = useMemo(() => latestMessages(columns), [columns])

  useEffect(() => {
    if (open) headingRef.current?.focus()
  }, [open])

  if (!open) return null

  return (
    <aside
      id={panelId}
      aria-labelledby={`${panelId}-title`}
      onKeyDown={event => { if (event.key === 'Escape') onClose() }}
      className="fixed inset-y-0 right-0 z-40 flex w-full max-w-sm flex-col border-l border-surface-700 bg-surface-900 shadow-card-md motion-safe:animate-slide-in lg:static lg:z-auto lg:max-h-[calc(100dvh-14rem)] lg:w-80 lg:max-w-none lg:flex-shrink-0 lg:rounded-r-card lg:shadow-none"
    >
      <div className="flex h-12 flex-shrink-0 items-center gap-2 border-b border-surface-700 px-4">
        <h3 id={`${panelId}-title`} ref={headingRef} tabIndex={-1} className="text-sm font-semibold text-gray-100">
          {t('kanbanBoard.activity.title')}
        </h3>
        <button type="button" onClick={onClose} aria-label={t('kanbanBoard.activity.close')}
          className="btn-ghost ml-auto h-7 w-7 rounded-md p-0">
          <X size={14} aria-hidden="true" />
        </button>
      </div>
      <p className="flex flex-shrink-0 items-start gap-1.5 border-b border-surface-700/70 bg-surface-950/40 px-4 py-2 text-[11px] leading-relaxed text-gray-400">
        <MessageSquareQuote size={12} aria-hidden="true" className="mt-0.5 flex-shrink-0" />
        {t('kanbanBoard.activity.claimNote')}
      </p>
      {entries.length === 0 ? (
        <p className="px-4 py-8 text-center text-xs text-gray-500">{t('kanbanBoard.activity.empty')}</p>
      ) : (
        <ol className="min-h-0 flex-1 divide-y divide-surface-700/60 overflow-y-auto px-4">
          {entries.map(entry => {
            const when = relativeTime(entry.createdAt, i18n.language)
            return (
              <li key={entry.key} className="flex gap-2.5 py-3">
                <RoleAvatar role={entry.author} />
                <div className="min-w-0 flex-1">
                  <p className="text-xs leading-relaxed text-gray-300">
                    <span className="font-semibold text-gray-100">{t(`teamRoles.${entry.author}`, { defaultValue: entry.author })}</span>
                    {' '}{t('kanbanBoard.activity.on')}{' '}
                    <Link to={`/sprints/${sprintId}/tasks/${entry.item.id}`} className="text-brand-300 hover:underline">
                      {entry.item.title}
                    </Link>
                  </p>
                  <p className="mt-1 line-clamp-3 rounded-md border border-dashed border-surface-600 px-2 py-1 text-xs text-gray-400">
                    <span className="mr-1 text-[10px] font-semibold uppercase tracking-wider text-gray-500">{t('kanbanBoard.activity.claim')}</span>
                    {entry.text}
                  </p>
                  {when && <time dateTime={entry.createdAt} className="mt-1 block text-[10px] text-gray-500">{when}</time>}
                </div>
              </li>
            )
          })}
        </ol>
      )}
    </aside>
  )
}
