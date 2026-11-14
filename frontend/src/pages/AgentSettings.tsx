// The company's agents: list, edit, create and delete them. The cards, the
// draft fields, the model recommendation and the state hooks live in ./agentSettings/.
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Cpu, Users } from 'lucide-react'
import { useAgents, useCreateAgent, useCurrentRole, useDeleteAgent, useModels, useUpdateAgentConfig } from '../hooks/useQueries'
import type { ManagedAgent } from '../services/api'
import { useAppStore } from '../store/useAppStore'
import { useToast } from '../store/toastStore'
import { defaultDraft, draftFromAgent, type AgentDraft } from './agentSettings/drafts'
import { useAgentCreator } from './agentSettings/useAgentCreator'
import { useAgentList } from './agentSettings/useAgentList'
import { useConfirmDelete } from './agentSettings/useConfirmDelete'
import { Toolbar } from './agentSettings/Toolbar'
import { AgentCreator } from './agentSettings/AgentCreator'
import { RoleGroups } from './agentSettings/RoleGroups'

export default function AgentSettings() {
  const { t } = useTranslation()
  const toast = useToast()
  const companyId = useAppStore(state => state.companyId)
  const companyName = useAppStore(state => state.companyName)
  const { data, isLoading, isError, refetch } = useAgents(companyId)
  const { data: modelsData } = useModels()
  const createAgent = useCreateAgent(companyId)
  const updateAgent = useUpdateAgentConfig(companyId)
  const deleteAgent = useDeleteAgent(companyId)
  // A Viewer reads the agents; only a Manager or the Owner changes them (the
  // backend refuses the rest). Without a login, as in a local demo, nothing is hidden.
  const readOnly = useCurrentRole() === 'Viewer'

  const models = useMemo(() => modelsData?.models || [], [modelsData?.models])
  const orchestrator = modelsData?.orchestrator
  const allAgents = useMemo(() => data?.agents ?? [], [data?.agents])

  const { newAgent, setNewAgent, creatorOpen, setCreatorOpen, requestCreatorScroll } = useAgentCreator(models)
  const { confirmingDeleteId, handleDeleteRequest } = useConfirmDelete(agentId => {
    deleteAgent.mutate(agentId, {
      onSuccess: () => toast.success(t('agentsConfig.deleteSuccess')),
      onError: (err) => toast.error(err instanceof Error ? err.message : t('agentsConfig.deleteError')),
    })
  })
  const list = useAgentList(allAgents)
  const { searchQuery, activeRoleFilter, visibleAgents } = list

  function handleCreate() {
    if (!companyId) return
    createAgent.mutate({
      company_id: companyId,
      ...newAgent,
    }, {
      onSuccess: () => {
        setNewAgent(defaultDraft())
        setCreatorOpen(false)
        toast.success(t('agentsConfig.createSuccess'))
      },
      onError: (err) => toast.error(err instanceof Error ? err.message : t('agentsConfig.createError')),
    })
  }

  function handleSave(agentId: number, draft: AgentDraft) {
    updateAgent.mutate({ agentId, data: draft }, {
      onSuccess: () => toast.success(t('agentsConfig.saveSuccess')),
      onError: (err) => toast.error(err instanceof Error ? err.message : t('agentsConfig.saveError')),
    })
  }

  function handleDuplicate(agent: ManagedAgent) {
    setNewAgent({
      ...draftFromAgent(agent),
      name: agent.name ? `${agent.name} ${t('agentsConfig.duplicateSuffix')}` : '',
    })
    setCreatorOpen(true)
    requestCreatorScroll()
  }

  return (
    <div className="min-h-full bg-surface-950 p-4 lg:p-6">
      <div className="max-w-6xl mx-auto space-y-5">
        {/* Header */}
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-xl font-bold text-white">{t('agentsConfig.title')}</h1>
            <p className="text-sm text-gray-500 mt-1">
              {companyName ? t('agentsConfig.subtitleWithCompany', { company: companyName }) : t('agentsConfig.subtitle')}
            </p>
          </div>
          <div className="flex items-stretch gap-3 flex-wrap">
            <div className="rounded-xl border border-surface-700 bg-surface-900 px-4 py-3 text-xs text-gray-400 min-w-[14rem]">
              <div className="flex items-center gap-2"><Users size={14} className="text-brand-400" /> {t('agentsConfig.factory')}</div>
              <div className="mt-2 text-sm text-gray-200">{companyName || '—'}</div>
            </div>
            <div className="rounded-xl border border-surface-700 bg-surface-900 px-4 py-3 text-xs text-gray-400">
              <div className="flex items-center gap-2"><Cpu size={14} className="text-brand-400" /> {t('agentsConfig.orchestrator')}</div>
              <div className="mt-1 font-mono text-gray-200">{orchestrator || '—'}</div>
            </div>
          </div>
        </div>

        {companyId === null ? (
          <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 p-4 text-sm text-amber-200">
            {t('agentsConfig.noCompany')}
          </div>
        ) : (
          <>
            <div className="rounded-xl border border-brand-700/20 bg-brand-950/10 p-4 text-sm text-gray-300">
              <p className="font-medium text-brand-200">{t('agentsConfig.runtimeNoteTitle')}</p>
              <p className="mt-1">{t('agentsConfig.runtimeNote')}</p>
              <p className="mt-2 text-xs text-gray-500">{t('agentsConfig.shardingNote')}</p>
            </div>

            {readOnly && (
              <p role="status" className="rounded-xl border border-surface-700 bg-surface-900 p-3 text-xs text-gray-400">
                {t('agentsConfig.readOnly', { defaultValue: 'You can read the agents. Only a Manager or the Owner changes them.' })}
              </p>
            )}

            {/* Toolbar: search + filter chips + new-agent button */}
            <Toolbar
              searchQuery={searchQuery}
              setSearchQuery={list.setSearchQuery}
              refetch={() => refetch()}
              readOnly={readOnly}
              creatorOpen={creatorOpen}
              onToggleCreator={() => {
                setNewAgent(defaultDraft())
                setCreatorOpen(o => !o)
                if (!creatorOpen) requestCreatorScroll()
              }}
              filterChips={list.filterChips}
              activeRoleFilter={activeRoleFilter}
              setActiveRoleFilter={list.setActiveRoleFilter}
              allAgentsCount={allAgents.length}
              roleCounts={list.roleCounts}
            />

            {/* New-agent creator */}
            {creatorOpen && !readOnly && (
              <AgentCreator
                newAgent={newAgent}
                setNewAgent={setNewAgent}
                models={models}
                creating={createAgent.isPending}
                onClose={() => setCreatorOpen(false)}
                onCancel={() => {
                  setNewAgent(defaultDraft())
                  setCreatorOpen(false)
                }}
                onCreate={handleCreate}
              />
            )}

            {/* List header with count */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-sm text-gray-300">
                <Users size={16} className="text-brand-400" />
                <span>{t('agentsConfig.currentAgents')}</span>
                <span className="rounded-full bg-surface-800 px-2 py-0.5 text-xs text-gray-400">
                  {visibleAgents.length}
                  {(activeRoleFilter || searchQuery) && allAgents.length !== visibleAgents.length && (
                    <span className="text-gray-600"> / {allAgents.length}</span>
                  )}
                </span>
              </div>
            </div>

            {isLoading ? (
              <div className="text-sm text-gray-500">{t('agentsConfig.loading')}</div>
            ) : isError ? (
              <div className="rounded-xl border border-red-500/20 bg-red-500/5 p-4 text-sm text-red-200">{t('agentsConfig.loadError')}</div>
            ) : visibleAgents.length === 0 ? (
              <div className="rounded-xl border border-surface-700 bg-surface-900 p-8 text-center text-sm text-gray-500">
                {(searchQuery || activeRoleFilter)
                  ? t('agentsConfig.emptyFiltered')
                  : t('agentsConfig.emptyNoAgents')}
              </div>
            ) : (
              <RoleGroups
                orderedRoles={list.orderedRoles}
                groupedByRole={list.groupedByRole}
                collapsedRoles={list.collapsedRoles}
                toggleRoleCollapse={list.toggleRoleCollapse}
                models={models}
                expandedAgents={list.expandedAgents}
                confirmingDeleteId={confirmingDeleteId}
                readOnly={readOnly}
                isSaving={agentId => updateAgent.isPending && updateAgent.variables?.agentId === agentId}
                isDeleting={agentId => deleteAgent.isPending && deleteAgent.variables === agentId}
                toggleAgentExpand={list.toggleAgentExpand}
                handleSave={handleSave}
                handleDeleteRequest={handleDeleteRequest}
                handleDuplicate={handleDuplicate}
              />
            )}
          </>
        )}
      </div>
    </div>
  )
}
