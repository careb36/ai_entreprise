import { useTranslation } from 'react-i18next'
import { Loader2, Wand2 } from 'lucide-react'
import { useGenerateAgentPrompt } from '../../hooks/useQueries'
import type { ModelCatalogEntry } from '../../services/api'
import type { AgentDraft } from './drafts'
import { AUTO_RECOMMENDED_MODEL_IDS, recommendModelForRole } from './recommendation'
import { COMMON_ROLES, SENIORITIES } from './display'
import { ModelSelector } from './ModelSelector'

export function DraftFields({
  draft,
  setDraft,
  models,
}: {
  draft: AgentDraft
  setDraft: React.Dispatch<React.SetStateAction<AgentDraft>>
  models: ModelCatalogEntry[]
}) {
  const { t } = useTranslation()
  const generatePrompt = useGenerateAgentPrompt()
  const roleOptions = COMMON_ROLES.includes(draft.role) ? COMMON_ROLES : [draft.role, ...COMMON_ROLES]

  function handleRoleChange(nextRole: string) {
    setDraft(prev => {
      const recommendation = recommendModelForRole(models, nextRole, prev.seniority)
      // An empty model stays "Default for role"; only an auto-picked one is replaced.
      const shouldAutoRecommend = AUTO_RECOMMENDED_MODEL_IDS.has(prev.model)
      return {
        ...prev,
        role: nextRole,
        model: shouldAutoRecommend ? (recommendation.primary?.id || prev.model) : prev.model,
      }
    })
  }

  function handleSeniorityChange(nextSeniority: string) {
    setDraft(prev => {
      const recommendation = recommendModelForRole(models, prev.role, nextSeniority)
      // An empty model stays "Default for role"; only an auto-picked one is replaced.
      const shouldAutoRecommend = AUTO_RECOMMENDED_MODEL_IDS.has(prev.model)
      return {
        ...prev,
        seniority: nextSeniority,
        model: shouldAutoRecommend ? (recommendation.primary?.id || prev.model) : prev.model,
      }
    })
  }

  function handleGeneratePrompt() {
    generatePrompt.mutate(
      { role: draft.role, seniority: draft.seniority, specialty: draft.specialty, persona: draft.persona },
      { onSuccess: (res) => setDraft(prev => ({ ...prev, system_prompt: res.system_prompt })) },
    )
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <label className="input-label">{t('agentsConfig.name')}</label>
          <input
            className="input w-full"
            value={draft.name}
            onChange={e => setDraft(prev => ({ ...prev, name: e.target.value }))}
            placeholder={t('agentsConfig.name')}
          />
        </div>
        <div>
          <label className="input-label">{t('agentsConfig.role')}</label>
          <select
            className="select w-full"
            value={draft.role}
            onChange={e => handleRoleChange(e.target.value)}
          >
            {roleOptions.map(role => <option key={role} value={role}>{t(`teamRoles.${role}`, { defaultValue: role })}</option>)}
          </select>
        </div>
        <div>
          <label className="input-label">{t('agentsConfig.seniority')}</label>
          <select
            className="select w-full"
            value={draft.seniority}
            onChange={e => handleSeniorityChange(e.target.value)}
          >
            {SENIORITIES.map(level => <option key={level} value={level}>{level}</option>)}
          </select>
        </div>
        <div>
          <label className="input-label">{t('agentsConfig.specialty')}</label>
          <input
            className="input w-full"
            value={draft.specialty}
            onChange={e => setDraft(prev => ({ ...prev, specialty: e.target.value }))}
            placeholder={t('agentsConfig.specialty')}
          />
        </div>
      </div>

      <ModelSelector draft={draft} setDraft={setDraft} models={models} />

      <div>
        <label className="input-label">{t('agentsConfig.persona')}</label>
        <textarea
          className="textarea w-full"
          rows={2}
          value={draft.persona}
          onChange={e => setDraft(prev => ({ ...prev, persona: e.target.value }))}
        />
      </div>

      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="input-label mb-0">{t('agentsConfig.systemPrompt')}</label>
          <button
            type="button"
            onClick={handleGeneratePrompt}
            disabled={generatePrompt.isPending || !draft.role.trim()}
            className="inline-flex items-center gap-1.5 rounded-lg border border-brand-500/30 bg-brand-500/10 px-2.5 py-1 text-[11px] font-semibold text-brand-200 disabled:opacity-50 hover:bg-brand-500/20 transition-colors"
          >
            {generatePrompt.isPending
              ? <><Loader2 size={11} className="animate-spin" />{t('agentsConfig.generatingPrompt')}</>
              : <><Wand2 size={11} />{t('agentsConfig.generatePrompt')}</>
            }
          </button>
        </div>
        <textarea
          className="textarea w-full font-mono text-xs"
          rows={4}
          value={draft.system_prompt}
          onChange={e => setDraft(prev => ({ ...prev, system_prompt: e.target.value }))}
          placeholder={t('agentsConfig.systemPromptPlaceholder')}
        />
        {generatePrompt.isError && (
          <p className="mt-1 text-[11px] text-red-400">{t('agentsConfig.generatePromptError')}</p>
        )}
      </div>

      <div>
        <label className="input-label">{t('agentsConfig.tokenBudget')}</label>
        <div className="flex items-center gap-3">
          <input
            type="range"
            min={100000}
            max={5000000}
            step={100000}
            className="flex-1 accent-brand-500"
            value={draft.token_budget}
            onChange={e => setDraft(prev => ({ ...prev, token_budget: Number(e.target.value) }))}
          />
          <span className="w-24 text-right text-xs font-mono text-brand-300">{Math.round(draft.token_budget / 1000)}K</span>
        </div>
      </div>
    </div>
  )
}
