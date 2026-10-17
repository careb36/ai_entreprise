import React, { useEffect } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { MonitorPlay, ArrowLeft, Radio } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import { useAgentStore } from '../store/agentStore'
import GlassRoom from '../components/GlassRoom'
import { useSprintBoard } from '../hooks/useQueries'
import { ApiError } from '../services/api'
import { parseRouteId } from '../lib/routeId'

export const SprintWorkspace: React.FC = () => {
  const { id } = useParams<{ id: string }>()
  const { t } = useTranslation()
  const setActiveSprint = useAppStore(state => state.setActiveSprint)
  const sprintId = parseRouteId(id)
  const boardQuery = useSprintBoard(sprintId)

  const isSSEConnected = useAgentStore(s => s.isConnected)
  const messageCount = useAgentStore(s => s.messages.length)

  useEffect(() => {
    if (sprintId !== null && boardQuery.data && !boardQuery.isError) {
      setActiveSprint(sprintId)
    }
  }, [sprintId, boardQuery.data, boardQuery.isError, setActiveSprint])

  if (sprintId === null || boardQuery.isError) {
    const notFound = boardQuery.error instanceof ApiError && boardQuery.error.status === 404
    return (
      <div className="page-container">
        <div role="alert" className="alert-error mb-4">
          {t(sprintId === null ? 'workspace.invalidId' : notFound ? 'workspace.notFound' : 'workspace.loadError', { id })}
        </div>
        {sprintId !== null && !notFound && (
          <button className="btn btn-ghost mr-3" onClick={() => boardQuery.refetch()}>{t('common.retry')}</button>
        )}
        <Link to="/dashboard" className="btn btn-ghost">{t('nav.dashboard')}</Link>
      </div>
    )
  }

  if (!boardQuery.data) {
    return <div role="status" className="page-container">{t('common.loading')}</div>
  }

  const currentId = String(sprintId)

  return (
    <div className="min-h-[calc(100vh-3.5rem)] xl:h-[calc(100vh-3.5rem)] overflow-x-auto overflow-y-auto xl:overflow-y-hidden bg-surface-950 text-white p-3 pb-24 xl:pb-3">
      <div className="card min-h-full xl:h-full min-w-[20rem] overflow-visible xl:overflow-hidden flex flex-col">
        <div className="min-h-11 border-b border-surface-700 bg-surface-900 px-4 py-2 flex items-center gap-3 flex-wrap">
          <MonitorPlay size={15} className="text-brand-400" />
          <h2 className="text-sm font-semibold text-gray-200">{t('workspace.glassroomTitle')}</h2>
          <span className="text-xs text-gray-500">Sprint #{currentId}</span>
          <span className={`w-1.5 h-1.5 rounded-full ${isSSEConnected ? 'bg-green-400 animate-pulse' : 'bg-red-500'}`} />

          {messageCount > 0 && (
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-brand-700/40 text-brand-300 font-medium">
              {messageCount}
            </span>
          )}

          <span className="ml-auto text-xs text-gray-500 hidden sm:flex items-center gap-1">
            <Radio size={11} /> {t('workspace.aiAgentsActive')}
          </span>

          <Link to="/dashboard" className="btn-ghost btn-sm flex items-center gap-1.5">
            <ArrowLeft size={13} />
            <span>{t('workspace.sprintBoard')}</span>
          </Link>
        </div>

        <div className="flex-1 min-h-0">
          <GlassRoom sprintId={currentId} />
        </div>
      </div>
    </div>
  )
}
