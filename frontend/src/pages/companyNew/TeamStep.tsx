import { useTranslation } from 'react-i18next'
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
        <h2 className="text-lg font-semibold text-white mb-1">{t('companyNew.teamTitle')}</h2>
        <p className="text-sm text-gray-400">
          {t(discoveryResult ? 'companyNew.suggestedTeamDescription' : 'companyNew.teamDescription')}
        </p>
      </div>
      <button type="button" onClick={addAgent} className="btn btn-secondary btn-sm flex-shrink-0">
        + {t('companyNew.addTeamAgent')}
      </button>
    </div>

    {agents.length === 0 && (
      <div className="alert-error text-sm">{t('companyNew.minAgentError')}</div>
    )}

    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
      {agents.map((agent, i) => (
        <AgentCard key={i} agent={agent} index={i}
          onUpdate={updateAgent} onRemove={removeAgent} canRemove={agents.length > 1}
          availableModels={availableModels} />
      ))}
    </div>

    <div className="rounded-lg bg-surface-800/30 border border-surface-700 p-3 text-xs text-gray-500 space-y-1">
      <p className="font-medium text-gray-400">{t('companyNew.teamBudget')}</p>
      <p>{t('companyNew.totalMonthly')} <span className="text-brand-300 font-mono">
        {t('companyNew.millionTokens', { amount: (agents.reduce((s, a) => s + (a.token_budget ?? 500000), 0) / 1000000).toFixed(1) })}
      </span></p>
      <p className="text-gray-600">{t('companyNew.budgetHint')}</p>
    </div>
  </div>
  )
}
