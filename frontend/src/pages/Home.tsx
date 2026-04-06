import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAppStore } from '../store/useAppStore'
import {
  Building2,
  CalendarDays,
  LayoutDashboard,
  MonitorPlay,
  Sparkles,
} from 'lucide-react'

export default function Home() {
  const { t } = useTranslation()
  const activeSprintId = useAppStore(state => state.activeSprintId)
  const companyName = useAppStore(state => state.companyName)

  const workspacePath = activeSprintId ? `/workspace/${activeSprintId}` : '/companies/new'

  return (
    <div className="page-container max-w-6xl">
      <div className="mb-8">
        <h1 className="page-title">{t('nav.dashboard')} Menu</h1>
        <p className="page-subtitle">
          Launch your workflow quickly. Start by creating a company, then run your sprint autopilot.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        <Link to="/companies/new" className="card-hover p-5">
          <div className="flex items-center gap-2 text-brand-300 mb-2">
            <Building2 size={18} />
            <span className="font-semibold">Nueva Compañia</span>
          </div>
          <p className="text-xs text-gray-400">Create or configure the company profile and agent team.</p>
        </Link>

        <Link to="/dashboard" className="card-hover p-5">
          <div className="flex items-center gap-2 text-cyan-300 mb-2">
            <LayoutDashboard size={18} />
            <span className="font-semibold">Sprint Board</span>
          </div>
          <p className="text-xs text-gray-400">Kanban + control panel + operational sprint dashboard.</p>
        </Link>

        <Link to={workspacePath} className="card-hover p-5">
          <div className="flex items-center gap-2 text-green-300 mb-2">
            <MonitorPlay size={18} />
            <span className="font-semibold">Agents Room</span>
          </div>
          <p className="text-xs text-gray-400">
            {companyName
              ? `Live execution for ${companyName}${activeSprintId ? ` · Sprint #${activeSprintId}` : ''}`
              : 'Open live multi-agent execution and monitoring.'}
          </p>
        </Link>

        <Link to="/generate" className="card-hover p-5">
          <div className="flex items-center gap-2 text-yellow-300 mb-2">
            <Sparkles size={18} />
            <span className="font-semibold">Generate</span>
          </div>
          <p className="text-xs text-gray-400">Describe a feature and let the team generate implementation tasks.</p>
        </Link>
      </div>

      <div className="mt-6 card p-4 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-gray-300">
          <CalendarDays size={16} className="text-brand-400" />
          <span className="text-sm">Need planning first? Open the sprint board and calendar view.</span>
        </div>
        <Link to="/dashboard" className="btn btn-secondary btn-sm">Open Dashboard</Link>
      </div>
    </div>
  )
}