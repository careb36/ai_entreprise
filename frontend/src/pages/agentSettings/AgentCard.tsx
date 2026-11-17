import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Bot, ChevronRight, Copy, Loader2, PencilLine, Save, Trash2 } from 'lucide-react'
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

  const name = agent.name || t('agentsConfig.unnamedAgent')

  return (
    <li className={expanded ? 'bg-surface-950/30' : ''}>
      <div className="flex items-start gap-2 px-3 py-2.5 transition-colors hover:bg-surface-800/40 motion-reduce:transition-none">
        <button
          type="button"
          onClick={onToggleExpand}
          aria-expanded={expanded}
          className="flex min-w-0 flex-1 items-start gap-2.5 rounded-sm text-left"
        >
          <ChevronRight
            size={14}
            aria-hidden="true"
            className={`mt-0.5 flex-shrink-0 text-gray-400 transition-transform motion-reduce:transition-none ${expanded ? 'rotate-90 text-brand-300' : ''}`}
          />
          <Bot size={14} aria-hidden="true" className="mt-0.5 flex-shrink-0 text-gray-400" />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="truncate text-[13px] font-semibold text-gray-100">{name}</span>
              <span className="chip">{agent.role || '—'}</span>
              <span className="text-meta">{agent.seniority}</span>
              <span className="text-gray-400" aria-hidden="true">·</span>
              <span className="truncate font-mono text-[11px] text-gray-300">{agent.model || '—'}</span>
              {modelInfo && (
                <span className={`rounded-md px-1.5 text-[11px] leading-5 ${tierClass(modelInfo.tier)}`}>
                  {modelInfo.tier}
                </span>
              )}
              {dirty && (
                <span className="status-badge border-amber-700/50 bg-amber-950/50 text-amber-300">
                  <PencilLine size={11} aria-hidden="true" />{t('agentsConfig.unsaved')}
                </span>
              )}
            </div>
            <ActivityRow activity={agent.activity} className="mt-1" />
          </div>
        </button>
        {!readOnly && (
          <div className="flex flex-shrink-0 items-center gap-1">
            <button
              type="button"
              onClick={() => onDuplicate(agent)}
              title={t('agentsConfig.duplicateTooltip')}
              className="btn-tool"
            >
              <Copy size={13} aria-hidden="true" />{t('agentsConfig.duplicate')}
            </button>
            <button
              type="button"
              onClick={() => onDelete(agent.id)}
              disabled={deleting}
              className={`btn-tool ${confirmingDelete ? 'bg-red-950/60 text-red-200 ring-1 ring-inset ring-red-700/60' : 'hover:text-red-300'}`}
            >
              <Trash2 size={13} aria-hidden="true" />
              {confirmingDelete ? t('agentsConfig.deleteConfirm') : t('agentsConfig.delete')}
            </button>
          </div>
        )}
      </div>

      {expanded && (
        <div className="space-y-4 border-t border-surface-700 px-3 py-3 sm:pl-10">
          <fieldset disabled={readOnly} className="min-w-0">
            <DraftFields draft={draft} setDraft={setDraft} models={models} />
          </fieldset>

          {!readOnly && <div className="flex flex-wrap items-center justify-between gap-3 border-t border-surface-700 pt-3">
            <span className={`text-[11px] ${dirty ? 'text-amber-300' : 'text-gray-400'}`}>
              {dirty ? t('agentsConfig.dirtyHint') : t('agentsConfig.cleanHint')}
            </span>
            <div className="flex items-center gap-2">
              {dirty && (
                <button
                  type="button"
                  onClick={() => setDraft(draftFromAgent(agent))}
                  className="btn-tool border border-surface-700"
                >
                  {t('agentsConfig.discard')}
                </button>
              )}
              <button
                type="button"
                onClick={() => onSave(agent.id, draft)}
                disabled={saving || !dirty}
                className="btn-primary btn-sm"
              >
                {saving
                  ? <Loader2 size={13} aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
                  : <Save size={13} aria-hidden="true" />}
                {saving ? t('agentsConfig.saving') : t('agentsConfig.save')}
              </button>
            </div>
          </div>}
        </div>
      )}
    </li>
  )
}
