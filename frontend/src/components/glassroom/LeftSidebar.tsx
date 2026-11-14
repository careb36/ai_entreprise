import { useTranslation } from 'react-i18next'
import { Hash, Layers, Loader2 } from 'lucide-react'
import type { AgentIdentity } from './identity'
import { AgentAvatar } from './atoms'

// ── Left sidebar: sprint selector + agent roster ──────────────────────────────

export function LeftSidebar({
  sprintId, scope, status, activeAgents, onlineAgents,
}: {
  sprintId: string
  scope?: string
  status?: string
  activeAgents: { key: string; identity: AgentIdentity; elapsedMs: number }[]
  onlineAgents: AgentIdentity[]
}) {
  const { t } = useTranslation()
  const SCOPE_COLOR: Record<string, string> = {
    S: 'text-emerald-400',
    M: 'text-brand-400',
    L: 'text-amber-400',
  }

  const STATUS_DOT: Record<string, string> = {
    active:      'bg-emerald-400 animate-pulse',
    needs_split: 'bg-amber-400 animate-pulse',
    completed:   'bg-gray-400',
  }

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
          {status && (
            <span className="flex items-center gap-1 text-[10px] text-gray-500">
              <span className={`w-1.5 h-1.5 rounded-full ${STATUS_DOT[status] ?? 'bg-gray-600'}`} />
              {status}
            </span>
          )}
        </div>
      </div>

      {/* Active agents */}
      <div className="py-2 xl:pb-24 xl:flex-1 xl:min-h-0 xl:overflow-y-auto">
        <p className="text-[10px] font-semibold text-gray-600 uppercase tracking-wider px-3 mb-1">
          {t('glassroom.agentsTitle', 'Agents')}
        </p>
        {onlineAgents.length === 0 ? (
          <p className="text-[11px] text-gray-600 px-3 py-2 italic">{t('glassroom.waitingAgents', 'Waiting for agents…')}</p>
        ) : (
          onlineAgents.map((identity, i) => {
            const isActive = activeAgents.some(a => a.identity.initials === identity.initials)
            return (
              <div
                key={`${identity.label}-${i}`}
                className="flex items-center gap-2 px-3 py-1.5 hover:bg-surface-800/40 transition-colors rounded"
              >
                <AgentAvatar identity={identity} size="sm" />
                <span className={`text-[11px] font-medium ${identity.color} flex-1 truncate`}>
                  {identity.label}
                </span>
                {isActive
                  ? <Loader2 size={10} className="text-emerald-400 animate-spin shrink-0" />
                  : <span className="w-1.5 h-1.5 rounded-full bg-surface-700 shrink-0" />
                }
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
