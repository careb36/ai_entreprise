import { useState, type FC } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Menu } from 'lucide-react'
import { NavDropdown } from './NavDropdown'
import { SidebarNavigation } from './SidebarNavigation'
import { getNavGroups, TOP_LEVEL_GROUPS_LIMIT } from './navConfig'

interface GlobalNavProps {
  waitingReviewsCount?: number
  onNavigate?: () => void
}

export const GlobalNav: FC<GlobalNavProps> = ({
  waitingReviewsCount = 0,
  onNavigate,
}) => {
  const { t } = useTranslation()
  const location = useLocation()
  const [sidebarOpen, setSidebarOpen] = useState(false)

  // Hick's Law: Exactly 5 top-level grouped navigation targets
  const navGroups = getNavGroups(waitingReviewsCount).slice(0, TOP_LEVEL_GROUPS_LIMIT)

  return (
    <>
      {/* Mobile / Tablet Hamburger Toggle */}
      <button
        type="button"
        data-testid="sidebar-toggle"
        onClick={() => setSidebarOpen(true)}
        aria-label={t('nav.openNavigation', { defaultValue: 'Open navigation' })}
        aria-expanded={sidebarOpen}
        className="lg:hidden p-2 text-gray-400 hover:text-white rounded-lg hover:bg-surface-800 focus:outline-none focus:ring-2 focus:ring-brand-500"
      >
        <Menu size={20} />
      </button>

      {/* Desktop Hick's Law Navigation (Strictly 5 Top-Level Choices) */}
      <nav
        data-testid="desktop-nav"
        aria-label={t('nav.mainNavigation', { defaultValue: 'Main navigation' })}
        className="hidden lg:flex items-center gap-1.5 flex-1 min-w-0 ml-4"
      >
        {navGroups.map((group) => {
          const isActive =
            location.pathname.startsWith(group.primaryTo) ||
            group.items.some((item) => location.pathname.startsWith(item.to))

          return (
            <NavDropdown
              key={group.id}
              group={group}
              isActive={isActive}
              currentPath={location.pathname}
              onNavigate={onNavigate}
            />
          )
        })}
      </nav>

      {/* Responsive Collapsible Sidebar Drawer */}
      <SidebarNavigation
        isOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        currentPath={location.pathname}
        waitingReviewsCount={waitingReviewsCount}
        onNavigate={onNavigate}
      />
    </>
  )
}
