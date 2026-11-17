import { useTranslation } from 'react-i18next'
import { Plus } from 'lucide-react'
import type { DetectedStack } from '../../services/api'
import { AgentCard } from './AgentCard'
import type { Agent, ModelInfo } from './types'

/** Step 4: the team, suggested by the scan or chosen by hand, and its budget. */
export function TeamStep({ discoveryResult, agents, addAgent, updateAgent, removeAgent, availableModels }: {
  discoveryResult: DetectedStack | null
  agents: Agent[]
  addAgent: () => void
  updateAgent: (i: number, field: keyof Agent, val: string | number) => void
  removeAgent: (i: number) => void
  availableModels: ModelInfo[]
}) {
  const { t } = useTranslation()
  return (
  <div className="space-y-5">
    <div className="flex items-start justify-between gap-4">
      <div>
        <h2 className="mb-1 text-base font-semibold text-gray-50">{t('companyNew.teamTitle')}</h2>
        <p className="text-[13px] text-gray-400">
          {t(discoveryResult ? 'companyNew.suggestedTeamDescription' : 'companyNew.teamDescription')}
        </p>
      </div>
      <button type="button" onClick={addAgent} className="btn-tool flex-shrink-0 border border-surface-700">
        <Plus size={13} aria-hidden="true" />{t('companyNew.addTeamAgent')}
      </button>
    </div>

    {agents.length === 0 && (
      <div className="alert-error text-sm">{t('companyNew.minAgentError')}</div>
    )}

    <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
      {agents.map((agent, i) => (
        <AgentCard key={i} agent={agent} index={i}
          onUpdate={updateAgent} onRemove={removeAgent} canRemove={agents.length > 1}
          availableModels={availableModels} />
      ))}
    </div>

    <div className="space-y-1 rounded-lg border border-surface-700 bg-surface-950/30 p-3 text-xs text-gray-300">
      <p className="text-[13px] font-semibold text-gray-100">{t('companyNew.teamBudget')}</p>
      <p>{t('companyNew.totalMonthly')} <span className="font-mono tabular-nums text-brand-300">
        {t('companyNew.millionTokens', { amount: (agents.reduce((s, a) => s + (a.token_budget ?? 500000), 0) / 1000000).toFixed(1) })}
      </span></p>
      <p className="text-gray-400">{t('companyNew.budgetHint')}</p>
    </div>
  </div>
  )
}
