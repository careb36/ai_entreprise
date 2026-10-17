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
        <h1 className="page-title">{t('home.title')}</h1>
        <p className="page-subtitle">
          {t('home.subtitle')}
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        <Link to="/companies/new" className="card-hover p-5">
          <div className="flex items-center gap-2 text-brand-300 mb-2">
            <Building2 size={18} />
            <span className="font-semibold">{t('nav.newCompany')}</span>
          </div>
          <p className="text-xs text-gray-400">{t('home.companyDescription')}</p>
        </Link>

        <Link to="/dashboard" className="card-hover p-5">
          <div className="flex items-center gap-2 text-cyan-300 mb-2">
            <LayoutDashboard size={18} />
            <span className="font-semibold">{t('home.sprintBoard')}</span>
          </div>
          <p className="text-xs text-gray-400">{t('home.boardDescription')}</p>
        </Link>

        <Link to={workspacePath} className="card-hover p-5">
          <div className="flex items-center gap-2 text-green-300 mb-2">
            <MonitorPlay size={18} />
            <span className="font-semibold">{t('nav.workspace')}</span>
          </div>
          <p className="text-xs text-gray-400">
            {companyName
              ? activeSprintId
                ? t('home.liveSprint', { companyName, sprintId: activeSprintId })
                : t('home.liveCompany', { companyName })
              : t('home.agentsDescription')}
          </p>
        </Link>

        <Link to="/generate" className="card-hover p-5">
          <div className="flex items-center gap-2 text-yellow-300 mb-2">
            <Sparkles size={18} />
            <span className="font-semibold">{t('nav.generate')}</span>
          </div>
          <p className="text-xs text-gray-400">{t('home.generateDescription')}</p>
        </Link>
      </div>

      <div className="mt-6 card p-4 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-gray-300">
          <CalendarDays size={16} className="text-brand-400" />
          <span className="text-sm">{t('home.planningHint')}</span>
        </div>
        <Link to="/dashboard" className="btn btn-secondary btn-sm">{t('home.openDashboard')}</Link>
      </div>
    </div>
  )
}