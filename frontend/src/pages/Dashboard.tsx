import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAppStore } from '../store/useAppStore'
import { useSprintBoard } from '../hooks/useQueries'
import ControlPanel from '../components/ControlPanel'
import KanbanBoard from '../components/KanbanBoard'
import SprintCalendar from '../components/SprintCalendar'
import {
  Building2, CalendarDays, MonitorPlay, PanelLeftClose, PanelLeftOpen
} from 'lucide-react'

export default function Dashboard() {
  const { t } = useTranslation()
  const [isControlPanelVisible, setIsControlPanelVisible] = useState(() => {
    if (typeof window === 'undefined') return true
    const saved = window.localStorage.getItem('dashboard.controlPanel.visible.desktop')
    return saved === null ? true : saved === 'true'
  })
  const [isMobileControlPanelVisible, setIsMobileControlPanelVisible] = useState(() => {
    if (typeof window === 'undefined') return false
    const saved = window.localStorage.getItem('dashboard.controlPanel.visible.mobile')
    return saved === null ? false : saved === 'true'
  })
  const companyName = useAppStore(state => state.companyName)
  const activeSprintId = useAppStore(state => state.activeSprintId)

  const currentSprintId = activeSprintId?.toString() || '1'
  const { data: board } = useSprintBoard(parseInt(currentSprintId, 10))

  function handleSprintStart(newSprintId: number) {
    useAppStore.getState().setActiveSprint(newSprintId)
  }

  useEffect(() => {
    window.localStorage.setItem('dashboard.controlPanel.visible.desktop', String(isControlPanelVisible))
  }, [isControlPanelVisible])

  useEffect(() => {
    window.localStorage.setItem('dashboard.controlPanel.visible.mobile', String(isMobileControlPanelVisible))
  }, [isMobileControlPanelVisible])

  useEffect(() => {
    const media = window.matchMedia('(min-width: 1024px)')

    const syncPanelsWithViewport = (matchesDesktop: boolean) => {
      if (matchesDesktop) {
        setIsMobileControlPanelVisible(false)
      } else {
        setIsControlPanelVisible(false)
      }
    }

    syncPanelsWithViewport(media.matches)

    const onChange = (event: MediaQueryListEvent) => {
      syncPanelsWithViewport(event.matches)
    }

    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])

  return (
    <div className="h-[calc(100vh-3.5rem)] flex overflow-hidden bg-surface-950 text-white">
      {isControlPanelVisible && (
        <aside className="hidden lg:block w-80 border-r border-surface-700 bg-surface-900 overflow-y-auto">
          <div className="p-4">
            <ControlPanel onSprintStart={handleSprintStart} />
          </div>
        </aside>
      )}

      <div className="flex-1 min-w-0 flex flex-col">
        <div className="h-14 px-4 border-b border-surface-700 bg-surface-900 flex items-center gap-3">
          <button
            type="button"
            className="hidden lg:inline-flex btn-ghost btn-icon"
            onClick={() => setIsControlPanelVisible(prev => !prev)}
            title={isControlPanelVisible ? 'Ocultar Control Panel' : 'Mostrar Control Panel'}
            aria-label={isControlPanelVisible ? 'Ocultar Control Panel' : 'Mostrar Control Panel'}
          >
            {isControlPanelVisible ? <PanelLeftClose size={16} /> : <PanelLeftOpen size={16} />}
          </button>

          <button
            type="button"
            className="lg:hidden inline-flex btn-ghost btn-sm"
            onClick={() => setIsMobileControlPanelVisible(prev => !prev)}
          >
            {isMobileControlPanelVisible ? 'Ocultar panel' : 'Mostrar panel'}
          </button>

          <CalendarDays size={16} className="text-brand-400" />
          <span className="text-sm font-semibold text-gray-200">{t('workspace.title')}</span>
          <span className="text-xs text-gray-500">Sprint #{currentSprintId}</span>
          {companyName && (
            <span className="ml-2 text-xs text-gray-400 flex items-center gap-1">
              <Building2 size={12} /> {companyName}
            </span>
          )}
          <Link
            to={`/workspace/${currentSprintId}`}
            className="ml-auto btn-ghost btn-sm flex items-center gap-1.5"
          >
            <MonitorPlay size={14} />
            <span>{t('workspace.glassroomTitle')}</span>
          </Link>
        </div>

        <div className={`${isMobileControlPanelVisible ? 'block' : 'hidden'} lg:hidden border-b border-surface-700 bg-surface-900 p-3`}>
          <ControlPanel onSprintStart={handleSprintStart} />
        </div>

        <div className="flex-1 min-h-0 p-3 grid grid-cols-1 xl:grid-cols-5 gap-3">
          <div className="xl:col-span-3 card overflow-hidden min-h-0 flex flex-col">
            <div className="flex-1 min-h-0 overflow-auto">
              <KanbanBoard sprintId={currentSprintId} />
            </div>
          </div>

          <div className="xl:col-span-2 card overflow-hidden min-h-0">
            <SprintCalendar
              sprintId={currentSprintId}
              sprintName={board?.sprint?.sprint_name}
              startDate={board?.sprint?.start_date}
              durationDays={board?.sprint?.duration_days}
            />
          </div>
        </div>
      </div>
    </div>
  )
}
