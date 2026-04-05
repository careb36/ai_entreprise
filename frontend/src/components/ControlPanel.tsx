import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../services/api'
import {
  PanelLeft, Play, Rocket, Settings, Zap,
  Brain, Loader2, CheckCircle2, AlertCircle
} from 'lucide-react'

interface SprintConfig {
  sprintName: string
  sprintGoal: string
  model: string
  durationDays: number
}

interface SystemStatus {
  backend: 'ok' | 'error' | 'loading'
  nats: 'ok' | 'unknown'
}

interface ControlPanelProps {
  onSprintStart?: (sprintId: number) => void
}

const AGENT_ROLE_KEYS = ['PO', 'SM', 'Dev', 'QA', 'DevOps'] as const

export default function ControlPanel({ onSprintStart }: ControlPanelProps) {
  const { t } = useTranslation()
  const [isLoading, setIsLoading] = useState(false)
  const [config, setConfig] = useState<SprintConfig>({
    sprintName: `Sprint ${new Date().getMonth() + 1}`,
    sprintGoal: '',
    model: 'gemini-pro',
    durationDays: 14
  })
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [status, setStatus] = useState<SystemStatus>({ backend: 'loading', nats: 'unknown' })

  // Poll backend health on mount
  useEffect(() => {
    api.health()
      .then(d => setStatus({ backend: d.status === 'healthy' ? 'ok' : 'error', nats: d.nats ?? 'unknown' }))
      .catch(() => setStatus({ backend: 'error', nats: 'unknown' }))
  }, [])

  async function handleStartSprint() {
    if (!config.sprintGoal.trim()) {
      setError(t('controlPanel.error'))
      return
    }
    setIsLoading(true)
    setError(null)
    setSuccess(null)
    try {
      const response = await api.post<{ sprint_id: number }>('/v1/start_sprint', {
        company_id: 1,
        sprint_name: config.sprintName,
        start_date: new Date().toISOString().split('T')[0],
        duration_days: config.durationDays,
        backlog_items: [{ title: config.sprintGoal, description: config.sprintGoal }]
      })
      setSuccess(`${t('controlPanel.success')} #${response.sprint_id}`)
      onSprintStart?.(response.sprint_id)
    } catch (e: any) {
      setError(e.message || t('controlPanel.error'))
    } finally {
      setIsLoading(false)
    }
  }

  const statusIcon = (s: 'ok' | 'error' | 'loading' | 'unknown') => {
    if (s === 'ok')      return <CheckCircle2 size={12} className="text-status-success" />
    if (s === 'error')   return <AlertCircle  size={12} className="text-status-error" />
    return <span className="w-2 h-2 rounded-full bg-gray-600 inline-block" />
  }

  const statusLabel = (s: string) =>
    s === 'ok' ? t('controlPanel.connected') : s === 'error' ? t('controlPanel.error') : '…'

  return (
    <div className="flex flex-col h-full">

      {/* Sprint form */}
      <section className="mb-5">
        <div className="flex items-center gap-2 mb-3">
          <Rocket size={15} className="text-brand-400" />
          <h3 className="text-sm font-semibold text-gray-300">{t('controlPanel.title')}</h3>
        </div>

        <div className="space-y-3">
          {/* Sprint name */}
          <div>
            <label className="input-label">{t('controlPanel.sprintName')}</label>
            <input
              type="text"
              value={config.sprintName}
              onChange={e => setConfig({ ...config, sprintName: e.target.value })}
              placeholder={t('controlPanel.sprintNamePlaceholder')}
              className="input w-full"
            />
          </div>

          {/* Sprint goal */}
          <div>
            <label className="input-label">{t('controlPanel.sprintGoal')} *</label>
            <textarea
              value={config.sprintGoal}
              onChange={e => setConfig({ ...config, sprintGoal: e.target.value })}
              placeholder={t('controlPanel.sprintGoalPlaceholder')}
              rows={3}
              className="textarea w-full"
            />
          </div>

          {/* Duration + Model */}
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="input-label">{t('controlPanel.duration')}</label>
              <input
                type="number"
                value={config.durationDays}
                onChange={e => setConfig({ ...config, durationDays: parseInt(e.target.value) || 14 })}
                min={1} max={90}
                className="input w-full"
              />
            </div>
            <div>
              <label className="input-label">{t('controlPanel.model')}</label>
              <select
                value={config.model}
                onChange={e => setConfig({ ...config, model: e.target.value })}
                className="select w-full"
              >
                <option value="gemini-pro">Gemini Pro</option>
                <option value="gemini-flash">Gemini Flash</option>
                <option value="deepseek">DeepSeek</option>
              </select>
            </div>
          </div>

          <button
            onClick={handleStartSprint}
            disabled={isLoading || !config.sprintGoal.trim()}
            className="btn btn-primary w-full"
          >
            {isLoading ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />}
            {isLoading ? t('controlPanel.starting') : t('controlPanel.start')}
          </button>

          {error   && <div className="alert-error text-xs">{error}</div>}
          {success && <div className="alert-success text-xs">{success}</div>}
        </div>
      </section>

      {/* Agents */}
      <section className="border-t border-surface-700 pt-4 mb-5">
        <div className="flex items-center gap-2 mb-3">
          <Brain size={15} className="text-blue-400" />
          <h3 className="text-sm font-semibold text-gray-300">{t('controlPanel.activeAgents')}</h3>
        </div>
        <div className="space-y-1.5">
          {AGENT_ROLE_KEYS.map(role => (
            <div key={role} className="flex items-center justify-between px-3 py-2 bg-surface-800 rounded-lg">
              <span className="text-gray-300 text-sm">{t(`controlPanel.agents.${role}`, role)}</span>
              <span className="status-dot-active" />
            </div>
          ))}
        </div>
      </section>

      {/* System status */}
      <section className="border-t border-surface-700 pt-4">
        <div className="flex items-center gap-2 mb-3">
          <Zap size={15} className="text-yellow-400" />
          <h3 className="text-sm font-semibold text-gray-300">{t('controlPanel.systemStatus')}</h3>
        </div>
        <div className="space-y-1.5 text-xs">
          {[
            { label: 'Backend', state: status.backend },
            { label: 'NATS',    state: status.nats    },
          ].map(({ label, state }) => (
            <div key={label} className="flex justify-between items-center">
              <span className="text-gray-500">{label}</span>
              <span className="flex items-center gap-1">
                {statusIcon(state as any)}
                <span className={state === 'ok' ? 'text-status-success' : state === 'error' ? 'text-status-error' : 'text-gray-500'}>
                  {statusLabel(state)}
                </span>
              </span>
            </div>
          ))}
        </div>
      </section>

      {/* Footer */}
      <div className="mt-auto pt-4 border-t border-surface-700">
        <button className="btn-ghost btn-sm flex items-center gap-1.5 text-gray-400 hover:text-white">
          <Settings size={14} />
          {t('controlPanel.settings', 'Settings')}
        </button>
      </div>
    </div>
  )
}

