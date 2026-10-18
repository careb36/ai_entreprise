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
  const companyId = useAppStore(state => state.companyId)
  const setActiveSprint = useAppStore(state => state.setActiveSprint)
  const sprintId = parseRouteId(id)
  const boardQuery = useSprintBoard(sprintId)
  const notFound = boardQuery.error instanceof ApiError && boardQuery.error.status === 404
  const noAccess = boardQuery.error instanceof ApiError && boardQuery.error.status === 403
  const terminalMessage = noAccess
    ? t('workspace.noAccess', { id, defaultValue: 'You no longer have access to sprint #{{id}}.' })
    : notFound
      ? t('workspace.notFound', { id, defaultValue: 'Sprint #{{id}} was not found.' })
      : null

  const isSSEConnected = useAgentStore(s => s.isConnected)
  const messageCount = useAgentStore(s => s.messages.length)

  useEffect(() => {
    if (sprintId !== null && boardQuery.data && !boardQuery.isError && boardQuery.data.sprint.company_id === companyId) {
      setActiveSprint(sprintId)
    }
  }, [sprintId, boardQuery.data, boardQuery.isError, companyId, setActiveSprint])

  if (sprintId === null || (boardQuery.isError && !boardQuery.data)) {
    const message = sprintId === null
      ? t('workspace.invalidId', { id, defaultValue: 'Invalid sprint ID: {{id}}.' })
      : terminalMessage ?? t('workspace.loadError', { id, defaultValue: 'Failed to load sprint #{{id}}.' })
    return (
      <div className="page-container">
        <div role="alert" className="alert-error mb-4">
          {message}
        </div>
        {sprintId !== null && !notFound && !noAccess && (
          <button className="btn btn-ghost mr-3" onClick={() => boardQuery.refetch()}>{t('common.retry', { defaultValue: 'Retry' })}</button>
        )}
        <Link to="/dashboard" className="btn btn-ghost">{t('nav.sprints', { defaultValue: 'Sprints' })}</Link>
      </div>
    )
  }

  if (!boardQuery.data) {
    return <div role="status" className="page-container">{t('common.loading', { defaultValue: 'Loading...' })}</div>
  }

  const currentId = String(sprintId)

  return (
    <div className="min-h-[calc(100vh-3.5rem)] xl:h-[calc(100vh-3.5rem)] overflow-x-auto overflow-y-auto xl:overflow-y-hidden bg-surface-950 text-white p-3 pb-24 xl:pb-3">
      <div className="card min-h-full xl:h-full min-w-[20rem] overflow-visible xl:overflow-hidden flex flex-col">
        {boardQuery.data.sprint.company_id !== companyId && (
          <div role="alert" className="alert-error p-3">
            {t('workspace.otherCompany', { defaultValue: 'This sprint belongs to another company. This view is read-only.' })}
            {' '}
            <Link to="/companies/select" className="underline">{t('workspace.selectCompany', { defaultValue: 'Select company' })}</Link>
          </div>
        )}
        {boardQuery.isError && (
          <div role="alert" className="alert-error p-3">
            {terminalMessage ?? t('workspace.refreshFailed', { defaultValue: 'Failed to refresh this sprint. Showing the last loaded view.' })}
            {!noAccess && !notFound && (
              <button className="btn btn-ghost ml-3" onClick={() => boardQuery.refetch()}>{t('common.retry', { defaultValue: 'Retry' })}</button>
            )}
          </div>
        )}
        <div className="min-h-11 border-b border-surface-700 bg-surface-900 px-4 py-2 flex items-center gap-3 flex-wrap">
          <MonitorPlay size={15} className="text-brand-400" />
          <h2 className="text-sm font-semibold text-gray-200">{t('workspace.glassroomTitle', { defaultValue: 'Glassroom — Live Agent Stream' })}</h2>
          <span className="text-xs text-gray-500">{t('workspace.sprintLabel', { id: currentId, defaultValue: 'Sprint #{{id}}' })}</span>
          <span className={`w-1.5 h-1.5 rounded-full ${isSSEConnected ? 'bg-green-400 animate-pulse' : 'bg-red-500'}`} />

          {messageCount > 0 && (
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-brand-700/40 text-brand-300 font-medium">
              {messageCount}
            </span>
          )}

          <span className="ml-auto text-xs text-gray-500 hidden sm:flex items-center gap-1">
            <Radio size={11} /> {t('workspace.aiAgentsActive', { defaultValue: 'AI Agents Active' })}
          </span>

          <Link to="/dashboard" className="btn-ghost btn-sm flex items-center gap-1.5">
            <ArrowLeft size={13} />
            <span>{t('workspace.sprintBoard', { defaultValue: 'Sprint Board' })}</span>
          </Link>
        </div>

        <div className="flex-1 min-h-0">
          <GlassRoom sprintId={currentId} />
        </div>
      </div>
    </div>
  )
}
