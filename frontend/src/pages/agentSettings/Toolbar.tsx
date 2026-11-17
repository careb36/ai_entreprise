import type { Dispatch, SetStateAction } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, RefreshCw, Search, X } from 'lucide-react'
import { SegmentedFilter, Toolbar as BarGroup, type SegmentedOption } from '../../components/ui'

const ALL = '__all__'

/** Search, refresh, the new-agent button and the role presets, in the board's toolbar. */
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
  const options: SegmentedOption<string>[] = [
    { value: ALL, label: t('agentsConfig.filterAll'), count: allAgentsCount },
    ...filterChips.map(role => ({ value: role, label: role, count: roleCounts.get(role) })),
  ]
  return (
    <BarGroup label={t('agentsConfig.toolbarLabel')} sticky>
      <div className="relative min-w-[220px] flex-1">
        <Search size={13} aria-hidden="true" className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
        <input
          type="search"
          aria-label={t('agentsConfig.searchPlaceholder')}
          className="input control-sm w-full pl-8 pr-8"
          value={searchQuery}
          onChange={e => setSearchQuery(e.target.value)}
          placeholder={t('agentsConfig.searchPlaceholder')}
        />
        {searchQuery && (
          <button
            type="button"
            aria-label={t('agentsConfig.clearSearch')}
            onClick={() => setSearchQuery('')}
            className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-1 text-gray-400 hover:text-gray-200"
          >
            <X size={13} aria-hidden="true" />
          </button>
        )}
      </div>
      {filterChips.length > 0 && (
        <SegmentedFilter label={t('agentsConfig.roleFilterLabel')} options={options} value={activeRoleFilter ?? ALL}
          onChange={value => setActiveRoleFilter(prev => (value === ALL || prev === value ? null : value))} />
      )}
      <button type="button" onClick={() => refetch()} className="btn-tool">
        <RefreshCw size={13} aria-hidden="true" />{t('agentsConfig.refresh')}
      </button>
      {!readOnly && (
        <button type="button" onClick={onToggleCreator} aria-expanded={creatorOpen} className="btn-primary btn-sm h-8">
          {creatorOpen ? <X size={13} aria-hidden="true" /> : <Plus size={13} aria-hidden="true" />}
          {creatorOpen ? t('agentsConfig.closeCreator') : t('agentsConfig.newAgent')}
        </button>
      )}
    </BarGroup>
  )
}
