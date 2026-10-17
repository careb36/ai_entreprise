import React, { useEffect } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { MonitorPlay, ArrowLeft, Radio } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import { useAgentStore } from '../store/agentStore'
import GlassRoom from '../components/GlassRoom'

export const SprintWorkspace: React.FC = () => {
  const { id } = useParams<{ id: string }>()
  const { t } = useTranslation()
  const activeSprintId = useAppStore(state => state.activeSprintId)
  const setActiveSprint = useAppStore(state => state.setActiveSprint)

  const isSSEConnected = useAgentStore(s => s.isConnected)
  const messageCount = useAgentStore(s => s.messages.length)

  // Sync URL param with store if needed
  useEffect(() => {
    if (id && parseInt(id) !== activeSprintId) {
      setActiveSprint(parseInt(id))
    }
  }, [id, activeSprintId, setActiveSprint])

  const currentId = activeSprintId?.toString() || id || '1'

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

          <Link to="/" className="btn-ghost btn-sm flex items-center gap-1.5">
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
