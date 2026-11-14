import { useTranslation } from 'react-i18next'
import type { WorkspaceInspection } from '../../services/api'
import type { GitWorkflowChoice } from '../../services/workControl'
import { StartChoice } from '../../components/SprintControls'
import { GitSetupChoice } from '../../components/GitWorkflowPanel'
import {
  DEFAULT_TIER, PERM_LEVELS, SENIORITIES, TIER_META, localizedAutonomy, localizedPermission, localizedRoles,
  resourceSummary,
} from './constants'
import type { Agent, ModelInfo, Permissions } from './types'

/** Step 5: everything chosen, with a way back to each step, and the button that creates the company. */
export function ReviewStep({
  companyName, companyDesc, scopeSize, timezone, autonomyLevel, startNow, setStartNow, inspected, gitChoice,
  setGitChoice, permissions, agents, availableModels, mutationError, loading, handleSubmit, setStep,
}: {
  companyName: string
  companyDesc: string
  scopeSize: 'S' | 'M' | 'L'
  timezone: string
  autonomyLevel: number
  startNow: boolean
  setStartNow: (value: boolean) => void
  inspected: WorkspaceInspection | null
  gitChoice: GitWorkflowChoice | null
  setGitChoice: (choice: GitWorkflowChoice) => void
  permissions: Permissions
  agents: Agent[]
  availableModels: ModelInfo[]
  mutationError: string | null
  loading: boolean
  handleSubmit: () => void
  setStep: (step: number) => void
}) {
  const { t } = useTranslation()
  const autonomyLevels = localizedAutonomy(t)
  const roles = localizedRoles(t)
  const selectedAutonomy = autonomyLevels.find(l => l.value === autonomyLevel)!
  const totalBudget = agents.reduce((s, a) => s + (a.token_budget ?? 500000), 0)
  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold text-white mb-1">{t('companyNew.reviewTitle')}</h2>
        <p className="text-sm text-gray-400">{t('companyNew.reviewDescription')}</p>
      </div>

      <div className="space-y-3">
        {/* Company */}
        <div className="card p-4">
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">{t('companyNew.steps.Company')}</p>
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="font-semibold text-white">{companyName}</p>
              {companyDesc && <p className="text-sm text-gray-400 mt-0.5">{companyDesc}</p>}
            </div>
            <button type="button" onClick={() => setStep(1)} className="text-xs text-brand-400 hover:underline flex-shrink-0">{t('companyNew.edit')}</button>
          </div>
          <div className="mt-2 flex gap-3 text-xs text-gray-500">
            <span>🔄 {t('companyNew.scopeSummary', { scope: scopeSize })}</span>
            <span>🌍 {timezone}</span>
          </div>
        </div>

        {/* Autonomy */}
        <div className="card p-4">
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">{t('companyNew.steps.Autonomy')}</p>
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-2">
              <span className="text-xl">{selectedAutonomy.icon}</span>
              <span className="text-sm font-medium text-white">{selectedAutonomy.label}</span>
              <span className={`badge text-xs ${selectedAutonomy.badge}`}>{t('companyNew.level', { level: autonomyLevel })}</span>
            </div>
            <button type="button" onClick={() => setStep(2)} className="text-xs text-brand-400 hover:underline">{t('companyNew.edit')}</button>
          </div>
          <StartChoice checked={startNow} humanLed={autonomyLevel === 1} onChange={setStartNow} />
          <GitSetupChoice inspection={inspected} choice={gitChoice} onChange={setGitChoice} />
        </div>

        {/* Permissions */}
        <div className="card p-4">
          <div className="flex items-center justify-between mb-2">
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider">{t('companyNew.permissions')}</p>
            <button type="button" onClick={() => setStep(3)} className="text-xs text-brand-400 hover:underline">{t('companyNew.edit')}</button>
          </div>
          <div className="grid grid-cols-5 gap-2">
            {resourceSummary(t).map(({ label, key, icon }) => {
              const val = permissions[key as keyof Permissions] as string
              const perm = PERM_LEVELS[val as keyof typeof PERM_LEVELS]
              return (
                <div key={key} className="bg-surface-800/60 rounded-lg p-2 text-center">
                  <span className="text-base">{icon}</span>
                  <p className="text-xs text-gray-500 mt-0.5">{label}</p>
                  <p className={`text-xs font-medium mt-0.5 ${perm?.color ?? 'text-gray-400'}`}>{localizedPermission(t, val)}</p>
                </div>
              )
            })}
          </div>
        </div>

        {/* Team */}
        <div className="card p-4">
          <div className="flex items-center justify-between mb-2">
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider">{t('companyNew.teamSummary', { count: agents.length })}</p>
            <button type="button" onClick={() => setStep(4)} className="text-xs text-brand-400 hover:underline">{t('companyNew.edit')}</button>
          </div>
          <div className="space-y-1.5">
            {agents.map((a, i) => {
              const roleInfo = roles.find(r => r.value === a.role)
              return (
                <div key={i} className="flex items-center gap-2 text-sm">
                  <span>{roleInfo?.icon ?? '🤖'}</span>
                  <span className="text-gray-300 font-medium">
                    {a.name ? `${a.name} — ${roleInfo?.label ?? a.role}` : (roleInfo?.label ?? a.role)}
                  </span>
                  <span className={`text-xs ${SENIORITIES.find(s => s.value === a.seniority)?.color ?? 'text-gray-400'}`}>{t(`companyNew.seniorities.${a.seniority}.label`, { defaultValue: a.seniority })}</span>
                  {a.model && (() => {
                    const mi = availableModels.find(m => m.id === a.model)
                    const tm = TIER_META[mi?.tier ?? DEFAULT_TIER]
                    return (
                      <span className={`badge text-[10px] font-mono ${tm.color}`}>
                        {tm.icon} {a.model}
                      </span>
                    )
                  })()}
                  {a.specialty && <span className="text-xs text-gray-600 truncate">· {a.specialty}</span>}
                </div>
              )
            })}
          </div>
          <p className="text-xs text-gray-600 mt-2">
            {t('companyNew.totalBudget')} <span className="text-brand-300 font-mono">{t('companyNew.millionTokensMonthly', { amount: (totalBudget / 1000000).toFixed(1) })}</span>
          </p>
        </div>
      </div>

      {mutationError && <div className="alert-error text-sm">{t('companyNew.initializationError', { message: mutationError })}</div>}

      <button type="button" onClick={handleSubmit}
        disabled={loading}
        className="btn btn-primary w-full text-base py-3">
        {loading ? (
          <span className="flex items-center gap-2 justify-center">
            <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
            {t('companyNew.submitting')}
          </span>
        ) : `🏭 ${t('companyNew.submit')}`}
      </button>
    </div>
  )
}
