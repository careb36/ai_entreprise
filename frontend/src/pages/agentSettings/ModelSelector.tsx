import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Sparkles } from 'lucide-react'
import type { ModelCatalogEntry } from '../../services/api'
import { ConnectionModelOptgroups, useConnectionModelOptions } from '../../components/ConnectionModelOptions'
import type { AgentDraft } from './drafts'
import { recommendModelForRole, type Recommendation } from './recommendation'
import { groupedTiers, tierClass } from './display'

function ModelRecommendationPanel({
  recommendation,
  activeModelId,
  onApply,
}: {
  recommendation: Recommendation
  activeModelId: string
  onApply: (modelId: string) => void
}) {
  const { t } = useTranslation()

  if (!recommendation.primary) {
    return (
      <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3 text-xs text-amber-200">
        {t('agentsConfig.noRecommendation')}
      </div>
    )
  }

  const primary = recommendation.primary
  const isActive = activeModelId === primary.id

  return (
    <div className="rounded-lg border border-brand-500/20 bg-brand-500/5 p-3 space-y-2">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wide text-brand-300">
            {t('agentsConfig.recommendedModel')}
          </p>
          <div className="mt-1 flex items-center gap-2 flex-wrap">
            <span className="font-mono text-sm text-white">{primary.id}</span>
            <span className={`rounded-md px-1.5 text-[11px] leading-5 ${tierClass(primary.tier)}`}>
              {primary.provider} · {primary.tier}
            </span>
          </div>
          <p className="mt-1 text-xs text-gray-400">
            {t(`agentsConfig.recommendationReasons.${recommendation.reasonKey}`)}
          </p>
        </div>
        <button
          type="button"
          onClick={() => onApply(primary.id)}
          disabled={isActive}
          className="rounded-lg border border-brand-500/30 bg-brand-500/10 px-2.5 py-1.5 text-[11px] font-semibold text-brand-200 disabled:opacity-50"
        >
          <span className="inline-flex items-center gap-1">
            <Sparkles size={12} />
            {isActive ? t('agentsConfig.recommendationApplied') : t('agentsConfig.applyRecommendation')}
          </span>
        </button>
      </div>

      {recommendation.alternatives.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-[11px]">
          <span className="text-gray-400">{t('agentsConfig.alternatives')}</span>
          {recommendation.alternatives.map(model => (
            <button
              key={model.id}
              type="button"
              onClick={() => onApply(model.id)}
              className="rounded-md border border-surface-700 bg-surface-800 px-1.5 leading-5 text-gray-300 hover:border-surface-600"
            >
              {model.id}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export function ModelSelector({
  draft,
  setDraft,
  models,
}: {
  draft: AgentDraft
  setDraft: React.Dispatch<React.SetStateAction<AgentDraft>>
  models: ModelCatalogEntry[]
}) {
  const { t } = useTranslation()
  const modelInfo = models.find(model => model.id === draft.model)
  const connectionOptions = useConnectionModelOptions()
  const connectionChoice = connectionOptions.find(option => option.value === draft.model)
  const recommendation = useMemo(
    () => recommendModelForRole(models, draft.role, draft.seniority),
    [models, draft.role, draft.seniority],
  )

  return (
    <div className="space-y-3">
      <ModelRecommendationPanel
        recommendation={recommendation}
        activeModelId={draft.model}
        onApply={(modelId) => setDraft(prev => ({ ...prev, model: modelId }))}
      />

      <div>
        <label className="input-label">{t('agentsConfig.model')}</label>
        <select
          className="select w-full text-xs"
          aria-label={t('agentsConfig.model')}
          value={draft.model}
          onChange={e => setDraft(prev => ({ ...prev, model: e.target.value }))}
        >
          <option value="">{t('agentsConfig.defaultForRole', { defaultValue: 'Default for role' })}</option>
          {draft.model && !modelInfo && !connectionChoice && (
            <option value={draft.model}>
              {t('agentsConfig.modelNotInCatalog', { model: draft.model, defaultValue: '{{model}} (not in catalog)' })}
            </option>
          )}
          {groupedTiers(models).map(tier => {
            const group = models.filter(model => model.tier === tier)
            if (!group.length) return null
            return (
              <optgroup key={tier} label={tier}>
                {group.map(model => (
                  <option key={model.id} value={model.id}>{model.id} ({model.provider})</option>
                ))}
              </optgroup>
            )
          })}
          <ConnectionModelOptgroups options={connectionOptions} />
        </select>
        {connectionChoice ? (
          <p className="mt-2 text-[11px] text-gray-400">{t('agentsConfig.connectionModelHint', { connection: connectionChoice.connection })}</p>
        ) : modelInfo ? (
          <div className="mt-2 flex items-center gap-2 flex-wrap">
            <span className={`rounded-md px-1.5 text-[11px] leading-5 ${tierClass(modelInfo.tier)}`}>
              {modelInfo.provider} · {modelInfo.tier}
            </span>
            <span className="text-[11px] text-gray-400">{modelInfo.description}</span>
          </div>
        ) : draft.model ? (
          <p className="mt-2 text-[11px] text-amber-300">{t('agentsConfig.customModelHint')}</p>
        ) : (
          <p className="mt-2 text-[11px] text-gray-400">
            {t('agentsConfig.defaultForRoleHint', { defaultValue: "Uses the server's default model for this role." })}
          </p>
        )}
      </div>
    </div>
  )
}
