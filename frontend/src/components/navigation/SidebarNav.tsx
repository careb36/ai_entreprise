import React, { useState, useEffect } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { ChevronDown, ChevronRight, Menu, X } from 'lucide-react'
import { NAVIGATION_CATEGORIES, getActiveCategory, type NavRootCategory } from '../../navigation/navSchema'

interface SidebarNavProps {
  className?: string
  inboxCount?: number
  alertCount?: number
}

export const SidebarNav: React.FC<SidebarNavProps> = ({
  className = '',
  inboxCount = 0,
  alertCount = 0,
}) => {
  const location = useLocation()
  const activeCategory = getActiveCategory(location.pathname)

  // Track expanded category IDs. Default open active category.
  const [openCategories, setOpenCategories] = useState<Record<string, boolean>>(() => {
    const initial: Record<string, boolean> = {}
    if (activeCategory) {
      initial[activeCategory.id] = true
    } else if (NAVIGATION_CATEGORIES.length > 0) {
      initial[NAVIGATION_CATEGORIES[0].id] = true
    }
    return initial
  })

  // Mobile drawer state
  const [mobileOpen, setMobileOpen] = useState(false)

  // Auto-expand active category when route changes
  useEffect(() => {
    if (activeCategory) {
      setOpenCategories((prev) => ({
        ...prev,
        [activeCategory.id]: true,
      }))
    }
    setMobileOpen(false)
  }, [location.pathname, activeCategory])

  const toggleCategory = (categoryId: string) => {
    setOpenCategories((prev) => ({
      ...prev,
      [categoryId]: !prev[categoryId],
    }))
  }

  const renderBadge = (badgeKey?: 'inboxCount' | 'alertCount') => {
    if (badgeKey === 'inboxCount' && inboxCount > 0) {
      return (
        <span
          data-testid="badge-inbox"
          className="ml-auto inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300"
        >
          {inboxCount}
        </span>
      )
    }
    if (badgeKey === 'alertCount' && alertCount > 0) {
      return (
        <span
          data-testid="badge-alert"
          className="ml-auto inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300"
        >
          {alertCount}
        </span>
      )
    }
    return null
  }

  const navContent = (
    <nav
      aria-label="Global Navigation"
      role="navigation"
      className="flex flex-col h-full bg-slate-900 text-slate-100 w-64 border-r border-slate-800 select-none"
    >
      {/* Brand Header */}
      <div className="flex items-center justify-between px-5 py-4 border-b border-slate-800">
        <div className="flex items-center space-x-2">
          <div className="w-8 h-8 rounded-lg bg-indigo-600 flex items-center justify-center font-bold text-white shadow-sm">
            LF
          </div>
          <span className="font-semibold text-base tracking-tight text-white">
            La Fabrique
          </span>
        </div>
        <button
          type="button"
          aria-label="Close mobile navigation"
          onClick={() => setMobileOpen(false)}
          className="md:hidden text-slate-400 hover:text-white p-1 rounded focus:outline-none focus:ring-2 focus:ring-indigo-500"
        >
          <X size={20} />
        </button>
      </div>

      {/* Top-Level Choices (Capped at 5 per Hick's Law) */}
      <div
        data-testid="nav-root-categories"
        className="flex-1 overflow-y-auto px-3 py-4 space-y-2"
      >
        <div className="px-2 pb-2 text-[11px] font-semibold tracking-wider uppercase text-slate-400">
          Core Workspaces ({NAVIGATION_CATEGORIES.length})
        </div>

        {NAVIGATION_CATEGORIES.map((category: NavRootCategory) => {
          const Icon = category.icon
          const isCategoryActive = activeCategory?.id === category.id
          const isExpanded = !!openCategories[category.id]

          return (
            <div
              key={category.id}
              data-testid={`nav-category-${category.id}`}
              className="rounded-lg transition-colors"
            >
              <button
                type="button"
                onClick={() => toggleCategory(category.id)}
                aria-expanded={isExpanded}
                aria-controls={`subnav-${category.id}`}
                className={`w-full flex items-center justify-between px-3 py-2.5 rounded-lg text-sm font-medium transition-all duration-150 ${
                  isCategoryActive
                    ? 'bg-slate-800/90 text-white font-semibold'
                    : 'text-slate-300 hover:bg-slate-800/50 hover:text-white'
                }`}
              >
                <div className="flex items-center space-x-3 truncate">
                  <Icon
                    size={18}
                    className={isCategoryActive ? 'text-indigo-400' : 'text-slate-400'}
                  />
                  <span className="truncate">{category.defaultLabel}</span>
                </div>
                <div className="text-slate-400 flex items-center pl-1">
                  {isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                </div>
              </button>

              {/* Sub-items drawer */}
              {isExpanded && (
                <div
                  id={`subnav-${category.id}`}
                  role="region"
                  aria-label={`${category.defaultLabel} sub-items`}
                  className="mt-1 ml-4 pl-3 border-l border-slate-800 space-y-1"
                >
                  {category.items.map((sub) => {
                    const SubIcon = sub.icon
                    return (
                      <NavLink
                        key={sub.id}
                        to={sub.to}
                        end={sub.exact}
                        data-testid={`nav-subitem-${sub.id}`}
                        className={({ isActive }) =>
                          `group flex items-center px-2.5 py-1.5 text-xs rounded-md transition-colors ${
                            isActive
                              ? 'bg-indigo-600/20 text-indigo-300 font-semibold border-l-2 border-indigo-500 -ml-[13px] pl-[11px]'
                              : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
                          }`
                        }
                      >
                        <SubIcon size={14} className="mr-2 text-slate-400 group-hover:text-slate-200" />
                        <span className="truncate">{sub.defaultLabel}</span>
                        {renderBadge(sub.badgeKey)}
                      </NavLink>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Footer Status */}
      <div className="p-3 border-t border-slate-800 text-[11px] text-slate-400 flex items-center justify-between">
        <span className="flex items-center space-x-1.5">
          <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
          <span>Evidence Engine: Online</span>
        </span>
        <span className="text-slate-500">v1.2</span>
      </div>
    </nav>
  )

  return (
    <>
      {/* Mobile hamburger button */}
      <div className="md:hidden flex items-center p-3 bg-slate-900 border-b border-slate-800 text-white">
        <button
          type="button"
          aria-label="Open mobile navigation"
          onClick={() => setMobileOpen(true)}
          className="p-1 rounded text-slate-300 hover:text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
        >
          <Menu size={22} />
        </button>
        <span className="ml-3 font-semibold text-sm">La Fabrique</span>
      </div>

      {/* Mobile Drawer Backdrop */}
      {mobileOpen && (
        <div
          role="presentation"
          onClick={() => setMobileOpen(false)}
          className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm md:hidden"
        />
      )}

      {/* Mobile Drawer Panel */}
      <div
        className={`fixed inset-y-0 left-0 z-50 transform md:hidden transition-transform duration-200 ease-in-out ${
          mobileOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        {navContent}
      </div>

      {/* Desktop Persistent Sidebar */}
      <aside className={`hidden md:flex md:flex-shrink-0 h-screen sticky top-0 ${className}`}>
        {navContent}
      </aside>
    </>
  )
}

export default SidebarNav
