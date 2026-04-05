import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAppStore } from '../store/useAppStore'
import { api } from '../services/api'

interface Agent {
  role: string
  persona: string
  seniority: string
  specialty: string
}

export default function CompanyNew() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const setCompany = useAppStore(state => state.setCompany)
  const [companyName, setCompanyName] = useState('')
  const [sprintLength, setSprintLength] = useState(14)
  const [autonomyLevel, setAutonomyLevel] = useState(2)
  const [permissions, setPermissions] = useState({
    git_access: 'read_only',
    cloud_access: 'none',
    db_access: 'read_only'
  })
  const [projectPath, setProjectPath] = useState('')
  const [isDiscovering, setIsDiscovering] = useState(false)
  const [agents, setAgents] = useState<Agent[]>([
    { role: 'PO', persona: '', seniority: 'Senior', specialty: '' }
  ])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleDiscover = async () => {
    if (!projectPath) {
      alert(t('companyNew.projectPathPlaceholder'))
      return
    }

    setIsDiscovering(true)
    setError(null)
    try {
      // Pass null company_id for pre-creation discovery
      const response = await api.discoverTeam(null as any, projectPath)
      
      if (response) {
        if (response.suggested_team) {
          setAgents(response.suggested_team)
        }
        if (response.detected_stack?.name && !companyName) {
          setCompanyName(response.detected_stack.name)
        }
      }
    } catch (e) {
      console.error(e)
      setError('Discovery failed. Please check the project path or backend connection.')
    } finally {
      setIsDiscovering(false)
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      const response = await api.post<any>('/v1/init_company', {
        company_name: companyName,
        default_sprint_length_days: sprintLength,
        autonomy_level: autonomyLevel,
        agents,
        permissions
      })
      const companyId = response?.company_id ?? response?.id ?? 1
      setCompany(companyId, companyName)
      navigate(`/workspace/${companyId}`)
    } catch (err) {
      console.error('Failed to create company:', err)
      setError(t('companyNew.error'))
    }
    setLoading(false)
  }

  function addAgent() {
    setAgents([...agents, { role: 'Dev', persona: '', seniority: 'Mid', specialty: '' }])
  }

  function removeAgent(index: number) {
    setAgents(agents.filter((_, i) => i !== index))
  }

  function updateAgent(index: number, field: keyof Agent, value: string) {
    const newAgents = [...agents]
    newAgents[index][field] = value
    setAgents(newAgents)
  }


  return (
    <div className="page-container max-w-3xl">
      <div className="page-header mb-8">
        <h1 className="page-title">{t('companyNew.title')}</h1>
        <p className="text-gray-400 mt-1">{t('companyNew.subtitle')}</p>
      </div>

      {/* AI Discovery */}
      <div className="card mb-6 border-brand-700/40 bg-brand-950/30">
        <div className="px-5 py-4 border-b border-brand-700/30">
          <h2 className="section-title text-brand-300">🤖 {t('companyNew.autoDetect')}</h2>
        </div>
        <div className="p-5 flex flex-col sm:flex-row gap-3">
          <input
            type="text"
            placeholder={t('companyNew.projectPathPlaceholder')}
            className="input flex-1"
            value={projectPath}
            onChange={e => setProjectPath(e.target.value)}
          />
          <button type="button" onClick={handleDiscover} disabled={isDiscovering} className="btn btn-primary flex-shrink-0">
            {isDiscovering ? t('companyNew.detecting') : t('companyNew.autoDetect')}
          </button>
        </div>
      </div>

      <form onSubmit={handleSubmit} className="card p-6 space-y-6">

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="input-label">{t('companyNew.companyName')} *</label>
            <input type="text" value={companyName} onChange={e => setCompanyName(e.target.value)}
              placeholder={t('companyNew.companyNamePlaceholder')} required className="input w-full" />
          </div>
          <div>
            <label className="input-label">{t('companyNew.sprintLength', 'Sprint length (days)')}</label>
            <input type="number" value={sprintLength} onChange={e => setSprintLength(parseInt(e.target.value))}
              min={1} max={90} className="input w-full" />
          </div>
        </div>

        <div className="rounded-lg border border-surface-700 p-4 bg-surface-800/40">
          <label className="input-label mb-1">{t('companyNew.autonomyLevel', 'Autonomy level')}</label>
          <select value={autonomyLevel} onChange={e => setAutonomyLevel(Number(e.target.value))} className="select w-full mt-2">
            <option value={1}>{t('companyNew.autonomyLevels.1', 'Level 1 – Copilot')}</option>
            <option value={2}>{t('companyNew.autonomyLevels.2', 'Level 2 – Exception Manager')}</option>
            <option value={3}>{t('companyNew.autonomyLevels.3', 'Level 3 – Full Autonomous')}</option>
          </select>
        </div>

        {autonomyLevel > 1 && (
          <div className="rounded-lg border border-brand-700/40 p-4 bg-brand-950/20">
            <label className="input-label mb-1 text-brand-300">🔐 {t('companyNew.infraPerms', 'Infrastructure permissions')}</label>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mt-3">
              {[
                { key: 'git_access', label: 'Git', options: [
                  ['read_only', t('companyNew.gitOptions.read_only','Read Only')],
                  ['write', t('companyNew.gitOptions.write','Write')],
                  ...(autonomyLevel === 3 ? [['admin', t('companyNew.gitOptions.admin','Admin')] as [string,string]] : []),
                ]},
                { key: 'cloud_access', label: 'Cloud', options: [
                  ['none', t('companyNew.cloudOptions.none','None')],
                  ['read_only', t('companyNew.cloudOptions.read_only','Read Only')],
                  ...(autonomyLevel === 3 ? [['write', t('companyNew.cloudOptions.write','Write')] as [string,string]] : []),
                ]},
                { key: 'db_access', label: 'DB', options: [
                  ['read_only', t('companyNew.dbOptions.read_only','Read Only')],
                  ['write', t('companyNew.dbOptions.write','Write')],
                  ...(autonomyLevel === 3 ? [['admin', t('companyNew.dbOptions.admin','Admin')] as [string,string]] : []),
                ]},
              ].map(({ key, label, options }) => (
                <div key={key}>
                  <label className="input-label">{label}</label>
                  <select value={(permissions as any)[key]}
                    onChange={e => setPermissions({ ...permissions, [key]: e.target.value })}
                    className="select w-full">
                    {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                  </select>
                </div>
              ))}
            </div>
          </div>
        )}

        <div>
          <label className="section-title mb-3 block">{t('companyNew.agents')}</label>
          <div className="space-y-2">
            {agents.map((agent, index) => (
              <div key={index} className="flex flex-wrap sm:flex-nowrap gap-2 items-center bg-surface-800 p-3 rounded-lg border border-surface-700">
                <select value={agent.role} onChange={e => updateAgent(index, 'role', e.target.value)} className="select w-24 flex-shrink-0">
                  {['PO','SM','Architect','Dev','QA','DevOps'].map(r => <option key={r} value={r}>{r}</option>)}
                </select>
                <select value={agent.seniority} onChange={e => updateAgent(index, 'seniority', e.target.value)} className="select w-28 flex-shrink-0">
                  {['Junior','Mid','Senior','Staff'].map(s => <option key={s} value={s}>{s}</option>)}
                </select>
                <input type="text" value={agent.specialty || agent.persona}
                  onChange={e => { updateAgent(index,'persona',e.target.value); updateAgent(index,'specialty',e.target.value) }}
                  placeholder={t('companyNew.agentSpecialty','Specialty (e.g. React, Cloud)')}
                  className="input flex-1 min-w-0" />
                {agents.length > 1 && (
                  <button type="button" onClick={() => removeAgent(index)}
                    className="btn-ghost btn-icon text-status-error hover:bg-red-900/20 flex-shrink-0"
                    aria-label={t('companyNew.removeAgent')}>×</button>
                )}
              </div>
            ))}
          </div>
          {agents.length === 0 && (
            <p className="text-sm text-status-error mt-2">{t('companyNew.minAgentError','At least 1 agent is required')}</p>
          )}
          <button type="button" onClick={addAgent} className="btn-ghost btn-sm mt-3 flex items-center gap-1.5 text-brand-400">
            + {t('companyNew.addAgent')}
          </button>
        </div>

        {error && <div className="alert-error">{error}</div>}

        <div className="pt-4 border-t border-surface-700">
          <button type="submit" disabled={loading || agents.length === 0} className="btn btn-primary w-full text-base py-3">
            {loading ? t('companyNew.submitting') : t('companyNew.submit')}
          </button>
        </div>
      </form>
    </div>
  )
}
