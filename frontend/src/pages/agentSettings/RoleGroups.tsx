import type { ManagedAgent, ModelCatalogEntry } from '../../services/api'
import { ChevronRight } from 'lucide-react'
import type { AgentDraft } from './drafts'
import { AgentCard } from './AgentCard'

/** The agents grouped by role, each group collapsible. */
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
  return (
    <div className="space-y-5">
      {orderedRoles.map(role => {
        const groupAgents = groupedByRole[role]
        const isCollapsed = collapsedRoles.has(role)
        return (
          <section key={role}>
            <button
              type="button"
              onClick={() => toggleRoleCollapse(role)}
              className="w-full flex items-center gap-2 text-left mb-2.5 group"
            >
              <ChevronRight
                size={14}
                className={`text-gray-500 transition-transform group-hover:text-gray-300 ${isCollapsed ? '' : 'rotate-90'}`}
              />
              <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-300">
                {role}
              </h3>
              <span className="rounded-full bg-surface-800 px-2 py-0.5 text-[10px] text-gray-400">
                {groupAgents.length}
              </span>
            </button>
            {!isCollapsed && (
              <div className="space-y-3">
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
              </div>
            )}
          </section>
        )
      })}
    </div>
  )
}
