import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Bot, ChevronDown, ChevronRight, Lightbulb, X } from 'lucide-react'
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
  const id = useId()

  return (
    <div className="panel space-y-3 p-4">
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-md border border-surface-700 text-gray-300">
            <Bot size={15} aria-hidden="true" />
          </span>
          <div className="min-w-0">
            {agent.name ? (
              <>
                <p className="text-[13px] font-semibold text-gray-100">{agent.name}</p>
                <p className="text-xs text-gray-400">{roleInfo.label} · {roleInfo.desc}</p>
              </>
            ) : (
              <>
                <p className="text-[13px] font-semibold text-gray-100">{roleInfo.label}</p>
                <p className="text-xs text-gray-400">{roleInfo.desc}</p>
              </>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1">
          {modelInfo && (
            <span className={`max-w-[10rem] truncate rounded-md px-1.5 font-mono text-[11px] leading-5 ${tierMeta.color}`} title={agent.model}>
              {tierMeta.icon} {agent.model}
            </span>
          )}
          {canRemove && (
            <button onClick={() => onRemove(index)}
              className="btn-tool px-2 hover:text-red-300"
              type="button" aria-label={t('companyNew.removeAgent')} title={t('companyNew.removeAgent')}><X size={13} aria-hidden="true" /></button>
          )}
        </div>
      </div>

      {/* Name / Alias */}
      <div>
        <label htmlFor={`${id}-name`} className="input-label">{t('companyNew.agentName')}</label>
        <input id={`${id}-name`} type="text" value={agent.name}
          onChange={e => onUpdate(index, 'name', e.target.value)}
          placeholder={t('companyNew.agentNamePlaceholder')}
          className="input w-full" />
      </div>

      {/* Role + Seniority */}
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label htmlFor={`${id}-role`} className="input-label">{t('companyNew.agentRole')}</label>
          <select id={`${id}-role`} value={agent.role} onChange={e => onUpdate(index, 'role', e.target.value)} className="select w-full">
            {roles.map(r => <option key={r.value} value={r.value}>{r.icon} {r.label}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor={`${id}-seniority`} className="input-label">{t('companyNew.seniority')}</label>
          <select id={`${id}-seniority`} value={agent.seniority} onChange={e => onUpdate(index, 'seniority', e.target.value)} className="select w-full">
            {seniorities.map(s => (
              <option key={s.value} value={s.value}>{s.label} ({s.desc})</option>
            ))}
          </select>
        </div>
      </div>

      {/* Model selector */}
      <div>
        <label htmlFor={`${id}-model`} className="input-label">{t('companyNew.llmModel')}</label>
        <select id={`${id}-model`} value={agent.model ?? ''}
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
          <p className="mt-1 text-[11px] text-gray-400">{modelInfo.description}</p>
        )}
      </div>

      {/* Specialty */}
      <div>
        <label htmlFor={`${id}-specialty`} className="input-label">{t('companyNew.specialty')}</label>
        <input id={`${id}-specialty`} type="text" value={agent.specialty || agent.persona}
          onChange={e => { onUpdate(index, 'persona', e.target.value); onUpdate(index, 'specialty', e.target.value) }}
          placeholder={t(`companyNew.specialtyPlaceholders.${['Dev', 'DevOps', 'QA'].includes(roleInfo.value) ? roleInfo.value : 'other'}`)}
          className="input w-full" />
      </div>

      {/* System prompt — collapsible */}
      {agent.system_prompt && (
        <div>
          <button type="button"
            onClick={() => setShowPrompt(v => !v)}
            aria-expanded={showPrompt}
            className="flex items-center gap-1 rounded-sm text-xs text-gray-400 hover:text-gray-200">
            {showPrompt ? <ChevronDown size={13} aria-hidden="true" /> : <ChevronRight size={13} aria-hidden="true" />}
            {t('companyNew.systemPrompt')}
          </button>
          {showPrompt && (
            <textarea
              aria-label={t('companyNew.systemPrompt')}
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
        <label htmlFor={`${id}-budget`} className="input-label">{t('companyNew.monthlyBudget')}</label>
        <div className="flex items-center gap-2">
          <input id={`${id}-budget`} type="range" min={100000} max={5000000} step={100000}
            value={agent.token_budget ?? 500000}
            onChange={e => onUpdate(index, 'token_budget', parseInt(e.target.value))}
            className="flex-1 accent-brand-500" />
          <span className="w-20 text-right font-mono text-xs tabular-nums text-brand-300">
            {t('companyNew.thousandTokens', { count: (agent.token_budget ?? 500000) / 1000 })}
          </span>
        </div>
      </div>

      {/* Badges + reasoning */}
      <div className="flex flex-wrap gap-1.5 pt-1">
        <span className={`chip ${senInfo.color}`}>{senInfo.label}</span>
        <span className="chip">{roleInfo.label}</span>
        {agent.specialty && <span className="chip border-brand-700/50 text-brand-300">{agent.specialty}</span>}
      </div>
      {agent.reasoning && (
        <p className="flex items-start gap-1.5 border-t border-surface-700 pt-2 text-xs italic text-gray-400">
          <Lightbulb size={13} aria-hidden="true" className="mt-px flex-shrink-0 not-italic" />{agent.reasoning}
        </p>
      )}
    </div>
  )
}
