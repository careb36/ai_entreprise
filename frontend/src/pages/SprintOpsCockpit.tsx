import { useEffect } from 'react'
import { Link, useParams, Navigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  ArrowLeft,
  LayoutDashboard,
  MonitorPlay,
} from 'lucide-react'
import KanbanBoard from '../components/KanbanBoard'
import { useAppStore } from '../store/useAppStore'
import { useSprintBoard } from '../hooks/useQueries'
import { ApiError } from '../services/api'

export default function SprintOpsCockpit() {
  const { id } = useParams<{ id: string }>()
  const { t } = useTranslation()
  const activeSprintId = useAppStore(state => state.activeSprintId)
  const setActiveSprint = useAppStore(state => state.setActiveSprint)

  const idNum = id ? parseInt(id, 10) : null
  const { isError, error } = useSprintBoard(idNum)

  useEffect(() => {
    if (id && parseInt(id, 10) !== activeSprintId) {
      setActiveSprint(parseInt(id, 10))
    }
  }, [id, activeSprintId, setActiveSprint])

  // Sprint no longer exists — clear stale id and go home
  if (isError && error instanceof ApiError && error.status === 404) {
    if (activeSprintId === idNum) setActiveSprint(null)
    return <Navigate to="/dashboard" replace />
  }

  const currentId = id || activeSprintId?.toString() || '1'

  return (
    <div className="min-h-[calc(100vh-3.5rem)] xl:h-[calc(100vh-3.5rem)] bg-surface-950 text-white p-4">
      <div className="mx-auto flex h-full max-w-[1920px] min-w-[20rem] flex-col gap-4">
        <div className="card flex flex-wrap items-center gap-3 border-b border-surface-700 bg-surface-900 px-4 py-3">
          <LayoutDashboard size={16} className="text-brand-400" />
          <div className="min-w-0">
            <h1 className="text-sm font-semibold text-gray-100">{t('workspace.operationsTitle')}</h1>
            <p className="text-xs text-gray-500">{t('workspace.operationsSubtitle')}</p>
          </div>
          <span className="rounded-full border border-surface-700 bg-surface-950 px-2 py-1 text-[11px] text-gray-400">
            Sprint #{currentId}
          </span>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <Link to={`/workspace/${currentId}/live`} className="btn-ghost btn-sm flex items-center gap-1.5">
              <MonitorPlay size={13} />
              <span>{t('workspace.openLiveRoom')}</span>
            </Link>
            <Link to="/dashboard" className="btn-ghost btn-sm flex items-center gap-1.5">
              <ArrowLeft size={13} />
              <span>{t('workspace.backToDashboard')}</span>
            </Link>
          </div>
        </div>

        {/* Kanban board — full width */}
        <div className="card flex flex-1 min-h-[34rem] flex-col overflow-hidden xl:min-h-0">
          <div className="border-b border-surface-700 bg-surface-900 px-4 py-3">
            <div className="flex items-center gap-2">
              <LayoutDashboard size={15} className="text-brand-400" />
              <h2 className="text-sm font-semibold text-gray-200">{t('workspace.boardOpsTitle')}</h2>
            </div>
            <p className="mt-1 text-xs text-gray-500">{t('workspace.boardOpsHint')}</p>
          </div>
          <div className="flex-1 min-h-0">
            <KanbanBoard sprintId={currentId} />
          </div>
        </div>
      </div>
    </div>
  )
}
