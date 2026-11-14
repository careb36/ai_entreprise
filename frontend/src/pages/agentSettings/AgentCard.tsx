import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Bot, ChevronRight, Copy, Loader2, Save, Trash2 } from 'lucide-react'
import type { ManagedAgent, ModelCatalogEntry } from '../../services/api'
import { draftFromAgent, isDraftDirty, type AgentDraft } from './drafts'
import { tierClass } from './display'
import { ActivityRow } from './ActivityRow'
import { DraftFields } from './DraftFields'

export function AgentCard({
  agent,
  models,
  saving,
  deleting,
  expanded,
  confirmingDelete,
  readOnly,
  onToggleExpand,
  onSave,
  onDelete,
  onDuplicate,
}: {
  agent: ManagedAgent
  models: ModelCatalogEntry[]
  saving: boolean
  deleting: boolean
  expanded: boolean
  confirmingDelete: boolean
  readOnly: boolean
  onToggleExpand: () => void
  onSave: (agentId: number, draft: AgentDraft) => void
  onDelete: (agentId: number) => void
  onDuplicate: (agent: ManagedAgent) => void
}) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState<AgentDraft>(() => draftFromAgent(agent))

  useEffect(() => {
    setDraft(draftFromAgent(agent))
  }, [agent])

  const dirty = isDraftDirty(draft, agent)
  const modelInfo = models.find(model => model.id === agent.model)

  return (
    <div className={`rounded-xl border ${expanded ? 'border-brand-500/40' : 'border-surface-700'} bg-surface-900 overflow-hidden transition-colors`}>
      <button
        type="button"
        onClick={onToggleExpand}
        className="w-full flex items-start gap-3 p-3.5 text-left hover:bg-surface-800/40 transition-colors"
      >
        <ChevronRight
          size={16}
          className={`mt-0.5 flex-shrink-0 text-gray-500 transition-transform ${expanded ? 'rotate-90 text-brand-400' : ''}`}
        />
        <Bot size={16} className="mt-0.5 flex-shrink-0 text-brand-400" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-semibold text-gray-100 truncate">
              {agent.name || t('agentsConfig.unnamedAgent')}
            </span>
            <span className="rounded-full border border-surface-700 bg-surface-800 px-2 py-0.5 text-[10px] text-gray-300">
              {agent.role || '—'}
            </span>
            <span className="text-[10px] text-gray-500">{agent.seniority}</span>
            <span className="text-gray-700">·</span>
            <span className="font-mono text-[11px] text-gray-400 truncate">{agent.model || '—'}</span>
            {modelInfo && (
              <span className={`rounded-full px-1.5 py-0.5 text-[10px] ${tierClass(modelInfo.tier)}`}>
                {modelInfo.tier}
              </span>
            )}
            {dirty && (
              <span className="rounded-full bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-amber-300">
                {t('agentsConfig.unsaved')}
              </span>
            )}
          </div>
          <ActivityRow activity={agent.activity} className="mt-1.5" />
        </div>
        {!readOnly && <div
          className="flex items-center gap-1 flex-shrink-0"
          onClick={e => e.stopPropagation()}
          role="presentation"
        >
          <button
            type="button"
            onClick={() => onDuplicate(agent)}
            title={t('agentsConfig.duplicateTooltip')}
            className="rounded-lg border border-surface-700 bg-surface-800 px-2 py-1 text-[11px] text-gray-300 hover:bg-surface-700 transition-colors"
          >
            <span className="inline-flex items-center gap-1"><Copy size={12} /> {t('agentsConfig.duplicate')}</span>
          </button>
          <button
            type="button"
            onClick={() => onDelete(agent.id)}
            disabled={deleting}
            className={`rounded-lg border px-2 py-1 text-[11px] disabled:opacity-50 transition-colors ${
              confirmingDelete
                ? 'border-red-500 bg-red-500/20 text-red-200'
                : 'border-red-500/20 bg-red-500/5 text-red-300 hover:bg-red-500/10'
            }`}
          >
            <span className="inline-flex items-center gap-1">
              <Trash2 size={12} />
              {confirmingDelete ? t('agentsConfig.deleteConfirm') : t('agentsConfig.delete')}
            </span>
          </button>
        </div>}
      </button>

      {expanded && (
        <div className="border-t border-surface-800 p-4 space-y-4 bg-surface-900/40">
          <fieldset disabled={readOnly} className="min-w-0">
            <DraftFields draft={draft} setDraft={setDraft} models={models} />
          </fieldset>

          {!readOnly && <div className="flex items-center justify-between gap-3">
            <span className={`text-[11px] ${dirty ? 'text-amber-300' : 'text-gray-500'}`}>
              {dirty ? t('agentsConfig.dirtyHint') : t('agentsConfig.cleanHint')}
            </span>
            <div className="flex items-center gap-2">
              {dirty && (
                <button
                  type="button"
                  onClick={() => setDraft(draftFromAgent(agent))}
                  className="rounded-lg border border-surface-700 bg-surface-800 px-3 py-2 text-xs text-gray-300 hover:bg-surface-700"
                >
                  {t('agentsConfig.discard')}
                </button>
              )}
              <button
                type="button"
                onClick={() => onSave(agent.id, draft)}
                disabled={saving || !dirty}
                className={`rounded-lg px-3 py-2 text-xs font-semibold disabled:opacity-50 transition-colors ${
                  dirty ? 'bg-brand-600 text-white hover:bg-brand-500' : 'bg-surface-700 text-gray-400'
                }`}
              >
                <span className="inline-flex items-center gap-1.5">
                  {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
                  {saving ? t('agentsConfig.saving') : t('agentsConfig.save')}
                </span>
              </button>
            </div>
          </div>}
        </div>
      )}
    </div>
  )
}
