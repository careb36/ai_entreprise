import React, { useState, useEffect } from 'react'
import { Link, useLocation } from 'react-router-dom'
import {
  ChevronDown,
  ChevronRight,
  ChevronLeft,
  X,
  Menu,
} from 'lucide-react'
import {
  NAV_ITEMS,
  HICKS_LAW_NAV_LIMIT,
  isNavGroupActive,
  filterNavByCapabilities,
  type NavGroup,
} from './navConfig'
import { NavDropdown } from './NavDropdown'

export interface SidebarNavigationProps {
  isOpenMobile?: boolean
  onCloseMobile?: () => void
  badges?: {
    waiting?: number
    activeTasks?: number
  }
  capabilities?: {
    agent_graph?: boolean
    memory?: boolean
  } | null
  defaultCollapsed?: boolean
  className?: string
}

export const SidebarNavigation: React.FC<SidebarNavigationProps> = ({
  isOpenMobile = false,
  onCloseMobile,
  badges = {},
  capabilities = null,
  defaultCollapsed = false,
  className = '',
}) => {
  const location = useLocation()
  const currentPath = location.pathname + location.search
  const [collapsed, setCollapsed] = useState(defaultCollapsed)
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({})
  const [hoveredGroupId, setHoveredGroupId] = useState<string | null>(null)

  const activeNavItems = filterNavByCapabilities(NAV_ITEMS, capabilities).slice(0, HICKS_LAW_NAV_LIMIT)

  // Automatically expand active group on route transition
  useEffect(() => {
    activeNavItems.forEach(group => {
      if (isNavGroupActive(group, location.pathname)) {
        setExpandedGroups(prev => ({ ...prev, [group.id]: true }))
      }
    })
  }, [location.pathname])

  const toggleGroup = (groupId: string) => {
    setExpandedGroups(prev => ({
      ...prev,
      [groupId]: !prev[groupId],
    }))
  }

  const renderBadge = (group: NavGroup) => {
    if (!group.badgeKey) return null
    const count = badges[group.badgeKey]
    if (!count || count <= 0) return null

    return (
      <span
        aria-label={`${count} pending`}
        className="ml-auto inline-flex items-center justify-center px-2 py-0.5 text-xs font-bold leading-none text-white bg-brand-600 rounded-full"
      >
        {count > 99 ? '99+' : count}
      </span>
    )
  }

  const navContent = (
    <div className="flex h-full flex-col justify-between overflow-y-auto overflow-x-hidden p-3">
      <div className="space-y-4">
        {/* Navigation Category Header */}
        {!collapsed && (
          <div className="px-3 pt-2">
            <p className="text-[11px] font-bold uppercase tracking-wider text-gray-400">
              Navigation (5 Domains)
            </p>
          </div>
        )}

        {/* Hick's Law 5 Groups List */}
        <nav aria-label="Main Navigation" className="space-y-1">
          {activeNavItems.map(group => {
            const Icon = group.icon
            const isGroupActive = isNavGroupActive(group, location.pathname)
            const isExpanded = !!expandedGroups[group.id]

            return (
              <div
                key={group.id}
                className="relative"
                onMouseEnter={() => collapsed && setHoveredGroupId(group.id)}
                onMouseLeave={() => collapsed && setHoveredGroupId(null)}
              >
                <div
                  className={`group flex items-center rounded-xl transition-all ${
                    isGroupActive
                      ? 'bg-brand-600/15 text-brand-300 font-medium'
                      : 'text-gray-300 hover:bg-surface-800 hover:text-white'
                  }`}
                >
                  <Link
                    to={group.path}
                    aria-current={location.pathname === group.path ? 'page' : undefined}
                    className={`flex items-center gap-3 flex-1 py-2.5 ${
                      collapsed ? 'justify-center px-2' : 'px-3'
                    }`}
                    title={collapsed ? group.label : undefined}
                  >
                    <Icon
                      size={20}
                      className={`shrink-0 transition-transform ${
                        isGroupActive ? 'text-brand-400' : 'text-gray-400 group-hover:text-white'
                      }`}
                    />
                    {!collapsed && (
                      <div className="flex flex-col text-left">
                        <span className="text-sm font-medium tracking-tight">
                          {group.label}
                        </span>
                        <span className="text-[11px] text-gray-400 group-hover:text-gray-300 truncate max-w-[150px]">
                          {group.description}
                        </span>
                      </div>
                    )}
                  </Link>

                  {!collapsed && (
                    <div className="flex items-center pr-2 gap-1">
                      {renderBadge(group)}
                      <button
                        type="button"
                        aria-expanded={isExpanded}
                        aria-label={`Toggle ${group.label} sub-items`}
                        onClick={() => toggleGroup(group.id)}
                        className="p-1 rounded-md text-gray-400 hover:text-white hover:bg-surface-700/50 transition-colors"
                      >
                        {isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                      </button>
                    </div>
                  )}
                </div>

                {/* Sub-items Accordion (Expanded mode) */}
                {!collapsed && isExpanded && (
                  <ul
                    aria-label={`${group.label} child routes`}
                    className="mt-1 ml-6 space-y-0.5 border-l border-surface-700/60 pl-3 animate-in slide-in-from-top-1 duration-150"
                  >
                    {group.children.map(child => {
                      const isChildActive = currentPath === child.path
                      return (
                        <li key={child.id}>
                          <Link
                            to={child.path}
                            aria-current={isChildActive ? 'page' : undefined}
                            className={`flex items-center justify-between py-1.5 px-2.5 rounded-lg text-xs transition-colors ${
                              isChildActive
                                ? 'bg-brand-600/25 text-brand-300 font-semibold'
                                : 'text-gray-400 hover:text-gray-100 hover:bg-surface-800'
                            }`}
                          >
                            <span>{child.label}</span>
                            {child.badge !== undefined && (
                              <span className="text-[10px] text-brand-300 bg-brand-500/20 px-1.5 py-0.2 rounded">
                                {child.badge}
                              </span>
                            )}
                          </Link>
                        </li>
                      )
                    })}
                  </ul>
                )}

                {/* Popover Flyout for Collapsed Sidebar */}
                {collapsed && (
                  <NavDropdown
                    isOpen={hoveredGroupId === group.id}
                    onClose={() => setHoveredGroupId(null)}
                    items={group.children}
                    currentPath={currentPath}
                    title={group.label}
                  />
                )}
              </div>
            )
          })}
        </nav>
      </div>

      {/* Sidebar Collapse Toggle (Desktop) */}
      <div className="border-t border-surface-800 pt-3">
        <button
          type="button"
          onClick={() => setCollapsed(!collapsed)}
          className={`hidden lg:flex w-full items-center gap-2 rounded-lg py-2 px-3 text-xs text-gray-400 hover:bg-surface-800 hover:text-gray-200 transition-colors ${
            collapsed ? 'justify-center' : 'justify-between'
          }`}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {!collapsed && <span>Collapse Sidebar</span>}
          {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
        </button>
      </div>
    </div>
  )

  return (
    <>
      {/* Desktop Sidebar */}
      <aside
        data-testid="desktop-sidebar"
        aria-label="Sidebar Navigation"
        className={`hidden lg:flex flex-col bg-surface-900 border-r border-surface-700 transition-all duration-200 ease-in-out select-none ${
          collapsed ? 'w-20' : 'w-64'
        } ${className}`}
      >
        {navContent}
      </aside>

      {/* Mobile Drawer Overlay */}
      {isOpenMobile && (
        <div
          data-testid="mobile-backdrop"
          onClick={onCloseMobile}
          className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm lg:hidden transition-opacity animate-in fade-in duration-200"
          aria-hidden="true"
        />
      )}

      {/* Mobile Off-canvas Drawer */}
      <div
        data-testid="mobile-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="Mobile Navigation"
        className={`fixed inset-y-0 left-0 z-50 w-72 bg-surface-900 border-r border-surface-700 shadow-2xl transition-transform duration-250 ease-in-out lg:hidden ${
          isOpenMobile ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="flex h-14 items-center justify-between border-b border-surface-800 px-4">
          <span className="text-sm font-bold text-white tracking-tight">Navigation</span>
          <button
            type="button"
            onClick={onCloseMobile}
            className="rounded-lg p-1.5 text-gray-400 hover:bg-surface-800 hover:text-white"
            aria-label="Close navigation"
          >
            <X size={18} />
          </button>
        </div>
        <div className="h-[calc(100%-3.5rem)]">{navContent}</div>
      </div>
    </>
  )
}
