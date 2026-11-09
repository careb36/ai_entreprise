import React, { useState, useEffect, useId } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  ChevronDown,
  ChevronRight,
  PanelLeftClose,
  PanelLeftOpen,
} from 'lucide-react'
import {
  NAVIGATION_CATEGORIES,
  findActiveCategory,
  isPathActive,
  type NavRootCategory,
  type NavSubItem,
} from '../../navigation/navSchema'

export interface SidebarNavProps {
  /** Capabilities map from useCapabilities */
  capabilities?: {
    agent_graph?: boolean
    memory?: boolean
  }
  /** Number of pending governance inbox items */
  inboxCount?: number
  /** Initial collapsed state for responsive rail mode */
  defaultCollapsed?: boolean
  /** Controlled collapsed state */
  collapsed?: boolean
  /** Callback on collapse toggle */
  onToggleCollapse?: (collapsed: boolean) => void
  /** Custom categories schema override if needed */
  categories?: readonly NavRootCategory[]
  className?: string
}

export function SidebarNav({
  capabilities,
  inboxCount = 0,
  defaultCollapsed = false,
  collapsed: controlledCollapsed,
  onToggleCollapse,
  categories = NAVIGATION_CATEGORIES,
  className = '',
}: SidebarNavProps) {
  const { t } = useTranslation()
  const location = useLocation()
  const baseId = useId()

  const [isInternalCollapsed, setIsInternalCollapsed] = useState(defaultCollapsed)
  const isCollapsed = controlledCollapsed !== undefined ? controlledCollapsed : isInternalCollapsed

  const activeCategoryId = findActiveCategory(location.pathname, categories)

  // Localized accordion state for submenus
  const [expandedCategories, setExpandedCategories] = useState<Record<string, boolean>>(() => {
    const initial: Record<string, boolean> = {}
    if (activeCategoryId) {
      initial[activeCategoryId] = true
    }
    return initial
  })

  // Auto-expand category when route changes to an item under it
  useEffect(() => {
    if (activeCategoryId) {
      setExpandedCategories(prev => ({
        ...prev,
        [activeCategoryId]: true,
      }))
    }
  }, [activeCategoryId])

  const toggleCategory = (categoryId: string) => {
    setExpandedCategories(prev => ({
      ...prev,
      [categoryId]: !prev[categoryId],
    }))
  }

  const handleToggleCollapse = () => {
    const next = !isCollapsed
    if (controlledCollapsed === undefined) {
      setIsInternalCollapsed(next)
    }
    onToggleCollapse?.(next)
  }

  // Filter items by capability flags if set
  const filterItems = (items: readonly NavSubItem[]) => {
    return items.filter(item => {
      if (!item.capability) return true
      if (item.capability === 'agent_graph') {
        return capabilities?.agent_graph === true
      }
      if (item.capability === 'memory') {
        return capabilities?.memory === true
      }
      return true
    })
  }

  return (
    <aside
      aria-label={t('nav.sidebarTitle', { defaultValue: 'Global Navigation' })}
      className={`relative flex flex-col bg-surface-900 border-r border-surface-700 transition-all duration-300 ease-in-out select-none ${
        isCollapsed ? 'w-16' : 'w-64'
      } ${className}`}
      data-testid="sidebar-navigation"
    >
      {/* Top Header & Collapse Toggle */}
      <div className="flex items-center justify-between h-14 px-3 border-b border-surface-800">
        {!isCollapsed && (
          <span className="text-xs font-semibold uppercase tracking-wider text-gray-400 pl-2">
            {t('nav.workspace', { defaultValue: 'Workspaces' })}
          </span>
        )}
        <button
          type="button"
          onClick={handleToggleCollapse}
          aria-label={isCollapsed ? t('nav.expandSidebar', { defaultValue: 'Expand navigation' }) : t('nav.collapseSidebar', { defaultValue: 'Collapse navigation' })}
          className="p-1.5 rounded-lg text-gray-400 hover:text-white hover:bg-surface-800 transition-colors ml-auto focus:outline-none focus:ring-2 focus:ring-brand-500"
          data-testid="sidebar-toggle-btn"
        >
          {isCollapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
        </button>
      </div>

      {/* 5 Top-Level Navigation Choices (Hick's Law) */}
      <nav className="flex-1 overflow-y-auto px-2 py-4 space-y-1" role="navigation">
        {categories.map(category => {
          const visibleItems = filterItems(category.items)
          const isCategoryActive = activeCategoryId === category.id
          const isExpanded = Boolean(expandedCategories[category.id])
          const Icon = category.icon
          const subMenuId = `${baseId}-sub-${category.id}`

          const hasMultipleItems = visibleItems.length > 1
          const singleDestination = visibleItems.length === 1 ? visibleItems[0].to : category.defaultPath
          const hasInboxBadge = visibleItems.some(i => i.badgeKey === 'inboxCount') && inboxCount > 0

          if (isCollapsed) {
            // Collapsed Rail View: Compact icon triggers
            return (
              <div key={category.id} className="relative group flex justify-center py-1">
                <Link
                  to={singleDestination}
                  title={t(category.labelKey, { defaultValue: category.defaultLabel })}
                  aria-label={t(category.labelKey, { defaultValue: category.defaultLabel })}
                  aria-current={isCategoryActive ? 'page' : undefined}
                  className={`flex items-center justify-center w-10 h-10 rounded-lg transition-colors relative ${
                    isCategoryActive
                      ? 'bg-brand-600/20 text-white border border-brand-500/30'
                      : 'text-gray-400 hover:text-white hover:bg-surface-800'
                  }`}
                  data-testid={`rail-category-${category.id}`}
                >
                  <Icon size={20} />
                  {hasInboxBadge && (
                    <span className="absolute top-1 right-1 w-2.5 h-2.5 bg-brand-500 rounded-full ring-2 ring-surface-900" />
                  )}
                </Link>
                {/* Tooltip for collapsed mode */}
                <div className="absolute left-full ml-2 px-2 py-1 bg-surface-800 text-gray-200 text-xs rounded shadow-lg whitespace-nowrap opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto transition-opacity z-50">
                  {t(category.labelKey, { defaultValue: category.defaultLabel })}
                </div>
              </div>
            )
          }

          // Expanded View with Collapsible Nested Menus
          return (
            <div key={category.id} className="space-y-1" data-testid={`nav-root-${category.id}`}>
              {hasMultipleItems ? (
                <button
                  type="button"
                  onClick={() => toggleCategory(category.id)}
                  aria-expanded={isExpanded}
                  aria-controls={subMenuId}
                  className={`w-full flex items-center justify-between px-3 py-2 text-sm font-medium rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-brand-500 ${
                    isCategoryActive
                      ? 'text-white bg-surface-800/80 font-semibold'
                      : 'text-gray-300 hover:text-white hover:bg-surface-800/50'
                  }`}
                  data-testid={`category-btn-${category.id}`}
                >
                  <span className="flex items-center gap-2.5 min-w-0">
                    <Icon size={18} className={isCategoryActive ? 'text-brand-400' : 'text-gray-400'} />
                    <span className="truncate">{t(category.labelKey, { defaultValue: category.defaultLabel })}</span>
                  </span>
                  <span className="flex items-center gap-1.5 ml-2">
                    {hasInboxBadge && (
                      <span className="px-1.5 py-0.5 text-[10px] font-bold bg-brand-600 text-white rounded-full">
                        {inboxCount}
                      </span>
                    )}
                    {isExpanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                  </span>
                </button>
              ) : (
                <Link
                  to={singleDestination}
                  aria-current={isCategoryActive ? 'page' : undefined}
                  className={`flex items-center justify-between px-3 py-2 text-sm font-medium rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-brand-500 ${
                    isCategoryActive
                      ? 'text-white bg-brand-600/20 border border-brand-500/30'
                      : 'text-gray-300 hover:text-white hover:bg-surface-800/50'
                  }`}
                  data-testid={`single-category-link-${category.id}`}
                >
                  <span className="flex items-center gap-2.5 min-w-0">
                    <Icon size={18} className={isCategoryActive ? 'text-brand-400' : 'text-gray-400'} />
                    <span className="truncate">{t(category.labelKey, { defaultValue: category.defaultLabel })}</span>
                  </span>
                  {hasInboxBadge && (
                    <span className="px-1.5 py-0.5 text-[10px] font-bold bg-brand-600 text-white rounded-full">
                      {inboxCount}
                    </span>
                  )}
                </Link>
              )}

              {/* Collapsible Sub-menu */}
              {hasMultipleItems && isExpanded && (
                <div
                  id={subMenuId}
                  role="group"
                  aria-label={t(category.labelKey, { defaultValue: category.defaultLabel })}
                  className="pl-7 pr-2 py-1 space-y-1 transition-all duration-200"
                  data-testid={`submenu-${category.id}`}
                >
                  {visibleItems.map(item => {
                    const active = isPathActive(location.pathname, item.to, item.exact)
                    const SubIcon = item.icon
                    const showBadge = item.badgeKey === 'inboxCount' && inboxCount > 0

                    return (
                      <Link
                        key={item.id}
                        to={item.to}
                        aria-current={active ? 'page' : undefined}
                        className={`flex items-center justify-between px-2.5 py-1.5 text-xs font-medium rounded-md transition-colors ${
                          active
                            ? 'text-brand-300 bg-brand-600/15 font-semibold'
                            : 'text-gray-400 hover:text-gray-200 hover:bg-surface-800'
                        }`}
                        data-testid={`submenu-item-${item.id}`}
                      >
                        <span className="flex items-center gap-2 min-w-0">
                          <SubIcon size={14} className={active ? 'text-brand-400' : 'text-gray-500'} />
                          <span className="truncate">{t(item.labelKey, { defaultValue: item.defaultLabel })}</span>
                        </span>
                        {showBadge && (
                          <span className="ml-auto px-1.5 py-0.2 text-[10px] font-bold bg-brand-600 text-white rounded-full">
                            {inboxCount}
                          </span>
                        )}
                      </Link>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </nav>

      {/* Footer subtle brand indicator */}
      {!isCollapsed && (
        <div className="p-3 border-t border-surface-800 text-[11px] text-gray-500 flex items-center justify-between">
          <span>La Fabrique</span>
          <span className="text-gray-600">v0.9</span>
        </div>
      )}
    </aside>
  )
}
