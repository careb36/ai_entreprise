import type { Dispatch, SetStateAction } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Search, X } from 'lucide-react'

/** Search, refresh, the new-agent button and the role filter chips. */
export function Toolbar({
  searchQuery, setSearchQuery, refetch, readOnly, creatorOpen, onToggleCreator, filterChips, activeRoleFilter,
  setActiveRoleFilter, allAgentsCount, roleCounts,
}: {
  searchQuery: string
  setSearchQuery: (value: string) => void
  refetch: () => void
  readOnly: boolean
  creatorOpen: boolean
  onToggleCreator: () => void
  filterChips: string[]
  activeRoleFilter: string | null
  setActiveRoleFilter: Dispatch<SetStateAction<string | null>>
  allAgentsCount: number
  roleCounts: Map<string, number>
}) {
  const { t } = useTranslation()
  return (
    <div className="rounded-xl border border-surface-700 bg-surface-900 p-3 space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <div className="relative flex-1 min-w-[220px]">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
          <input
            type="search"
            aria-label={t('agentsConfig.searchPlaceholder')}
            className="input w-full pl-9"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            placeholder={t('agentsConfig.searchPlaceholder')}
          />
          {searchQuery && (
            <button
              type="button"
              aria-label={t('agentsConfig.clearSearch')}
              onClick={() => setSearchQuery('')}
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-gray-500 hover:text-gray-300"
            >
              <X size={14} />
            </button>
          )}
        </div>
        <button
          type="button"
          onClick={() => refetch()}
          className="rounded-lg border border-surface-700 bg-surface-800 px-3 py-2 text-xs text-gray-300 hover:bg-surface-700"
        >
          {t('agentsConfig.refresh')}
        </button>
        {!readOnly && (
          <button
            type="button"
            onClick={onToggleCreator}
            className="rounded-lg bg-brand-600 px-3 py-2 text-xs font-semibold text-white hover:bg-brand-500"
          >
            <span className="inline-flex items-center gap-1.5">
              <Plus size={12} />
              {creatorOpen ? t('agentsConfig.closeCreator') : t('agentsConfig.newAgent')}
            </span>
          </button>
        )}
      </div>

      {filterChips.length > 0 && (
        <div className="flex items-center gap-1.5 flex-wrap">
          <button
            type="button"
            onClick={() => setActiveRoleFilter(null)}
            className={`rounded-full px-2.5 py-1 text-[11px] font-semibold transition-colors ${
              activeRoleFilter === null
                ? 'bg-brand-500/20 text-brand-200 border border-brand-500/40'
                : 'border border-surface-700 bg-surface-800 text-gray-400 hover:text-gray-200'
            }`}
          >
            {t('agentsConfig.filterAll')} <span className="opacity-60">{allAgentsCount}</span>
          </button>
          {filterChips.map(role => (
            <button
              key={role}
              type="button"
              onClick={() => setActiveRoleFilter(prev => (prev === role ? null : role))}
              className={`rounded-full px-2.5 py-1 text-[11px] font-semibold transition-colors ${
                activeRoleFilter === role
                  ? 'bg-brand-500/20 text-brand-200 border border-brand-500/40'
                  : 'border border-surface-700 bg-surface-800 text-gray-400 hover:text-gray-200'
              }`}
            >
              {role} <span className="opacity-60">{roleCounts.get(role)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
