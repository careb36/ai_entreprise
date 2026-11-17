import type { Dispatch, SetStateAction } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, Plus, X } from 'lucide-react'
import type { ModelCatalogEntry } from '../../services/api'
import { Panel } from '../../components/ui'
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
    <div id="agent-creator" className="scroll-mt-20">
      <Panel
        className="border-brand-700/50"
        title={t('agentsConfig.addAgent')}
        actions={
          <button
            type="button"
            onClick={onClose}
            className="btn-tool px-2"
            aria-label={t('agentsConfig.closeCreator')}
          >
            <X size={13} aria-hidden="true" />
          </button>
        }
      >
        <div className="space-y-4 p-3">
          <DraftFields draft={newAgent} setDraft={setNewAgent} models={models} />

          <div className="flex justify-end gap-2 border-t border-surface-700 pt-3">
            <button type="button" onClick={onCancel} className="btn-tool border border-surface-700">
              {t('agentsConfig.cancel')}
            </button>
            <button
              type="button"
              onClick={onCreate}
              disabled={creating || !newAgent.role.trim()}
              className="btn-primary btn-sm"
            >
              {creating
                ? <Loader2 size={13} aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
                : <Plus size={13} aria-hidden="true" />}
              {t('agentsConfig.create')}
            </button>
          </div>
        </div>
      </Panel>
    </div>
  )
}
