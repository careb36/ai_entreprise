import { useState, type FC } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronRight, X } from 'lucide-react'
import { getNavGroups, TOP_LEVEL_GROUPS_LIMIT } from './navConfig'
import type { NavigationProps } from './types'

interface SidebarProps extends NavigationProps {
  isOpen: boolean
  onClose: () => void
}

export const SidebarNavigation: FC<SidebarProps> = ({
  currentPath,
  waitingReviewsCount = 0,
  isOpen,
  onClose,
  onNavigate,
}) => {
  const { t } = useTranslation()
  const navGroups = getNavGroups(waitingReviewsCount).slice(0, TOP_LEVEL_GROUPS_LIMIT)

  // Track expanded groups in sidebar (accordion state)
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>(() => {
    // Automatically expand the group corresponding to currentPath
    const initial: Record<string, boolean> = {}
    for (const group of navGroups) {
      if (
        currentPath.startsWith(group.primaryTo) ||
        group.items.some((item) => currentPath.startsWith(item.to))
      ) {
        initial[group.id] = true
      }
    }
    return initial
  })

  const toggleGroup = (groupId: string) => {
    setExpandedGroups((prev) => ({
      ...prev,
      [groupId]: !prev[groupId],
    }))
  }

  if (!isOpen) return null

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={t('nav.sidebarNavigation', { defaultValue: 'Global navigation' })}
      className="fixed inset-0 z-50 flex lg:hidden animate-in fade-in duration-200"
    >
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/75 backdrop-blur-sm transition-opacity"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Drawer panel */}
      <div
        data-testid="responsive-sidebar"
        className="relative flex w-full max-w-xs flex-1 flex-col bg-surface-900 border-r border-surface-700 pt-5 pb-4 shadow-2xl"
      >
        <div className="flex items-center justify-between px-4 pb-4 border-b border-surface-800">
          <Link
            to="/evidence"
            onClick={() => {
              onClose()
              onNavigate?.()
            }}
            className="flex items-center gap-2.5"
          >
            <img src="/fabrique-icon.svg" alt="La Fabrique" width={28} height={28} className="rounded" />
            <span className="text-base font-bold text-white tracking-tight">La Fabrique</span>
          </Link>
          <button
            type="button"
            onClick={onClose}
            aria-label={t('nav.closeSidebar', { defaultValue: 'Close sidebar' })}
            className="rounded-lg p-1.5 text-gray-400 hover:text-white hover:bg-surface-800"
          >
            <X size={20} />
          </button>
        </div>

        {/* Hick's Law: Exactly 5 Top-Level Groups in mobile accordion */}
        <nav
          aria-label={t('nav.mainNavigation', { defaultValue: 'Main navigation' })}
          className="flex-1 overflow-y-auto px-3 py-4 space-y-1.5"
        >
          <div className="px-2 pb-2">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-gray-500">
              {t('nav.primaryMenu', { defaultValue: 'Primary Modules' })}
            </span>
          </div>

          {navGroups.map((group) => {
            const GroupIcon = group.icon
            const isGroupExpanded = Boolean(expandedGroups[group.id])
            const isGroupActive =
              currentPath.startsWith(group.primaryTo) ||
              group.items.some((item) => currentPath.startsWith(item.to))
            const label = t(group.labelKey, { defaultValue: group.defaultLabel })

            return (
              <div
                key={group.id}
                data-testid={`sidebar-group-${group.id}`}
                className="rounded-xl border border-surface-800/80 bg-surface-950/40 overflow-hidden"
              >
                <div className="flex items-center justify-between p-1">
                  <Link
                    to={group.primaryTo}
                    onClick={() => {
                      onClose()
                      onNavigate?.()
                    }}
                    className={`flex flex-1 items-center gap-2.5 px-3 py-2 text-sm font-medium rounded-lg transition-colors ${
                      isGroupActive
                        ? 'text-white bg-brand-600/25 border border-brand-500/40'
                        : 'text-gray-300 hover:text-white hover:bg-surface-800/60'
                    }`}
                  >
                    <GroupIcon size={18} className={isGroupActive ? 'text-brand-400' : 'text-gray-400'} />
                    <span className="truncate">{label}</span>
                    {group.badgeCount !== undefined && group.badgeCount > 0 && (
                      <span className="ml-auto inline-flex items-center px-1.5 py-0.5 rounded-full text-xs font-semibold bg-brand-500/30 text-brand-300">
                        {group.badgeCount}
                      </span>
                    )}
                  </Link>

                  <button
                    type="button"
                    onClick={() => toggleGroup(group.id)}
                    aria-expanded={isGroupExpanded}
                    aria-label={t('nav.toggleSubMenu', {
                      group: label,
                      defaultValue: `Expand ${label} sub-items`,
                    })}
                    className="p-2 text-gray-400 hover:text-white rounded-lg hover:bg-surface-800 transition-colors"
                  >
                    {isGroupExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                  </button>
                </div>

                {isGroupExpanded && (
                  <div className="px-3 pb-2 pt-1 space-y-1 border-t border-surface-800/50">
                    {group.items.map((subItem) => {
                      const SubIcon = subItem.icon
                      const isSubActive =
                        currentPath === subItem.to || currentPath.startsWith(`${subItem.to}/`)
                      const subLabel = t(subItem.labelKey, { defaultValue: subItem.defaultLabel })

                      return (
                        <Link
                          key={subItem.id}
                          to={subItem.to}
                          onClick={() => {
                            onClose()
                            onNavigate?.()
                          }}
                          className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs transition-colors ${
                            isSubActive
                              ? 'bg-brand-600/20 text-white font-medium'
                              : 'text-gray-400 hover:text-white hover:bg-surface-800/50'
                          }`}
                        >
                          {SubIcon && <SubIcon size={14} className={isSubActive ? 'text-brand-400' : 'text-gray-500'} />}
                          <span className="truncate">{subLabel}</span>
                          {subItem.badgeCount !== undefined && subItem.badgeCount > 0 && (
                            <span className="ml-auto text-[10px] bg-brand-500/25 text-brand-300 px-1.5 py-0.5 rounded-full">
                              {subItem.badgeCount}
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
      </div>
    </div>
  )
}
