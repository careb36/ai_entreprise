import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useApp } from '../context/AppContext'
import { api } from '../services/api'
import {
  Plus, MonitorPlay, ShieldCheck, PieChart,
  Search, Code2, Building2, CheckCircle2, XCircle, Loader2
} from 'lucide-react'

export default function Dashboard() {
  const { t } = useTranslation()
  const { companyName, activeSprintId } = useApp()
  const [systemOnline, setSystemOnline] = useState<boolean | null>(null)

  useEffect(() => {
    api.get('/health')
      .then(() => setSystemOnline(true))
      .catch(() => setSystemOnline(false))
  }, [])

  const workspaceId = activeSprintId || 1

  const cards = [
    {
      to: '/companies/new',
      icon: <Plus size={28} className="text-indigo-500" />,
      title: t('dashboard.newFactory'),
      desc: t('dashboard.newFactoryDesc'),
      color: 'border-indigo-100 hover:border-indigo-300',
    },
    {
      to: `/workspace/${workspaceId}`,
      icon: <MonitorPlay size={28} className="text-emerald-500" />,
      title: t('dashboard.activeWorkspace'),
      desc: t('dashboard.activeWorkspaceDesc'),
      color: 'border-emerald-100 hover:border-emerald-300',
    },
    {
      to: '/governance',
      icon: <ShieldCheck size={28} className="text-red-500" />,
      title: t('dashboard.governance'),
      desc: t('dashboard.governanceDesc'),
      color: 'border-red-100 hover:border-red-300',
    },
    {
      to: '/analytics',
      icon: <PieChart size={28} className="text-blue-500" />,
      title: t('dashboard.analytics'),
      desc: t('dashboard.analyticsDesc'),
      color: 'border-blue-100 hover:border-blue-300',
    },
    {
      to: '/memory',
      icon: <Search size={28} className="text-purple-500" />,
      title: t('dashboard.memorySearch'),
      desc: t('dashboard.memorySearchDesc'),
      color: 'border-purple-100 hover:border-purple-300',
    },
    {
      to: '/generate',
      icon: <Code2 size={28} className="text-orange-500" />,
      title: t('dashboard.generate'),
      desc: t('dashboard.generateDesc'),
      color: 'border-orange-100 hover:border-orange-300',
    },
  ]

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Hero banner */}
      <div className="bg-gray-900 text-white px-8 py-10">
        <div className="max-w-5xl mx-auto flex items-center justify-between">
          <div>
            <h1 className="text-4xl font-black tracking-tight">{t('dashboard.title')}</h1>
            <p className="mt-2 text-gray-400 text-lg">{t('dashboard.subtitle')}</p>
            {companyName && (
              <div className="mt-3 flex items-center gap-2 text-indigo-400 font-semibold">
                <Building2 size={18} /> {companyName}
              </div>
            )}
          </div>
          <div className="flex flex-col items-end gap-2">
            <span className="text-sm text-gray-400">{t('dashboard.systemStatus')}</span>
            {systemOnline === null ? (
              <div className="flex items-center gap-2 text-gray-400 text-sm">
                <Loader2 size={16} className="animate-spin" /> {t('dashboard.checking')}
              </div>
            ) : systemOnline ? (
              <div className="flex items-center gap-2 text-emerald-400 text-sm font-medium">
                <CheckCircle2 size={16} /> {t('dashboard.online')}
              </div>
            ) : (
              <div className="flex items-center gap-2 text-red-400 text-sm font-medium">
                <XCircle size={16} /> {t('dashboard.offline')}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Quick access cards */}
      <div className="max-w-5xl mx-auto px-8 py-10">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
          {cards.map((card) => (
            <Link
              key={card.to}
              to={card.to}
              className={`bg-white rounded-xl border-2 p-6 shadow-sm hover:shadow-md transition-all ${card.color}`}
            >
              <div className="mb-3">{card.icon}</div>
              <h2 className="text-base font-bold text-gray-900">{card.title}</h2>
              <p className="mt-1 text-sm text-gray-500">{card.desc}</p>
            </Link>
          ))}
        </div>
      </div>
    </div>
  )
}
