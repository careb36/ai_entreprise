import { useTranslation } from 'react-i18next'
import type { SprintRetro } from '../../store/agentStore'
import { getAgent } from './identity'
import type {
  AssistantSummaryEntry, HealthPanelEntry, QuotaTelemetry, SprintTaskEntry, TaskAutonomyEntry,
} from './types'
import { AgentAvatar } from './atoms'
import {
  AssistantSummaryPanel, HealthPanel, QuotaTelemetryPanel, SprintRetroPanel, SprintTaskPanel, TaskAutonomyTree,
} from './panels'

// ── Right panel: agent detail ─────────────────────────────────────────────────

export function RightPanel({ sprintId, agentStates, messageCount, isConnected, assistantSummary, sprintTasks, healthEntries, autonomyTree, quotaTelemetry, lastRetro }: {
  sprintId: string
  agentStates: Record<string, { role: string; fsmState: string; currentActivity: string; color: string; lastSeen: string; lastTaskTitle?: string; lastTaskId?: number; lastSemanticType?: string }>
  messageCount: number
  isConnected: boolean
  assistantSummary: AssistantSummaryEntry[]
  sprintTasks: SprintTaskEntry[]
  healthEntries: HealthPanelEntry[]
  autonomyTree: TaskAutonomyEntry[]
  quotaTelemetry?: QuotaTelemetry
  lastRetro?: SprintRetro | null
}) {
  const { t } = useTranslation()
  const entries = Object.entries(agentStates)

  return (
    <div className="w-full border-l lg:border-l border-t lg:border-t-0 border-surface-700/60 bg-surface-950 flex flex-col lg:min-h-0 lg:w-auto lg:min-w-0 max-h-none lg:max-h-none overflow-visible lg:overflow-y-auto lg:overflow-x-hidden xl:pb-24">
      <div className="p-3 border-b border-surface-700/60">
        <p className="text-[10px] font-semibold text-gray-600 uppercase tracking-wider">{t('glassroom.liveStateTitle')}</p>
      </div>

      <HealthPanel entries={healthEntries} />
      <SprintRetroPanel retro={lastRetro ?? null} />
      <QuotaTelemetryPanel quota={quotaTelemetry} />
      <SprintTaskPanel tasks={sprintTasks} sprintId={sprintId} />
      <TaskAutonomyTree tasks={autonomyTree} />
      <AssistantSummaryPanel entries={assistantSummary} />

      <div className="py-2 space-y-px shrink-0">
        {entries.length === 0 ? (
          <p className="text-[11px] text-gray-600 px-3 py-2 italic">{t('glassroom.noAgentActivity')}</p>
        ) : (
          entries.map(([role, state]) => {
            const identity = getAgent(role)
            return (
              <div key={role} className="px-3 py-2 hover:bg-surface-800/40 transition-colors">
                <div className="flex items-center gap-2 mb-1">
                  <AgentAvatar identity={identity} size="sm" />
                  <span className={`text-[11px] font-semibold ${identity.color} truncate flex-1`}>
                    {identity.label}
                  </span>
                </div>
                <div className="ml-7">
                  <span className={`inline-block text-[10px] font-medium px-1.5 py-0.5 rounded-full ${
                    state.fsmState === 'active'  ? 'bg-emerald-500/10 text-emerald-400' :
                    state.fsmState === 'waiting' ? 'bg-brand-500/10 text-brand-400'    :
                    state.fsmState === 'error'   ? 'bg-red-500/10 text-red-400'        :
                                                   'bg-surface-800 text-gray-600'
                  }`}>
                    {state.fsmState}
                  </span>
                  {state.currentActivity && (
                    <p className="text-[10px] text-gray-600 mt-0.5 break-words leading-tight line-clamp-2">
                      {state.currentActivity}
                    </p>
                  )}
                  {state.lastTaskTitle && (
                    <p className="text-[10px] text-gray-500 mt-1 break-words leading-tight">
                      {state.lastTaskTitle}
                    </p>
                  )}
                </div>
              </div>
            )
          })
        )}
      </div>

      {/* Stats footer */}
      <div className="p-3 border-t border-surface-700/60 space-y-1.5">
        <div className="flex items-center justify-between text-[10px]">
          <span className="text-gray-600">{t('glassroom.healthLabels.stream')}</span>
          <span className={`flex items-center gap-1 font-medium ${isConnected ? 'text-emerald-400' : 'text-red-400'}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-emerald-400 animate-pulse' : 'bg-red-400'}`} />
            {isConnected ? t('glassroom.streamStatus.live', 'Live') : t('glassroom.streamStatus.off', 'Off')}
          </span>
        </div>
        <div className="flex items-center justify-between text-[10px]">
          <span className="text-gray-600">{t('glassroom.eventsLabel', 'Events')}</span>
          <span className="text-gray-400 font-medium tabular-nums">{messageCount}</span>
        </div>
      </div>
    </div>
  )
}
