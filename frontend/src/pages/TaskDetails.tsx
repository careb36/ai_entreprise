import { useParams, Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, User, Flag, MessageSquare, Clock } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import { useSprintBoard } from '../hooks/useQueries'
import { SkeletonBlock, SkeletonLine } from '../components/Skeleton'
import type { BacklogItem } from '../services/api'

const PRIORITY_STYLES: Record<BacklogItem['priority'], string> = {
  critical: 'bg-red-900/40 text-red-300 border-red-700/40',
  high:     'bg-orange-900/40 text-orange-300 border-orange-700/40',
  medium:   'bg-blue-900/40 text-blue-300 border-blue-700/40',
  low:      'bg-gray-800 text-gray-400 border-surface-600',
}

const STATUS_STYLES: Record<BacklogItem['status'], string> = {
  todo:        'bg-surface-800 text-gray-300 border-surface-700',
  in_progress: 'bg-brand-900/40 text-brand-300 border-brand-700/40',
  in_review:   'bg-yellow-900/40 text-yellow-300 border-yellow-700/40',
  done:        'bg-emerald-900/40 text-emerald-300 border-emerald-700/40',
  blocked:     'bg-red-900/40 text-red-300 border-red-700/40',
}

function formatDate(iso: string) {
  try {
    return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  } catch {
    return iso
  }
}

export default function TaskDetails() {
  const { t } = useTranslation()
  const { taskId, sprintId: paramSprintId } = useParams<{ taskId: string; sprintId: string }>()
  const { activeSprintId } = useAppStore()

  const resolvedSprintId = paramSprintId ? Number(paramSprintId) : activeSprintId
  const { data: board, isLoading, isError } = useSprintBoard(resolvedSprintId)

  const task: BacklogItem | undefined = board
    ? Object.values(board.columns).flat().find(item => String(item.id) === taskId)
    : undefined

  const backTo = resolvedSprintId ? `/workspace/${resolvedSprintId}` : '/dashboard'

  if (isLoading) {
    return (
      <div className="max-w-2xl mx-auto py-8 px-4 space-y-5">
        <SkeletonLine className="w-32 h-4" />
        <SkeletonBlock className="h-8 w-3/4" />
        <SkeletonBlock className="h-24" />
        <div className="grid grid-cols-2 gap-4">
          <SkeletonBlock className="h-12" />
          <SkeletonBlock className="h-12" />
        </div>
      </div>
    )
  }

  if (isError || !task) {
    return (
      <div className="max-w-2xl mx-auto py-16 px-4 text-center">
        <p className="text-gray-400 mb-4">{t('common.noData')}</p>
        <Link to={backTo} className="btn btn-ghost text-sm">
          <ArrowLeft size={14} className="mr-1" /> {t('common.back')}
        </Link>
      </div>
    )
  }

  return (
    <div className="max-w-2xl mx-auto py-8 px-4">
      {/* Back link */}
      <Link to={backTo} className="flex items-center gap-1.5 text-sm text-gray-400 hover:text-white transition-colors mb-6">
        <ArrowLeft size={14} />
        {t('common.back')}
      </Link>

      {/* Header */}
      <div className="mb-6">
        <div className="flex flex-wrap items-center gap-2 mb-2">
          <span className={`px-2 py-0.5 rounded-md border text-xs font-semibold uppercase tracking-wide ${STATUS_STYLES[task.status]}`}>
            {t(`kanban.status.${task.status}`, task.status)}
          </span>
          <span className={`px-2 py-0.5 rounded-md border text-xs font-medium ${PRIORITY_STYLES[task.priority]}`}>
            <Flag size={10} className="inline mr-1" />
            {t(`kanban.${task.priority}`, task.priority)}
          </span>
        </div>
        <h1 className="text-xl font-bold text-white leading-snug">{task.title}</h1>
      </div>

      {/* Description */}
      {task.description && (
        <div className="card p-4 mb-4">
          <p className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-2">{t('generate.story')}</p>
          <p className="text-gray-300 text-sm leading-relaxed whitespace-pre-wrap">{task.description}</p>
        </div>
      )}

      {/* Meta */}
      <div className="grid grid-cols-2 gap-4 mb-4">
        <div className="card p-3">
          <p className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-1 flex items-center gap-1">
            <User size={10} /> {t('kanban.assignee')}
          </p>
          <p className="text-sm text-gray-200">{task.assignee || t('common.na')}</p>
        </div>
        <div className="card p-3">
          <p className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-1 flex items-center gap-1">
            <Clock size={10} /> {t('support.updated')}
          </p>
          <p className="text-sm text-gray-200">{formatDate(task.updated_at)}</p>
        </div>
      </div>

      {/* Comments */}
      {task.comments.length > 0 && (
        <div className="card p-4">
          <p className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-3 flex items-center gap-1">
            <MessageSquare size={10} /> {t('support.timeline')} ({task.comments.length})
          </p>
          <div className="space-y-3">
            {task.comments.map((c, i) => (
              <div key={i} className="flex gap-3 text-sm">
                <div className="w-6 h-6 rounded-full bg-brand-600/30 border border-brand-600/40 flex items-center justify-center text-[10px] font-bold text-brand-300 shrink-0 mt-0.5">
                  {c.author.slice(0, 1).toUpperCase()}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className="text-xs font-medium text-gray-300">{c.author}</span>
                    <span className="text-[11px] text-gray-600">{formatDate(c.created_at)}</span>
                  </div>
                  <p className="text-gray-400 text-xs leading-relaxed">{c.text}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
