import { useTranslation } from 'react-i18next'
import type { ReactNode } from 'react'
import { Bot, Factory, Globe, Pencil, RefreshCw } from 'lucide-react'
import type { WorkspaceInspection } from '../../services/api'
import type { GitWorkflowChoice } from '../../services/workControl'
import { StartChoice } from '../../components/SprintControls'
import { GitSetupChoice, gitChoiceFor } from '../../components/GitWorkflowPanel'
import { RemoteSyncChoice, type RemoteChoice } from '../../components/RemoteSyncChoice'
import {
  DEFAULT_TIER, PERM_LEVELS, SENIORITIES, TIER_META, localizedAutonomy, localizedPermission, localizedRoles,
  resourceSummary,
} from './constants'
import type { Agent, ModelInfo, Permissions } from './types'
import { AUTONOMY_ICONS, RESOURCE_ICONS } from './icons'
import { Spinner } from '../../components/ui'

/** One summary group: its title, the way back to its step, and what was chosen there. */
function Summary({ title, onEdit, editLabel, children }: { title: ReactNode; onEdit: () => void; editLabel: string; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-surface-700 bg-surface-950/30">
      <div className="flex items-center justify-between gap-3 border-b border-surface-700 px-3 py-1.5">
        <h3 className="text-[13px] font-semibold text-gray-100">{title}</h3>
        <button type="button" onClick={onEdit} className="btn-tool h-7 px-2 text-brand-300 hover:text-brand-200">
          <Pencil size={12} aria-hidden="true" />{editLabel}
        </button>
      </div>
      <div className="p-3">{children}</div>
    </section>
  )
}

/** Step 5: everything chosen, with a way back to each step, and the button that creates the company. */
export function ReviewStep({
  companyName, companyDesc, scopeSize, timezone, autonomyLevel, startNow, setStartNow, inspected, gitChoice,
  setGitChoice, remoteChoice, setRemoteChoice, permissions, agents, availableModels, mutationError, loading,
  handleSubmit, setStep,
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
  remoteChoice: RemoteChoice | null
  setRemoteChoice: (choice: RemoteChoice) => void
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
  const AutonomyIcon = AUTONOMY_ICONS[autonomyLevel]
  const totalBudget = agents.reduce((s, a) => s + (a.token_budget ?? 500000), 0)
  return (
    <div className="space-y-5">
      <div>
        <h2 className="mb-1 text-base font-semibold text-gray-50">{t('companyNew.reviewTitle')}</h2>
        <p className="text-[13px] text-gray-400">{t('companyNew.reviewDescription')}</p>
      </div>

      <div className="space-y-3">
        <Summary title={t('companyNew.steps.Company')} onEdit={() => setStep(1)} editLabel={t('companyNew.edit')}>
          <p className="text-[13px] font-semibold text-gray-100">{companyName}</p>
          {companyDesc && <p className="mt-0.5 text-xs text-gray-300">{companyDesc}</p>}
          <div className="text-meta mt-2 flex flex-wrap gap-3">
            <span className="inline-flex items-center gap-1"><RefreshCw size={11} aria-hidden="true" />{t('companyNew.scopeSummary', { scope: scopeSize })}</span>
            <span className="inline-flex items-center gap-1"><Globe size={11} aria-hidden="true" />{timezone}</span>
          </div>
        </Summary>

        <Summary title={t('companyNew.steps.Autonomy')} onEdit={() => setStep(2)} editLabel={t('companyNew.edit')}>
          <div className="flex flex-wrap items-center gap-2">
            {AutonomyIcon && <AutonomyIcon size={15} aria-hidden="true" className="text-gray-300" />}
            <span className="text-[13px] font-medium text-gray-100">{selectedAutonomy.label}</span>
            <span className={`rounded-md px-1.5 text-[11px] font-medium leading-5 ${selectedAutonomy.badge}`}>{t('companyNew.level', { level: autonomyLevel })}</span>
          </div>
          <StartChoice checked={startNow} humanLed={autonomyLevel === 1} onChange={setStartNow} />
          <GitSetupChoice inspection={inspected} choice={gitChoice} onChange={setGitChoice} />
          <RemoteSyncChoice inspection={inspected} base={gitChoiceFor(inspected, gitChoice).git_base_branch}
            choice={remoteChoice} onChange={setRemoteChoice} />
        </Summary>

        <Summary title={t('companyNew.permissions')} onEdit={() => setStep(3)} editLabel={t('companyNew.edit')}>
          <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-md border border-surface-700 bg-surface-700 sm:grid-cols-5">
            {resourceSummary(t).map(({ label, key }) => {
              const val = permissions[key as keyof Permissions] as string
              const perm = PERM_LEVELS[val as keyof typeof PERM_LEVELS]
              const Icon = RESOURCE_ICONS[key]
              return (
                <div key={key} className="bg-surface-900 px-2 py-2 text-center">
                  <dt className="text-meta flex items-center justify-center gap-1">{Icon && <Icon size={11} aria-hidden="true" />}{label}</dt>
                  <dd className={`mt-0.5 text-xs font-medium ${perm?.color ?? 'text-gray-400'}`}>{localizedPermission(t, val)}</dd>
                </div>
              )
            })}
          </dl>
        </Summary>

        <Summary title={t('companyNew.teamSummary', { count: agents.length })} onEdit={() => setStep(4)} editLabel={t('companyNew.edit')}>
          <ul className="space-y-1.5">
            {agents.map((a, i) => {
              const roleInfo = roles.find(r => r.value === a.role)
              return (
                <li key={i} className="flex flex-wrap items-center gap-2 text-xs">
                  <Bot size={13} aria-hidden="true" className="flex-shrink-0 text-gray-400" />
                  <span className="text-[13px] font-medium text-gray-200">
                    {a.name ? `${a.name} — ${roleInfo?.label ?? a.role}` : (roleInfo?.label ?? a.role)}
                  </span>
                  <span className={SENIORITIES.find(s => s.value === a.seniority)?.color ?? 'text-gray-400'}>{t(`companyNew.seniorities.${a.seniority}.label`, { defaultValue: a.seniority })}</span>
                  {a.model && (() => {
                    const mi = availableModels.find(m => m.id === a.model)
                    const tm = TIER_META[mi?.tier ?? DEFAULT_TIER]
                    return (
                      <span className={`rounded-md px-1.5 font-mono text-[11px] leading-5 ${tm.color}`}>
                        {tm.icon} {a.model}
                      </span>
                    )
                  })()}
                  {a.specialty && <span className="truncate text-gray-400">· {a.specialty}</span>}
                </li>
              )
            })}
          </ul>
          <p className="mt-2 text-xs text-gray-400">
            {t('companyNew.totalBudget')} <span className="font-mono tabular-nums text-brand-300">{t('companyNew.millionTokensMonthly', { amount: (totalBudget / 1000000).toFixed(1) })}</span>
          </p>
        </Summary>
      </div>

      {mutationError && <div className="alert-error text-sm">{t('companyNew.initializationError', { message: mutationError })}</div>}

      <button type="button" onClick={handleSubmit}
        disabled={loading}
        className="btn-primary w-full">
        {loading ? <Spinner size={13} /> : <Factory size={15} aria-hidden="true" />}
        {loading ? t('companyNew.submitting') : t('companyNew.submit')}
      </button>
    </div>
  )
}
