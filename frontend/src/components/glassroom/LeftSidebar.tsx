import { useTranslation } from 'react-i18next'
import { Hash, Layers, Loader2 } from 'lucide-react'
import type { IntegrationState, PauseKind } from '../../services/api'
import type { AgentIdentity } from './identity'
import type { ActiveAgent } from './liveStatus'
import { AgentAvatar } from './atoms'
import { SprintStatusBadge } from '../ui/SprintStatusBadge'

// ── Left sidebar: sprint selector + agent roster ──────────────────────────────

export function LeftSidebar({
  sprintId, scope, status, integrationState, pauseReason, pauseKind, activeAgents, onlineAgents,
}: {
  sprintId: string
  scope?: string
  status?: string
  integrationState?: IntegrationState
  pauseReason?: string | null
  pauseKind?: PauseKind | null
  activeAgents: ActiveAgent[]
  onlineAgents: AgentIdentity[]
}) {
  const { t } = useTranslation()
  const SCOPE_COLOR: Record<string, string> = {
    S: 'text-emerald-400',
    M: 'text-brand-400',
    L: 'text-amber-400',
  }
  const working = new Set(activeAgents.map(agent => agent.identity.key))

  return (
    <div className="w-full border-r lg:border-r border-b lg:border-b-0 border-surface-700/60 bg-surface-950 flex flex-col lg:min-h-0 lg:w-auto lg:min-w-0">
      {/* Sprint info */}
      <div className="p-3 border-b border-surface-700/60">
        <div className="flex items-center gap-2 mb-1">
          <Hash size={12} className="text-gray-500" />
          <span className="text-xs font-semibold text-gray-300 truncate">{t('glassroom.sprintLabel', { id: sprintId, defaultValue: 'Sprint #{{id}}' })}</span>
        </div>
        <div className="flex items-center gap-2">
          {scope && (
            <span className={`flex items-center gap-1 text-[10px] font-semibold ${SCOPE_COLOR[scope] ?? 'text-gray-400'}`}>
              <Layers size={9} /> {scope}
            </span>
          )}
          {status && <SprintStatusBadge status={status} integrationState={integrationState} />}
        </div>
        {status === 'paused' && (
          <p className="mt-1.5 text-[10px] leading-snug text-amber-300/80">
            {t(`workControl.pauseKind.${pauseKind ?? (pauseReason ? 'other' : 'person')}.title`)}
          </p>
        )}
      </div>

      {/* Active agents */}
      <div className="py-2 xl:pb-24 xl:flex-1 xl:min-h-0 xl:overflow-y-auto">
        <p className="text-[10px] font-semibold text-gray-600 uppercase tracking-wider px-3 mb-1">
          {t('glassroom.agentsTitle', 'Agents')}
        </p>
        {onlineAgents.length === 0 ? (
          <p className="text-[11px] text-gray-600 px-3 py-2 italic">{t('glassroom.waitingAgents', 'Waiting for agents…')}</p>
        ) : (
          onlineAgents.map(identity => {
            const isActive = working.has(identity.key)
            return (
              <div
                key={identity.key}
                className="flex items-center gap-2 px-3 py-1.5 hover:bg-surface-800/40 transition-colors rounded"
              >
                <AgentAvatar identity={identity} size="sm" />
                <span className={`text-[11px] font-medium ${identity.color} flex-1 truncate`}>
                  {identity.label}
                </span>
                {isActive
                  ? <Loader2 size={10} className="text-emerald-400 animate-spin shrink-0" aria-label={t('glassroom.agentState.working')} />
                  : <span className="w-1.5 h-1.5 rounded-full bg-surface-700 shrink-0" aria-label={t('glassroom.agentState.idle')} />
                }
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
