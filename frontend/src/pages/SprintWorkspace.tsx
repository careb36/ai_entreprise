import React, { useState, useEffect } from 'react'
import { useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { PanelLeftClose, PanelLeftOpen, MonitorPlay } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import ControlPanel from '../components/ControlPanel'
import KanbanBoard from '../components/KanbanBoard'
import GlassRoom from '../components/GlassRoom'

export const SprintWorkspace: React.FC = () => {
  const { id } = useParams<{ id: string }>()
  const { t } = useTranslation()
  const activeSprintId = useAppStore(state => state.activeSprintId)
  const setActiveSprint = useAppStore(state => state.setActiveSprint)
  
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [glassroomOpen, setGlassroomOpen] = useState(true)

  // Sync URL param with store if needed
  useEffect(() => {
    if (id && parseInt(id) !== activeSprintId) {
      setActiveSprint(parseInt(id))
    }
  }, [id, activeSprintId, setActiveSprint])

  function handleSprintStart(newSprintId: number) {
    setActiveSprint(newSprintId)
  }

  const currentId = activeSprintId?.toString() || id || '1'

  return (
    <div className="flex h-[calc(100vh-3.5rem)] overflow-hidden bg-surface-950 text-white">

      {/* ── Sidebar ─────────────────────────────────────────────────── */}
      <aside
        className={`
          flex-shrink-0 border-r border-surface-700 bg-surface-900 overflow-y-auto
          transition-all duration-200
          ${sidebarOpen ? 'w-80' : 'w-0 overflow-hidden'}
          /* On mobile the sidebar is an overlay when open */
          md:relative absolute md:translate-x-0 z-10
          ${sidebarOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0'}
        `}
        aria-label="Control panel"
      >
        <div className="p-4 min-w-[20rem]">
          {/* Active Sprint indicator */}
          <div className="mb-4 pb-4 border-b border-surface-700">
            <p className="text-xs font-bold text-gray-500 uppercase tracking-wider mb-2">
              {t('workspace.activeContext')}
            </p>
            <div className="flex items-center justify-between bg-surface-800 px-3 py-2 rounded-lg border border-surface-700">
              <span className="text-brand-400 font-mono text-sm">Sprint #{currentId}</span>
              <span className="status-dot-pulse" />
            </div>
          </div>

          <ControlPanel onSprintStart={handleSprintStart} />
        </div>
      </aside>

      {/* Mobile overlay backdrop */}
      {sidebarOpen && (
        <div
          className="md:hidden absolute inset-0 bg-black/50 z-[9]"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* ── Main area ───────────────────────────────────────────────── */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden bg-surface-950">

        {/* Toolbar */}
        <div className="flex items-center gap-2 px-4 h-10 border-b border-surface-700 bg-surface-900 flex-shrink-0">
          <button
            onClick={() => setSidebarOpen(v => !v)}
            className="btn-ghost btn-icon"
            aria-label={sidebarOpen ? t('workspace.collapseSidebar') : t('workspace.expandSidebar')}
            title={sidebarOpen ? t('workspace.collapseSidebar') : t('workspace.expandSidebar')}
          >
            {sidebarOpen ? <PanelLeftClose size={16} /> : <PanelLeftOpen size={16} />}
          </button>

          <span className="text-xs text-gray-500 hidden sm:flex items-center gap-1">
            <span className="status-dot-active" />
            {t('workspace.aiAgentsActive')}
          </span>

          <button
            onClick={() => setGlassroomOpen(v => !v)}
            className="btn-ghost btn-sm ml-auto flex items-center gap-1.5"
            title="Toggle Glassroom"
          >
            <MonitorPlay size={14} />
            <span className="hidden sm:inline text-xs">{t('workspace.glassroomTitle')}</span>
          </button>
        </div>

        {/* Kanban + Glassroom area */}
        <div className="flex-1 min-h-0 flex flex-col p-3 gap-3 overflow-hidden">
          {/* Board — grows to fill available space, scrolls internally */}
          <div className={`min-h-0 card overflow-hidden flex flex-col ${glassroomOpen ? 'flex-[3]' : 'flex-1'}`}>
            <div className="flex-1 overflow-auto">
              <KanbanBoard sprintId={currentId} />
            </div>
          </div>

          {/* Glassroom — collapsible bottom panel */}
          <div
            className={`
              card overflow-hidden flex flex-col flex-shrink-0
              transition-all duration-200
              ${glassroomOpen ? 'flex-[2] min-h-[10rem]' : 'h-10'}
            `}
          >
            <button
              onClick={() => setGlassroomOpen(v => !v)}
              className="flex items-center gap-2 px-4 py-2.5 border-b border-surface-700 w-full text-left hover:bg-surface-800 transition-colors"
            >
              <MonitorPlay size={14} className="text-brand-400" />
              <h3 className="font-semibold text-gray-200 text-sm flex-1">{t('workspace.glassroomTitle')}</h3>
              <span className="text-gray-500 text-xs">{glassroomOpen ? '▼' : '▲'}</span>
            </button>
            {glassroomOpen && (
              <div className="flex-1 overflow-y-auto">
                <GlassRoom sprintId={currentId} />
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
