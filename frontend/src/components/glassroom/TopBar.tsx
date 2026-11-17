import { useTranslation } from 'react-i18next'
import { Hash, Layers, RotateCcw, Search, X } from 'lucide-react'
import type { AgentIdentity } from './identity'
import type { getEventTypes } from './format'
import type { FeedFilters } from './useFeed'

/** The room's top bar: sprint, scope, connection, the feed's filters and who is present. */
export function TopBar({ sprintId, scopeSize, isConnected, filters, eventTypes, onlineAgents }: {
  sprintId: string
  scopeSize?: string
  isConnected: boolean
  filters: FeedFilters
  eventTypes: ReturnType<typeof getEventTypes>
  onlineAgents: AgentIdentity[]
}) {
  const { t } = useTranslation()
  return (
    <div className="flex items-center gap-2 px-4 py-2 min-h-11 border-b border-surface-700/60 bg-surface-900 shrink-0 flex-wrap overflow-x-auto">
      <Hash size={13} className="text-gray-600" />
      <span className="text-sm font-semibold text-gray-200">{t('glassroom.sprintLabel', { id: sprintId, defaultValue: 'Sprint #{{id}}' })}</span>
      {scopeSize && (
        <span className={`text-[10px] font-semibold flex items-center gap-0.5 ${
          scopeSize === 'S' ? 'text-emerald-400' :
          scopeSize === 'L' ? 'text-amber-400' : 'text-brand-400'
        }`}>
          <Layers size={9} />
          {scopeSize}
        </span>
      )}
      <span className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-emerald-400 animate-pulse' : 'bg-red-500'}`} />

      {/* Filters */}
      <div className="flex items-center gap-1.5 ml-3">
        <div className="relative">
          <Search size={11} className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-600" />
          <input
            type="text"
            placeholder={t('glassroom.searchPlaceholder', { defaultValue: 'Search…' })}
            value={filters.searchText}
            onChange={e => filters.setSearchText(e.target.value)}
            className="pl-6 pr-5 py-1 text-[11px] bg-surface-800 border border-surface-700 rounded-md text-gray-300 placeholder-gray-600 focus:outline-none focus:border-brand-500 w-32 transition-colors"
          />
          {filters.searchText && (
            <button onClick={() => filters.setSearchText('')} className="absolute right-1.5 top-1/2 -translate-y-1/2">
              <X size={11} className="text-gray-500 hover:text-gray-300" />
            </button>
          )}
        </div>

        <select
          value={filters.filterAgent}
          onChange={e => filters.setFilterAgent(e.target.value)}
          className="py-1 px-2 text-[11px] bg-surface-800 border border-surface-700 rounded-md text-gray-300 focus:outline-none focus:border-brand-500"
        >
          <option value="">{t('glassroom.allAgents', 'All agents')}</option>
          {onlineAgents.map(a => (
            <option key={a.label} value={a.label}>{a.label}</option>
          ))}
        </select>

        <select
          value={filters.filterType}
          onChange={e => filters.setFilterType(e.target.value)}
          className="py-1 px-2 text-[11px] bg-surface-800 border border-surface-700 rounded-md text-gray-300 focus:outline-none focus:border-brand-500"
        >
          {eventTypes.map(et => (
            <option key={et.value} value={et.value}>{et.label}</option>
          ))}
        </select>

        {filters.isFiltered && (
          <button
            onClick={filters.clearFilters}
            className="p-1 hover:bg-surface-700 rounded text-gray-500 hover:text-gray-300 transition-colors"
            title={t('glassroom.clearFilters', { defaultValue: 'Clear filters' })}
          >
            <RotateCcw size={11} />
          </button>
        )}
      </div>

      {/* Presence avatars */}
      {onlineAgents.length > 0 && (
        <div className="flex items-center gap-1.5 ml-auto">
          <div className="flex -space-x-1.5">
            {onlineAgents.slice(0, 5).map((a, i) => (
              <div
                key={`${a.label}-${i}`}
                title={a.label}
                className={`w-5 h-5 ${a.bg} rounded-md flex items-center justify-center text-[8px] font-bold text-white ring-1 ring-surface-900`}
              >
                {a.initials}
              </div>
            ))}
            {onlineAgents.length > 5 && (
              <div className="w-5 h-5 bg-surface-700 rounded-md flex items-center justify-center text-[8px] font-bold text-gray-400 ring-1 ring-surface-900">
                +{onlineAgents.length - 5}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
