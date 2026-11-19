// The company's agents: list, edit, create and delete them. The cards, the
// draft fields, the model recommendation and the state hooks live in ./agentSettings/.
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Bot, Building2, Cpu, Eye, Info, SearchX } from 'lucide-react'
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
import { CountPill, EmptyState, LoadError, PageHeader, Panel } from '../components/ui'

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

  const filtered = Boolean(activeRoleFilter || searchQuery)

  return (
    <div className="page-container max-w-6xl space-y-4">
      <PageHeader
        title={t('agentsConfig.title')}
        subtitle={companyName ? t('agentsConfig.subtitleWithCompany', { company: companyName }) : t('agentsConfig.subtitle')}
        actions={
          <dl className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
            <div className="flex items-center gap-1.5">
              <dt className="text-meta flex items-center gap-1"><Building2 size={11} aria-hidden="true" />{t('agentsConfig.factory')}</dt>
              <dd className="text-gray-200">{companyName || '—'}</dd>
            </div>
            <div className="flex items-center gap-1.5">
              <dt className="text-meta flex items-center gap-1"><Cpu size={11} aria-hidden="true" />{t('agentsConfig.orchestrator')}</dt>
              <dd className="font-mono text-gray-200">{orchestrator || '—'}</dd>
            </div>
          </dl>
        }
      />

      {companyId === null ? (
        <EmptyState icon={Building2} title={t('agentsConfig.noCompany')} dashed />
      ) : (
        <>
          <Panel>
            <div className="flex items-start gap-2.5 p-3 text-xs text-gray-300">
              <Info size={14} aria-hidden="true" className="mt-0.5 flex-shrink-0 text-brand-300" />
              <div>
                <p className="text-[13px] font-medium text-gray-100">{t('agentsConfig.runtimeNoteTitle')}</p>
                <p className="mt-1">{t('agentsConfig.runtimeNote')}</p>
                <p className="mt-1.5 text-gray-400">{t('agentsConfig.shardingNote')}</p>
              </div>
            </div>
          </Panel>

          {readOnly && (
            <p role="status" className="flex items-center gap-1.5 text-xs text-gray-300">
              <Eye size={13} aria-hidden="true" className="flex-shrink-0 text-gray-400" />
              {t('agentsConfig.readOnly', { defaultValue: 'You can read the agents. Only a Manager or the Owner changes them.' })}
            </p>
          )}

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

          <div className="flex items-center gap-2">
            <h2 className="text-[13px] font-semibold text-gray-100">{t('agentsConfig.currentAgents')}</h2>
            <CountPill count={visibleAgents.length} total={filtered ? allAgents.length : undefined}
              label={filtered && allAgents.length !== visibleAgents.length
                ? t('agentsConfig.shownCount', { shown: visibleAgents.length, total: allAgents.length })
                : t('agentsConfig.agentCount', { count: visibleAgents.length })} />
          </div>

          {isLoading ? (
            <div aria-busy="true" className="space-y-2">
              <p className="sr-only">{t('agentsConfig.loading')}</p>
              {[0, 1, 2].map(index => <div key={index} className="skeleton h-14 rounded-xl" />)}
            </div>
          ) : isError ? (
            <LoadError onRetry={() => refetch()}>{t('agentsConfig.loadError')}</LoadError>
          ) : visibleAgents.length === 0 ? (
            filtered
              ? <EmptyState icon={SearchX} title={t('agentsConfig.emptyFiltered')} dashed />
              : <EmptyState icon={Bot} title={t('agentsConfig.emptyNoAgents')} dashed />
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
  )
}
