import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { TIER_META, localizedRoles, localizedSeniorities } from './constants'
import type { Agent, ModelInfo } from './types'

export function AgentCard({ agent, index, onUpdate, onRemove, canRemove, availableModels }: {
  agent: Agent
  index: number
  onUpdate: (i: number, field: keyof Agent, val: string | number) => void
  onRemove: (i: number) => void
  canRemove: boolean
  availableModels: ModelInfo[]
}) {
  const { t } = useTranslation()
  const roles = localizedRoles(t)
  const seniorities = localizedSeniorities(t)
  const roleInfo  = roles.find(r => r.value === agent.role) ?? roles[3]
  const senInfo   = seniorities.find(s => s.value === agent.seniority) ?? seniorities[2]
  const modelInfo = availableModels.find(m => m.id === (agent.model ?? ''))
  const tierMeta  = TIER_META[modelInfo?.tier ?? 'balanced']
  const [showPrompt, setShowPrompt] = useState(false)

  return (
    <div className="bg-surface-800 border border-surface-700 rounded-xl p-5 space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-2xl">{roleInfo.icon}</span>
          <div>
            {agent.name ? (
              <>
                <p className="text-sm font-medium text-white">{agent.name}</p>
                <p className="text-xs text-gray-500">{roleInfo.label} · {roleInfo.desc}</p>
              </>
            ) : (
              <>
                <p className="text-sm font-medium text-white">{roleInfo.label}</p>
                <p className="text-xs text-gray-500">{roleInfo.desc}</p>
              </>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1">
          {modelInfo && (
            <span className={`badge text-[10px] font-mono ${tierMeta.color}`}>
              {tierMeta.icon} {agent.model}
            </span>
          )}
          {canRemove && (
            <button onClick={() => onRemove(index)}
              className="text-gray-600 hover:text-red-400 transition-colors text-lg leading-none p-1 ml-1"
              title={t('companyNew.removeAgent')}>×</button>
          )}
        </div>
      </div>

      {/* Name / Alias */}
      <div>
        <label className="input-label">{t('companyNew.agentName')}</label>
        <input type="text" value={agent.name}
          onChange={e => onUpdate(index, 'name', e.target.value)}
          placeholder={t('companyNew.agentNamePlaceholder')}
          className="input w-full" />
      </div>

      {/* Role + Seniority */}
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="input-label">{t('companyNew.agentRole')}</label>
          <select value={agent.role} onChange={e => onUpdate(index, 'role', e.target.value)} className="select w-full">
            {roles.map(r => <option key={r.value} value={r.value}>{r.icon} {r.label}</option>)}
          </select>
        </div>
        <div>
          <label className="input-label">{t('companyNew.seniority')}</label>
          <select value={agent.seniority} onChange={e => onUpdate(index, 'seniority', e.target.value)} className="select w-full">
            {seniorities.map(s => (
              <option key={s.value} value={s.value}>{s.label} ({s.desc})</option>
            ))}
          </select>
        </div>
      </div>

      {/* Model selector */}
      <div>
        <label className="input-label">{t('companyNew.llmModel')}</label>
        <select value={agent.model ?? ''}
          onChange={e => onUpdate(index, 'model', e.target.value)} className="select w-full text-xs">
          <option value="">{t('companyNew.selectModel')}</option>
          {availableModels.length === 0 && (
            <option value="" disabled>{t('companyNew.loadingModels')}</option>
          )}
          {Object.keys(TIER_META).map(tier => {
            const group = availableModels.filter(m => m.tier === tier)
            if (!group.length) return null
            const tm = TIER_META[tier]
            return (
              <optgroup key={tier} label={`${tm.icon} ${t(`companyNew.tiers.${tier}`, { defaultValue: tm.label })}`}>
                {group.map(m => (
                  <option key={m.id} value={m.id}>
                    {m.id} ({m.provider})
                  </option>
                ))}
              </optgroup>
            )
          })}
          {agent.model && !availableModels.find(m => m.id === agent.model) && (
            <option value={agent.model}>{t('companyNew.unresolvedModel', { model: agent.model })}</option>
          )}
        </select>
        {modelInfo && (
          <p className="text-[10px] text-gray-600 mt-1">{modelInfo.description}</p>
        )}
      </div>

      {/* Specialty */}
      <div>
        <label className="input-label">{t('companyNew.specialty')}</label>
        <input type="text" value={agent.specialty || agent.persona}
          onChange={e => { onUpdate(index, 'persona', e.target.value); onUpdate(index, 'specialty', e.target.value) }}
          placeholder={t(`companyNew.specialtyPlaceholders.${['Dev', 'DevOps', 'QA'].includes(roleInfo.value) ? roleInfo.value : 'other'}`)}
          className="input w-full" />
      </div>

      {/* System prompt — collapsible */}
      {agent.system_prompt && (
        <div>
          <button type="button"
            onClick={() => setShowPrompt(v => !v)}
            className="text-xs text-gray-500 hover:text-gray-300 flex items-center gap-1 transition-colors">
            {showPrompt ? '▾' : '▸'} {t('companyNew.systemPrompt')}
          </button>
          {showPrompt && (
            <textarea
              value={agent.system_prompt}
              onChange={e => onUpdate(index, 'system_prompt', e.target.value)}
              rows={4}
              className="textarea w-full text-xs mt-1 text-gray-300 font-mono"
            />
          )}
        </div>
      )}

      {/* Token budget */}
      <div>
        <label className="input-label">{t('companyNew.monthlyBudget')}</label>
        <div className="flex items-center gap-2">
          <input type="range" min={100000} max={5000000} step={100000}
            value={agent.token_budget ?? 500000}
            onChange={e => onUpdate(index, 'token_budget', parseInt(e.target.value))}
            className="flex-1 accent-brand-500" />
          <span className="text-xs text-brand-300 w-20 text-right font-mono">
            {t('companyNew.thousandTokens', { count: (agent.token_budget ?? 500000) / 1000 })}
          </span>
        </div>
      </div>

      {/* Badges + reasoning */}
      <div className="flex gap-2 flex-wrap pt-1">
        <span className={`badge bg-surface-700 ${senInfo.color}`}>{senInfo.label}</span>
        <span className="badge bg-surface-700 text-gray-400">{roleInfo.label}</span>
        {agent.specialty && <span className="badge bg-brand-900/30 text-brand-300">{agent.specialty}</span>}
      </div>
      {agent.reasoning && (
        <p className="text-xs text-gray-500 italic border-t border-surface-700/50 pt-2 mt-1">
          💡 {agent.reasoning}
        </p>
      )}
    </div>
  )
}
