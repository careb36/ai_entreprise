import { useTranslation } from 'react-i18next'
import type { ManagedAgent, ModelCatalogEntry } from '../../services/api'
import { ChevronRight } from 'lucide-react'
import { CountPill, Panel } from '../../components/ui'
import type { AgentDraft } from './drafts'
import { AgentCard } from './AgentCard'

/** The agents grouped by role, one panel per role, each collapsible. */
export function RoleGroups({
  orderedRoles, groupedByRole, collapsedRoles, toggleRoleCollapse, models, expandedAgents, confirmingDeleteId,
  readOnly, isSaving, isDeleting, toggleAgentExpand, handleSave, handleDeleteRequest, handleDuplicate,
}: {
  orderedRoles: string[]
  groupedByRole: Record<string, ManagedAgent[]>
  collapsedRoles: Set<string>
  toggleRoleCollapse: (role: string) => void
  models: ModelCatalogEntry[]
  expandedAgents: Set<number>
  confirmingDeleteId: number | null
  readOnly: boolean
  isSaving: (agentId: number) => boolean
  isDeleting: (agentId: number) => boolean
  toggleAgentExpand: (agentId: number) => void
  handleSave: (agentId: number, draft: AgentDraft) => void
  handleDeleteRequest: (agentId: number) => void
  handleDuplicate: (agent: ManagedAgent) => void
}) {
  const { t } = useTranslation()
  return (
    <div className="space-y-3">
      {orderedRoles.map(role => {
        const groupAgents = groupedByRole[role]
        const isCollapsed = collapsedRoles.has(role)
        return (
          <Panel
            key={role}
            title={
              <button
                type="button"
                onClick={() => toggleRoleCollapse(role)}
                aria-expanded={!isCollapsed}
                className="group flex items-center gap-1.5 rounded-sm text-left"
              >
                <ChevronRight
                  size={14}
                  aria-hidden="true"
                  className={`text-gray-400 transition-transform group-hover:text-gray-200 motion-reduce:transition-none ${isCollapsed ? '' : 'rotate-90'}`}
                />
                {role}
              </button>
            }
            count={<CountPill count={groupAgents.length} label={t('agentsConfig.agentCount', { count: groupAgents.length })} />}
          >
            {!isCollapsed && (
              <ul className="data-list">
                {groupAgents.map(agent => (
                  <AgentCard
                    key={agent.id}
                    agent={agent}
                    models={models}
                    expanded={expandedAgents.has(agent.id)}
                    confirmingDelete={confirmingDeleteId === agent.id}
                    readOnly={readOnly}
                    saving={isSaving(agent.id)}
                    deleting={isDeleting(agent.id)}
                    onToggleExpand={() => toggleAgentExpand(agent.id)}
                    onSave={handleSave}
                    onDelete={handleDeleteRequest}
                    onDuplicate={handleDuplicate}
                  />
                ))}
              </ul>
            )}
          </Panel>
        )
      })}
    </div>
  )
}
