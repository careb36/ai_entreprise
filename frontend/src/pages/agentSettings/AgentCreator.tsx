import type { Dispatch, SetStateAction } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, Plus, X } from 'lucide-react'
import type { ModelCatalogEntry } from '../../services/api'
import type { AgentDraft } from './drafts'
import { DraftFields } from './DraftFields'

/** The form that creates an agent, from scratch or from a duplicate. */
export function AgentCreator({ newAgent, setNewAgent, models, creating, onClose, onCancel, onCreate }: {
  newAgent: AgentDraft
  setNewAgent: Dispatch<SetStateAction<AgentDraft>>
  models: ModelCatalogEntry[]
  creating: boolean
  onClose: () => void
  onCancel: () => void
  onCreate: () => void
}) {
  const { t } = useTranslation()
  return (
    <div id="agent-creator" className="rounded-xl border border-brand-500/30 bg-surface-900 p-4 space-y-4">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Plus size={16} className="text-brand-400" />
          <h2 className="text-sm font-semibold text-gray-100">{t('agentsConfig.addAgent')}</h2>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded p-1 text-gray-500 hover:text-gray-300"
          aria-label={t('agentsConfig.closeCreator')}
        >
          <X size={14} />
        </button>
      </div>

      <DraftFields draft={newAgent} setDraft={setNewAgent} models={models} />

      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-lg border border-surface-700 bg-surface-800 px-3 py-2 text-xs text-gray-300"
        >
          {t('agentsConfig.cancel')}
        </button>
        <button
          type="button"
          onClick={onCreate}
          disabled={creating || !newAgent.role.trim()}
          className="rounded-lg bg-brand-600 px-3 py-2 text-xs font-semibold text-white disabled:opacity-50 hover:bg-brand-500"
        >
          <span className="inline-flex items-center gap-1.5">
            {creating ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />}
            {t('agentsConfig.create')}
          </span>
        </button>
      </div>
    </div>
  )
}
