import type { RefObject } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Activity, AlertTriangle, Hourglass, LayoutList, Maximize2, Minimize2, RefreshCw, Rows3, Search, UserRoundCheck, X,
} from 'lucide-react'
import { BOARD_PRESETS, EMPTY_FILTERS, filtersActive, type BoardFilters, type BoardPreset } from '../../lib/kanban'
import type { BoardDensity } from '../../hooks/useBoardPreferences'

const PRESET_ICON: Record<BoardPreset, typeof Search | null> = {
  all: null,
  needsPerson: UserRoundCheck,
  highRisk: AlertTriangle,
  waiting: Hourglass,
}

export interface BoardToolbarProps {
  title: string
  total: number
  shown: number
  filters: BoardFilters
  onFiltersChange: (filters: BoardFilters) => void
  roles: string[]
  presetCounts: Record<BoardPreset, number>
  density: BoardDensity
  onDensityChange: (density: BoardDensity) => void
  allCollapsed: boolean
  onToggleAll: () => void
  activityOpen: boolean
  onToggleActivity: () => void
  activityPanelId: string
  onRefresh: () => void
  refreshing: boolean
  searchRef: RefObject<HTMLInputElement>
}

const TOOL = 'btn-ghost btn-sm h-8 gap-1.5 px-2.5'

/** The board's header: its name, the filters and presets, and the layout controls. */
export function BoardToolbar(props: BoardToolbarProps) {
  const { t } = useTranslation()
  const { filters, onFiltersChange } = props
  const active = filtersActive(filters)
  const set = (patch: Partial<BoardFilters>) => onFiltersChange({ ...filters, ...patch })
  const compact = props.density === 'compact'

  return (
    <div className="flex-shrink-0 space-y-2.5 border-b border-surface-700 px-4 py-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <div className="flex min-w-0 items-baseline gap-2">
          <h2 className="truncate text-sm font-semibold text-gray-100">{props.title}</h2>
          <span className="text-xs tabular-nums text-gray-500">{t('kanbanBoard.toolbar.total', { count: props.total })}</span>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-1">
          <button type="button" className={`${TOOL} ${compact ? 'bg-surface-800 text-brand-300' : ''}`}
            aria-pressed={compact} onClick={() => props.onDensityChange(compact ? 'comfortable' : 'compact')}>
            {compact ? <Rows3 size={13} aria-hidden="true" /> : <LayoutList size={13} aria-hidden="true" />}
            <span className="sr-only sm:not-sr-only">{t('kanbanBoard.toolbar.compact')}</span>
          </button>
          <button type="button" className={TOOL} onClick={props.onToggleAll}>
            {props.allCollapsed ? <Maximize2 size={13} aria-hidden="true" /> : <Minimize2 size={13} aria-hidden="true" />}
            <span className="sr-only sm:not-sr-only">{props.allCollapsed ? t('kanbanBoard.toolbar.expandAll') : t('kanbanBoard.toolbar.collapseAll')}</span>
          </button>
          <button type="button" className={`${TOOL} ${props.activityOpen ? 'bg-surface-800 text-brand-300' : ''}`}
            aria-expanded={props.activityOpen} aria-controls={props.activityPanelId} onClick={props.onToggleActivity}>
            <Activity size={13} aria-hidden="true" />
            <span>{t('kanbanBoard.toolbar.activity')}</span>
          </button>
          <button type="button" className={TOOL} onClick={props.onRefresh} disabled={props.refreshing}>
            <RefreshCw size={13} aria-hidden="true" className={props.refreshing ? 'animate-spin motion-reduce:animate-none' : ''} />
            <span className="sr-only sm:not-sr-only">{t('kanban.refresh')}</span>
          </button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <label className="relative flex w-full items-center sm:w-56">
          <span className="sr-only">{t('kanbanBoard.toolbar.search')}</span>
          <Search size={13} aria-hidden="true" className="pointer-events-none absolute left-2.5 text-gray-500" />
          <input ref={props.searchRef} type="search" value={filters.search}
            onChange={e => set({ search: e.target.value })}
            placeholder={t('kanbanBoard.toolbar.searchPlaceholder')} aria-keyshortcuts="/"
            className="input h-8 py-1 pl-8 pr-8 text-xs" />
          <kbd aria-hidden="true" className="pointer-events-none absolute right-2 hidden sm:block rounded border border-surface-600 px-1 text-[10px] text-gray-500">/</kbd>
        </label>
        <label className="flex items-center gap-1.5 text-xs text-gray-400">
          <span className="sr-only">{t('kanbanBoard.toolbar.role')}</span>
          <select value={filters.role} onChange={e => set({ role: e.target.value })}
            className="select h-8 w-auto py-1 pr-7 text-xs">
            <option value="">{t('kanbanBoard.toolbar.allRoles')}</option>
            {props.roles.map(role => (
              <option key={role} value={role}>{t(`teamRoles.${role}`, { defaultValue: role })}</option>
            ))}
          </select>
        </label>
        <div role="group" aria-label={t('kanbanBoard.toolbar.presets')}
          className="flex max-w-full flex-nowrap items-center gap-0.5 overflow-x-auto rounded-lg border border-surface-700 bg-surface-950/50 p-0.5">
          {BOARD_PRESETS.map(preset => {
            const Icon = PRESET_ICON[preset]
            const pressed = filters.preset === preset
            return (
              <button key={preset} type="button" aria-pressed={pressed} onClick={() => set({ preset })}
                className={`inline-flex h-7 flex-shrink-0 items-center gap-1 whitespace-nowrap rounded-md px-2 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 motion-reduce:transition-none ${
                  pressed ? 'bg-surface-700 text-white shadow-sm' : 'text-gray-400 hover:bg-surface-800 hover:text-gray-200'}`}>
                {Icon && <Icon size={12} aria-hidden="true" />}
                {t(`kanbanBoard.preset.${preset}`)}
                {preset !== 'all' && (
                  <span className={`tabular-nums ${pressed ? 'text-gray-300' : 'text-gray-500'}`}>{props.presetCounts[preset]}</span>
                )}
              </button>
            )
          })}
        </div>
        {active && (
          <>
            <button type="button" className="btn-ghost btn-sm h-8 gap-1 px-2" onClick={() => onFiltersChange(EMPTY_FILTERS)}>
              <X size={12} aria-hidden="true" />{t('kanbanBoard.toolbar.clear')}
            </button>
            <span className="text-xs tabular-nums text-gray-500">
              {t('kanbanBoard.toolbar.showing', { shown: props.shown, count: props.total })}
            </span>
          </>
        )}
      </div>
    </div>
  )
}
